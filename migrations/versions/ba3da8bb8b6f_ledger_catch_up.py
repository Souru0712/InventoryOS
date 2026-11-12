"""Ledger catch-up (T1.18, ADR-0041 consequence, ADR-0042): the ledger equals the stock rows again.

Data only. Between the opening balances (a6eec9216078) and this release the old writers still
changed `stock_items.qty_base` directly, and new stock rows were added without a balance. Before
the posting service becomes the only writer, for every stock row (one item at one location):
- with a balance that differs from its quantity: one `adjustment` transaction holding one
  `adjustment` movement of the difference;
- with no balance: one `opening` transaction holding one `opening_balance` movement of its
  quantity (none for zero), and its balance row.
Each is posted by the system now, at the location's current cost, keyed
`migration:catch-up:<item>:<location>`. Running it again posts nothing.

SQL, not app/ledger, for the reason the opening balances give (ADR-0041).

Revision ID: ba3da8bb8b6f
Revises: a6eec9216078
Create Date: 2026-11-12
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "ba3da8bb8b6f"
down_revision = "a6eec9216078"
branch_labels = None
depends_on = None

PLACES = Decimal("0.0001")
ADJUSTED = "Catch-up: stock changed outside the ledger before T1.18 (ADR-0041)"
OPENED = "Opening balance: stock row added outside the ledger before T1.18 (ADR-0041)"

transactions = sa.table(
    "inventory_transactions",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("location_id"),
    sa.column("kind"),
    sa.column("source_type"),
    sa.column("source_id"),
    sa.column("revision"),
    sa.column("idempotency_key"),
    sa.column("actor_user_id"),
    sa.column("reason"),
    sa.column("occurred_at", UTCDateTime()),
    sa.column("recorded_at", UTCDateTime()),
)
movements = sa.table(
    "inventory_movements",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("transaction_id"),
    sa.column("location_id"),
    sa.column("inventory_item_id"),
    sa.column("allocation_mode"),
    sa.column("movement_type"),
    sa.column("qty_base", Qty()),
    sa.column("unit_cost_snapshot", Qty()),
    sa.column("reason"),
    sa.column("occurred_at", UTCDateTime()),
    sa.column("recorded_at", UTCDateTime()),
)
balances = sa.table(
    "stock_balances",
    sa.column("location_id"),
    sa.column("inventory_item_id"),
    sa.column("tenant_id"),
    sa.column("qty_base", Qty()),
    sa.column("last_movement_id"),
    sa.column("updated_at", UTCDateTime()),
)
stock = sa.table(
    "stock_items",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("location_id"),
    sa.column("inventory_item_id"),
    sa.column("qty_base", Qty()),
    sa.column("cost_per_base_cents", Qty()),
)


def _post(connection: sa.Connection, row, kind: str, movement: str, qty: Decimal, now) -> int:
    """One transaction with one movement; the movement's id."""
    pair = f"{row.inventory_item_id}:{row.location_id}"
    reason = ADJUSTED if kind == "adjustment" else OPENED
    transaction_id = connection.execute(
        sa.insert(transactions)
        .values(
            tenant_id=row.tenant_id,
            location_id=row.location_id,
            kind=kind,
            source_type="migration",
            source_id=f"catch-up:{pair}",
            revision=0,
            idempotency_key=f"migration:catch-up:{pair}",
            actor_user_id=None,
            reason=reason,
            occurred_at=now,
            recorded_at=now,
        )
        .returning(transactions.c.id)
    ).scalar_one()
    # A cost below zero is a slip in the stock row; the ledger keeps costs at zero or above.
    cost = max(Decimal(row.cost_per_base_cents or 0), Decimal("0"))
    return connection.execute(
        sa.insert(movements)
        .values(
            tenant_id=row.tenant_id,
            transaction_id=transaction_id,
            location_id=row.location_id,
            inventory_item_id=row.inventory_item_id,
            allocation_mode="none",
            movement_type=movement,
            qty_base=qty,
            unit_cost_snapshot=cost,
            reason=reason,
            occurred_at=now,
            recorded_at=now,
        )
        .returning(movements.c.id)
    ).scalar_one()


def catch_up(connection: sa.Connection) -> int:
    """Post what the ledger is missing; the number of transactions posted."""
    now = datetime.now(UTC)
    posted = 0
    rows = connection.execute(
        sa.select(stock)
        .where(stock.c.inventory_item_id.is_not(None))
        .order_by(stock.c.tenant_id, stock.c.inventory_item_id, stock.c.location_id)
    ).all()
    for row in rows:
        quantity = Decimal(row.qty_base or 0).quantize(PLACES)
        key = f"migration:catch-up:{row.inventory_item_id}:{row.location_id}"
        already = connection.execute(
            sa.select(transactions.c.id).where(
                transactions.c.tenant_id == row.tenant_id,
                transactions.c.idempotency_key == key,
            )
        ).first()
        if already is not None:
            continue
        where = (
            balances.c.location_id == row.location_id,
            balances.c.inventory_item_id == row.inventory_item_id,
        )
        held = connection.execute(sa.select(balances.c.qty_base).where(*where)).first()
        if held is None:
            last = None
            if quantity != 0:
                last = _post(connection, row, "opening", "opening_balance", quantity, now)
                posted += 1
            connection.execute(
                sa.insert(balances).values(
                    location_id=row.location_id,
                    inventory_item_id=row.inventory_item_id,
                    tenant_id=row.tenant_id,
                    qty_base=quantity,
                    last_movement_id=last,
                    updated_at=now,
                )
            )
            continue
        difference = quantity - Decimal(held.qty_base or 0)
        if difference == 0:
            continue
        last = _post(connection, row, "adjustment", "adjustment", difference, now)
        posted += 1
        connection.execute(
            sa.update(balances)
            .where(*where)
            .values(qty_base=quantity, last_movement_id=last, updated_at=now)
        )
    return posted


def upgrade() -> None:
    catch_up(op.get_bind())


def downgrade() -> None:
    connection = op.get_bind()
    caught_up = sa.select(transactions.c.id).where(
        transactions.c.source_type == "migration",
        transactions.c.source_id.like("catch-up:%"),
    )
    posted = connection.execute(
        sa.select(movements.c.location_id, movements.c.inventory_item_id)
        .where(movements.c.transaction_id.in_(caught_up))
        .distinct()
    ).all()
    for location_id, item_id in posted:
        # A balance may point at a movement about to go; it is pointed again below.
        connection.execute(
            sa.update(balances)
            .where(balances.c.location_id == location_id, balances.c.inventory_item_id == item_id)
            .values(last_movement_id=None)
        )
    connection.execute(sa.delete(movements).where(movements.c.transaction_id.in_(caught_up)))
    connection.execute(
        sa.delete(transactions).where(
            transactions.c.source_type == "migration",
            transactions.c.source_id.like("catch-up:%"),
        )
    )
    # Each balance goes back to what its remaining movements add up to. A pair this migration
    # opened keeps its balance row, at zero, as the opening balances give every pair one.
    for location_id, item_id in posted:
        pair = (
            movements.c.location_id == location_id,
            movements.c.inventory_item_id == item_id,
        )
        total = connection.execute(sa.select(movements.c.qty_base).where(*pair)).scalars().all()
        last = connection.execute(sa.select(sa.func.max(movements.c.id)).where(*pair)).scalar()
        connection.execute(
            sa.update(balances)
            .where(balances.c.location_id == location_id, balances.c.inventory_item_id == item_id)
            .values(
                qty_base=sum((Decimal(q) for q in total), Decimal("0")),
                last_movement_id=last,
                updated_at=datetime.now(UTC),
            )
        )
