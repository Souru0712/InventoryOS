"""Order-line ingestion (T3.05): POS orders staged per connection, a cursor per connection.

Additive. `external_orders` (unique per connection and external id), `external_order_lines`
(unique per order and line uid), `order_cursors` (one per connection). `ingest_orders` becomes
a kind of job.

Revision ID: e7c3a9d1b5f2
Revises: d5a8c2e6f9b1
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "e7c3a9d1b5f2"
down_revision = "d5a8c2e6f9b1"
branch_labels = None
depends_on = None

KINDS = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger', 'consume_sale', 'connection_revoked_email', 'reprocess_mapping'"
)
PENDING = "status = 'pending' AND dedupe_key IS NOT NULL"


def _fk(
    table: str, column: str, target: str, ondelete: str = "RESTRICT"
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"{table}_{column}_fkey", ondelete=ondelete
    )


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
        "order_cursors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("lines_from", sa.Date(), nullable=False),
        sa.Column("through", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        _fk("order_cursors", "tenant_id", "tenants.id"),
        _fk("order_cursors", "connection_id", "provider_connections.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", name="uq_order_cursors_connection"),
    )
    op.create_index("ix_order_cursors_tenant_id", "order_cursors", ["tenant_id"])
    op.create_table(
        "external_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("external_order_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=True),
        sa.Column("external_location_id", sa.String(length=120), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("sales_transaction_id", sa.Integer(), nullable=True),
        sa.Column("received_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('open', 'completed', 'canceled')", name="ck_external_orders_state"
        ),
        _fk("external_orders", "tenant_id", "tenants.id"),
        _fk("external_orders", "connection_id", "provider_connections.id"),
        _fk("external_orders", "location_id", "locations.id"),
        _fk("external_orders", "sales_transaction_id", "sales_transactions.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "external_order_id", name="uq_external_orders_external_id"
        ),
    )
    op.create_index("ix_external_orders_tenant_id", "external_orders", ["tenant_id"])
    op.create_index("ix_external_orders_connection_id", "external_orders", ["connection_id"])
    op.create_table(
        "external_order_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("line_uid", sa.String(length=120), nullable=False),
        sa.Column("external_variation_id", sa.String(length=120), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("quantity", Qty(), nullable=False),
        sa.Column("modifiers", sa.Text(), nullable=False),
        sa.Column("gross_cents", sa.Integer(), nullable=True),
        sa.Column("net_cents", sa.Integer(), nullable=True),
        _fk("external_order_lines", "tenant_id", "tenants.id"),
        _fk("external_order_lines", "order_id", "external_orders.id", "CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "line_uid", name="uq_external_order_lines_uid"),
    )
    op.create_index("ix_external_order_lines_order_id", "external_order_lines", ["order_id"])
    _job_kinds(KINDS + ", 'ingest_orders'")


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'ingest_orders'")
    _job_kinds(KINDS)
    op.drop_table("external_order_lines")
    op.drop_table("external_orders")
    op.drop_table("order_cursors")
