"""Full data export (T4.05, SAAS-D-01): export records and the `export_data` job kind.

Additive. One row per export a member asked for: its status, where its zip is kept and when the
download stops working. `export_data` joins the job kinds (CHECK and the pending index rebuilt).

Revision ID: d4f7a2c9e6b1
Revises: c8e3f1a5d2b7
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "d4f7a2c9e6b1"
down_revision = "c8e3f1a5d2b7"
branch_labels = None
depends_on = None

BEFORE = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger', 'consume_sale', 'connection_revoked_email', 'reprocess_mapping',"
    " 'ingest_orders', 'refresh_catalog', 'reconcile_orders', 'reprocess_recipe',"
    " 'sync_subscription'"
)
AFTER = BEFORE + ", 'export_data'"
PENDING = "status = 'pending' AND dedupe_key IS NOT NULL"


def _job_kinds(kinds: str) -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_kind", type_="check")
        batch.create_check_constraint("ck_jobs_kind", f"kind IN ({kinds})")
    # Rebuilt explicitly: a table rebuild must not leave this index without its WHERE.
    op.drop_index("uq_jobs_pending", table_name="jobs")
    op.create_index(
        "uq_jobs_pending",
        "jobs",
        ["tenant_id", "kind", "dedupe_key"],
        unique=True,
        sqlite_where=sa.text(PENDING),
        postgresql_where=sa.text(PENDING),
    )


def upgrade() -> None:
    op.create_table(
        "data_exports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("with_costs", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("storage_key", sa.String(length=300), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.Column("expires_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed', 'expired')", name="ck_data_exports_status"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_exports_tenant_id", "data_exports", ["tenant_id"])
    _job_kinds(AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'export_data'")
    _job_kinds(BEFORE)
    op.drop_index("ix_data_exports_tenant_id", table_name="data_exports")
    op.drop_table("data_exports")
