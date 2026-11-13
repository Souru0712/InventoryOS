"""Owned product catalog (T2.01, C-36): `products`, `product_variations`, `modifiers`.

Expand + backfill (DM-9, products half; recipes follow in T2.02). Every POS sale item
(`sold_items`) becomes a variation of an owned product. Square names a variation "Item -
Variation", so a name is split at its last " - " into product and variation; a name without one
is a product with a single "Regular" variation. Each variation remembers the sale item it came
from (`source_sold_item_id`) for T2.02's recipes and T3.03's mappings (DM-13). A price above zero
is kept as the variation's informational price. Running the backfill again adds nothing: a sale
item that already has a variation is skipped, and a product name already taken is not reused.

The split rule is copied here, not imported, so this migration keeps doing what it did when it
was written.

Revision ID: 5e0b7c41d2a9
Revises: 2832ec7b91b7
Create Date: 2026-11-13
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "5e0b7c41d2a9"
down_revision = "2832ec7b91b7"
branch_labels = None
depends_on = None

sold_items = sa.table(
    "sold_items",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("name"),
    sa.column("category"),
    sa.column("price_cents"),
    sa.column("is_active"),
)
products = sa.table(
    "products",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("name"),
    sa.column("category"),
    sa.column("is_active"),
    sa.column("created_at", UTCDateTime()),
)
variations = sa.table(
    "product_variations",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("product_id"),
    sa.column("name"),
    sa.column("price_cents"),
    sa.column("is_active"),
    sa.column("source_sold_item_id"),
)


def _split(name: str) -> tuple[str, str]:
    clean = " ".join((name or "").split())
    product, sep, variation = clean.rpartition(" - ")
    if sep and product and variation:
        return product[:200], variation[:100]
    return clean[:200], "Regular"


def backfill(connection: sa.Connection) -> int:
    """Products and variations for every sale item without one; the number of variations made."""
    now = datetime.now(UTC)
    linked = {
        row.source_sold_item_id
        for row in connection.execute(sa.select(variations.c.source_sold_item_id))
        if row.source_sold_item_id is not None
    }
    made = 0
    tenants = sorted({row.tenant_id for row in connection.execute(sa.select(sold_items))})
    for tenant_id in tenants:
        taken = {
            row.name.casefold()
            for row in connection.execute(
                sa.select(products.c.name).where(products.c.tenant_id == tenant_id)
            )
        }
        groups: dict[str, list] = {}
        names: dict[str, str] = {}
        for row in connection.execute(
            sa.select(sold_items)
            .where(sold_items.c.tenant_id == tenant_id)
            .order_by(sold_items.c.id)
        ):
            if row.id in linked:
                continue
            product_name, _ = _split(row.name)
            key = product_name.casefold()
            if key in taken:
                continue
            groups.setdefault(key, []).append(row)
            names.setdefault(key, product_name)
        for key, rows in groups.items():
            product_id = connection.execute(
                sa.insert(products)
                .values(
                    tenant_id=tenant_id,
                    name=names[key],
                    category=rows[0].category or None,
                    is_active=any(bool(r.is_active) for r in rows),
                    created_at=now,
                )
                .returning(products.c.id)
            ).scalar_one()
            seen: set[str] = set()
            for row in rows:
                variation = _split(row.name)[1]
                unique, n = variation, 2
                while unique.casefold() in seen:
                    unique, n = f"{variation} {n}", n + 1
                seen.add(unique.casefold())
                connection.execute(
                    sa.insert(variations).values(
                        tenant_id=tenant_id,
                        product_id=product_id,
                        name=unique,
                        price_cents=row.price_cents if (row.price_cents or 0) > 0 else None,
                        is_active=bool(row.is_active),
                        source_sold_item_id=row.id,
                    )
                )
                made += 1
    return made


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="products_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_products_name"),
    )
    with op.batch_alter_table("products") as batch:
        batch.create_index("ix_products_tenant_id", ["tenant_id"], unique=False)

    op.create_table(
        "product_variations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source_sold_item_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "CAST(price_cents AS NUMERIC) >= 0",
            name="ck_product_variations_price_cents_not_negative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="product_variations_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="product_variations_product_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_sold_item_id"],
            ["sold_items.id"],
            name="product_variations_source_sold_item_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "name", name="uq_product_variations_name"),
        sa.UniqueConstraint("source_sold_item_id"),
    )
    with op.batch_alter_table("product_variations") as batch:
        batch.create_index("ix_product_variations_product_id", ["product_id"], unique=False)
        batch.create_index("ix_product_variations_tenant_id", ["tenant_id"], unique=False)

    op.create_table(
        "modifiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("group_name", sa.String(length=100), nullable=True),
        sa.Column("effect", sa.String(length=8), nullable=False),
        sa.Column("scale_factor", Qty(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("effect IN ('none', 'scale', 'add')", name="ck_modifiers_effect"),
        sa.CheckConstraint(
            "(effect = 'scale' AND scale_factor IS NOT NULL "
            "AND CAST(scale_factor AS NUMERIC) > 0) "
            "OR (effect <> 'scale' AND scale_factor IS NULL)",
            name="ck_modifiers_scale_factor",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="modifiers_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_modifiers_name"),
    )
    with op.batch_alter_table("modifiers") as batch:
        batch.create_index("ix_modifiers_tenant_id", ["tenant_id"], unique=False)

    backfill(op.get_bind())


def downgrade() -> None:
    op.drop_table("modifiers")
    op.drop_table("product_variations")
    op.drop_table("products")
