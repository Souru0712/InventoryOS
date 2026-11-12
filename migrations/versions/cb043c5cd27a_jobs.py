"""Background jobs (T1.20, ID-06): the `jobs` table, and `job_failed` as an exception type.

Additive. `jobs` holds one row per piece of background work for one organization, with a partial
unique index so one waiting copy of the same work is queued once. The exceptions type CHECK gains
`job_failed`, where a dead-lettered job goes. The partial unique index on open exceptions is
rebuilt after the CHECK changes, because SQLite rebuilds the table to alter a constraint.

Revision ID: cb043c5cd27a
Revises: 0dca80ee957e
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "cb043c5cd27a"
down_revision = "0dca80ee957e"
branch_labels = None
depends_on = None

TYPES = (
    "'unmapped_product', 'negative_balance', 'count_variance_high', 'batch_expired_unresolved',"
    " 'batch_shortfall', 'sync_failure', 'pos_reconciliation_mismatch', 'receiving_discrepancy',"
    " 'price_variance', 'balance_drift', 'missing_cost', 'conversion_missing', 'sales_missing',"
    " 'sales_source_overlap', 'out_of_stock', 'low_stock', 'stale_count', 'waste_risk'"
)
OPEN = "status IN ('open', 'acknowledged')"
PENDING = "status = 'pending' AND dedupe_key IS NOT NULL"


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
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('evaluate_exceptions', 'send_alert_digest', 'send_weekly_report',"
            " 'extract_document')",
            name="ck_jobs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'dead')", name="ck_jobs_status"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_jobs_attempts_not_negative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_jobs_max_attempts_positive"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="jobs_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("jobs") as batch:
        batch.create_index("ix_jobs_due", ["status", "run_at"], unique=False)
        batch.create_index("ix_jobs_tenant_status", ["tenant_id", "status"], unique=False)
        batch.create_index(
            "uq_jobs_pending",
            ["tenant_id", "kind", "dedupe_key"],
            unique=True,
            sqlite_where=sa.text(PENDING),
            postgresql_where=sa.text(PENDING),
        )
    _exception_types(TYPES + ", 'job_failed'")


def downgrade() -> None:
    op.execute("DELETE FROM exceptions WHERE type = 'job_failed'")
    _exception_types(TYPES)
    op.drop_table("jobs")
