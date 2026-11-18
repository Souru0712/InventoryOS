"""Sale consumption (T2.05-b, WF-12; DM-7 contract): `consume_sale` jobs, history kept
unconsumed, `sales_daily` dropped.

- The jobs kind CHECK gains `consume_sale`.
- Sale lines recorded before this revision were never consumed (sales consumption was absent
  until T2.05), so the ones still `pending` become `skipped`: history is never replayed into
  the ledger (DATA_MIGRATION_ROADMAP rule 6).
- `sales_daily`, frozen since T2.04 and read by nothing, is dropped (destructive; approved by
  the owner on 2026-11-18). The downgrade rebuilds it from the daily POS transactions T2.04
  made of it, and sends the skipped lines of counted sales back to `pending`.

Revision ID: 1da987c1c195
Revises: b3f5d8e2a147
Create Date: 2026-11-18
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty

revision = "1da987c1c195"
down_revision = "b3f5d8e2a147"
branch_labels = None
depends_on = None

KINDS = (
    "'evaluate_exceptions', 'send_alert_digest', 'send_weekly_report', 'extract_document',"
    " 'verify_ledger'"
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
    _job_kinds(KINDS + ", 'consume_sale'")
    op.execute(
        "UPDATE sales_transaction_lines SET consumption_status = 'skipped'"
        " WHERE consumption_status = 'pending'"
    )
    op.drop_table("sales_daily")


def _fk(column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"sales_daily_{column}_fkey", ondelete="RESTRICT"
    )


def _rebuild(connection: sa.Connection) -> None:
    """One row per sold item, location and day, from the daily transactions of POS sources:
    what T2.04 made of `sales_daily`, and what the POS feed has recorded since."""
    rows = connection.execute(
        sa.text(
            "SELECT t.tenant_id, t.location_id, t.business_date, s.provider, l.external_ref,"
            " l.quantity, l.gross_cents, l.net_cents FROM sales_transaction_lines l"
            " JOIN sales_transactions t ON t.id = l.sales_transaction_id"
            " JOIN sales_sources s ON s.id = t.sales_source_id"
            " WHERE s.kind = 'pos_adapter' AND t.status = 'completed'"
            " AND t.occurred_at IS NULL AND l.external_ref IS NOT NULL"
        ).columns(business_date=sa.Date(), quantity=Qty())
    ).all()
    sold = {
        (row.tenant_id, row.provider, row.provider_item_id): row.id
        for row in connection.execute(
            sa.text("SELECT id, tenant_id, provider, provider_item_id FROM sold_items")
        )
    }
    days: dict[tuple, list] = defaultdict(lambda: [Decimal(0), 0, 0])
    for row in rows:
        item = sold.get((row.tenant_id, row.provider, row.external_ref))
        if item is None:
            continue
        total = days[(row.tenant_id, item, row.location_id, row.business_date)]
        total[0] += Decimal(row.quantity)
        total[1] += row.gross_cents or 0
        total[2] += row.net_cents or 0
    daily = sa.table(
        "sales_daily",
        sa.column("tenant_id"),
        sa.column("sold_item_id"),
        sa.column("location_id"),
        sa.column("dt", sa.Date()),
        sa.column("units", Qty()),
        sa.column("gross_cents"),
        sa.column("net_cents"),
    )
    values = [
        {
            "tenant_id": tenant_id,
            "sold_item_id": item,
            "location_id": location_id,
            "dt": day,
            "units": units,
            "gross_cents": gross,
            "net_cents": net,
        }
        for (tenant_id, item, location_id, day), (units, gross, net) in days.items()
    ]
    if values:
        connection.execute(sa.insert(daily), values)


def downgrade() -> None:
    op.create_table(
        "sales_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("sold_item_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("dt", sa.Date(), nullable=False),
        sa.Column("units", Qty(), nullable=False),
        sa.Column("gross_cents", sa.Integer(), nullable=False),
        sa.Column("net_cents", sa.Integer(), nullable=False),
        _fk("location_id", "locations.id"),
        _fk("sold_item_id", "sold_items.id"),
        _fk("tenant_id", "tenants.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "sold_item_id", "location_id", "dt"),
    )
    with op.batch_alter_table("sales_daily") as batch:
        batch.create_index("ix_sales_daily_location_id", ["location_id"])
        batch.create_index("ix_sales_daily_sold_item_id", ["sold_item_id"])
        batch.create_index("ix_sales_daily_tenant_id", ["tenant_id"])
        batch.create_index("ix_sales_tenant_dt", ["tenant_id", "dt"])
    _rebuild(op.get_bind())
    op.execute(
        "UPDATE sales_transaction_lines SET consumption_status = 'pending'"
        " WHERE consumption_status = 'skipped' AND sales_transaction_id IN"
        " (SELECT id FROM sales_transactions WHERE of_record)"
    )
    op.execute("DELETE FROM jobs WHERE kind = 'consume_sale'")
    _job_kinds(KINDS)
