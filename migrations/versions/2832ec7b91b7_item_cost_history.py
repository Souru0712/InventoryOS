"""Cost history (T1.24): `item_cost_history`, one entry per stock row's current cost.

Expand + backfill. Each stock row (one item at one location) with a cost above zero gets one
`migration` entry of that cost, effective now: the current cost becomes the first point of its
history (DATA_MIGRATION_ROADMAP: cost_per_base_cents -> current cost + item_cost_history). Running
the backfill again adds nothing: a pair that already has history is skipped.

Revision ID: 2832ec7b91b7
Revises: f38f77cefc20
Create Date: 2026-11-13
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "2832ec7b91b7"
down_revision = "f38f77cefc20"
branch_labels = None
depends_on = None

history = sa.table(
    "item_cost_history",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("location_id"),
    sa.column("inventory_item_id"),
    sa.column("cost_per_base_cents", Qty()),
    sa.column("source_type"),
    sa.column("source_id"),
    sa.column("actor_user_id"),
    sa.column("effective_at", UTCDateTime()),
    sa.column("recorded_at", UTCDateTime()),
)
stock = sa.table(
    "stock_items",
    sa.column("tenant_id"),
    sa.column("location_id"),
    sa.column("inventory_item_id"),
    sa.column("cost_per_base_cents", Qty()),
)


def backfill(connection: sa.Connection) -> int:
    """One entry for each pair with a cost and no history yet; the number added."""
    now = datetime.now(UTC)
    added = 0
    rows = connection.execute(sa.select(stock).where(stock.c.inventory_item_id.is_not(None))).all()
    for row in rows:
        cost = Decimal(row.cost_per_base_cents or 0).quantize(Decimal("0.0001"))
        if cost <= 0:
            continue  # no cost is not a cost of zero; a slip below zero is not kept either
        known = connection.execute(
            sa.select(history.c.id).where(
                history.c.location_id == row.location_id,
                history.c.inventory_item_id == row.inventory_item_id,
            )
        ).first()
        if known is not None:
            continue
        connection.execute(
            sa.insert(history).values(
                tenant_id=row.tenant_id,
                location_id=row.location_id,
                inventory_item_id=row.inventory_item_id,
                cost_per_base_cents=cost,
                source_type="migration",
                source_id=None,
                actor_user_id=None,
                effective_at=now,
                recorded_at=now,
            )
        )
        added += 1
    return added


def upgrade() -> None:
    op.create_table(
        "item_cost_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("cost_per_base_cents", Qty(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=80), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('opening', 'receipt', 'override', 'migration')",
            name="ck_item_cost_history_source_type",
        ),
        sa.CheckConstraint(
            "CAST(cost_per_base_cents AS NUMERIC) >= 0",
            name="ck_item_cost_history_cost_per_base_cents_not_negative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="item_cost_history_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="item_cost_history_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="item_cost_history_inventory_item_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="item_cost_history_actor_user_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("item_cost_history") as batch:
        batch.create_index(
            "ix_item_cost_history_pair",
            ["location_id", "inventory_item_id", "effective_at"],
            unique=False,
        )
    backfill(op.get_bind())


def downgrade() -> None:
    # The stock rows keep their current cost; only its history goes.
    op.drop_table("item_cost_history")
