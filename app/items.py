"""Managing items and categories (T1.14; INV-FR-01, -02, -03, -06, -10; DR-24, AC-INV-01).

An item is defined once for the business (inventory_items) and stocked at locations; quantities
still live on each location's stock item (until the ledger, T1.16), so creating an item, or
stocking it somewhere new, creates stock items and lets app/catalog.py link them. Definitions and
levels are written to the split tables directly. Nothing is deleted: items are deactivated,
categories archived or merged (DR-24). Every change is recorded in the business history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped, update_scoped
from app.audit import changes, record_audit
from app.catalog import category_named
from app.models import (
    InventoryCategory,
    InventoryCount,
    InventoryEvent,
    InventoryItem,
    ItemLocation,
    ItemPackage,
    ItemUnitConversion,
    Location,
    ProductIdentifier,
    StockBatch,
    StockItem,
    TrackMode,
)
from app.orchestration.scanning import CodeInUse, link_code
from app.transform.business_days import business_date
from app.transform.conversions import (
    Conversion,
    Package,
    check_conversion,
    check_package,
)
from app.transform.conversions import to_base as resolve
from app.transform.items import (
    ItemRuleError,
    base_unit,
    check_dimension_change,
    check_levels,
    clean_name,
    display_unit_for,
    known_item_type,
    unique_slug,
)
from app.units import Dimension, UnitError, to_base

log = logging.getLogger(__name__)

EXPIRING_DAYS = 7  # "expiring" on the item list: a dated lot within a week (INV-FR-10)


class NotFound(LookupError):
    """An item, setting, category or location that is not this business's: answered with 404."""


# --- reading ------------------------------------------------------------------------------------


@dataclass
class Stocked:
    """An item at one location: its settings, its stock row and the place."""

    setting: ItemLocation
    stock: StockItem
    location: Location

    @property
    def low(self) -> bool:
        reorder = self.setting.reorder_point_base or Decimal("0")
        return self.setting.is_active and reorder > 0 and (self.stock.qty_base or 0) <= reorder


@dataclass
class ItemRow:
    item: InventoryItem
    places: list[Stocked] = field(default_factory=list)
    expiring: bool = False

    @property
    def low(self) -> bool:
        return any(place.low for place in self.places)

    @property
    def on_hand_base(self) -> Decimal:
        return sum((place.stock.qty_base or Decimal("0") for place in self.places), Decimal("0"))


def _stocked(db: Session, org: OrgContext, location_id: int | None = None) -> list[Stocked]:
    """Every stock row org may see, with its settings and location."""
    locations = {loc.id: loc for loc in db.scalars(select_scoped(org, Location))}
    query = select_scoped(org, StockItem)
    if location_id is not None:
        query = query.where(StockItem.location_id == location_id)
    return [
        Stocked(stock.item_location, stock, locations[stock.location_id])
        for stock in db.scalars(query)
        if stock.item_location is not None and stock.location_id in locations
    ]


def _expiring_stock_ids(db: Session, org: OrgContext, places: list[Stocked]) -> set[int]:
    """Stock rows with a dated lot due within EXPIRING_DAYS of their location's today."""
    if not places:
        return set()
    by_id = {place.stock.id: place for place in places}
    now = datetime.now(UTC)
    found = set()
    for batch in db.scalars(
        select_scoped(org, StockBatch).where(StockBatch.stock_item_id.in_(list(by_id)))
    ):
        location = by_id[batch.stock_item_id].location
        today = business_date(now, location.timezone, location.business_day_cutoff_hour)
        if batch.expires_on <= today + timedelta(days=EXPIRING_DAYS):
            found.add(batch.stock_item_id)
    return found


