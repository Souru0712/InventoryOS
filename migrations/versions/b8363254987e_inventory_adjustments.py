"""Manual adjustments (T1.22): `inventory_adjustments`, and `adjustment_review` as an exception.

Additive. An adjustment is the source record of one `adjustment` transaction. The exceptions type
CHECK gains `adjustment_review`. The partial unique index on open exceptions is rebuilt after the
CHECK changes, because SQLite rebuilds the table to alter a constraint.

Revision ID: b8363254987e
Revises: 6bedfbcf985f
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import Qty

revision = "b8363254987e"
down_revision = "6bedfbcf985f"
branch_labels = None
depends_on = None

TYPES = (
    "'unmapped_product', 'negative_balance', 'count_variance_high', 'batch_expired_unresolved',"
    " 'batch_shortfall', 'sync_failure', 'pos_reconciliation_mismatch', 'receiving_discrepancy',"
    " 'price_variance', 'balance_drift', 'missing_cost', 'conversion_missing', 'sales_missing',"
    " 'sales_source_overlap', 'out_of_stock', 'low_stock', 'stale_count', 'waste_risk'"
)
OPEN = "status IN ('open', 'acknowledged')"


def _exception_types(types: str) -> None:
    with op.batch_alter_table("exceptions") as batch:
        batch.drop_constraint("ck_exceptions_type", type_="check")
        batch.create_check_constraint("ck_exceptions_type", f"type IN ({types})")
    # Rebuilt explicitly: a table rebuild must not leave this index without its WHERE.
    op.drop_index("uq_exceptions_open", table_name="exceptions")
    op.create_index(
        "uq_exceptions_open",
        "exceptions",
        ["tenant_id", "type", "subject_type", "subject_id"],
        unique=True,
        sqlite_where=sa.text(OPEN),
        postgresql_where=sa.text(OPEN),
    )


def upgrade() -> None:
    op.create_table(
        "inventory_adjustments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("qty_base", Qty(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("override_reason", sa.String(length=300), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("value_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reason IN ('found', 'correction', 'sample', 'transfer_in', 'other')",
            name="ck_inventory_adjustments_reason",
        ),
        sa.CheckConstraint(
            "CAST(qty_base AS NUMERIC) <> 0", name="ck_inventory_adjustments_qty_base_not_zero"
        ),
        sa.CheckConstraint(
            "reason <> 'other' OR note IS NOT NULL",
            name="ck_inventory_adjustments_other_has_note",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="inventory_adjustments_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="inventory_adjustments_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="inventory_adjustments_inventory_item_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="inventory_adjustments_actor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["inventory_transactions.id"],
            name="inventory_adjustments_transaction_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("inventory_adjustments") as batch:
        batch.create_index(
            "ix_inventory_adjustments_item", ["tenant_id", "inventory_item_id"], unique=False
        )
        batch.create_index("ix_inventory_adjustments_location_id", ["location_id"], unique=False)
    _exception_types(TYPES + ", 'adjustment_review', 'job_failed'")


def downgrade() -> None:
    op.execute("DELETE FROM exceptions WHERE type = 'adjustment_review'")
    _exception_types(TYPES + ", 'job_failed'")
    op.drop_table("inventory_adjustments")
