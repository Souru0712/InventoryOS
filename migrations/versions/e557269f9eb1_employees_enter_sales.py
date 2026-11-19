"""PD-37 (T2.12): whether Employees may enter daily sales; off, so Managers and above do.

Additive.

Revision ID: e557269f9eb1
Revises: ebcbee00f30e
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e557269f9eb1"
down_revision = "ebcbee00f30e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(
            sa.Column(
                "employees_enter_sales", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
    with op.batch_alter_table("tenants") as batch:
        batch.alter_column("employees_enter_sales", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("employees_enter_sales")