def list_items(
    db: Session,
    org: OrgContext,
    *,
    query: str = "",
    category_id: int | None = None,
    supplier: str = "",
    status: str = "active",
    low: bool = False,
    expiring: bool = False,
    location_id: int | None = None,
) -> list[ItemRow]:
    """The items org may see (those stocked at its locations), searched and filtered."""
    if category_id is not None and get_scoped(db, org, InventoryCategory, category_id) is None:
        raise NotFound("category not found")
    places = _stocked(db, org, location_id)
    rows: dict[int, ItemRow] = {}
    items = {item.id: item for item in db.scalars(select_scoped(org, InventoryItem))}
    for place in places:
        item = items.get(place.stock.inventory_item_id or 0)
        if item is not None:
            rows.setdefault(item.id, ItemRow(item)).places.append(place)
    soon = _expiring_stock_ids(db, org, places) if expiring else set()
    wanted_supplier = supplier.strip().casefold()
    wanted_name = query.strip().casefold()
    result = []
    for row in rows.values():
        item = row.item
        if status == "active" and not item.is_active or status == "inactive" and item.is_active:
            continue
        if wanted_name and wanted_name not in item.name.casefold():
            continue
        if category_id is not None and item.category_id != category_id:
            continue
        if wanted_supplier and not any(
            wanted_supplier in (place.stock.supplier or "").casefold() for place in row.places
        ):
            continue
        if low and not row.low:
            continue
        row.expiring = any(place.stock.id in soon for place in row.places)
        if expiring and not row.expiring:
            continue
        result.append(row)
    return sorted(result, key=lambda row: row.item.name.casefold())


def item_row(db: Session, org: OrgContext, inventory_item_id: int) -> ItemRow:
    """One item with its locations, if org can see it stocked somewhere."""
    item = get_scoped(db, org, InventoryItem, inventory_item_id)
    places = [p for p in _stocked(db, org) if p.stock.inventory_item_id == inventory_item_id]
    if item is None or not places:
        raise NotFound("item not found")
    places.sort(key=lambda place: place.location.name)
    return ItemRow(item, places)


def has_history(db: Session, org: OrgContext, row: ItemRow) -> bool:
    """Whether stock of this item has moved anywhere org can see: a quantity, a count of
    something, a stock event or a dated lot. The zero opening count of a new item is not."""
    ids = [place.stock.id for place in row.places]
    if any((place.stock.qty_base or 0) != 0 for place in row.places):
        return True
    counted = db.scalars(
        select_scoped(org, InventoryCount).where(InventoryCount.stock_item_id.in_(ids))
    )
    if any((count.qty_base or 0) != 0 for count in counted):
        return True
    for model in (InventoryEvent, StockBatch):
        query = select_scoped(org, model).where(model.stock_item_id.in_(ids))
        if db.scalar(select(func.count()).select_from(query.subquery())):
            return True
    return False


def codes(db: Session, org: OrgContext, row: ItemRow) -> list[ProductIdentifier]:
    ids = [place.stock.id for place in row.places]
    return list(
        db.scalars(
            select_scoped(org, ProductIdentifier)
            .where(ProductIdentifier.stock_item_id.in_(ids))
            .order_by(ProductIdentifier.value)
        )
    )


def categories(db: Session, org: OrgContext, archived: bool = False) -> list[InventoryCategory]:
    query = select_scoped(org, InventoryCategory)
    if not archived:
        query = query.where(InventoryCategory.archived_at.is_(None))
    return sorted(db.scalars(query), key=lambda category: category.name.casefold())


def category_counts(db: Session, org: OrgContext) -> dict[int, int]:
    rows = db.execute(
        select(InventoryItem.category_id, func.count())
        .where(InventoryItem.tenant_id == org.tenant_id, InventoryItem.category_id.is_not(None))
        .group_by(InventoryItem.category_id)
    )
    return {category_id: count for category_id, count in rows if category_id is not None}


# --- items --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NewItem:
    name: str
    location_ids: list[int]
    dimension: str
    display_unit: str
    item_type: str = "raw"
    category: str = ""
    notes: str = ""
    quantity: str = "0"
    reorder_point: str = "0"
    par_level: str = ""
    cost_per_unit: str = "0"
    retail_price: str = ""
    supplier: str = ""
    receive_unit: str = ""
    pack_size: str = ""
    lead_time_days: str = "2"
    codes: tuple[str, ...] = ()


def _name_taken(db: Session, org: OrgContext, name: str, other_than: int | None = None) -> bool:
    wanted = name.casefold()
    return any(
        item.name.casefold() == wanted and item.id != other_than
        for item in db.scalars(select_scoped(org, InventoryItem))
    )


def _quantity(raw: str, unit: str) -> Decimal:
    try:
        return to_base(raw or "0", unit)
    except (UnitError, ArithmeticError, ValueError) as exc:
        raise ItemRuleError(f"{raw} is not a quantity.") from exc


