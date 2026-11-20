"""Onboarding state (T2.20-b; SAAS §5 step 0): the payment answer, skipped steps, opening count.

Additive.

Revision ID: 4ebc618f9510
Revises: 3636bfa718b2
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "4ebc618f9510"
down_revision = "3636bfa718b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onboarding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("payment_answer", sa.String(length=20), nullable=True),
        sa.Column("pos_name", sa.String(length=80), nullable=True),
        sa.Column("answered_at", UTCDateTime(), nullable=True),
        sa.Column("skipped", sa.String(length=200), nullable=False),
        sa.Column("opening_counted_at", UTCDateTime(), nullable=True),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "payment_answer IN ('pos_supported', 'none', 'none_wants_pos', 'pos_unsupported')",
            name="ck_onboarding_payment_answer",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="onboarding_tenant_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_onboarding_tenant"),
    )


def downgrade() -> None:
    op.drop_table("onboarding")
