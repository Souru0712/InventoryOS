"""email preferences and suppressions

Revision ID: 74ceec6f42d3
Revises: 54a587fd18dd
"""

from alembic import op
import sqlalchemy as sa

revision = "74ceec6f42d3"
down_revision = "54a587fd18dd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing users keep getting what they already got: everything.
    with op.batch_alter_table("users") as batch_op:
        for column in ("wants_stock_alerts", "wants_expiry_alerts", "wants_weekly_report"):
            batch_op.add_column(
                sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.true())
            )
    with op.batch_alter_table("users") as batch_op:
        for column in ("wants_stock_alerts", "wants_expiry_alerts", "wants_weekly_report"):
            batch_op.alter_column(column, server_default=None)

    op.create_table(
        "email_suppressions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_email_suppressions_email", "email_suppressions", ["email"])


def downgrade() -> None:
    op.drop_index("ix_email_suppressions_email", table_name="email_suppressions")
    op.drop_table("email_suppressions")
    with op.batch_alter_table("users") as batch_op:
        for column in ("wants_weekly_report", "wants_expiry_alerts", "wants_stock_alerts"):
            batch_op.drop_column(column)
