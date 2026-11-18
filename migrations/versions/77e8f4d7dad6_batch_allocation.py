"""Batch allocation (T2.06-a, DM-10): batches hold what their movements say.

Expand + backfill.
- `inventory_items.batch_order` (fefo or fifo, default fefo) and `stock_batches.lot_code`.
- The ledger gains the `batch_assignment` kind and movement type: stock moved between a batch
  and undated stock, net zero for the item.
- Every existing batch is given what it held at migration, worked out as before (the item's
  recorded quantity spread over its batches from the latest expiry back, each up to what it
  arrived with), by one `batch_assignment` transaction from undated stock, keyed
  `migration:DM-10:batch:<id>`. No balance changes. Running it again posts nothing.

It writes with SQL, not through app/ledger: a migration must not depend on application code
that will keep changing. The downgrade deletes the assignments (net zero, so no balance moves)
and restores the constraints.

Revision ID: 77e8f4d7dad6
Revises: 1da987c1c195
Create Date: 2026-11-18
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "77e8f4d7dad6"
down_revision = "1da987c1c195"
branch_labels = None
depends_on = None

PLACES = Decimal("0.0001")
REASON = "Batch allocation began: what each batch held when the ledger started tracking it (DM-10)"
INCREASES = "'production_output', 'receipt', 'transfer_in'"
DECREASES = "'manual_consumption', 'production_input', 'sale_consumption', 'transfer_out', 'waste'"
EITHER_WAY = "'adjustment', 'count_variance', 'opening_balance', 'pos_correction', 'reversal'"
KINDS = (
    "'adjustment', 'count', 'opening', 'pos_correction', 'production', 'receipt', 'reversal',"
    " 'sale', 'transfer', 'usage', 'waste'"
)
# As the models write them: sorted, with the new kind and type in place.
EITHER_WAY_NOW = (
    "'adjustment', 'batch_assignment', 'count_variance', 'opening_balance', 'pos_correction',"
    " 'reversal'"
)
KINDS_NOW = (
    "'adjustment', 'batch_assignment', 'count', 'opening', 'pos_correction', 'production',"
    " 'receipt', 'reversal', 'sale', 'transfer', 'usage', 'waste'"
)

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
    sa.column("batch_id"),
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
    sa.column("qty_base", Qty()),
    sa.column("last_movement_id"),
)
stock = sa.table(
    "stock_items",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("location_id"),
    sa.column("inventory_item_id"),
    sa.column("cost_per_base_cents", Qty()),
)
batches = sa.table(
    "stock_batches",
    sa.column("id"),
    sa.column("stock_item_id"),
    sa.column("received_qty_base", Qty()),
    sa.column("expires_on", sa.Date()),
)


def _ledger_checks(either_way: str, kinds: str) -> None:
    with op.batch_alter_table("inventory_transactions") as batch:
        batch.drop_constraint("ck_inventory_transactions_kind", type_="check")
        batch.create_check_constraint("ck_inventory_transactions_kind", f"kind IN ({kinds})")
    with op.batch_alter_table("inventory_movements") as batch:
        batch.drop_constraint("ck_inventory_movements_movement_type", type_="check")
        batch.drop_constraint("ck_inventory_movements_sign_by_type", type_="check")
        batch.create_check_constraint(
            "ck_inventory_movements_movement_type",
            f"movement_type IN ({INCREASES}, {DECREASES}, {either_way})",
        )
        batch.create_check_constraint(
            "ck_inventory_movements_sign_by_type",
            f"(movement_type IN ({INCREASES}) AND CAST(qty_base AS NUMERIC) > 0)"
            f" OR (movement_type IN ({DECREASES}) AND CAST(qty_base AS NUMERIC) < 0)"
            f" OR (movement_type IN ({either_way}) AND CAST(qty_base AS NUMERIC) <> 0)",
        )


def backfill(connection: sa.Connection) -> int:
    """Give every batch what it held at migration; the number of batches given stock."""
    now = datetime.now(UTC)
    lots = defaultdict(list)
    for row in connection.execute(sa.select(batches)):
        lots[row.stock_item_id].append(row)
    if not lots:
        return 0
    given = 0
    for row in connection.execute(
        sa.select(stock).where(stock.c.id.in_(list(lots)), stock.c.inventory_item_id.is_not(None))
    ):
        held = connection.execute(
            sa.select(balances.c.qty_base).where(
                balances.c.location_id == row.location_id,
                balances.c.inventory_item_id == row.inventory_item_id,
            )
        ).scalar()
        left = max(Decimal(held or 0), Decimal(0))
        cost = max(Decimal(row.cost_per_base_cents or 0), Decimal(0))
        # As the product showed it until now: sales took the soonest-dated stock first, so what
        # is left is the latest-dated, each batch up to what it arrived with.
        for lot in sorted(lots[row.id], key=lambda b: (b.expires_on, b.id), reverse=True):
            part = min(Decimal(lot.received_qty_base), left).quantize(PLACES)
            left -= part
            key = f"migration:DM-10:batch:{lot.id}"
            already = connection.execute(
                sa.select(transactions.c.id).where(
                    transactions.c.tenant_id == row.tenant_id,
                    transactions.c.idempotency_key == key,
                )
            ).first()
            if part <= 0 or already is not None:
                continue
            transaction_id = connection.execute(
                sa.insert(transactions)
                .values(
                    tenant_id=row.tenant_id,
                    location_id=row.location_id,
                    kind="batch_assignment",
                    source_type="migration",
                    source_id=f"DM-10:batch:{lot.id}",
                    revision=0,
                    idempotency_key=key,
                    actor_user_id=None,
                    reason=REASON,
                    occurred_at=now,
                    recorded_at=now,
                )
                .returning(transactions.c.id)
            ).scalar_one()
            last = None
            for batch_id, qty, mode in ((None, -part, "none"), (lot.id, part, "explicit")):
                last = connection.execute(
                    sa.insert(movements)
                    .values(
                        tenant_id=row.tenant_id,
                        transaction_id=transaction_id,
                        location_id=row.location_id,
                        inventory_item_id=row.inventory_item_id,
                        batch_id=batch_id,
                        allocation_mode=mode,
                        movement_type="batch_assignment",
                        qty_base=qty,
                        unit_cost_snapshot=cost,
                        reason=REASON,
                        occurred_at=now,
                        recorded_at=now,
                    )
                    .returning(movements.c.id)
                ).scalar_one()
            connection.execute(
                sa.update(balances)
                .where(
                    balances.c.location_id == row.location_id,
                    balances.c.inventory_item_id == row.inventory_item_id,
                )
                .values(last_movement_id=last)
            )
            given += 1
    return given


def upgrade() -> None:
    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(
            sa.Column("batch_order", sa.String(length=4), nullable=False, server_default="fefo")
        )
        batch.create_check_constraint(
            "ck_inventory_items_batch_order", "batch_order IN ('fefo', 'fifo')"
        )
    with op.batch_alter_table("inventory_items") as batch:
        batch.alter_column("batch_order", server_default=None)
    with op.batch_alter_table("stock_batches") as batch:
        batch.add_column(sa.Column("lot_code", sa.String(length=80), nullable=True))
    _ledger_checks(EITHER_WAY_NOW, KINDS_NOW)
    backfill(op.get_bind())


def downgrade() -> None:
    connection = op.get_bind()
    assigned = sa.select(transactions.c.id).where(transactions.c.kind == "batch_assignment")
    # Every assignment nets to zero, so no balance moves; the last movement is re-pointed.
    connection.execute(sa.delete(movements).where(movements.c.transaction_id.in_(assigned)))
    connection.execute(sa.delete(transactions).where(transactions.c.kind == "batch_assignment"))
    connection.execute(
        sa.update(balances).values(
            last_movement_id=sa.select(sa.func.max(movements.c.id))
            .where(
                movements.c.location_id == balances.c.location_id,
                movements.c.inventory_item_id == balances.c.inventory_item_id,
            )
            .scalar_subquery()
        )
    )
    _ledger_checks(EITHER_WAY, KINDS)
    with op.batch_alter_table("stock_batches") as batch:
        batch.drop_column("lot_code")
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_constraint("ck_inventory_items_batch_order", type_="check")
        batch.drop_column("batch_order")
