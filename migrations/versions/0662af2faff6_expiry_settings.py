"""Expiry settings (T2.08; BAT-FR-09, -11, PD-29): the alert window and auto-dispose.

Additive.
- `tenants.expiry_alert_days` (2) and `tenants.auto_dispose_prepared` (off).
- `inventory_items.expiry_alert_days` and `inventory_categories.expiry_alert_days`, empty meaning
  the next level up.
- The exceptions type CHECK gains `expiring_soon`.

Revision ID: 0662af2faff6
Revises: 2f829ccb897e
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0662af2faff6"
down_revision = "2f829ccb897e"
branch_labels = None
depends_on = None

BEFORE = (
    "'unmapped_product', 'negative_balance', 'count_variance_high', 'batch_expired_unresolved',"
    " 'batch_shortfall', 'sync_failure', 'pos_reconciliation_mismatch', 'receiving_discrepancy',"
    " 'price_variance', 'balance_drift', 'missing_cost', 'conversion_missing', 'sales_missing',"
    " 'sales_source_overlap', 'out_of_stock', 'low_stock', 'stale_count', 'waste_risk',"
    " 'adjustment_review', 'job_failed'"
)
AFTER = (
    "'unmapped_product', 'negative_balance', 'count_variance_high', 'batch_expired_unresolved',"
    " 'batch_shortfall', 'sync_failure', 'pos_reconciliation_mismatch', 'receiving_discrepancy',"
    " 'price_variance', 'balance_drift', 'missing_cost', 'conversion_missing', 'sales_missing',"
    " 'sales_source_overlap', 'out_of_stock', 'low_stock', 'stale_count', 'waste_risk',"
    " 'expiring_soon', 'adjustment_review', 'job_failed'"
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
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(
            sa.Column("expiry_alert_days", sa.Integer(), nullable=False, server_default="2")
        )
        batch.add_column(
            sa.Column(
                "auto_dispose_prepared", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
    with op.batch_alter_table("tenants") as batch:
        batch.alter_column("expiry_alert_days", server_default=None)
        batch.alter_column("auto_dispose_prepared", server_default=None)
    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(sa.Column("expiry_alert_days", sa.Integer(), nullable=True))
    with op.batch_alter_table("inventory_categories") as batch:
        batch.add_column(sa.Column("expiry_alert_days", sa.Integer(), nullable=True))
    _exception_types(AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM exceptions WHERE type = 'expiring_soon'")
    _exception_types(BEFORE)
    with op.batch_alter_table("inventory_categories") as batch:
        batch.drop_column("expiry_alert_days")
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_column("expiry_alert_days")
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("auto_dispose_prepared")
        batch.drop_column("expiry_alert_days")
