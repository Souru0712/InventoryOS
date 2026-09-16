"""sync runs remember their kind

Revision ID: 54a587fd18dd
Revises: b3d91f2a6c47
"""
from alembic import op
import sqlalchemy as sa

revision = '54a587fd18dd'
down_revision = 'b3d91f2a6c47'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Runs recorded before this revision were all full syncs.
    with op.batch_alter_table('sync_runs') as batch_op:
        batch_op.add_column(
            sa.Column('kind', sa.String(length=16), nullable=False, server_default='full')
        )
    with op.batch_alter_table('sync_runs') as batch_op:
        batch_op.alter_column('kind', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('sync_runs') as batch_op:
        batch_op.drop_column('kind')
