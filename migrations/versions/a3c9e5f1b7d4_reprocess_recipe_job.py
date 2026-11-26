"""Applying a back-dated recipe to past sales is a job (C-51).

Additive: `reprocess_recipe` joins the job kinds (CHECK and the pending index rebuilt).

Revision ID: a3c9e5f1b7d4
Revises: f1b4c7e2a9d3
Create Date: 2026-11-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3c9e5f1b7d4"
down_revision = "f1b4c7e2a9d3"
branch_labels = None
depends_on = None

BEFORE = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger', 'consume_sale', 'connection_revoked_email', 'reprocess_mapping',"
    " 'ingest_orders', 'refresh_catalog', 'reconcile_orders'"
)
AFTER = BEFORE + ", 'reprocess_recipe'"
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
    _job_kinds(AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'reprocess_recipe'")
    _job_kinds(BEFORE)
