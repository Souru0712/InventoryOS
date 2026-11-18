"""Production mode per product (T2.05, PRD-FR-11, DR-18): made-to-order or made-ahead.

Expand only: every existing product is made to order, which is what a sale did before: it
consumed the lines of its recipe.

Revision ID: b3f5d8e2a147
Revises: 9c4e1b7a2d05
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3f5d8e2a147"
down_revision = "9c4e1b7a2d05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(
            sa.Column(
                "production_mode",
                sa.String(length=16),
                nullable=False,
                server_default="made_to_order",
            )
        )
        batch.create_check_constraint(
            "ck_products_production_mode",
            "production_mode IN ('made_to_order', 'made_ahead')",
        )
    with op.batch_alter_table("products") as batch:
        batch.alter_column("production_mode", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_constraint("ck_products_production_mode", type_="check")
        batch.drop_column("production_mode")
