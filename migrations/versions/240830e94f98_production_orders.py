"""Production runs (T2.09; PRD-FR-02..08, -12): `production_orders`, and `production` as a cost
source and a batch source.

Additive.

Revision ID: 240830e94f98
Revises: 0662af2faff6
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import Qty

revision = "240830e94f98"
down_revision = "0662af2faff6"
branch_labels = None
depends_on = None


def _fk(table: str, column: str, target: str):
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"{table}_{column}_fkey", ondelete="RESTRICT"
    )


def _cost_sources(values: str) -> None:
    with op.batch_alter_table("item_cost_history") as batch:
        batch.drop_constraint("ck_item_cost_history_source_type", type_="check")
        batch.create_check_constraint(
            "ck_item_cost_history_source_type", f"source_type IN ({values})"
        )


def _batch_sources(values: str) -> None:
    with op.batch_alter_table("stock_batches") as batch:
        batch.drop_constraint("ck_stock_batches_source", type_="check")
        batch.create_check_constraint("ck_stock_batches_source", f"source IN ({values})")


def upgrade() -> None:
    op.create_table(
        "production_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("recipe_version_id", sa.Integer(), nullable=False),
        sa.Column("multiplier", Qty(), nullable=False),
        sa.Column("expected_yield_base", Qty(), nullable=False),
        sa.Column("actual_yield_base", Qty(), nullable=False),
        sa.Column("variance_base", Qty(), nullable=False),
        sa.Column("unit_cost", Qty(), nullable=True),
        sa.Column("batch_code", sa.String(length=40), nullable=False),
        sa.Column("output_batch_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("void_reason", sa.String(length=300), nullable=True),
        sa.CheckConstraint(
            "status IN ('completed', 'voided')", name="ck_production_orders_status"
        ),
        sa.CheckConstraint(
            "CAST(multiplier AS NUMERIC) > 0", name="ck_production_orders_multiplier_positive"
        ),
        sa.CheckConstraint(
            "CAST(actual_yield_base AS NUMERIC) > 0",
            name="ck_production_orders_actual_yield_base_positive",
        ),
        _fk("production_orders", "tenant_id", "tenants.id"),
        _fk("production_orders", "location_id", "locations.id"),
        _fk("production_orders", "inventory_item_id", "inventory_items.id"),
        _fk("production_orders", "recipe_version_id", "recipe_versions.id"),
        _fk("production_orders", "output_batch_id", "stock_batches.id"),
        _fk("production_orders", "actor_user_id", "users.id"),
        _fk("production_orders", "transaction_id", "inventory_transactions.id"),
        _fk("production_orders", "voided_by_user_id", "users.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_production_orders_tenant_id", "production_orders", ["tenant_id"])
    op.create_index(
        "ix_production_orders_location_time", "production_orders", ["location_id", "occurred_at"]
    )
    _cost_sources("'opening', 'receipt', 'override', 'migration', 'production'")
    _batch_sources("'receipt', 'label', 'scan', 'production'")


def downgrade() -> None:
    op.execute("DELETE FROM item_cost_history WHERE source_type = 'production'")
    # Produced batches stay, as dated stock the earlier schema knows how to name.
    op.execute("UPDATE stock_batches SET source = 'label' WHERE source = 'production'")
    _batch_sources("'receipt', 'label', 'scan'")
    _cost_sources("'opening', 'receipt', 'override', 'migration'")
    op.drop_table("production_orders")
