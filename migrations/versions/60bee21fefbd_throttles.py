"""Throttles (T1.09): counters for sign-in and emailed-link attempts, shared by every server.

Additive.

Revision ID: 60bee21fefbd
Revises: 6fbdcbb20640
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "60bee21fefbd"
down_revision = "6fbdcbb20640"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "throttles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "CAST(attempts AS NUMERIC) >= 0", name="ck_throttles_attempts_not_negative"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    with op.batch_alter_table("throttles") as batch:
        batch.create_index("ix_throttles_updated_at", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_table("throttles")
