"""The ledger (T1.16-a, DM-6 schema; C-35): transactions, movements, balances.

Additive. No data moves here: the opening balances come with T1.17 (DM-6 backfill), and the
existing writers move onto the ledger in T1.18.

Revision ID: 632386e0eb3f
Revises: 6d82b619c66f
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import Qty

revision = "632386e0eb3f"
down_revision = "6d82b619c66f"
branch_labels = None
depends_on = None

INCREASES = "'production_output', 'receipt', 'transfer_in'"
DECREASES = "'manual_consumption', 'production_input', 'sale_consumption', 'transfer_out', 'waste'"
EITHER_WAY = "'adjustment', 'count_variance', 'opening_balance', 'pos_correction', 'reversal'"
KINDS = (
    "'adjustment', 'count', 'opening', 'pos_correction', 'production', 'receipt', 'reversal',"
    " 'sale', 'transfer', 'usage', 'waste'"
)


def upgrade() -> None:
    op.create_table(
        "inventory_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reverses_transaction_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_inventory_transactions_kind"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="inventory_transactions_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="inventory_transactions_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="inventory_transactions_actor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_transaction_id"],
            ["inventory_transactions.id"],
            name="inventory_transactions_reverses_transaction_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    with op.batch_alter_table("inventory_transactions") as batch:
        batch.create_index("ix_inventory_transactions_tenant_id", ["tenant_id"], unique=False)
        batch.create_index("ix_inventory_transactions_location_id", ["location_id"], unique=False)
        batch.create_index(
            "ix_inventory_transactions_reverses_transaction_id",
            ["reverses_transaction_id"],
            unique=False,
        )
        batch.create_index(
            "ix_inventory_transactions_source",
            ["tenant_id", "source_type", "source_id"],
            unique=False,
        )

    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("allocation_mode", sa.String(length=10), nullable=False),
        sa.Column("movement_type", sa.String(length=24), nullable=False),
        sa.Column("qty_base", Qty(), nullable=False),
        sa.Column("unit_cost_snapshot", Qty(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("reverses_movement_id", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"movement_type IN ({INCREASES}, {DECREASES}, {EITHER_WAY})",
            name="ck_inventory_movements_movement_type",
        ),
        sa.CheckConstraint(
            "allocation_mode IN ('explicit', 'inferred', 'none')",
            name="ck_inventory_movements_allocation_mode",
        ),
        sa.CheckConstraint(
            f"(movement_type IN ({INCREASES}) AND CAST(qty_base AS NUMERIC) > 0)"
            f" OR (movement_type IN ({DECREASES}) AND CAST(qty_base AS NUMERIC) < 0)"
            f" OR (movement_type IN ({EITHER_WAY}) AND CAST(qty_base AS NUMERIC) <> 0)",
            name="ck_inventory_movements_sign_by_type",
        ),
        sa.CheckConstraint(
            "CAST(unit_cost_snapshot AS NUMERIC) >= 0",
            name="ck_inventory_movements_unit_cost_snapshot_not_negative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="inventory_movements_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["inventory_transactions.id"],
            name="inventory_movements_transaction_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="inventory_movements_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="inventory_movements_inventory_item_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["stock_batches.id"],
            name="inventory_movements_batch_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_movement_id"],
            ["inventory_movements.id"],
            name="inventory_movements_reverses_movement_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("inventory_movements") as batch:
        batch.create_index(
            "ix_inventory_movements_transaction_id", ["transaction_id"], unique=False
        )
        batch.create_index(
            "ix_inventory_movements_item_time",
            ["tenant_id", "location_id", "inventory_item_id", "occurred_at"],
            unique=False,
        )
        batch.create_index("ix_inventory_movements_batch", ["tenant_id", "batch_id"], unique=False)

    op.create_table(
        "stock_balances",
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("qty_base", Qty(), nullable=False),
        sa.Column("last_movement_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="stock_balances_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="stock_balances_inventory_item_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="stock_balances_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_movement_id"],
            ["inventory_movements.id"],
            name="stock_balances_last_movement_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("location_id", "inventory_item_id"),
    )
    with op.batch_alter_table("stock_balances") as batch:
        batch.create_index("ix_stock_balances_tenant_id", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_table("stock_balances")
    op.drop_table("inventory_movements")
    op.drop_table("inventory_transactions")
