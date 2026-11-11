"""Two-step sign-in (T1.11): an encrypted authenticator secret per user, and recovery codes.

Additive.

Revision ID: c5e1f0a94b2d
Revises: a3c7d133e37d
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5e1f0a94b2d"
down_revision = "a3c7d133e37d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("mfa_secret_enc", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("mfa_enabled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("mfa_last_step", sa.Integer(), nullable=True))

    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="recovery_codes_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("recovery_codes") as batch:
        batch.create_index("ix_recovery_codes_user_id", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_table("recovery_codes")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("mfa_last_step")
        batch.drop_column("mfa_enabled_at")
        batch.drop_column("mfa_secret_enc")