def create_item(db: Session, org: OrgContext, new: NewItem, ip: str | None = None) -> InventoryItem:
    """An item stocked at the chosen locations, each with an opening count (AC-INV-01)."""
    from app.load.upsert import slugify

    name = clean_name(new.name)
    try:
        dimension = Dimension(new.dimension)
    except ValueError as exc:
        raise ItemRuleError("Pick how the item is measured.") from exc
    unit = display_unit_for(dimension, new.display_unit)
    item_type = known_item_type(new.item_type)
    found = [get_scoped(db, org, Location, location_id) for location_id in new.location_ids]
    places = [place for place in found if place is not None]
    if len(places) != len(found):
        raise NotFound("location not found")
    if not places:
        raise ItemRuleError("Pick at least one location to stock it at.")
    if _name_taken(db, org, name):
        raise ItemRuleError(f"There is already an item called {name}.")
    quantity = _quantity(new.quantity, unit)
    reorder = _quantity(new.reorder_point, unit)
    par = _quantity(new.par_level, unit) if new.par_level.strip() else None
    check_levels(reorder, par, None)
    try:
        cost = (Decimal(new.cost_per_unit or "0") * 100) / to_base("1", unit)
        retail = (
            (Decimal(new.retail_price) * 100) / to_base("1", unit)
            if new.retail_price.strip()
            else None
        )
    except ArithmeticError as exc:
        raise ItemRuleError("Prices are amounts like 4.50.") from exc
    if cost < 0 or (retail is not None and retail < 0):
        raise ItemRuleError("Prices cannot be negative.")
    receive_unit = new.receive_unit or unit
    if receive_unit != unit:
        display_unit_for(dimension, receive_unit)
    pack = _quantity(new.pack_size, unit) if new.pack_size.strip() else None
    if pack is not None and pack <= 0:
        raise ItemRuleError("A pack holds more than nothing: enter a pack size above zero.")
    try:
        lead_time = int(new.lead_time_days or "2")
    except ValueError as exc:
        raise ItemRuleError("Lead time is a number of days.") from exc
    if lead_time < 0:
        raise ItemRuleError("Lead time cannot be negative.")
    taken = {
        slug
        for slug in db.scalars(
            select(InventoryItem.slug).where(InventoryItem.tenant_id == org.tenant_id)
        )
    }
    slug = unique_slug(slugify(name), taken)
    now = datetime.now(UTC)
    stock_rows = []
    for place in places:
        stock = StockItem(
            tenant_id=org.tenant_id,
            location_id=place.id,
            slug=slug,
            name=name,
            category=new.category,
            dimension=dimension,
            stock_unit=unit,
            track_mode=TrackMode.DERIVED if dimension is not Dimension.COUNT else TrackMode.MANUAL,
            qty_base=quantity,
            reorder_point_base=reorder,
            par_level_base=par,
            cost_per_base_cents=cost,
            retail_per_base_cents=retail,
            receive_unit=receive_unit,
            pack_size_base=pack,
            supplier=new.supplier.strip()[:200] or None,
            lead_time_days=lead_time,
            last_counted_at=now,
        )
        db.add(stock)
        stock_rows.append(stock)
    db.flush()
    for stock in stock_rows:
        db.add(
            InventoryCount(
                tenant_id=org.tenant_id,
                stock_item_id=stock.id,
                qty_base=quantity,
                source="opening",
                note="opening count",
                counted_at=now,
            )
        )
    item = stock_rows[0].inventory_item
    if item is None:  # the flush links every new stock item (app/catalog.py)
        raise RuntimeError("a new stock item was not linked to its item")
    item.item_type = item_type
    item.notes = new.notes.strip() or None
    if pack is not None:
        # The pack size the form asks for is the item's purchase package (T1.15).
        item.packages.append(
            ItemPackage(
                tenant_id=org.tenant_id,
                inventory_item_id=item.id,
                name="pack",
                quantity=Decimal(new.pack_size.strip()),
                unit=unit,
                is_purchase=True,
            )
        )
    for raw in (code for code in new.codes if code.strip()):
        # A code points at one stock row; the first location's, as the old form did.
        try:
            link_code(db, org, stock_rows[0].id, raw, source="manual")
        except CodeInUse:
            log.info("code already identifies another item for tenant %s", org.tenant_id)
    db.flush()
    record_audit(
        db,
        org,
        "item.created",
        f"Added the item {name} at {', '.join(sorted(p.name for p in places))}",
        subject_type="inventory_item",
        subject_id=item.id,
        after={"name": name, "type": item_type, "unit": unit, "category": item.category},
        ip=ip,
    )
    log.info("item %s created at %d locations", item.id, len(places))
    return item


