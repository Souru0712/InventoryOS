"""Inventory categories (T1.14, INV-FR-03): managed per business; items point at one.

Expand + backfill + contract within the item split's own tables: each business's distinct
category names (first spelling wins, any case) become categories, items link to them, and the
free-text column added in T1.13-a goes. Adds item notes (INV-FR-02).

Revision ID: 9c903ba85a8f
Revises: e3f7a1c9d5b2
Create Date: 2026-11-12
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "9c903ba85a8f"
down_revision = "e3f7a1c9d5b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="inventory_categories_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name"),
    )
    with op.batch_alter_table("inventory_categories") as batch:
        batch.create_index("ix_inventory_categories_tenant_id", ["tenant_id"], unique=False)

    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "inventory_items_category_id_fkey",
            "inventory_categories",
            ["category_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_inventory_items_category_id", ["category_id"], unique=False)

    connection = op.get_bind()
    items = sa.table(
        "inventory_items",
        sa.column("id"),
        sa.column("tenant_id"),
        sa.column("category"),
        sa.column("category_id"),
    )
    categories = sa.table(
        "inventory_categories",
        sa.column("id"),
        sa.column("tenant_id"),
        sa.column("name"),
        sa.column("created_at"),
    )
    known: dict[tuple[int, str], int] = {}
    now = datetime.now(UTC)
    rows = connection.execute(
        sa.select(items.c.id, items.c.tenant_id, items.c.category).order_by(items.c.id)
    ).all()
    for item_id, tenant_id, raw in rows:
        name = " ".join((raw or "").split())[:120]
        if not name:
            continue
        key = (tenant_id, name.casefold())
        if key not in known:
            known[key] = connection.execute(
                sa.insert(categories)
                .values(tenant_id=tenant_id, name=name, created_at=now)
                .returning(categories.c.id)
            ).scalar_one()
        connection.execute(
            sa.update(items).where(items.c.id == item_id).values(category_id=known[key])
        )

    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_column("category")


def downgrade() -> None:
    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(sa.Column("category", sa.String(length=200), nullable=True))
    op.execute(
        "UPDATE inventory_items SET category = (SELECT name FROM inventory_categories"
        " WHERE inventory_categories.id = inventory_items.category_id)"
    )
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_index("ix_inventory_items_category_id")
        batch.drop_constraint("inventory_items_category_id_fkey", type_="foreignkey")
        batch.drop_column("notes")
        batch.drop_column("category_id")
    op.drop_table("inventory_categories")
