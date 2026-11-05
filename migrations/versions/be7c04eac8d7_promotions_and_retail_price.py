"""promotions and retail price

Revision ID: be7c04eac8d7
Revises: f10ff10cb169
Create Date: 2026-09-13 11:50:24.577200
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db import Qty

revision: str = "be7c04eac8d7"
down_revision: str | None = "f10ff10cb169"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table(
        "promotions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("stock_item_id", sa.Integer(), nullable=False),
        sa.Column("stock_batch_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "DISMISSED", "SNOOZED", "ENDED", name="promotionstatus"),
            nullable=False,
        ),
        sa.Column("urgency", sa.String(length=16), nullable=False),
        sa.Column("normal_price_cents", sa.Integer(), nullable=False),
        sa.Column("suggested_price_cents", sa.Integer(), nullable=False),
        sa.Column("accepted_price_cents", sa.Integer(), nullable=True),
        sa.Column("units_at_risk_base", Qty(), nullable=False),
        sa.Column("cost_at_risk_cents", sa.Integer(), nullable=False),
        sa.Column("required_velocity", Qty(), nullable=True),
        sa.Column("velocity_before", Qty(), nullable=True),
        sa.Column("units_before_base", Qty(), nullable=True),
        sa.Column("units_after_base", Qty(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("snooze_until", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["stock_batch_id"],
            ["stock_batches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["stock_item_id"],
            ["stock_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("promotions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_promotions_stock_item_id"), ["stock_item_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_promotions_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index("ix_promotions_tenant_status", ["tenant_id", "status"], unique=False)

    with op.batch_alter_table("stock_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("retail_per_base_cents", Qty(), nullable=True))


def downgrade() -> None:

    with op.batch_alter_table("stock_items", schema=None) as batch_op:
        batch_op.drop_column("retail_per_base_cents")

    with op.batch_alter_table("promotions", schema=None) as batch_op:
        batch_op.drop_index("ix_promotions_tenant_status")
        batch_op.drop_index(batch_op.f("ix_promotions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_promotions_stock_item_id"))

    op.drop_table("promotions")

    # Postgres keeps enum types after their tables are dropped; remove them so upgrade can rebuild.
    if op.get_bind().dialect.name == "postgresql":
        for name in ("promotionstatus",):
            op.execute(f"DROP TYPE IF EXISTS {name}")
