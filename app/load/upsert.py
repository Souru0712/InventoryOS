"""Tenant-scoped writes. Idempotent: a re-run of a sync changes nothing."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.models import (
    IdentifierType,
    Location,
    ProductIdentifier,
    SoldItem,
    StockItem,
    TrackMode,
)
from app.sales import DayOfSales, record_daily_pos_sales
from app.transform.barcodes import canonical
from app.units import Dimension, to_base

log = logging.getLogger(__name__)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:160] or "item"


def upsert_locations(
    db: Session, tenant_id: int, rows: list[dict], provider: str = "square"
) -> int:
    existing = {
        loc.provider_location_id: loc
        for loc in db.scalars(
            select(Location).where(Location.tenant_id == tenant_id, Location.provider == provider)
        )
    }
    changed = 0
    for row in rows:
        location = existing.get(row["provider_location_id"])
        if location is None:
            location = Location(tenant_id=tenant_id, provider=provider, **row)
            db.add(location)
            changed += 1
            continue
        for key, value in row.items():
            # An archived location stays off even while the POS still lists it as open.
            if key == "is_active" and location.archived_at is not None:
                continue
            if getattr(location, key) != value:
                setattr(location, key, value)
                changed += 1
    db.flush()
    return changed


SOLD_ITEM_FIELDS = (
    "provider_item_id",
    "name",
    "category",
    "sku",
    "price_cents",
    "currency",
    "sell_unit",
    "is_active",
)


def upsert_sold_items(
    db: Session, tenant_id: int, rows: list[dict], provider: str = "square"
) -> int:
    existing = {
        item.provider_item_id: item
        for item in db.scalars(
            select(SoldItem).where(SoldItem.tenant_id == tenant_id, SoldItem.provider == provider)
        )
    }
    changed = 0
    for row in rows:
        # Named, not "everything the normalizer produced": a new key there is not a new column.
        fields = {key: row[key] for key in SOLD_ITEM_FIELDS if key in row}
        item = existing.get(row["provider_item_id"])
        if item is None:
            db.add(SoldItem(tenant_id=tenant_id, provider=provider, **fields))
            changed += 1
            continue
        for key, value in fields.items():
            if getattr(item, key) != value:
                setattr(item, key, value)
                changed += 1
    db.flush()
    return changed


def ensure_tracked_stock_items(
    db: Session, tenant_id: int, catalog_rows: list[dict], provider: str = "square"
) -> int:
    """Create a countable stock item for every POS-tracked variation, at that POS's locations."""
    locations = list(
        db.scalars(
            select(Location).where(
                Location.tenant_id == tenant_id,
                Location.provider == provider,
                Location.is_active.is_(True),
            )
        )
    )
    if not locations:
        return 0

    known = {
        (item.location_id, item.provider_variation_id)
        for item in db.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id))
        if item.provider_variation_id
    }
    created = 0
    for row in catalog_rows:
        if not row.get("tracks_inventory"):
            continue  # weight and volume stock is defined by the operator, not the POS
        for location in locations:
            if (location.id, row["provider_item_id"]) in known:
                continue
            db.add(
                StockItem(
                    tenant_id=tenant_id,
                    location_id=location.id,
                    slug=slugify(f"{row['name']}-{row['provider_item_id'][-6:]}"),
                    name=row["name"],
                    category=row.get("category"),
                    dimension=Dimension.COUNT,
                    stock_unit="each",
                    receive_unit="each",
                    track_mode=TrackMode.PROVIDER,
                    provider_variation_id=row["provider_item_id"],
                )
            )
            created += 1
    db.flush()
    return created


def sync_catalog_presence(
    db: Session, tenant_id: int, catalog_rows: list[dict], provider: str = "square"
) -> int:
    """Follow Square's catalog in and out of existence, without losing a row.

    A variation the merchant deleted or archived is marked inactive, so it leaves the shelf,
    the alerts and the reorder list, while its counts, receipts and sales stay exactly where
    they are. Restore it in Square and it comes back on its own.
    """
    present = {row["provider_item_id"] for row in catalog_rows}
    changed = 0

    for sold in db.scalars(
        select(SoldItem).where(SoldItem.tenant_id == tenant_id, SoldItem.provider == provider)
    ):
        wanted = sold.provider_item_id in present
        if sold.is_active is not wanted:
            sold.is_active = wanted
            changed += 1

    for item in db.scalars(
        select(StockItem).where(
            StockItem.tenant_id == tenant_id, StockItem.track_mode == TrackMode.PROVIDER
        )
    ):
        if not item.provider_variation_id:
            continue
        wanted = item.provider_variation_id in present
        if item.is_active is not wanted:
            item.is_active = wanted
            changed += 1

    db.flush()
    return changed


