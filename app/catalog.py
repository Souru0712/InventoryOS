"""Inventory items defined once per business, with per-location settings (T1.13, C-38, ID-10).

A stock item is the compatibility row for one item at one location until DM-16: its definition
fields read from the inventory item and its settings from the item location (SplitField in
app/models.py, T1.13-b). Whenever a session flushes a new stock item, this module links it to its
item (found by slug and dimension, or created) and a new item location. Queries that filter or
sort on those fields go through the split tables, using the helpers at the bottom.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, event, select
from sqlalchemy.orm import Session

# The module, not its names: app.access imports the models, which import this module last, so
# a process that starts from app.access (the command line does) finds it half-loaded here.
import app.access as access
from app.models import InventoryCategory, InventoryItem, ItemLocation, StockItem

if TYPE_CHECKING:
    from app.access import OrgContext
from app.transform.items import base_unit, item_slug, item_type


def category_named(
    session: Session | None, tenant_id: int, name: str | None
) -> InventoryCategory | None:
    """The business's category with this name in any case, created if it has none; an
    archived one comes back into use. None for a blank name."""
    name = " ".join((name or "").split())[:120]
    if not name:
        return None
    if session is None:
        raise RuntimeError("add the item to a session before naming its category")
    wanted = name.casefold()
    for pending in session.new:
        if (
            isinstance(pending, InventoryCategory)
            and pending.tenant_id == tenant_id
            and pending.name.casefold() == wanted
        ):
            return pending
    with session.no_autoflush:
        found = next(
            (
                category
                for category in session.scalars(
                    select(InventoryCategory).where(InventoryCategory.tenant_id == tenant_id)
                )
                if category.name.casefold() == wanted
            ),
            None,
        )
    if found is not None:
        found.archived_at = None
        return found
    category = InventoryCategory(tenant_id=tenant_id, name=name)
    session.add(category)
    return category


def _item_values(stock: StockItem) -> dict:
    return {
        "name": stock.name,
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
    item.category_ref = category_named(session, stock.tenant_id, stock.category)
    pending[(stock.tenant_id, slug)] = item
    return item


def mirror(session: Session, flush_context: object, instances: object) -> None:
    """Before a flush: link each new stock item to its item and a new item location."""
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


event.listen(Session, "before_flush", mirror)


# --- reading the split tables -------------------------------------------------------------------


def inventory_items(db: Session, org: OrgContext) -> list[InventoryItem]:
    """The business's items, by name. Definitions are business-wide (C-38)."""
    return list(db.scalars(access.select_scoped(org, InventoryItem).order_by(InventoryItem.name)))


def item_locations(db: Session, org: OrgContext, inventory_item_id: int) -> list[ItemLocation]:
    """An item's settings at the locations org may see."""
    return list(
        db.scalars(
            access.select_scoped(org, ItemLocation)
            .where(ItemLocation.inventory_item_id == inventory_item_id)
            .order_by(ItemLocation.location_id)
        )
    )


def active_stock(org: OrgContext) -> Select[StockItem]:
    """Stock items whose item, and whose settings at their location, are active, within org."""
    return (
        access.select_scoped(org, StockItem)
        .join(ItemLocation, ItemLocation.id == StockItem.item_location_id)
        .join(InventoryItem, InventoryItem.id == StockItem.inventory_item_id)
        .where(ItemLocation.is_active.is_(True), InventoryItem.is_active.is_(True))
    )


def by_item_name(query: Select[StockItem]) -> Select[StockItem]:
    """An active_stock() query in item-name order."""
    return query.order_by(InventoryItem.name)