def update_item(
    db: Session,
    org: OrgContext,
    inventory_item_id: int,
    *,
    name: str,
    item_type: str,
    category: str,
    dimension: str,
    display_unit: str,
    notes: str,
    shelf_life_days: str,
    tracks_expiry: bool | None,
    ip: str | None = None,
) -> InventoryItem:
    """Change an item's definition for every location at once (INV-FR-02, INV-FR-14)."""
    row = item_row(db, org, inventory_item_id)
    item = row.item
    name = clean_name(name)
    if _name_taken(db, org, name, other_than=item.id):
        raise ItemRuleError(f"There is already an item called {name}.")
    try:
        wanted = Dimension(dimension)
    except ValueError as exc:
        raise ItemRuleError("Pick how the item is measured.") from exc
    check_dimension_change(item.dimension, wanted, has_history(db, org, row))
    unit = display_unit_for(wanted, display_unit)
    try:
        shelf_life = int(shelf_life_days) if shelf_life_days.strip() else None
    except ValueError as exc:
        raise ItemRuleError("Shelf life is a number of days.") from exc
    if shelf_life is not None and shelf_life < 0:
        raise ItemRuleError("Shelf life cannot be negative.")
    was = _definition(item)
    item.name = name
    item.item_type = known_item_type(item_type)
    item.category_ref = category_named(db, org.tenant_id, category)
    item.dimension, item.base_unit, item.display_unit = wanted, base_unit(wanted), unit
    item.notes = notes.strip() or None
    item.default_shelf_life_days = shelf_life
    item.tracks_expiry = tracks_expiry
    item.tracks_batches = bool(tracks_expiry)
    db.flush()
    before, after = changes(was, _definition(item))
    if after:
        record_audit(
            db,
            org,
            "item.updated",
            f"Changed {', '.join(after)} for {item.name}",
            subject_type="inventory_item",
            subject_id=item.id,
            before=before,
            after=after,
            ip=ip,
        )
    return item


def _definition(item: InventoryItem) -> dict:
    return {
        "name": item.name,
        "type": item.item_type,
        "category": item.category,
        "measured by": item.dimension.value,
        "unit": item.display_unit,
        "notes": item.notes,
        "shelf life (days)": item.default_shelf_life_days,
        "tracks expiry": item.tracks_expiry,
    }


def update_levels(
    db: Session,
    org: OrgContext,
    inventory_item_id: int,
    item_location_id: int,
    *,
    reorder_point: str,
    par_level: str,
    min_level: str,
    storage_area: str,
    active: bool,
    ip: str | None = None,
) -> ItemLocation:
    """An item's levels and storage at one location (INV-FR-06), in its display unit."""
    row = item_row(db, org, inventory_item_id)
    place = next((p for p in row.places if p.setting.id == item_location_id), None)
    if place is None:
        raise NotFound("setting not found")
    unit = row.item.display_unit
    reorder = _quantity(reorder_point, unit)
    par = _quantity(par_level, unit) if par_level.strip() else None
    minimum = _quantity(min_level, unit) if min_level.strip() else None
    check_levels(reorder, par, minimum)
    setting = place.setting
    was = _levels(setting)
    setting.reorder_point_base, setting.par_level_base = reorder, par
    setting.min_level_base = minimum
    setting.storage_area = storage_area.strip()[:120] or None
    setting.is_active = active
    before, after = changes(was, _levels(setting))
    if after:
        record_audit(
            db,
            org,
            "item.levels_changed",
            f"Changed {', '.join(after)} for {row.item.name} at {place.location.name}",
            subject_type="item_location",
            subject_id=setting.id,
            location_id=place.location.id,
            before=before,
            after=after,
            ip=ip,
        )
    return setting


def _levels(setting: ItemLocation) -> dict:
    return {
        "reorder point": str(setting.reorder_point_base),
        "par": None if setting.par_level_base is None else str(setting.par_level_base),
        "minimum": None if setting.min_level_base is None else str(setting.min_level_base),
        "storage area": setting.storage_area,
        "stocked": setting.is_active,
    }


