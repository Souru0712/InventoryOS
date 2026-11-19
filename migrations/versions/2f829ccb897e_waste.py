"""Waste (T2.07; WST-FR-01..12, DR-33): reasons, events and their lines.

Additive. Each business's default reasons are created the first time it records waste; nothing
is backfilled.

Revision ID: 2f829ccb897e
Revises: 77e8f4d7dad6
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import Qty

revision = "2f829ccb897e"
down_revision = "77e8f4d7dad6"
branch_labels = None
depends_on = None


def _fk(table: str, column: str, target: str, on_delete: str = "RESTRICT"):
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"{table}_{column}_fkey", ondelete=on_delete
    )


def upgrade() -> None:
    op.create_table(
        "waste_reasons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _fk("waste_reasons", "tenant_id", "tenants.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_waste_reasons_code"),
    )
    op.create_index("ix_waste_reasons_tenant_id", "waste_reasons", ["tenant_id"])

    op.create_table(
        "waste_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("reason_id", sa.Integer(), nullable=False),
        sa.Column("reason_label", sa.String(length=80), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=True),
        sa.Column("product_variation_id", sa.Integer(), nullable=True),
        sa.Column("recipe_version_id", sa.Integer(), nullable=True),
        sa.Column("quantity", Qty(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("qty_base", Qty(), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("related_type", sa.String(length=20), nullable=True),
        sa.Column("related_id", sa.String(length=80), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("void_reason", sa.String(length=300), nullable=True),
        sa.CheckConstraint("status IN ('recorded', 'voided')", name="ck_waste_events_status"),
        sa.CheckConstraint(
            "CAST(qty_base AS NUMERIC) > 0", name="ck_waste_events_qty_base_positive"
        ),
        sa.CheckConstraint(
            "(inventory_item_id IS NULL) <> (product_variation_id IS NULL)",
            name="ck_waste_events_what",
        ),
        _fk("waste_events", "tenant_id", "tenants.id"),
        _fk("waste_events", "location_id", "locations.id"),
        _fk("waste_events", "reason_id", "waste_reasons.id"),
        _fk("waste_events", "inventory_item_id", "inventory_items.id"),
        _fk("waste_events", "product_variation_id", "product_variations.id"),
        _fk("waste_events", "recipe_version_id", "recipe_versions.id"),
        _fk("waste_events", "actor_user_id", "users.id"),
        _fk("waste_events", "transaction_id", "inventory_transactions.id"),
        _fk("waste_events", "voided_by_user_id", "users.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_waste_events_tenant_id", "waste_events", ["tenant_id"])
    op.create_index("ix_waste_events_location_time", "waste_events", ["location_id", "occurred_at"])

    op.create_table(
        "waste_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("waste_event_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("qty_base", Qty(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "CAST(qty_base AS NUMERIC) > 0", name="ck_waste_lines_qty_base_positive"
        ),
        _fk("waste_lines", "tenant_id", "tenants.id"),
        _fk("waste_lines", "waste_event_id", "waste_events.id", "CASCADE"),
        _fk("waste_lines", "inventory_item_id", "inventory_items.id"),
        _fk("waste_lines", "batch_id", "stock_batches.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_waste_lines_waste_event_id", "waste_lines", ["waste_event_id"])


def downgrade() -> None:
    for table in ("waste_lines", "waste_events", "waste_reasons"):
        op.drop_table(table)
