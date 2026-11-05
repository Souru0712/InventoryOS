"""subscription state on the tenant

Revision ID: bddc0b07d9b2
Revises: 9bbbef6381cc
"""

from datetime import datetime, timedelta

from alembic import op
import sqlalchemy as sa

# Kept as a literal: a migration must not change if the product later changes its trial.
TRIAL_DAYS = 30

revision = "bddc0b07d9b2"
down_revision = "9bbbef6381cc"
branch_labels = None
depends_on = None

STATUS = sa.Enum(
    "TRIALING",
    "ACTIVE",
    "PAST_DUE",
    "EXPIRED",
    "CANCELED",
    "PAUSED",
    name="subscriptionstatus",
)


def upgrade() -> None:
    STATUS.create(op.get_bind(), checkfirst=True)
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(
            sa.Column("subscription_status", STATUS, nullable=False, server_default="TRIALING")
        )
        batch_op.add_column(sa.Column("trial_started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("trial_ends_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("current_period_start", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("current_period_end", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("past_due_since", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("stripe_subscription_id", sa.String(length=80), nullable=True)
        )

    # What used to be recorded in the plan column is a status, not a plan. A trial was the full
    # product then and is now, so it becomes Pro; a canceled account keeps its floor at Starter.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE tenants SET subscription_status = 'TRIALING', plan = 'PRO', "
            "trial_started_at = created_at WHERE plan = 'TRIAL'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE tenants SET subscription_status = 'CANCELED', plan = 'STARTER' WHERE plan = 'CANCELED'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE tenants SET subscription_status = 'ACTIVE' WHERE plan IN ('STARTER', 'PRO') "
            "AND subscription_status = 'TRIALING' AND trial_started_at IS NULL"
        )
    )
    # 30 days from when they signed up, for anyone mid-trial. Date arithmetic differs between
    # SQLite and Postgres, so it is done here rather than in SQL.
    rows = (
        bind.execute(
            sa.text("SELECT id, trial_started_at FROM tenants WHERE trial_started_at IS NOT NULL")
        )
        .mappings()
        .all()
    )
    for row in rows:
        started = row["trial_started_at"]
        if isinstance(started, str):
            started = datetime.fromisoformat(started)
        bind.execute(
            sa.text("UPDATE tenants SET trial_ends_at = :ends WHERE id = :id"),
            {"ends": started + timedelta(days=TRIAL_DAYS), "id": row["id"]},
        )

    with op.batch_alter_table("tenants") as batch_op:
        batch_op.alter_column("subscription_status", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        for column in (
            "stripe_subscription_id",
            "past_due_since",
            "current_period_end",
            "current_period_start",
            "trial_ends_at",
            "trial_started_at",
            "subscription_status",
        ):
            batch_op.drop_column(column)
    STATUS.drop(op.get_bind(), checkfirst=True)
