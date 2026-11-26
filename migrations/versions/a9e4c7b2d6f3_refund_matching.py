"""Refunds linked to their orders (T3.07, WF-17): a sales return names its original sale.

Additive. `sales_returns.original_reference` and `sales_return_lines.line_key` let a refund that
arrives before its order keep what it gave back, and be linked when the order does.

Revision ID: a9e4c7b2d6f3
Revises: f2b6d8a4c0e9
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9e4c7b2d6f3"
down_revision = "f2b6d8a4c0e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sales_returns") as batch:
        batch.add_column(sa.Column("original_reference", sa.String(length=200), nullable=True))
    with op.batch_alter_table("sales_return_lines") as batch:
        batch.add_column(sa.Column("line_key", sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sales_return_lines") as batch:
        batch.drop_column("line_key")
    with op.batch_alter_table("sales_returns") as batch:
        batch.drop_column("original_reference")
