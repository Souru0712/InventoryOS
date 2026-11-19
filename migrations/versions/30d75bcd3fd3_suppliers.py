"""Suppliers (T2.10, DM-11): `suppliers` and `supplier_items`, backfilled from free text.

Expand + backfill. Each business's distinct free-text supplier names on stock items and documents
(trimmed, compared without case) become suppliers, with the lead time of the first stock item
that named them. Each stock item that names a supplier gets a supplier item for its item: its
receive unit and pack size (one base unit when it has none) and, where it has a cost, a last
price of that cost for the pack; it is the item's preferred supplier. The free-text columns stay.
Running it again adds nothing: a business that already has suppliers is skipped.

Revision ID: 30d75bcd3fd3
Revises: 240830e94f98
Create Date: 2026-11-19
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.db import Qty, UTCDateTime

revision = "30d75bcd3fd3"
down_revision = "240830e94f98"
branch_labels = None
depends_on = None

suppliers = sa.table(
    "suppliers",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("name"),
    sa.column("ordering_method"),
    sa.column("delivery_days"),
    sa.column("lead_time_days"),
    sa.column("is_active"),
    sa.column("created_at", UTCDateTime()),
)
supplier_items = sa.table(
    "supplier_items",
    sa.column("tenant_id"),
    sa.column("supplier_id"),
    sa.column("inventory_item_id"),
    sa.column("purchase_unit"),
    sa.column("pack_size_base", Qty()),
    sa.column("last_price_cents"),
    sa.column("last_price_at", UTCDateTime()),
    sa.column("preferred"),
    sa.column("is_active"),
    sa.column("created_at", UTCDateTime()),
)
stock = sa.table(
    "stock_items",
    sa.column("id"),
    sa.column("tenant_id"),
    sa.column("inventory_item_id"),
    sa.column("supplier"),
    sa.column("lead_time_days"),
    sa.column("receive_unit"),
    sa.column("pack_size_base", Qty()),
    sa.column("cost_per_base_cents", Qty()),
)
documents = sa.table("documents", sa.column("tenant_id"), sa.column("supplier"))


def _fk(table: str, column: str, target: str, on_delete: str = "RESTRICT"):
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"{table}_{column}_fkey", ondelete=on_delete
    )


def backfill(connection: sa.Connection) -> int:
    """Suppliers and supplier items from free text; the number of suppliers made."""
    now = datetime.now(UTC)
    have = {row.tenant_id for row in connection.execute(sa.select(suppliers.c.tenant_id))}
    names: dict[int, dict[str, tuple[str, int]]] = {}
    rows = connection.execute(sa.select(stock).order_by(stock.c.id)).all()
    for row in rows:
        name = " ".join((row.supplier or "").split())
        if name and row.tenant_id not in have:
            names.setdefault(row.tenant_id, {}).setdefault(
                name.casefold(), (name[:200], row.lead_time_days or 2)
            )
    for row in connection.execute(sa.select(documents)):
        name = " ".join((row.supplier or "").split())
        if name and row.tenant_id not in have:
            names.setdefault(row.tenant_id, {}).setdefault(name.casefold(), (name[:200], 2))
    made = 0
    ids: dict[tuple[int, str], int] = {}
    for tenant_id, found in names.items():
        for key, (name, lead) in sorted(found.items()):
            ids[(tenant_id, key)] = connection.execute(
                sa.insert(suppliers)
                .values(
                    tenant_id=tenant_id,
                    name=name,
                    ordering_method="email",
                    delivery_days=0,
                    lead_time_days=max(0, min(lead, 90)),
                    is_active=True,
                    created_at=now,
                )
                .returning(suppliers.c.id)
            ).scalar_one()
            made += 1
    seen: set[tuple[int, int]] = set()
    for row in rows:
        name = " ".join((row.supplier or "").split()).casefold()
        supplier_id = ids.get((row.tenant_id, name))
        if supplier_id is None or row.inventory_item_id is None:
            continue
        if (supplier_id, row.inventory_item_id) in seen:
            continue
        seen.add((supplier_id, row.inventory_item_id))
        pack = Decimal(row.pack_size_base) if row.pack_size_base else Decimal(1)
        cost = Decimal(row.cost_per_base_cents or 0)
        connection.execute(
            sa.insert(supplier_items).values(
                tenant_id=row.tenant_id,
                supplier_id=supplier_id,
                inventory_item_id=row.inventory_item_id,
                purchase_unit=(row.receive_unit or "each")[:40],
                pack_size_base=pack,
                last_price_cents=int((cost * pack).quantize(Decimal(1))) if cost > 0 else None,
                last_price_at=now if cost > 0 else None,
                preferred=True,
                is_active=True,
                created_at=now,
            )
        )
    return made


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("contact_name", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("ordering_method", sa.String(length=10), nullable=False),
        sa.Column("delivery_days", sa.Integer(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("minimum_order_cents", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ordering_method IN ('email', 'phone', 'portal', 'rep', 'other')",
            name="ck_suppliers_ordering_method",
        ),
        sa.CheckConstraint("delivery_days BETWEEN 0 AND 127", name="ck_suppliers_delivery_days"),
        sa.CheckConstraint(
            "CAST(lead_time_days AS NUMERIC) >= 0", name="ck_suppliers_lead_time_days_not_negative"
        ),
        _fk("suppliers", "tenant_id", "tenants.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])
    op.create_table(
        "supplier_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("purchase_unit", sa.String(length=40), nullable=False),
        sa.Column("pack_size_base", Qty(), nullable=False),
        sa.Column("last_price_cents", sa.Integer(), nullable=True),
        sa.Column("last_price_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preferred", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "CAST(pack_size_base AS NUMERIC) > 0", name="ck_supplier_items_pack_size_base_positive"
        ),
        sa.CheckConstraint(
            "CAST(last_price_cents AS NUMERIC) >= 0",
            name="ck_supplier_items_last_price_cents_not_negative",
        ),
        _fk("supplier_items", "tenant_id", "tenants.id"),
        _fk("supplier_items", "supplier_id", "suppliers.id", "CASCADE"),
        _fk("supplier_items", "inventory_item_id", "inventory_items.id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supplier_items_tenant_id", "supplier_items", ["tenant_id"])
    op.create_index("ix_supplier_items_supplier_id", "supplier_items", ["supplier_id"])
    op.create_index("ix_supplier_items_inventory_item_id", "supplier_items", ["inventory_item_id"])
    backfill(op.get_bind())


def downgrade() -> None:
    op.drop_table("supplier_items")
    op.drop_table("suppliers")
