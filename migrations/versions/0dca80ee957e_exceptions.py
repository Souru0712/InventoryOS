"""Exceptions (T1.19, DM-8): one record for everything that needs a person; alerts become them.

Expand + backfill. Creates `exceptions` with its named CHECKs and a partial unique index (one
open exception per type and subject, REC-FR-13). Every alert is copied across: its kind becomes
the matching type (`expired` -> `batch_expired_unresolved`), its stock item the subject and its
location the exception's. An open alert opens an exception; a resolved one is closed as
resolved by the system. `alerts` is kept as read-only history (ADR-0043). Running the backfill
again copies nothing twice: an alert already copied is skipped.

Revision ID: 0dca80ee957e
Revises: ba3da8bb8b6f
Create Date: 2026-11-12
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "0dca80ee957e"
down_revision = "ba3da8bb8b6f"
branch_labels = None
depends_on = None

TYPES = (
    "'unmapped_product', 'negative_balance', 'count_variance_high', 'batch_expired_unresolved',"
    " 'batch_shortfall', 'sync_failure', 'pos_reconciliation_mismatch', 'receiving_discrepancy',"
    " 'price_variance', 'balance_drift', 'missing_cost', 'conversion_missing', 'sales_missing',"
    " 'sales_source_overlap', 'out_of_stock', 'low_stock', 'stale_count', 'waste_risk'"
)
OPEN = "status IN ('open', 'acknowledged')"
# The kind each alert had, as stored (the enum's name), and the type and severity it becomes.
FROM_KIND = {
    "OUT_OF_STOCK": ("out_of_stock", "warning"),
    "LOW_STOCK": ("low_stock", "warning"),
    "STALE_COUNT": ("stale_count", "info"),
    "WASTE_RISK": ("waste_risk", "warning"),
    "EXPIRED": ("batch_expired_unresolved", "warning"),
}
CLEARED = "The condition cleared"

alerts = sa.table(
    "alerts",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("stock_item_id"),
    sa.column("kind"),
    sa.column("message"),
    sa.column("qty_base", Qty()),
    sa.column("days_of_cover", Qty()),
    sa.column("value_cents"),
    sa.column("created_at", UTCDateTime()),
    sa.column("notified_at", UTCDateTime()),
    sa.column("resolved_at", UTCDateTime()),
)
stock = sa.table("stock_items", sa.column("id"), sa.column("location_id"))
exceptions = sa.table(
    "exceptions",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("location_id"),
    sa.column("type"),
    sa.column("severity"),
    sa.column("subject_type"),
    sa.column("subject_id"),
    sa.column("title"),
    sa.column("money_impact_cents"),
    sa.column("quantity_base", Qty()),
    sa.column("days_of_cover", Qty()),
    sa.column("occurrences"),
    sa.column("status"),
    sa.column("detected_at", UTCDateTime()),
    sa.column("last_seen_at", UTCDateTime()),
    sa.column("resolved_at", UTCDateTime()),
    sa.column("resolution"),
    sa.column("resolution_note"),
    sa.column("notified_at", UTCDateTime()),
)


def backfill(connection: sa.Connection) -> int:
    """Copy every alert not copied yet; the number copied."""
    now = datetime.now(UTC)
    copied = 0
    rows = connection.execute(
        sa.select(alerts, stock.c.location_id)
        .join(stock, stock.c.id == alerts.c.stock_item_id)
        # Newest first: if two open alerts share a kind and item, the newer one stays open.
        .order_by(alerts.c.created_at.desc(), alerts.c.id.desc())
    ).all()
    for row in rows:
        kind = getattr(row.kind, "name", str(row.kind)).upper()
        exception_type, severity = FROM_KIND[kind]
        subject = str(row.stock_item_id)
        detected = row.created_at or now
        same = (
            exceptions.c.tenant_id == row.tenant_id,
            exceptions.c.type == exception_type,
            exceptions.c.subject_type == "stock_item",
            exceptions.c.subject_id == subject,
        )
        already = connection.execute(
            sa.select(exceptions.c.id).where(*same, exceptions.c.detected_at == detected)
        ).first()
        if already is not None:
            continue
        still_open = row.resolved_at is None
        if still_open:
            held = connection.execute(
                sa.select(exceptions.c.id).where(*same, exceptions.c.status == "open")
            ).first()
            still_open = held is None  # an older duplicate closes, the newest stays open
        connection.execute(
            sa.insert(exceptions).values(
                tenant_id=row.tenant_id,
                location_id=row.location_id,
                type=exception_type,
                severity=severity,
                subject_type="stock_item",
                subject_id=subject,
                title=(row.message or exception_type)[:500],
                money_impact_cents=row.value_cents,
                quantity_base=row.qty_base,
                days_of_cover=row.days_of_cover,
                occurrences=1,
                status="open" if still_open else "resolved",
                detected_at=detected,
                last_seen_at=row.resolved_at or detected,
                resolved_at=None if still_open else (row.resolved_at or now),
                resolution=None if still_open else "system",
                resolution_note=None if still_open else CLEARED,
                notified_at=row.notified_at,
            )
        )
        copied += 1
    return copied


def upgrade() -> None:
    op.create_table(
        "exceptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("subject_type", sa.String(length=30), nullable=False),
        sa.Column("subject_id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("money_impact_cents", sa.Integer(), nullable=True),
        sa.Column("quantity_base", Qty(), nullable=True),
        sa.Column("days_of_cover", Qty(), nullable=True),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("resolution", sa.String(length=16), nullable=True),
        sa.Column("resolution_note", sa.String(length=500), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"type IN ({TYPES})", name="ck_exceptions_type"),
        sa.CheckConstraint(
            "severity IN ('critical', 'warning', 'info')", name="ck_exceptions_severity"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'dismissed')",
            name="ck_exceptions_status",
        ),
        sa.CheckConstraint(
            "resolution IN ('action', 'dismissed', 'system')", name="ck_exceptions_resolution"
        ),
        sa.CheckConstraint("occurrences >= 1", name="ck_exceptions_occurrences_positive"),
        sa.CheckConstraint(
            f"{OPEN} OR (resolved_at IS NOT NULL AND resolution IS NOT NULL)",
            name="ck_exceptions_closed_has_resolution",
        ),
        sa.CheckConstraint(
            "status <> 'dismissed' OR resolution_note IS NOT NULL",
            name="ck_exceptions_dismissal_has_reason",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="exceptions_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="exceptions_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"],
            ["users.id"],
            name="exceptions_acknowledged_by_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name="exceptions_resolved_by_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("exceptions") as batch:
        batch.create_index("ix_exceptions_tenant_status", ["tenant_id", "status"], unique=False)
        batch.create_index("ix_exceptions_location_id", ["location_id"], unique=False)
        batch.create_index(
            "uq_exceptions_open",
            ["tenant_id", "type", "subject_type", "subject_id"],
            unique=True,
            sqlite_where=sa.text(OPEN),
            postgresql_where=sa.text(OPEN),
        )
    backfill(op.get_bind())


def downgrade() -> None:
    # The alerts were never changed, so they are still the record the old code reads.
    op.drop_table("exceptions")