def set_active(
    db: Session, org: OrgContext, inventory_item_id: int, active: bool, ip: str | None = None
) -> InventoryItem:
    """Deactivate or bring back an item; its history stays either way (DR-24, AC-INV-01)."""
    row = item_row(db, org, inventory_item_id)
    if row.item.is_active is not active:
        row.item.is_active = active
        record_audit(
            db,
            org,
            "item.activated" if active else "item.deactivated",
            f"{'Brought back' if active else 'Deactivated'} the item {row.item.name}",
            subject_type="inventory_item",
            subject_id=row.item.id,
            ip=ip,
        )
    return row.item


def stock_at(
    db: Session, org: OrgContext, inventory_item_id: int, location_id: int, ip: str | None = None
) -> StockItem:
    """Start keeping an item at another location (none on hand), or again where it stopped."""
    row = item_row(db, org, inventory_item_id)
    location = get_scoped(db, org, Location, location_id)
    if location is None:
        raise NotFound("location not found")
    existing = next((p for p in row.places if p.location.id == location.id), None)
    if existing is not None:
        if not existing.setting.is_active:
            existing.setting.is_active = True
            record_audit(
                db,
                org,
                "item.stocked",
                f"Stocked {row.item.name} at {location.name} again",
                subject_type="item_location",
                subject_id=existing.setting.id,
                location_id=location.id,
                ip=ip,
            )
        return existing.stock
    item = row.item
    template = row.places[0].stock
    stock = StockItem(
        tenant_id=org.tenant_id,
        location_id=location.id,
        slug=item.slug,
        name=item.name,
        dimension=item.dimension,
        stock_unit=item.display_unit,
        track_mode=template.track_mode,
        qty_base=Decimal("0"),
        cost_per_base_cents=template.cost_per_base_cents,
        retail_per_base_cents=template.retail_per_base_cents,
        receive_unit=template.receive_unit,
        supplier=template.supplier,
    )
    db.add(stock)
    db.flush()
    record_audit(
        db,
        org,
        "item.stocked",
        f"Stocked {item.name} at {location.name}",
        subject_type="item_location",
        subject_id=stock.item_location_id,
        location_id=location.id,
        ip=ip,
    )
    return stock


# --- categories ---------------------------------------------------------------------------------


def _category(db: Session, org: OrgContext, category_id: int) -> InventoryCategory:
    category = get_scoped(db, org, InventoryCategory, category_id)
    if category is None:
        raise NotFound("category not found")
    return category


def _category_taken(db: Session, org: OrgContext, name: str, other_than: int | None) -> bool:
    wanted = name.casefold()
    return any(
        category.name.casefold() == wanted and category.id != other_than
        for category in db.scalars(select_scoped(org, InventoryCategory))
    )


def create_category(db: Session, org: OrgContext, name: str, ip: str | None = None) -> None:
    name = clean_name(name, 120)
    if _category_taken(db, org, name, None):
        raise ItemRuleError(f"There is already a category called {name}.")
    category = InventoryCategory(tenant_id=org.tenant_id, name=name)
    db.add(category)
    db.flush()
    record_audit(
        db,
        org,
        "item.category_created",
        f"Added the category {name}",
        subject_type="category",
        subject_id=category.id,
        ip=ip,
    )


def rename_category(
    db: Session, org: OrgContext, category_id: int, name: str, ip: str | None = None
) -> None:
    category = _category(db, org, category_id)
    name = clean_name(name, 120)
    if _category_taken(db, org, name, category.id):
        raise ItemRuleError(f"There is already a category called {name}. Merge them instead.")
    if name != category.name:
        record_audit(
            db,
            org,
            "item.category_renamed",
            f"Renamed the category {category.name} to {name}",
            subject_type="category",
            subject_id=category.id,
            before={"name": category.name},
            after={"name": name},
            ip=ip,
        )
        category.name = name


def merge_category(
    db: Session, org: OrgContext, category_id: int, into_id: int, ip: str | None = None
) -> None:
    """Move every item to another category and archive this one."""
    source, target = _category(db, org, category_id), _category(db, org, into_id)
    if source.id == target.id:
        raise ItemRuleError("Pick a different category to merge into.")
    moved = db.execute(
        update_scoped(org, InventoryItem)
        .where(InventoryItem.category_id == source.id)
        .values(category_id=target.id)
    )
    source.archived_at = datetime.now(UTC)
    target.archived_at = None
    record_audit(
        db,
        org,
        "item.categories_merged",
        f"Merged {source.name} into {target.name} ({getattr(moved, 'rowcount', 0)} items)",
        subject_type="category",
        subject_id=target.id,
        ip=ip,
    )


