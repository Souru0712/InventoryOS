"""Memberships (DM-4): a person reaches a business through a membership with a role and scope.

Expand and backfill: every existing user becomes the active Owner of the business it signed up
with. users.tenant_id stays until the contract step (DM-16).

Revision ID: 545dd2eaa736
Revises: 912d17673f03
Create Date: 2026-11-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "545dd2eaa736"
down_revision = "912d17673f03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("all_locations", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner', 'administrator', 'manager', 'employee', 'read_only')",
            name="ck_memberships_role",
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_memberships_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="memberships_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="memberships_user_id_fkey", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id"),
    )
    with op.batch_alter_table("memberships") as batch:
        batch.create_index("ix_memberships_tenant_id", ["tenant_id"], unique=False)
        batch.create_index("ix_memberships_user_id", ["user_id"], unique=False)

    op.create_table(
        "membership_locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("membership_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="membership_locations_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["memberships.id"],
            name="membership_locations_membership_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="membership_locations_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("membership_id", "location_id"),
    )
    with op.batch_alter_table("membership_locations") as batch:
        batch.create_index("ix_membership_locations_membership_id", ["membership_id"], unique=False)
        batch.create_index("ix_membership_locations_tenant_id", ["tenant_id"], unique=False)

    # Until now a user was the owner of the one business it signed up with.
    op.execute(
        "INSERT INTO memberships (tenant_id, user_id, role, status, all_locations, created_at) "
        "SELECT tenant_id, id, 'owner', 'active', true, created_at FROM users"
    )


def downgrade() -> None:
    op.drop_table("membership_locations")
    op.drop_table("memberships")
