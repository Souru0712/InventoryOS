"""product identifiers and inventory events

Revision ID: 8ebcae844ae7
Revises: 0752da37c150
Create Date: 2026-09-13 11:06:52.353404
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from datetime import datetime, timezone
from app.db import Qty

revision: str = "8ebcae844ae7"
down_revision: str | None = "0752da37c150"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_identifiers() -> None:
    """Codes that lived on stock_items become rows, so one item can carry several.

    A value already claimed by another item is skipped: the table promises one code, one item.
    """
    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(
                "SELECT id, tenant_id, sku, upc FROM stock_items "
                "WHERE (sku IS NOT NULL AND trim(sku) <> '') OR (upc IS NOT NULL AND trim(upc) <> '')"
            )
        )
        .mappings()
        .all()
    )
    seen: set[tuple[int, str]] = set()
    now = datetime.now(timezone.utc)
    for row in rows:
        for kind, raw in (("UPC", row["upc"]), ("SKU", row["sku"])):
            value = (raw or "").strip()
            if not value or (row["tenant_id"], value) in seen:
                continue
            seen.add((row["tenant_id"], value))
            bind.execute(
                sa.text(
                    "INSERT INTO product_identifiers "
                    "(tenant_id, stock_item_id, identifier_type, value, source, created_at) "
                    "VALUES (:tenant_id, :item, :kind, :value, 'manual', :now)"
                ),
                {
                    "tenant_id": row["tenant_id"],
                    "item": row["id"],
                    "kind": kind,
                    "value": value,
                    "now": now,
                },
            )


def upgrade() -> None:
    op.create_table(
        "inventory_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("stock_item_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("delta_base", Qty(), nullable=True),
        sa.Column("qty_after_base", Qty(), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("pushed_to_pos", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["stock_item_id"],
            ["stock_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("inventory_events", schema=None) as batch_op:
        batch_op.create_index("ix_events_tenant_time", ["tenant_id", "created_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_inventory_events_stock_item_id"), ["stock_item_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_inventory_events_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "product_identifiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("stock_item_id", sa.Integer(), nullable=False),
        sa.Column(
            "identifier_type",
            sa.Enum("UPC", "EAN", "SKU", "OTHER", name="identifiertype"),
            nullable=False,
        ),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["stock_item_id"],
            ["stock_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "value"),
    )
    with op.batch_alter_table("product_identifiers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_product_identifiers_stock_item_id"), ["stock_item_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_product_identifiers_tenant_id"), ["tenant_id"], unique=False
        )

    _backfill_identifiers()

    with op.batch_alter_table("stock_items", schema=None) as batch_op:
        batch_op.drop_column("upc")
        batch_op.drop_column("sku")


def downgrade() -> None:
    with op.batch_alter_table("stock_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sku", sa.VARCHAR(length=120), nullable=True))
        batch_op.add_column(sa.Column("upc", sa.VARCHAR(length=32), nullable=True))

    with op.batch_alter_table("product_identifiers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_product_identifiers_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_product_identifiers_stock_item_id"))

    op.drop_table("product_identifiers")
    with op.batch_alter_table("inventory_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_inventory_events_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_inventory_events_stock_item_id"))
        batch_op.drop_index("ix_events_tenant_time")

    op.drop_table("inventory_events")

    # Postgres keeps enum types after their tables are dropped; remove them so upgrade can rebuild.
    if op.get_bind().dialect.name == "postgresql":
        for name in ("identifiertype",):
            op.execute(f"DROP TYPE IF EXISTS {name}")
