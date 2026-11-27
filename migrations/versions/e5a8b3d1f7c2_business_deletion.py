"""Account deletion and retention (T4.06, SAAS-D-03, SAAS-D-04): deletion state on tenants.

Additive. A deletion's request time, due time, requester id and reason (an Owner's request or the
end of retention), and when it happened (the business row stays as an emptied tombstone).
`delete_business` joins the job kinds (CHECK and the pending index rebuilt).

Revision ID: e5a8b3d1f7c2
Revises: d4f7a2c9e6b1
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "e5a8b3d1f7c2"
down_revision = "d4f7a2c9e6b1"
branch_labels = None
depends_on = None

BEFORE = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger', 'consume_sale', 'connection_revoked_email', 'reprocess_mapping',"
    " 'ingest_orders', 'refresh_catalog', 'reconcile_orders', 'reprocess_recipe',"
    " 'sync_subscription', 'export_data'"
)
AFTER = BEFORE + ", 'delete_business'"
PENDING = "status = 'pending' AND dedupe_key IS NOT NULL"
REASON = "deletion_reason IS NULL OR deletion_reason IN ('owner', 'retention')"


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
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("deletion_requested_at", UTCDateTime(), nullable=True))
        batch.add_column(sa.Column("deletion_due_at", UTCDateTime(), nullable=True))
        batch.add_column(sa.Column("deletion_requested_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("deletion_reason", sa.String(10), nullable=True))
        batch.add_column(sa.Column("deleted_at", UTCDateTime(), nullable=True))
        batch.create_check_constraint("ck_tenants_deletion_reason", REASON)
    _job_kinds(AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'delete_business'")
    _job_kinds(BEFORE)
    with op.batch_alter_table("tenants") as batch:
        batch.drop_constraint("ck_tenants_deletion_reason", type_="check")
        batch.drop_column("deleted_at")
        batch.drop_column("deletion_reason")
        batch.drop_column("deletion_requested_by")
        batch.drop_column("deletion_due_at")
        batch.drop_column("deletion_requested_at")
