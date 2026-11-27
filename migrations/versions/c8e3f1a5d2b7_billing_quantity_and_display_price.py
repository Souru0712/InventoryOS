"""Per-location billing (T4.02): the subscription item and its quantity, and plans' display prices.

Additive. `tenants` gains the Stripe subscription item and the quantity Stripe last reported;
`plans` gains a display copy of its provider price (amount, currency, interval) for the pricing
page; `sync_subscription` joins the job kinds (CHECK and the pending index rebuilt).

Revision ID: c8e3f1a5d2b7
Revises: b7d2e9f4c1a6
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8e3f1a5d2b7"
down_revision = "b7d2e9f4c1a6"
branch_labels = None
depends_on = None

BEFORE = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger', 'consume_sale', 'connection_revoked_email', 'reprocess_mapping',"
    " 'ingest_orders', 'refresh_catalog', 'reconcile_orders', 'reprocess_recipe'"
)
AFTER = BEFORE + ", 'sync_subscription'"
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
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("stripe_subscription_item_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("billed_quantity", sa.Integer(), nullable=True))
    with op.batch_alter_table("plans") as batch:
        batch.add_column(sa.Column("display_amount_cents", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("display_currency", sa.String(3), nullable=True))
        batch.add_column(sa.Column("display_interval", sa.String(10), nullable=True))
    _job_kinds(AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'sync_subscription'")
    _job_kinds(BEFORE)
    with op.batch_alter_table("plans") as batch:
        batch.drop_column("display_interval")
        batch.drop_column("display_currency")
        batch.drop_column("display_amount_cents")
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("billed_quantity")
        batch.drop_column("stripe_subscription_item_id")
