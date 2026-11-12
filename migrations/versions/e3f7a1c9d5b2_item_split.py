"""Item split (T1.13-a, DM-5, C-38): inventory items once per business, settings per location.

Expand + backfill. Every stock item gets exactly one inventory item and one item location. The
same slug at several locations becomes one item when measured the same way; a slug measured
differently elsewhere becomes its own item, the dimension appended to its slug. stock_items stays
as the compatibility layer (linked to both) until DM-16.

Revision ID: e3f7a1c9d5b2
Revises: d8a2b6e4f1c3
Create Date: 2026-11-12
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from app.db import Qty

revision = "e3f7a1c9d5b2"
down_revision = "d8a2b6e4f1c3"
branch_labels = None
depends_on = None

BASE_UNIT = {"count": "each", "weight": "g", "volume": "ml"}


def upgrade() -> None:
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("item_type", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("base_unit", sa.String(length=16), nullable=False),
        sa.Column("display_unit", sa.String(length=16), nullable=False),
        sa.Column("tracks_batches", sa.Boolean(), nullable=False),
        sa.Column("tracks_expiry", sa.Boolean(), nullable=True),
        sa.Column("default_shelf_life_days", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "item_type IN ('raw', 'prepared', 'finished', 'packaging', 'supply', 'resale')",
            name="ck_inventory_items_item_type",
        ),
        sa.CheckConstraint(
            "dimension IN ('count', 'weight', 'volume')", name="ck_inventory_items_dimension"
        ),
        sa.CheckConstraint(
            "CAST(default_shelf_life_days AS NUMERIC) >= 0",
            name="ck_inventory_items_default_shelf_life_days_not_negative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="inventory_items_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "slug"),
    )
    with op.batch_alter_table("inventory_items") as batch:
        batch.create_index("ix_inventory_items_tenant_id", ["tenant_id"], unique=False)

    op.create_table(
        "item_locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("reorder_point_base", Qty(), nullable=False),
        sa.Column("par_level_base", Qty(), nullable=True),
        sa.Column("min_level_base", Qty(), nullable=True),
        sa.Column("storage_area", sa.String(length=120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "CAST(reorder_point_base AS NUMERIC) >= 0",
            name="ck_item_locations_reorder_point_base_not_negative",
        ),
        sa.CheckConstraint(
            "CAST(par_level_base AS NUMERIC) >= 0",
            name="ck_item_locations_par_level_base_not_negative",
        ),
        sa.CheckConstraint(
            "CAST(min_level_base AS NUMERIC) >= 0",
            name="ck_item_locations_min_level_base_not_negative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="item_locations_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="item_locations_location_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="item_locations_inventory_item_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "inventory_item_id"),
    )
    with op.batch_alter_table("item_locations") as batch:
        batch.create_index("ix_item_locations_tenant_id", ["tenant_id"], unique=False)
        batch.create_index("ix_item_locations_location_id", ["location_id"], unique=False)
        batch.create_index(
            "ix_item_locations_inventory_item_id", ["inventory_item_id"], unique=False
        )

    with op.batch_alter_table("stock_items") as batch:
        batch.add_column(sa.Column("inventory_item_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("item_location_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "stock_items_inventory_item_id_fkey",
            "inventory_items",
            ["inventory_item_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "stock_items_item_location_id_fkey",
            "item_locations",
            ["item_location_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_stock_items_inventory_item_id", ["inventory_item_id"], unique=False)
        batch.create_unique_constraint("uq_stock_items_item_location_id", ["item_location_id"])

    _backfill()


def _backfill() -> None:
    """One item per (business, slug, dimension); one item location per stock item."""
    connection = op.get_bind()
    items = sa.table(
        "inventory_items",
        *(
            sa.column(name)
            for name in (
                "id",
                "tenant_id",
                "slug",
                "name",
                "item_type",
                "category",
                "dimension",
                "base_unit",
                "display_unit",
                "tracks_batches",
                "tracks_expiry",
                "is_active",
                "created_at",
            )
        ),
    )
    settings = sa.table(
        "item_locations",
        *(
            sa.column(name)
            for name in (
                "id",
                "tenant_id",
                "location_id",
                "inventory_item_id",
                "reorder_point_base",
                "par_level_base",
                "is_active",
            )
        ),
    )
    stock = sa.table(
        "stock_items",
        *(
            sa.column(name)
            for name in (
                "id",
                "tenant_id",
                "location_id",
                "slug",
                "name",
                "category",
                "dimension",
                "stock_unit",
                "tracks_expiry",
                "reorder_point_base",
                "par_level_base",
                "retail_per_base_cents",
                "is_active",
                "inventory_item_id",
                "item_location_id",
            )
        ),
    )
    now = datetime.now(UTC)
    known: dict[tuple[int, str], tuple[int, str]] = {}  # (tenant, slug) -> (item id, dimension)
    rows = connection.execute(sa.select(stock).order_by(stock.c.id)).mappings().all()
    for row in rows:
        dimension = str(row["dimension"]).lower()
        slug = row["slug"]
        existing = known.get((row["tenant_id"], slug))
        if existing is not None and existing[1] != dimension:
            slug = f"{slug}-{dimension}"[:160]
            existing = known.get((row["tenant_id"], slug))
        if existing is None:
            item_id = connection.execute(
                sa.insert(items)
                .values(
                    tenant_id=row["tenant_id"],
                    slug=slug,
                    name=row["name"],
                    item_type="resale" if row["retail_per_base_cents"] is not None else "raw",
                    category=row["category"],
                    dimension=dimension,
                    base_unit=BASE_UNIT[dimension],
                    display_unit=row["stock_unit"],
                    tracks_batches=bool(row["tracks_expiry"]),
                    tracks_expiry=row["tracks_expiry"],
                    is_active=True,
                    created_at=now,
                )
                .returning(items.c.id)
            ).scalar_one()
            known[(row["tenant_id"], slug)] = (item_id, dimension)
        else:
            item_id = existing[0]
        setting_id = connection.execute(
            sa.insert(settings)
            .values(
                tenant_id=row["tenant_id"],
                location_id=row["location_id"],
                inventory_item_id=item_id,
                reorder_point_base=row["reorder_point_base"],
                par_level_base=row["par_level_base"],
                is_active=row["is_active"],
            )
            .returning(settings.c.id)
        ).scalar_one()
        connection.execute(
            sa.update(stock)
            .where(stock.c.id == row["id"])
            .values(inventory_item_id=item_id, item_location_id=setting_id)
        )


def downgrade() -> None:
    with op.batch_alter_table("stock_items") as batch:
        batch.drop_constraint("uq_stock_items_item_location_id", type_="unique")
        batch.drop_index("ix_stock_items_inventory_item_id")
        batch.drop_constraint("stock_items_item_location_id_fkey", type_="foreignkey")
        batch.drop_constraint("stock_items_inventory_item_id_fkey", type_="foreignkey")
        batch.drop_column("item_location_id")
        batch.drop_column("inventory_item_id")
    op.drop_table("item_locations")
    op.drop_table("inventory_items")
