"""Webhook events as jobs (T3.06; DR-21, SEC-11, PERF-07): events stored once, processed later.

Expand. `webhook_events` gains its status (received, processed, ignored), the job that acts on
it, the entities it names and when it was processed. `refresh_catalog` becomes a kind of job.

Revision ID: f2b6d8a4c0e9
Revises: e7c3a9d1b5f2
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "f2b6d8a4c0e9"
down_revision = "e7c3a9d1b5f2"
branch_labels = None
depends_on = None

KINDS = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger', 'consume_sale', 'connection_revoked_email', 'reprocess_mapping',"
    " 'ingest_orders'"
)
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
    with op.batch_alter_table("webhook_events") as batch:
        batch.add_column(
            sa.Column("status", sa.String(length=12), nullable=False, server_default="received")
        )
        batch.add_column(sa.Column("handled_by", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("entity_ids", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("processed_at", UTCDateTime(), nullable=True))
        batch.create_check_constraint(
            "ck_webhook_events_status", "status IN ('received', 'processed', 'ignored')"
        )
    # Every event stored before this change was acted on as it arrived.
    op.execute("UPDATE webhook_events SET status = 'processed', processed_at = received_at")
    _job_kinds(KINDS + ", 'refresh_catalog'")


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'refresh_catalog'")
    _job_kinds(KINDS)
    with op.batch_alter_table("webhook_events") as batch:
        batch.drop_constraint("ck_webhook_events_status", type_="check")
        batch.drop_column("processed_at")
        batch.drop_column("entity_ids")
        batch.drop_column("handled_by")
        batch.drop_column("status")
