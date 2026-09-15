"""expiry date type confidence and coverage flag

Revision ID: f10ff10cb169
Revises: 8ebcae844ae7
Create Date: 2026-09-13 11:34:12.510058
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.db import Qty

revision: str = 'f10ff10cb169'
down_revision: str | None = '8ebcae844ae7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lots that predate this migration have no recorded date type, and a NOT NULL column
    # cannot be added to a table that already holds rows without something to put in them.
    # The default is dropped again afterwards so the schema still matches the models.
    with op.batch_alter_table('stock_batches', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('date_type', sa.String(length=20), nullable=False, server_default='unknown')
        )
        batch_op.add_column(sa.Column('date_confidence', Qty(), nullable=True))

    with op.batch_alter_table('stock_batches', schema=None) as batch_op:
        batch_op.alter_column('date_type', server_default=None)

    with op.batch_alter_table('stock_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tracks_expiry', sa.Boolean(), nullable=True))


def downgrade() -> None:

    with op.batch_alter_table('stock_items', schema=None) as batch_op:
        batch_op.drop_column('tracks_expiry')

    with op.batch_alter_table('stock_batches', schema=None) as batch_op:
        batch_op.drop_column('date_confidence')
        batch_op.drop_column('date_type')


