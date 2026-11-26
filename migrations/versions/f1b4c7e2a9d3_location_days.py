"""Days a location accounted for without sales (PD-54, C-54): confirmed no sales, or closed.

Additive. One row per location and business day: `no_sales` (open, nothing sold) or `closed`
(a one-off closure, with its reason).

Revision ID: f1b4c7e2a9d3
Revises: c4e8a2f6b9d1
Create Date: 2026-11-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "f1b4c7e2a9d3"
down_revision = "c4e8a2f6b9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_days",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("recorded_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint("status IN ('no_sales', 'closed')", name="ck_location_days_status"),
        sa.CheckConstraint(
            "status <> 'closed' OR (reason IS NOT NULL AND reason <> '')",
            name="ck_location_days_reason",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "business_date", name="uq_location_days_day"),
    )
    op.create_index("ix_location_days_tenant_id", "location_days", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_location_days_tenant_id", table_name="location_days")
    op.drop_table("location_days")