def apply_provider_counts(db: Session, tenant_id: int, rows: list[dict]) -> int:
    """Note when the POS last reported its count of each item it tracks; the number of rows
    that matched one. The count itself changes nothing: InventoryOS's ledger is the source of
    truth for every item, Square's included (C-34, T1.18)."""
    location_ids = {
        loc.provider_location_id: loc.id
        for loc in db.scalars(select(Location).where(Location.tenant_id == tenant_id))
    }
    items = {
        (item.location_id, item.provider_variation_id): item
        for item in db.scalars(
            select(StockItem).where(
                StockItem.tenant_id == tenant_id,
                StockItem.track_mode == TrackMode.PROVIDER,
            )
        )
        if item.provider_variation_id
    }

    now = datetime.now(UTC)
    seen = 0
    for row in rows:
        location_id = location_ids.get(row["provider_location_id"])
        if location_id is None:
            continue
        item = items.get((location_id, row["provider_item_id"]))
        if item is None:
            continue
        item.last_synced_at = now
        seen += 1
    db.flush()
    return seen


def upsert_sales_daily(
    db: Session, tenant_id: int, rows: list[dict], provider: str = "square"
) -> int:
    """The POS's sales summed by day, recorded as daily sales transactions (DM-7: `sales_daily`
    is frozen; order lines replace this with T3.05). Returns the days new or changed."""
    location_ids = {
        loc.provider_location_id: loc.id
        for loc in db.scalars(select(Location).where(Location.tenant_id == tenant_id))
    }
    sold = {
        item.provider_item_id: item
        for item in db.scalars(select(SoldItem).where(SoldItem.tenant_id == tenant_id))
    }
    days = []
    for row in rows:
        item = sold.get(row["provider_item_id"])
        location_id = location_ids.get(row["provider_location_id"])
        if item is None or location_id is None:
            continue
        days.append(
            DayOfSales(
                location_id,
                row["dt"],
                item,
                Decimal(row["units"]),
                int(row["gross_cents"] or 0),
                int(row["net_cents"] or 0),
            )
        )
    return record_daily_pos_sales(db, OrgContext.system(tenant_id), provider, days)


def sync_identifiers_from_catalog(db: Session, tenant_id: int, catalog_rows: list[dict]) -> int:
    """Record the SKU and barcode Square already holds, so a scan resolves on the first try."""
    items = {
        item.provider_variation_id: item
        for item in db.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id))
        if item.provider_variation_id
    }
    if not items:
        return 0
    taken = {
        row.value
        for row in db.scalars(
            select(ProductIdentifier).where(ProductIdentifier.tenant_id == tenant_id)
        )
    }

    created = 0
    for row in catalog_rows:
        item = items.get(row["provider_item_id"])
        if item is None:
            continue
        for raw, kind_hint in ((row.get("upc"), None), (row.get("sku"), "sku")):
            value, kind = canonical(raw)
            if not value or value in taken:
                continue
            taken.add(value)
            db.add(
                ProductIdentifier(
                    tenant_id=tenant_id,
                    stock_item_id=item.id,
                    identifier_type=IdentifierType(kind_hint or kind),
                    value=value,
                    source="square",
                )
            )
            created += 1
    db.flush()
    return created


def sync_retail_prices(db: Session, tenant_id: int, catalog_rows: list[dict]) -> int:
    """Copy Square's selling price onto the stock item, so a markdown has something to cut."""
    items = {
        item.provider_variation_id: item
        for item in db.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id))
        if item.provider_variation_id
    }
    if not items:
        return 0

    updated = 0
    for row in catalog_rows:
        item = items.get(row["provider_item_id"])
        price = row.get("price_cents")
        if item is None or not price:
            continue
        # Square prices one sellable unit; our quantities are in base units.
        per_base = (Decimal(price) / to_base(1, item.stock_unit)).quantize(Decimal("0.0001"))
        if item.retail_per_base_cents != per_base:
            item.retail_per_base_cents = per_base
            updated += 1
    db.flush()
    return updated
