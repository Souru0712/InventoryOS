"""Reorder suggestions on ledger usage (T2.15): `recipe_components` dropped (DM-9 contract).

- Velocity, reorder suggestions and the completeness notice read the ledger and versioned
  recipes; nothing reads `recipe_components` since T2.15. Dropped (destructive; the owner's
  decision of 2026-11-18, DM-9: "dropped once velocity reads ledger usage").
- The downgrade rebuilds it from the version-1 recipes DM-9 made of it: each line of a POS sale
  item's first recipe, once per stock row of that item.

Revision ID: 2675519dc2bc
Revises: 68df2db99f00
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db import Qty

revision = "2675519dc2bc"
down_revision = "68df2db99f00"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("recipe_components")


def downgrade() -> None:
    op.create_table(
        "recipe_components",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("sold_item_id", sa.Integer(), nullable=False),
        sa.Column("stock_item_id", sa.Integer(), nullable=False),
        sa.Column("qty_base_per_sale", Qty(), nullable=False),
        sa.CheckConstraint(
            "CAST(qty_base_per_sale AS NUMERIC) > 0",
            name="ck_recipe_components_qty_base_per_sale_positive",
        ),
        sa.ForeignKeyConstraint(
            ["sold_item_id"],
            ["sold_items.id"],
            name="recipe_components_sold_item_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stock_item_id"],
            ["stock_items.id"],
            name="recipe_components_stock_item_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="recipe_components_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sold_item_id", "stock_item_id"),
    )
    with op.batch_alter_table("recipe_components") as batch:
        batch.create_index("ix_recipe_components_sold_item_id", ["sold_item_id"])
        batch.create_index("ix_recipe_components_stock_item_id", ["stock_item_id"])
        batch.create_index("ix_recipe_components_tenant_id", ["tenant_id"])
    op.execute(
        "INSERT INTO recipe_components (tenant_id, sold_item_id, stock_item_id, qty_base_per_sale)"
        " SELECT DISTINCT r.tenant_id, v.source_sold_item_id, s.id, l.qty_base"
        " FROM recipes r"
        " JOIN product_variations v ON v.id = r.product_variation_id"
        " JOIN recipe_versions rv ON rv.recipe_id = r.id AND rv.version_no = 1"
        " JOIN recipe_lines l ON l.recipe_version_id = rv.id"
        " JOIN stock_items s ON s.inventory_item_id = l.inventory_item_id"
        " AND s.tenant_id = r.tenant_id"
        " WHERE v.source_sold_item_id IS NOT NULL"
    )
