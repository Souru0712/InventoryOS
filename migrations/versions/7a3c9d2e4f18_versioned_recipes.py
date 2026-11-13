"""Versioned recipes (T2.02): `recipes`, `recipe_versions`, `recipe_lines`.

Expand + backfill (DM-9, recipes half). Each product variation made from a sold item
(`source_sold_item_id`, T2.01) gets a recipe whose version 1 holds that sold item's
`recipe_components`: one line per item, in its base unit, effective from 2000-01-01 so every
past sale reads it. An item that appears at several locations is one line (the first
component's amount). Packaging items are flagged. Running it again adds nothing: a variation
that already has a recipe is skipped. `recipe_components` stays until the consumption engine
(T2.05) replaces it.

Revision ID: 7a3c9d2e4f18
Revises: 5e0b7c41d2a9
Create Date: 2026-11-13
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "7a3c9d2e4f18"
down_revision = "5e0b7c41d2a9"
branch_labels = None
depends_on = None

FROM_THE_START = datetime(2000, 1, 1, tzinfo=UTC)

components = sa.table(
    "recipe_components",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("sold_item_id"),
    sa.column("stock_item_id"),
    sa.column("qty_base_per_sale", Qty()),
)
stock = sa.table("stock_items", sa.column("id"), sa.column("inventory_item_id"))
items = sa.table("inventory_items", sa.column("id"), sa.column("base_unit"), sa.column("item_type"))
variations = sa.table(
    "product_variations", sa.column("id"), sa.column("tenant_id"), sa.column("source_sold_item_id")
)
recipes = sa.table(
    "recipes",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("product_variation_id"),
    sa.column("created_at", UTCDateTime()),
)
versions = sa.table(
    "recipe_versions",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("recipe_id"),
    sa.column("version_no"),
    sa.column("effective_from", UTCDateTime()),
    sa.column("yield_quantity", Qty()),
    sa.column("yield_unit"),
    sa.column("yield_base", Qty()),
    sa.column("instructions"),
    sa.column("created_by"),
    sa.column("created_at", UTCDateTime()),
)
lines = sa.table(
    "recipe_lines",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("recipe_version_id"),
    sa.column("position"),
    sa.column("inventory_item_id"),
    sa.column("quantity", Qty()),
    sa.column("unit"),
    sa.column("qty_base", Qty()),
    sa.column("is_packaging"),
)


def backfill(connection: sa.Connection) -> int:
    """Version 1 for every variation whose sold item has components; the number of recipes."""
    now = datetime.now(UTC)
    has_recipe = {
        row.product_variation_id
        for row in connection.execute(sa.select(recipes.c.product_variation_id))
    }
    by_sold = {
        row.source_sold_item_id: (row.id, row.tenant_id)
        for row in connection.execute(sa.select(variations))
        if row.source_sold_item_id is not None
    }
    found = connection.execute(
        sa.select(
            components.c.sold_item_id,
            components.c.qty_base_per_sale,
            items.c.id.label("item_id"),
            items.c.base_unit,
            items.c.item_type,
        )
        .select_from(components)
        .join(stock, stock.c.id == components.c.stock_item_id)
        .join(items, items.c.id == stock.c.inventory_item_id)
        .order_by(components.c.id)
    ).all()
    wanted: dict[int, dict[int, tuple]] = {}
    for row in found:
        owner = by_sold.get(row.sold_item_id)
        if owner is None or owner[0] in has_recipe:
            continue
        wanted.setdefault(row.sold_item_id, {}).setdefault(row.item_id, tuple(row))
    made = 0
    for sold_item_id, by_item in wanted.items():
        variation_id, tenant_id = by_sold[sold_item_id]
        recipe_id = connection.execute(
            sa.insert(recipes)
            .values(tenant_id=tenant_id, product_variation_id=variation_id, created_at=now)
            .returning(recipes.c.id)
        ).scalar_one()
        version_id = connection.execute(
            sa.insert(versions)
            .values(
                tenant_id=tenant_id,
                recipe_id=recipe_id,
                version_no=1,
                effective_from=FROM_THE_START,
                yield_quantity=Decimal(1),
                yield_unit="serving",
                yield_base=Decimal(1),
                instructions=None,
                created_by=None,
                created_at=now,
            )
            .returning(versions.c.id)
        ).scalar_one()
        for position, (_, qty, item_id, base_unit, item_type) in enumerate(by_item.values()):
            amount = Decimal(qty).quantize(Decimal("0.0001"))
            connection.execute(
                sa.insert(lines).values(
                    tenant_id=tenant_id,
                    recipe_version_id=version_id,
                    position=position,
                    inventory_item_id=item_id,
                    quantity=amount,
                    unit=base_unit,
                    qty_base=amount,
                    is_packaging=item_type == "packaging",
                )
            )
        made += 1
    return made


def upgrade() -> None:
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("product_variation_id", sa.Integer(), nullable=True),
        sa.Column("modifier_id", sa.Integer(), nullable=True),
        sa.Column("inventory_item_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(CASE WHEN product_variation_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN modifier_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN inventory_item_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_recipes_one_owner",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="recipes_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["product_variation_id"],
            ["product_variations.id"],
            name="recipes_product_variation_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["modifier_id"], ["modifiers.id"], name="recipes_modifier_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="recipes_inventory_item_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_variation_id"),
        sa.UniqueConstraint("modifier_id"),
        sa.UniqueConstraint("inventory_item_id"),
    )
    with op.batch_alter_table("recipes") as batch:
        batch.create_index("ix_recipes_tenant_id", ["tenant_id"], unique=False)

    op.create_table(
        "recipe_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yield_quantity", Qty(), nullable=False),
        sa.Column("yield_unit", sa.String(length=16), nullable=False),
        sa.Column("yield_base", Qty(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "CAST(version_no AS NUMERIC) > 0", name="ck_recipe_versions_version_no_positive"
        ),
        sa.CheckConstraint(
            "CAST(yield_base AS NUMERIC) > 0", name="ck_recipe_versions_yield_base_positive"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="recipe_versions_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["recipes.id"],
            name="recipe_versions_recipe_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="recipe_versions_created_by_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipe_id", "version_no", name="uq_recipe_versions_number"),
    )
    with op.batch_alter_table("recipe_versions") as batch:
        batch.create_index(
            "ix_recipe_versions_effective", ["recipe_id", "effective_from"], unique=False
        )
        batch.create_index("ix_recipe_versions_recipe_id", ["recipe_id"], unique=False)

    op.create_table(
        "recipe_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("recipe_version_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("quantity", Qty(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("qty_base", Qty(), nullable=False),
        sa.Column("is_packaging", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "CAST(quantity AS NUMERIC) > 0", name="ck_recipe_lines_quantity_positive"
        ),
        sa.CheckConstraint(
            "CAST(qty_base AS NUMERIC) > 0", name="ck_recipe_lines_qty_base_positive"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="recipe_lines_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recipe_version_id"],
            ["recipe_versions.id"],
            name="recipe_lines_recipe_version_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="recipe_lines_inventory_item_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipe_version_id", "inventory_item_id", name="uq_recipe_lines_item"),
    )
    with op.batch_alter_table("recipe_lines") as batch:
        batch.create_index("ix_recipe_lines_inventory_item_id", ["inventory_item_id"], unique=False)
        batch.create_index("ix_recipe_lines_recipe_version_id", ["recipe_version_id"], unique=False)

    backfill(op.get_bind())


def downgrade() -> None:
    op.drop_table("recipe_lines")
    op.drop_table("recipe_versions")
    op.drop_table("recipes")
