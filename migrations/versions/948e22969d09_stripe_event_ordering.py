"""remember the newest stripe event applied

Revision ID: 948e22969d09
Revises: bddc0b07d9b2
"""

from alembic import op
import sqlalchemy as sa

revision = "948e22969d09"
down_revision = "bddc0b07d9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(sa.Column("stripe_event_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_column("stripe_event_at")
