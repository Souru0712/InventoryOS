"""Connection lifecycle (T3.02, DM-14): one active connection per merchant across businesses.

Migrate (constraint). Connection statuses are `active`, `revoked` or `disconnected`; an active
connection holds a token, a disconnected one may not; a merchant is actively connected to at most
one business (DR-22, AC-POS-11). `connection_revoked_email` becomes a kind of job.

Refuses to run while two businesses hold an active connection to the same merchant: which one
keeps it is the owner's call, not the migration's.

Revision ID: 9c41e7a2d5b3
Revises: 4ebc618f9510
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9c41e7a2d5b3"
down_revision = "4ebc618f9510"
branch_labels = None
depends_on = None

KINDS = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger', 'consume_sale'"
)
PENDING = "status = 'pending' AND dedupe_key IS NOT NULL"
ACTIVE = "status = 'active'"


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
    shared = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT provider, merchant_id, COUNT(*) FROM provider_connections"
                " WHERE status = 'active' GROUP BY provider, merchant_id HAVING COUNT(*) > 1"
            )
        )
        .all()
    )
    if shared:
        merchants = ", ".join(f"{p}:{m} ({n} businesses)" for p, m, n in shared)
        raise RuntimeError(
            "DM-14: these merchants are actively connected to more than one business; "
            f"disconnect all but one before upgrading: {merchants}"
        )

    with op.batch_alter_table("provider_connections") as batch:
        batch.alter_column("access_token_enc", existing_type=sa.Text(), nullable=True)
        batch.create_check_constraint(
            "ck_provider_connections_status", "status IN ('active', 'revoked', 'disconnected')"
        )
        batch.create_check_constraint(
            "ck_provider_connections_token", "status <> 'active' OR access_token_enc IS NOT NULL"
        )
    op.create_index(
        "uq_provider_connections_active",
        "provider_connections",
        ["provider", "merchant_id"],
        unique=True,
        sqlite_where=sa.text(ACTIVE),
        postgresql_where=sa.text(ACTIVE),
    )
    _job_kinds(KINDS + ", 'connection_revoked_email'")


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'connection_revoked_email'")
    _job_kinds(KINDS)
    op.drop_index("uq_provider_connections_active", table_name="provider_connections")
    op.execute(
        "UPDATE provider_connections SET access_token_enc = '' WHERE access_token_enc IS NULL"
    )
    with op.batch_alter_table("provider_connections") as batch:
        batch.drop_constraint("ck_provider_connections_token", type_="check")
        batch.drop_constraint("ck_provider_connections_status", type_="check")
        batch.alter_column("access_token_enc", existing_type=sa.Text(), nullable=False)
