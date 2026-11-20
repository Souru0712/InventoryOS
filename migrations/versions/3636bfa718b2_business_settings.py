"""Settings module (T2.19; ADM-FR-03, -05, -06, AC-ADM-06): business settings, modifier scales.

Additive.

- `business_settings`: one row per business (made on first read): units offered first, default
  production mode, the alert email's hour, the count and adjustment review thresholds.
- `modifier_scales`: append-only history of a scale modifier's factor, so a sale reads the factor
  in effect at its own moment. Each modifier's current factor is backfilled as in effect from the
  start, which is what every sale so far has read.

Revision ID: 3636bfa718b2
Revises: 2675519dc2bc
Create Date: 2026-11-19
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "3636bfa718b2"
down_revision = "2675519dc2bc"
branch_labels = None
depends_on = None

FROM_THE_START = datetime(2000, 1, 1, tzinfo=UTC)  # what every sale so far has read


def _fk(table: str, column: str, target: str, ondelete: str = "RESTRICT"):
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"{table}_{column}_fkey", ondelete=ondelete
    )


def upgrade() -> None:
    op.create_table(
        "business_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("unit_system", sa.String(length=8), nullable=False),
        sa.Column("default_production_mode", sa.String(length=16), nullable=False),
        sa.Column("digest_hour", sa.Integer(), nullable=True),
        sa.Column("count_review_cents", sa.Integer(), nullable=False),
        sa.Column("count_review_percent", sa.Integer(), nullable=False),
        sa.Column("adjustment_review_cents", sa.Integer(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "unit_system IN ('metric', 'us')", name="ck_business_settings_unit_system"
        ),
        sa.CheckConstraint(
            "default_production_mode IN ('made_to_order', 'made_ahead')",
            name="ck_business_settings_default_production_mode",
        ),
        sa.CheckConstraint(
            "digest_hour IS NULL OR (digest_hour >= 0 AND digest_hour <= 23)",
            name="ck_business_settings_digest_hour",
        ),
        sa.CheckConstraint(
            "count_review_percent >= 1 AND count_review_percent <= 100",
            name="ck_business_settings_count_review_percent",
        ),
        sa.CheckConstraint(
            "CAST(count_review_cents AS NUMERIC) >= 0",
            name="ck_business_settings_count_review_cents_not_negative",
        ),
        sa.CheckConstraint(
            "CAST(adjustment_review_cents AS NUMERIC) >= 0",
            name="ck_business_settings_adjustment_review_cents_not_negative",
        ),
        _fk("business_settings", "tenant_id", "tenants.id", "CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_business_settings_tenant"),
    )
    op.create_table(
        "modifier_scales",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("modifier_id", sa.Integer(), nullable=False),
        sa.Column("scale_factor", Qty(), nullable=False),
        sa.Column("effective_from", UTCDateTime(), nullable=False),
        sa.Column("recorded_by", sa.Integer(), nullable=True),
        sa.Column("recorded_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "CAST(scale_factor AS NUMERIC) > 0", name="ck_modifier_scales_scale_factor_positive"
        ),
        _fk("modifier_scales", "tenant_id", "tenants.id"),
        _fk("modifier_scales", "modifier_id", "modifiers.id", "CASCADE"),
        _fk("modifier_scales", "recorded_by", "users.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("modifier_scales") as batch:
        batch.create_index("ix_modifier_scales_tenant_id", ["tenant_id"])
        batch.create_index("ix_modifier_scales_modifier_id", ["modifier_id"])
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT tenant_id, id, scale_factor FROM modifiers WHERE scale_factor IS NOT NULL"
            )
        )
        .all()
    )
    if rows:
        now = datetime.now(UTC)
        op.bulk_insert(
            sa.table(
                "modifier_scales",
                sa.column("tenant_id", sa.Integer()),
                sa.column("modifier_id", sa.Integer()),
                sa.column("scale_factor", Qty()),
                sa.column("effective_from", UTCDateTime()),
                sa.column("recorded_at", UTCDateTime()),
            ),
            [
                {
                    "tenant_id": tenant_id,
                    "modifier_id": modifier_id,
                    "scale_factor": Decimal(str(factor)),
                    "effective_from": FROM_THE_START,
                    "recorded_at": now,
                }
                for tenant_id, modifier_id, factor in rows
            ],
        )


def downgrade() -> None:
    op.drop_table("modifier_scales")
    op.drop_table("business_settings")