def set_category_archived(
    db: Session, org: OrgContext, category_id: int, archived: bool, ip: str | None = None
) -> None:
    """Archive (leave the pickers, stay on its items) or restore a category."""
    category = _category(db, org, category_id)
    if (category.archived_at is not None) is archived:
        return
    category.archived_at = datetime.now(UTC) if archived else None
    record_audit(
        db,
        org,
        "item.category_archived" if archived else "item.category_restored",
        f"{'Archived' if archived else 'Restored'} the category {category.name}",
        subject_type="category",
        subject_id=category.id,
        ip=ip,
    )


# --- packages and conversions (T1.15; INV-FR-04, -05, -11..13) -----------------------------------


def _packages(item: InventoryItem) -> dict[str, Package]:
    return {p.name.casefold(): Package(p.name, p.quantity, p.unit) for p in item.packages}


def _conversions(item: InventoryItem) -> list[Conversion]:
    return [
        Conversion(c.from_quantity, c.from_unit, c.to_quantity, c.to_unit) for c in item.conversions
    ]


def entered_base(item: InventoryItem, quantity: str, unit: str) -> Decimal:
    """What someone typed (in any unit or package of the item) in its base unit, or refused
    with the reason (DR-11): a missing conversion is named."""
    try:
        amount = Decimal(quantity.strip())
    except ArithmeticError as exc:
        raise ItemRuleError(f"{quantity} is not a quantity.") from exc
    try:
        return resolve(
            amount,
            unit or item.display_unit,
            dimension=item.dimension,
            packages=_packages(item),
            conversions=_conversions(item),
            item=item.name,
        )
    except UnitError as exc:
        raise ItemRuleError(str(exc)) from exc


def package_bases(item: InventoryItem) -> dict[int, Decimal | None]:
    """What one of each package is in the item's base unit; None when it cannot be resolved."""
    result: dict[int, Decimal | None] = {}
    for package in item.packages:
        try:
            result[package.id] = entered_base(item, "1", package.name)
        except ItemRuleError:
            result[package.id] = None
    return result


def _sync_pack(db: Session, org: OrgContext, item: InventoryItem) -> None:
    """Deliveries arrive in the purchase package: every stock row of the item takes its size,
    which receiving and scanning read (no purchase package, no pack size)."""
    db.flush()
    purchase = next((p for p in item.packages if p.is_purchase), None)
    size = package_bases(item).get(purchase.id) if purchase is not None else None
    every_location = OrgContext.system(org.tenant_id)
    for stock in db.scalars(
        select_scoped(every_location, StockItem).where(StockItem.inventory_item_id == item.id)
    ):
        stock.pack_size_base = size


def _amount(value: Decimal) -> str:
    """6 rather than 6.0000 in a sentence."""
    return f"{value.normalize():f}"


def add_package(
    db: Session,
    org: OrgContext,
    inventory_item_id: int,
    *,
    name: str,
    quantity: str,
    unit: str,
    purchase: bool,
    ip: str | None = None,
) -> ItemPackage:
    row = item_row(db, org, inventory_item_id)
    item = row.item
    name = clean_name(name, 60)
    if name.casefold() in _packages(item):
        raise ItemRuleError(f"{item.name} already has a package called {name}.")
    try:
        amount = Decimal(quantity.strip())
        check_package(Package(name, amount, unit), _packages(item))
        base = resolve(
            amount,
            unit,
            dimension=item.dimension,
            packages=_packages(item),
            conversions=_conversions(item),
            item=item.name,
        )
    except ArithmeticError as exc:
        raise ItemRuleError(f"{quantity} is not a quantity.") from exc
    except UnitError as exc:
        raise ItemRuleError(str(exc)) from exc
    if purchase:
        for other in item.packages:
            other.is_purchase = False
    package = ItemPackage(
        tenant_id=org.tenant_id,
        inventory_item_id=item.id,
        name=name,
        quantity=amount,
        unit=unit,
        is_purchase=purchase,
    )
    item.packages.append(package)
    _sync_pack(db, org, item)
    holds = f"{_amount(amount)} {unit}"
    record_audit(
        db,
        org,
        "item.package_added",
        f"Added the package {name} = {holds} to {item.name}",
        subject_type="item_package",
        subject_id=package.id,
        after={"package": name, "holds": holds, "base": str(base)},
        ip=ip,
    )
    return package


