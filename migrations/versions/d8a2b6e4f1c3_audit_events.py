"""Audit events (T1.12): an append-only history of a business's non-stock changes (DR-23).

Additive.

Revision ID: d8a2b6e4f1c3
Revises: c5e1f0a94b2d
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8a2b6e4f1c3"
down_revision = "c5e1f0a94b2d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'system', 'support')", name="ck_audit_events_actor_kind"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="audit_events_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="audit_events_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="audit_events_actor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_events") as batch:
        batch.create_index("ix_audit_events_tenant_at", ["tenant_id", "at"], unique=False)


def downgrade() -> None:
    op.drop_table("audit_events")
