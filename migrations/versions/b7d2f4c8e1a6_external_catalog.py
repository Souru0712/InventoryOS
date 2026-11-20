"""External catalog (T3.03, DM-13): what a POS reports, per connection.

Expand + backfill. `external_locations`, `external_products` and `external_modifiers`, each unique
per (connection, external id). Backfilled from what the legacy sync left: a Square location row a
business already has becomes the POS location it tracks, and a Square-origin sold item becomes an
external product (its item id unknown, its name whole, until the next catalog sync). Rows go to
the business's latest Square connection; a business with Square rows but no connection is
skipped and reported. The mapping to products arrives with `product_mappings` (T3.04).

Revision ID: b7d2f4c8e1a6
Revises: 9c41e7a2d5b3
Create Date: 2026-11-19
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "b7d2f4c8e1a6"
down_revision = "9c41e7a2d5b3"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def _table(name: str, key: str, unique: str, *columns: sa.Column) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column(key, sa.String(length=120), nullable=False),
        *columns,
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("seen_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"{name}_tenant_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["provider_connections.id"],
            name=f"{name}_connection_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", key, name=unique),
    )
    op.create_index(f"ix_{name}_tenant_id", name, ["tenant_id"])
    op.create_index(f"ix_{name}_connection_id", name, ["connection_id"])


def upgrade() -> None:
    _table(
        "external_locations",
        "external_location_id",
        "uq_external_locations_external_id",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("address", sa.String(length=300), nullable=True),
    )
    _table(
        "external_products",
        "external_variation_id",
        "uq_external_products_variation",
        sa.Column("external_item_id", sa.String(length=120), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("variation_name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
    )
    _table(
        "external_modifiers",
        "external_modifier_id",
        "uq_external_modifiers_external_id",
        sa.Column("list_name", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
    )
    _backfill()


# Each business's latest Square connection, whatever its status: history belongs to it.
LOCATION_CONNECTION = (
    "(SELECT MAX(c.id) FROM provider_connections c"
    " WHERE c.tenant_id = locations.tenant_id AND c.provider = 'square')"
)
SOLD_CONNECTION = (
    "(SELECT MAX(c.id) FROM provider_connections c"
    " WHERE c.tenant_id = sold_items.tenant_id AND c.provider = 'square')"
)
SQUARE_LOCATIONS = "locations.provider = 'square' AND locations.provider_location_id IS NOT NULL"
SQUARE_SOLD = "sold_items.provider = 'square'"
ORPHANS = (
    f"SELECT (SELECT COUNT(*) FROM locations WHERE {SQUARE_LOCATIONS}"  # noqa: S608 (constants of this file)
    f" AND {LOCATION_CONNECTION} IS NULL)"
    f" + (SELECT COUNT(*) FROM sold_items WHERE {SQUARE_SOLD} AND {SOLD_CONNECTION} IS NULL)"
)
COPY_LOCATIONS = (
    "INSERT INTO external_locations"  # noqa: S608 (constants of this file)
    " (tenant_id, connection_id, external_location_id, name, timezone, address, active, seen_at)"
    f" SELECT tenant_id, {LOCATION_CONNECTION}, provider_location_id, name, timezone, address,"
    " is_active, CURRENT_TIMESTAMP"
    f" FROM locations WHERE {SQUARE_LOCATIONS} AND {LOCATION_CONNECTION} IS NOT NULL"
)
COPY_SOLD_ITEMS = (
    "INSERT INTO external_products"  # noqa: S608 (constants of this file)
    " (tenant_id, connection_id, external_variation_id, external_item_id, name, variation_name,"
    " category, price_cents, unit, active, seen_at)"
    f" SELECT tenant_id, {SOLD_CONNECTION}, provider_item_id, NULL, name, '', category,"
    " price_cents, sell_unit, is_active, CURRENT_TIMESTAMP"
    f" FROM sold_items WHERE {SQUARE_SOLD} AND {SOLD_CONNECTION} IS NOT NULL"
)


def _backfill() -> None:
    # Fixed SQL: every piece above is a constant of this file.
    bind = op.get_bind()
    orphans = bind.execute(sa.text(ORPHANS)).scalar_one()
    if orphans:
        log.warning(
            "DM-13: %d Square row(s) belong to a business with no Square connection", orphans
        )
    bind.execute(sa.text(COPY_LOCATIONS))
    bind.execute(sa.text(COPY_SOLD_ITEMS))


def downgrade() -> None:
    for name in ("external_modifiers", "external_products", "external_locations"):
        op.drop_index(f"ix_{name}_connection_id", table_name=name)
        op.drop_index(f"ix_{name}_tenant_id", table_name=name)
        op.drop_table(name)
