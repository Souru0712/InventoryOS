"""Opening balances (T1.17, DM-6 backfill): the ledger starts equal to what the product shows.

Data only. For every stock row (one item at one location) whose quantity is not zero, one
`opening` transaction holding one `opening_balance` movement of exactly that quantity, at the
location's current cost, posted by the system now, keyed `migration:opening:<item>:<location>`.
Every pair gets its `stock_balances` row, zero ones included. Running it again posts nothing: a
key already there is skipped.

It writes with SQL, not through app/ledger: a migration must not depend on application code that
will keep changing. The rules it follows are the posting service's (DR-01..05, DR-19).

Revision ID: a6eec9216078
Revises: 4166ed9858d6
Create Date: 2026-11-12
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "a6eec9216078"
down_revision = "4166ed9858d6"
branch_labels = None
depends_on = None

PLACES = Decimal("0.0001")
REASON = "Opening balance: the quantity the product showed when the ledger began (DM-6)"

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


def backfill(connection: sa.Connection) -> int:
    """Post the opening balances that are missing; the number of transactions posted."""
    now = datetime.now(UTC)
    posted = 0
    rows = connection.execute(
        sa.select(stock)
        .where(stock.c.inventory_item_id.is_not(None))
        .order_by(stock.c.tenant_id, stock.c.inventory_item_id, stock.c.location_id)
    ).all()
    for row in rows:
        quantity = Decimal(row.qty_base or 0).quantize(PLACES)
        # A cost below zero is a typing slip in the legacy row; the ledger keeps costs at zero
        # or above (DR-19), so it starts from zero rather than refusing the whole migration.
        cost = max(Decimal(row.cost_per_base_cents or 0), Decimal("0"))
        key = f"migration:opening:{row.inventory_item_id}:{row.location_id}"
        already = connection.execute(
            sa.select(transactions.c.id).where(
                transactions.c.tenant_id == row.tenant_id,
                transactions.c.idempotency_key == key,
            )
        ).first()
        has_balance = connection.execute(
            sa.select(balances.c.location_id).where(
                balances.c.location_id == row.location_id,
                balances.c.inventory_item_id == row.inventory_item_id,
            )
        ).first()
        if already is not None or has_balance is not None:
            continue
        last_movement = None
        if quantity != 0:
            transaction_id = connection.execute(
                sa.insert(transactions)
                .values(
                    tenant_id=row.tenant_id,
                    location_id=row.location_id,
                    kind="opening",
                    source_type="migration",
                    source_id=f"opening:{row.inventory_item_id}:{row.location_id}",
                    revision=0,
                    idempotency_key=key,
                    actor_user_id=None,
                    reason=REASON,
                    occurred_at=now,
                    recorded_at=now,
                )
                .returning(transactions.c.id)
            ).scalar_one()
            last_movement = connection.execute(
                sa.insert(movements)
                .values(
                    tenant_id=row.tenant_id,
                    transaction_id=transaction_id,
                    location_id=row.location_id,
                    inventory_item_id=row.inventory_item_id,
                    allocation_mode="none",
                    movement_type="opening_balance",
                    qty_base=quantity,
                    unit_cost_snapshot=cost,
                    reason=REASON,
                    occurred_at=now,
                    recorded_at=now,
                )
                .returning(movements.c.id)
            ).scalar_one()
            posted += 1
        connection.execute(
            sa.insert(balances).values(
                location_id=row.location_id,
                inventory_item_id=row.inventory_item_id,
                tenant_id=row.tenant_id,
                qty_base=quantity,
                last_movement_id=last_movement,
                updated_at=now,
            )
        )
    return posted


def upgrade() -> None:
    backfill(op.get_bind())


def downgrade() -> None:
    connection = op.get_bind()
    opening = sa.select(transactions.c.id).where(
        transactions.c.source_type == "migration", transactions.c.kind == "opening"
    )
    # Nothing else had posted when these were written; balances start again from nothing.
    connection.execute(sa.delete(balances))
    connection.execute(sa.delete(movements).where(movements.c.transaction_id.in_(opening)))
    connection.execute(
        sa.delete(transactions).where(
            transactions.c.source_type == "migration", transactions.c.kind == "opening"
        )
    )
