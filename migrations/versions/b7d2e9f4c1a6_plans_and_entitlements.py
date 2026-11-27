"""Plans and entitlements as data (T4.01, DM-15, C-41).

Expand and backfill. Adds the plan catalogue (`plans`) with one entitlement set per plan
(`entitlements`), seeds Vontry Complete and Vontry Standalone, and points every business at a plan
through `tenants.plan_id`. Every existing business gets Complete: there are no paying customers
(C-40), and Complete is the whole product, as the Pro plan and the trial were. The legacy
`tenants.plan` column (Starter/Pro) stays and is no longer read.

Revision ID: b7d2e9f4c1a6
Revises: a3c9e5f1b7d4
Create Date: 2026-11-26
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "b7d2e9f4c1a6"
down_revision = "a3c9e5f1b7d4"
branch_labels = None
depends_on = None

# The catalogue as of this migration; app/plans.py INITIAL_CATALOGUE matches it for test schemas.
CATALOGUE = (
    {
        "code": "complete",
        "name": "Vontry Complete",
        "billing_basis": "per_location",
        "allowed_modes": ["connected", "standalone"],
        "max_locations": None,
        "capability_flags": [],
        "limits": {"invoice_extractions_per_day": 40},
    },
    {
        "code": "standalone",
        "name": "Vontry Standalone",
        "billing_basis": "per_business",
        "allowed_modes": ["standalone"],
        "max_locations": 1,
        "capability_flags": [],
        "limits": {"invoice_extractions_per_day": 40},
    },
)


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("billing_basis", sa.String(length=20), nullable=False),
        sa.Column("provider_price_ref", sa.String(length=120), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "billing_basis IN ('per_location', 'per_business')", name="ck_plans_billing_basis"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_plans_code"),
    )
    op.create_table(
        "entitlements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("allowed_modes", sa.JSON(), nullable=False),
        sa.Column("max_locations", sa.Integer(), nullable=True),
        sa.Column("capability_flags", sa.JSON(), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "max_locations IS NULL OR max_locations >= 1", name="ck_entitlements_max_locations"
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", name="uq_entitlements_plan"),
    )
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("plan_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "tenants_plan_id_fkey", "plans", ["plan_id"], ["id"], ondelete="RESTRICT"
        )

    connection = op.get_bind()
    plans = sa.table(
        "plans",
        sa.column("id", sa.Integer()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("billing_basis", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("created_at", UTCDateTime()),
    )
    entitlements = sa.table(
        "entitlements",
        sa.column("plan_id", sa.Integer()),
        sa.column("allowed_modes", sa.JSON()),
        sa.column("max_locations", sa.Integer()),
        sa.column("capability_flags", sa.JSON()),
        sa.column("limits", sa.JSON()),
    )
    now = datetime.now(UTC)
    for entry in CATALOGUE:
        connection.execute(
            plans.insert().values(
                code=entry["code"],
                name=entry["name"],
                billing_basis=entry["billing_basis"],
                active=True,
                created_at=now,
            )
        )
        plan_id = connection.execute(
            sa.select(plans.c.id).where(plans.c.code == entry["code"])
        ).scalar_one()
        connection.execute(
            entitlements.insert().values(
                plan_id=plan_id,
                allowed_modes=entry["allowed_modes"],
                max_locations=entry["max_locations"],
                capability_flags=entry["capability_flags"],
                limits=entry["limits"],
            )
        )

    complete = connection.execute(
        sa.select(plans.c.id).where(plans.c.code == "complete")
    ).scalar_one()
    connection.execute(sa.text("UPDATE tenants SET plan_id = :plan"), {"plan": complete})


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.drop_constraint("tenants_plan_id_fkey", type_="foreignkey")
        batch.drop_column("plan_id")
    op.drop_table("entitlements")
    op.drop_table("plans")
