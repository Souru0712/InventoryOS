"""Invitations (T1.10): emailed, single-use offers to join a business with a role and scope.

Additive.

Revision ID: a3c7d133e37d
Revises: 60bee21fefbd
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3c7d133e37d"
down_revision = "60bee21fefbd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("all_locations", sa.Boolean(), nullable=False),
        sa.Column("invited_by_user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('owner', 'administrator', 'manager', 'employee', 'read_only')",
            name="ck_invitations_role",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="invitations_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="invitations_invited_by_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    with op.batch_alter_table("invitations") as batch:
        batch.create_index("ix_invitations_tenant_id", ["tenant_id"], unique=False)

    op.create_table(
        "invitation_locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("invitation_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="invitation_locations_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["invitations.id"],
            name="invitation_locations_invitation_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="invitation_locations_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invitation_id", "location_id"),
    )
    with op.batch_alter_table("invitation_locations") as batch:
        batch.create_index("ix_invitation_locations_invitation_id", ["invitation_id"], unique=False)
        batch.create_index("ix_invitation_locations_tenant_id", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_table("invitation_locations")
    op.drop_table("invitations")
