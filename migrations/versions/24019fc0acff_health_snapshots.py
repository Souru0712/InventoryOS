"""health snapshots

Revision ID: 24019fc0acff
Revises: be7c04eac8d7
Create Date: 2026-09-14 14:05:21.382150
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "24019fc0acff"
down_revision: str | None = "be7c04eac8d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table(
        "health_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("dt", sa.Date(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("band", sa.String(length=20), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "dt"),
    )
    with op.batch_alter_table("health_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_health_snapshots_tenant_id"), ["tenant_id"], unique=False
        )


def downgrade() -> None:

    with op.batch_alter_table("health_snapshots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_health_snapshots_tenant_id"))

    op.drop_table("health_snapshots")
