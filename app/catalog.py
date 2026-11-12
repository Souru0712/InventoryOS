"""Inventory items defined once per business, with per-location settings (T1.13, C-38, ID-10).

During the transition `stock_items` is still the row every screen reads and writes. This module
keeps `inventory_items` and `item_locations` in step with it: whenever a session flushes a new or
changed stock item, the item it belongs to and its settings at that location are created or
updated, and the stock item is linked to both. Reads of the new tables go through the scoped
functions at the bottom. The compatibility layer goes when every reader and writer has moved
(T1.13-b, then DM-16).
"""

from __future__ import annotations

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.models import InventoryItem, ItemLocation, StockItem
from app.transform.items import base_unit, item_slug, item_type

# Stock item fields the business-wide item mirrors, and those the location's settings mirror.
ITEM_FIELDS = ("name", "category", "dimension", "stock_unit", "tracks_expiry")
LOCATION_FIELDS = ("reorder_point_base", "par_level_base", "is_active")


def _assign(target: object, values: dict) -> None:
    """Set only what differs, so an unchanged mirror writes nothing."""
    for key, value in values.items():
        if getattr(target, key) != value:
            setattr(target, key, value)


def _item_values(stock: StockItem) -> dict:
    return {
        "name": stock.name,
        "category": stock.category,
        "dimension": stock.dimension,
        "base_unit": base_unit(stock.dimension),
        "display_unit": stock.stock_unit,
        "tracks_expiry": stock.tracks_expiry,
        # Perishable stock is what is kept in dated batches until T2.06 says more.
        "tracks_batches": bool(stock.tracks_expiry),
    }


def _location_values(stock: StockItem) -> dict:
    return {
        "reorder_point_base": stock.reorder_point_base,
        "par_level_base": stock.par_level_base,
        "is_active": stock.is_active,
    }


def _lookup(
    session: Session, tenant_id: int, slug: str, pending: dict[tuple[int, str], InventoryItem]
) -> InventoryItem | None:
    """An item with this slug: one created earlier in this flush, or one already stored."""
    return (
        pending.get((tenant_id, slug))
        or session.scalars(
            select(InventoryItem).where(
                InventoryItem.tenant_id == tenant_id, InventoryItem.slug == slug
            )
        ).first()
    )


def _find_item(
    session: Session, stock: StockItem, pending: dict[tuple[int, str], InventoryItem]
) -> InventoryItem:
    """The business's item for this stock item, created if it has none (DM-5 rules)."""
    existing = _lookup(session, stock.tenant_id, stock.slug, pending)
    slug = item_slug(stock.slug, stock.dimension, existing.dimension if existing else None)
    found = existing if slug == stock.slug else _lookup(session, stock.tenant_id, slug, pending)
    if found is not None:
        return found
    item = InventoryItem(
        tenant_id=stock.tenant_id,
        slug=slug,
        item_type=item_type(stock.retail_per_base_cents),
        **_item_values(stock),
    )
    session.add(item)
    pending[(stock.tenant_id, slug)] = item
    return item


def _changed(stock: StockItem, fields: tuple[str, ...]) -> bool:
    state = inspect(stock)
    return any(state.attrs[name].history.has_changes() for name in fields)


def mirror(session: Session, flush_context: object, instances: object) -> None:
    """Before a flush: link new stock items, and carry changed fields across."""
    pending: dict[tuple[int, str], InventoryItem] = {}
    with session.no_autoflush:
        for stock in [obj for obj in session.new if isinstance(obj, StockItem)]:
            if stock.item_location is not None:
                continue
            item = _find_item(session, stock, pending)
            setting = ItemLocation(
                tenant_id=stock.tenant_id,
                location_id=stock.location_id,
                inventory_item=item,
                **_location_values(stock),
            )
            session.add(setting)
            stock.inventory_item = item
            stock.item_location = setting
        for stock in [obj for obj in session.dirty if isinstance(obj, StockItem)]:
            if stock.item_location is None:
                continue
            if _changed(stock, ITEM_FIELDS) and stock.inventory_item is not None:
                _assign(stock.inventory_item, _item_values(stock))
            if _changed(stock, LOCATION_FIELDS):
                _assign(stock.item_location, _location_values(stock))


event.listen(Session, "before_flush", mirror)


# --- reading the split tables -------------------------------------------------------------------


def inventory_items(db: Session, org: OrgContext) -> list[InventoryItem]:
    """The business's items, by name. Definitions are business-wide (C-38)."""
    return list(db.scalars(select_scoped(org, InventoryItem).order_by(InventoryItem.name)))


def item_locations(db: Session, org: OrgContext, inventory_item_id: int) -> list[ItemLocation]:
    """An item's settings at the locations org may see."""
    return list(
        db.scalars(
            select_scoped(org, ItemLocation)
            .where(ItemLocation.inventory_item_id == inventory_item_id)
            .order_by(ItemLocation.location_id)
        )
    )
