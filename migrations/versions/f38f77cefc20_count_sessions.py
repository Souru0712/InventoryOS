"""Count sessions (T1.23-a): `stock_counts` and `stock_count_lines`.

Additive. A session is the source record of one `count` transaction keyed `count:<id>`; each line
is an observation with what the ledger expected at its instant and the variance posted for it.

Revision ID: f38f77cefc20
Revises: b8363254987e
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import Qty

revision = "f38f77cefc20"
down_revision = "b8363254987e"
branch_labels = None
depends_on = None


def _fk(table: str, column: str, target: str, ondelete: str = "RESTRICT"):
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"{table}_{column}_fkey", ondelete=ondelete
    )


def upgrade() -> None:
    op.create_table(
        "stock_counts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_ref", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("started_by_user_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "scope IN ('full', 'category', 'area', 'item')", name="ck_stock_counts_scope"
        ),
        sa.CheckConstraint("status IN ('open', 'posted')", name="ck_stock_counts_status"),
        sa.CheckConstraint(
            "status <> 'posted' OR posted_at IS NOT NULL", name="ck_stock_counts_posted_has_time"
        ),
        _fk("stock_counts", "tenant_id", "tenants.id"),
        _fk("stock_counts", "location_id", "locations.id"),
        _fk("stock_counts", "started_by_user_id", "users.id"),
        _fk("stock_counts", "posted_by_user_id", "users.id"),
        _fk("stock_counts", "transaction_id", "inventory_transactions.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("stock_counts") as batch:
        batch.create_index("ix_stock_counts_location_id", ["location_id"], unique=False)
        batch.create_index("ix_stock_counts_tenant_status", ["tenant_id", "status"], unique=False)

    op.create_table(
        "stock_count_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("stock_count_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("counted_qty_base", Qty(), nullable=False),
        sa.Column("entered", sa.String(length=200), nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("counted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("expected_qty_base", Qty(), nullable=True),
        sa.Column("variance_qty_base", Qty(), nullable=True),
        sa.Column("unit_cost", Qty(), nullable=True),
        sa.Column("variance_cents", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=24), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("movement_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "CAST(counted_qty_base AS NUMERIC) >= 0",
            name="ck_stock_count_lines_counted_qty_base_not_negative",
        ),
        sa.CheckConstraint(
            "reason IN ('unrecorded_waste', 'spillage', 'receiving_error', 'recipe_error',"
            " 'theft_suspected', 'count_error', 'unknown')",
            name="ck_stock_count_lines_reason",
        ),
        sa.UniqueConstraint(
            "stock_count_id", "inventory_item_id", name="uq_stock_count_lines_item"
        ),
        _fk("stock_count_lines", "tenant_id", "tenants.id"),
        _fk("stock_count_lines", "stock_count_id", "stock_counts.id", ondelete="CASCADE"),
        _fk("stock_count_lines", "inventory_item_id", "inventory_items.id"),
        _fk("stock_count_lines", "counted_by_user_id", "users.id"),
        _fk("stock_count_lines", "movement_id", "inventory_movements.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("stock_count_lines") as batch:
        batch.create_index("ix_stock_count_lines_stock_count_id", ["stock_count_id"], unique=False)


def downgrade() -> None:
    op.drop_table("stock_count_lines")
    op.drop_table("stock_counts")
