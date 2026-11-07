"""Email verification and password reset (T1.08): users.email_verified_at and auth_tokens.

Additive. Existing users start unverified and confirm their address from the banner the app
shows them, or an operator marks them with `python -m app.cli verify-email`.

Revision ID: 6fbdcbb20640
Revises: 8fadf35f1453
Create Date: 2026-11-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6fbdcbb20640"
down_revision = "8fadf35f1453"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')", name="ck_auth_tokens_purpose"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="auth_tokens_user_id_fkey", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    with op.batch_alter_table("auth_tokens") as batch:
        batch.create_index("ix_auth_tokens_user_id", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_table("auth_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verified_at")