def _package(row: ItemRow, package_id: int) -> ItemPackage:
    package = next((p for p in row.item.packages if p.id == package_id), None)
    if package is None:
        raise NotFound("package not found")
    return package


def set_purchase_package(
    db: Session, org: OrgContext, inventory_item_id: int, package_id: int, ip: str | None = None
) -> None:
    row = item_row(db, org, inventory_item_id)
    chosen = _package(row, package_id)
    if chosen.is_purchase:
        return
    for package in row.item.packages:
        package.is_purchase = package.id == chosen.id
    _sync_pack(db, org, row.item)
    record_audit(
        db,
        org,
        "item.package_purchase",
        f"Deliveries of {row.item.name} now arrive by the {chosen.name}",
        subject_type="item_package",
        subject_id=chosen.id,
        ip=ip,
    )


def remove_package(
    db: Session, org: OrgContext, inventory_item_id: int, package_id: int, ip: str | None = None
) -> None:
    row = item_row(db, org, inventory_item_id)
    package = _package(row, package_id)
    users = [p.name for p in row.item.packages if p.unit.casefold() == package.name.casefold()]
    if users:
        raise ItemRuleError(f"{', '.join(users)} is measured in {package.name}; remove it first.")
    row.item.packages.remove(package)
    _sync_pack(db, org, row.item)
    record_audit(
        db,
        org,
        "item.package_removed",
        f"Removed the package {package.name} from {row.item.name}",
        subject_type="item_package",
        subject_id=package_id,
        ip=ip,
    )


def add_conversion(
    db: Session,
    org: OrgContext,
    inventory_item_id: int,
    *,
    from_quantity: str,
    from_unit: str,
    to_quantity: str,
    to_unit: str,
    ip: str | None = None,
) -> ItemUnitConversion:
    """A measured link between two kinds of measure for this item (INV-FR-05)."""
    row = item_row(db, org, inventory_item_id)
    item = row.item
    try:
        link = Conversion(
            Decimal(from_quantity.strip()), from_unit, Decimal(to_quantity.strip()), to_unit
        )
        check_conversion(link)
    except ArithmeticError as exc:
        raise ItemRuleError("Both sides of a conversion are amounts, like 1 and 340.") from exc
    except UnitError as exc:
        raise ItemRuleError(str(exc)) from exc
    if any(existing.dimensions() == link.dimensions() for existing in _conversions(item)):
        kinds = " and ".join(sorted(d.value for d in link.dimensions()))
        raise ItemRuleError(f"{item.name} already converts between {kinds}; remove that first.")
    conversion = ItemUnitConversion(
        tenant_id=org.tenant_id,
        inventory_item_id=item.id,
        from_quantity=link.from_quantity,
        from_unit=from_unit,
        to_quantity=link.to_quantity,
        to_unit=to_unit,
    )
    item.conversions.append(conversion)
    _sync_pack(db, org, item)  # a package in the other measure may resolve now
    text = f"{_amount(link.from_quantity)} {from_unit} = {_amount(link.to_quantity)} {to_unit}"
    record_audit(
        db,
        org,
        "item.conversion_added",
        f"Added {text} for {item.name}",
        subject_type="item_unit_conversion",
        subject_id=conversion.id,
        after={"conversion": text},
        ip=ip,
    )
    return conversion


def remove_conversion(
    db: Session, org: OrgContext, inventory_item_id: int, conversion_id: int, ip: str | None = None
) -> None:
    row = item_row(db, org, inventory_item_id)
    conversion = next((c for c in row.item.conversions if c.id == conversion_id), None)
    if conversion is None:
        raise NotFound("conversion not found")
    row.item.conversions.remove(conversion)
    _sync_pack(db, org, row.item)
    record_audit(
        db,
        org,
        "item.conversion_removed",
        f"Removed a conversion from {row.item.name}",
        subject_type="item_unit_conversion",
        subject_id=conversion_id,
        ip=ip,
    )
