"""Nightly ledger verification (T1.21): `verify_ledger` becomes a kind of job.

Additive. The jobs kind CHECK gains `verify_ledger`. The partial unique index on pending jobs is
rebuilt after the CHECK changes, because SQLite rebuilds the table to alter a constraint.

Revision ID: 6bedfbcf985f
Revises: cb043c5cd27a
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6bedfbcf985f"
down_revision = "cb043c5cd27a"
branch_labels = None
depends_on = None

KINDS = "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document'"
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
    _job_kinds(KINDS + ", 'verify_ledger'")


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'verify_ledger'")
    _job_kinds(KINDS)
