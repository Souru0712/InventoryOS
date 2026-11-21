"""Reconciliation (T3.08; POS-FR-10, POS-FR-12, SQ-12, SQ-17): a wider re-read, a daily check.

Additive. `order_cursors.reread_at` remembers the last read that went back a whole window.
`reconcile_orders` becomes a kind of job. Results are kept on the sync runs.

Revision ID: b3d7f1a5c8e2
Revises: a9e4c7b2d6f3
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "b3d7f1a5c8e2"
down_revision = "a9e4c7b2d6f3"
branch_labels = None
depends_on = None

KINDS = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger', 'consume_sale', 'connection_revoked_email', 'reprocess_mapping',"
    " 'ingest_orders', 'refresh_catalog'"
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
    with op.batch_alter_table("order_cursors") as batch:
        batch.add_column(sa.Column("reread_at", UTCDateTime(), nullable=True))
    _job_kinds(KINDS + ", 'reconcile_orders'")


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'reconcile_orders'")
    _job_kinds(KINDS)
    with op.batch_alter_table("order_cursors") as batch:
        batch.drop_column("reread_at")
