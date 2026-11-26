"""Sales-source cutover (T3.10, WF-25): a source's chosen first day is kept.

Additive. `sales_sources.fixed_from` marks a source whose first business day was chosen at a
cutover, so a POS sale for an earlier day arriving late is never counted by moving it back.

Revision ID: c4e8a2f6b9d1
Revises: b3d7f1a5c8e2
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4e8a2f6b9d1"
down_revision = "b3d7f1a5c8e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sales_sources") as batch:
        batch.add_column(
            sa.Column("fixed_from", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("sales_sources") as batch:
        batch.drop_column("fixed_from")
