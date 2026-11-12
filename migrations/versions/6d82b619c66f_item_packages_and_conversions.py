"""Item packages and unit conversions (T1.15, DM-5 second half; INV-FR-04, -05, -11).

Expand + backfill: each item's pack sizes, kept until now on its stock rows, become packages
("pack" = N × its unit). The first one found is marked as how deliveries arrive. Conversions
start empty: they are measured, never assumed.

Revision ID: 6d82b619c66f
Revises: 9c903ba85a8f
Create Date: 2026-11-12
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty
from app.units import UNITS

revision = "6d82b619c66f"
down_revision = "9c903ba85a8f"
branch_labels = None
depends_on = None

BASE_UNIT = {"count": "each", "weight": "g", "volume": "ml"}
PLACES = Decimal("0.0001")


def upgrade() -> None:
    op.create_table(
        "item_packages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("quantity", Qty(), nullable=False),
        sa.Column("unit", sa.String(length=60), nullable=False),
        sa.Column("is_purchase", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "CAST(quantity AS NUMERIC) > 0", name="ck_item_packages_quantity_positive"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="item_packages_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="item_packages_inventory_item_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inventory_item_id", "name"),
    )
    with op.batch_alter_table("item_packages") as batch:
        batch.create_index("ix_item_packages_tenant_id", ["tenant_id"], unique=False)
        batch.create_index(
            "ix_item_packages_inventory_item_id", ["inventory_item_id"], unique=False
        )

    op.create_table(
        "item_unit_conversions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("from_quantity", Qty(), nullable=False),
        sa.Column("from_unit", sa.String(length=16), nullable=False),
        sa.Column("to_quantity", Qty(), nullable=False),
        sa.Column("to_unit", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "CAST(from_quantity AS NUMERIC) > 0",
            name="ck_item_unit_conversions_from_quantity_positive",
        ),
        sa.CheckConstraint(
            "CAST(to_quantity AS NUMERIC) > 0",
            name="ck_item_unit_conversions_to_quantity_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="item_unit_conversions_tenant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name="item_unit_conversions_inventory_item_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("item_unit_conversions") as batch:
        batch.create_index("ix_item_unit_conversions_tenant_id", ["tenant_id"], unique=False)
        batch.create_index(
            "ix_item_unit_conversions_inventory_item_id", ["inventory_item_id"], unique=False
        )

    _backfill_packages()


def _as_package(base_amount: Decimal, display_unit: str, dimension: str) -> tuple[Decimal, str]:
    """A pack size in the item's display unit when that is exact, else in its base unit."""
    unit = UNITS.get(display_unit)
    if unit is not None and unit.dimension.value == dimension:
        quantity = (base_amount / unit.to_base).quantize(PLACES)
        if quantity * unit.to_base == base_amount:
            return quantity, display_unit
    return base_amount, BASE_UNIT[dimension]


def _backfill_packages() -> None:
    connection = op.get_bind()
    packages = sa.table(
        "item_packages",
        sa.column("tenant_id"),
        sa.column("inventory_item_id"),
        sa.column("name"),
        sa.column("quantity", Qty()),
        sa.column("unit"),
        sa.column("is_purchase"),
    )
    rows = connection.execute(
        sa.text(
            "SELECT s.tenant_id, s.inventory_item_id, s.pack_size_base, i.display_unit,"
            " i.dimension FROM stock_items s JOIN inventory_items i ON i.id = s.inventory_item_id"
            " WHERE s.pack_size_base IS NOT NULL ORDER BY s.id"
        )
    ).all()
    seen: dict[int, list[Decimal]] = {}
    for tenant_id, item_id, raw, display_unit, dimension in rows:
        amount = Decimal(str(raw)).quantize(PLACES)
        if amount <= 0 or amount in seen.setdefault(item_id, []):
            continue
        seen[item_id].append(amount)
        number = len(seen[item_id])
        quantity, unit = _as_package(amount, display_unit, dimension)
        connection.execute(
            sa.insert(packages).values(
                tenant_id=tenant_id,
                inventory_item_id=item_id,
                name="pack" if number == 1 else f"pack {number}",
                quantity=quantity,
                unit=unit,
                is_purchase=number == 1,
            )
        )


def downgrade() -> None:
    op.drop_table("item_unit_conversions")
    op.drop_table("item_packages")
