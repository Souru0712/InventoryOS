"""Server-side sessions (T1.07): a signed-in browser is a row that can be revoked.

Additive. Sessions held in cookies before this revision carry no token, so everyone signs in
again once.

Revision ID: 8fadf35f1453
Revises: 545dd2eaa736
Create Date: 2026-11-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8fadf35f1453"
down_revision = "545dd2eaa736"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("membership_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="sessions_user_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["memberships.id"],
            name="sessions_membership_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    with op.batch_alter_table("sessions") as batch:
        batch.create_index("ix_sessions_user_id", ["user_id"], unique=False)
        batch.create_index("ix_sessions_membership_id", ["membership_id"], unique=False)


def downgrade() -> None:
    op.drop_table("sessions")
