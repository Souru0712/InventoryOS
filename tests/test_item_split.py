"""T1.13-a: inventory items once per business, settings per location (C-38, ID-10, DM-5)."""

import argparse
from decimal import Decimal

from sqlalchemy import event, func, select

from app.access import OrgContext
from app.catalog import inventory_items, item_locations
from app.cli import cmd_demo
from app.db import Base, SessionLocal, engine
from app.models import InventoryItem, ItemLocation, Location, StockItem, Tenant, TrackMode
from app.transform.items import item_slug, item_type
from app.units import Dimension


def _location(db, tenant: Tenant, name: str) -> Location:
    location = Location(tenant_id=tenant.id, name=name, timezone="UTC")
    db.add(location)
    db.flush()
    return location


def _stock(db, location: Location, slug: str, **fields) -> StockItem:
    row = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=slug,
        name=fields.pop("name", slug.title()),
        dimension=fields.pop("dimension", Dimension.VOLUME),
        stock_unit=fields.pop("stock_unit", "l"),
        track_mode=TrackMode.MANUAL,
        **fields,
    )
    db.add(row)
    db.flush()
    return row


# --- a saved stock item always stands for one item and one item location ------------------------


def test_a_new_stock_item_gets_its_item_and_its_settings(db, tenant, location):
    milk = _stock(db, location, "milk", reorder_point_base=Decimal("2000"), tracks_expiry=True)
    db.commit()
    item, setting = milk.inventory_item, milk.item_location
    assert (item.slug, item.name, item.dimension, item.base_unit, item.display_unit) == (
        "milk",
        "Milk",
        Dimension.VOLUME,
        "ml",
        "l",
    )
    assert item.item_type == "raw" and item.tracks_expiry and item.tracks_batches
    assert (setting.location_id, setting.reorder_point_base) == (location.id, Decimal("2000"))
    assert setting.inventory_item_id == item.id


def test_the_same_item_at_two_locations_is_one_item_with_two_settings(db, tenant, location):
    harbor = _location(db, tenant, "Harbor")
    here = _stock(db, location, "milk")
    there = _stock(db, harbor, "milk", reorder_point_base=Decimal("500"))
    db.commit()
    assert here.inventory_item_id == there.inventory_item_id
    assert here.item_location_id != there.item_location_id
    assert db.scalar(select(func.count()).select_from(InventoryItem)) == 1
    assert {s.reorder_point_base for s in db.scalars(select(ItemLocation))} == {
        Decimal("0"),
        Decimal("500"),
    }


def test_items_added_together_at_two_locations_are_still_one_item(db, tenant, location):
    """The Square sync adds a product at every location in one flush."""
    harbor = _location(db, tenant, "Harbor")
    for place in (location, harbor):
        db.add(
            StockItem(
                tenant_id=tenant.id,
                location_id=place.id,
                slug="oat-bar",
                name="Oat bar",
                dimension=Dimension.COUNT,
                stock_unit="each",
            )
        )
    db.commit()
    assert db.scalar(select(func.count()).select_from(InventoryItem)) == 1
    assert db.scalar(select(func.count()).select_from(ItemLocation)) == 2


def test_a_slug_measured_differently_elsewhere_becomes_its_own_item(db, tenant, location):
    harbor = _location(db, tenant, "Harbor")
    by_weight = _stock(db, location, "flour", dimension=Dimension.WEIGHT, stock_unit="kg")
    by_count = _stock(db, harbor, "flour", dimension=Dimension.COUNT, stock_unit="each")
    db.commit()
    assert by_weight.inventory_item.slug == "flour"
    assert by_count.inventory_item.slug == "flour-count"
    assert by_count.inventory_item.base_unit == "each"


def test_changes_to_a_stock_item_reach_its_item_and_settings(db, tenant, location):
    milk = _stock(db, location, "milk")
    db.commit()
    milk.name, milk.category, milk.tracks_expiry = "Whole milk", "Dairy", True
    milk.reorder_point_base, milk.par_level_base, milk.is_active = Decimal("3000"), None, False
    db.commit()
    item, setting = milk.inventory_item, milk.item_location
    assert (item.name, item.category, item.tracks_expiry, item.tracks_batches) == (
        "Whole milk",
        "Dairy",
        True,
        True,
    )
    assert (setting.reorder_point_base, setting.is_active) == (Decimal("3000"), False)


def test_a_quantity_change_writes_nothing_to_the_split_tables(db, tenant, location):
    milk = _stock(db, location, "milk")
    db.commit()
    statements: list[str] = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        milk.qty_base = Decimal("4000")
        db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    writes = [s for s in statements if s.lstrip().upper().startswith(("UPDATE", "INSERT"))]
    assert writes and all("inventory_items" not in s and "item_locations" not in s for s in writes)


def test_something_with_a_shelf_price_is_resale():
    assert item_type(Decimal("249")) == "resale"
    assert item_type(None) == "raw"
    assert item_slug("milk", Dimension.VOLUME, None) == "milk"
    assert item_slug("milk", Dimension.VOLUME, Dimension.VOLUME) == "milk"
    assert item_slug("milk", Dimension.COUNT, Dimension.VOLUME) == "milk-count"


# --- DM-5 over the demo business ----------------------------------------------------------------


def test_every_demo_stock_item_maps_to_exactly_one_item_and_location():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    args = argparse.Namespace(
        email="demo@stockpulse.local", password="demopassword", name="Rosewood Coffee", days=7
    )
    assert cmd_demo(args) == 0
    with SessionLocal() as db:
        stock = list(db.scalars(select(StockItem)))
        assert stock and all(s.inventory_item_id and s.item_location_id for s in stock)
        assert len({s.item_location_id for s in stock}) == len(stock)
        settings = {setting.id: setting for setting in db.scalars(select(ItemLocation))}
        assert set(settings) == {s.item_location_id for s in stock}
        for row in stock:
            setting = settings[row.item_location_id]
            assert (setting.location_id, setting.inventory_item_id) == (
                row.location_id,
                row.inventory_item_id,
            )
            assert setting.reorder_point_base == row.reorder_point_base
        items = list(db.scalars(select(InventoryItem)))
        assert all(any(s.inventory_item_id == i.id for s in settings.values()) for i in items)


# --- reading the split tables -------------------------------------------------------------------


def test_items_are_business_wide_and_settings_follow_the_location_scope(db, tenant, location):
    harbor = _location(db, tenant, "Harbor")
    _stock(db, location, "milk")
    _stock(db, harbor, "milk")
    other = Tenant(name="Juniper")
    db.add(other)
    db.flush()
    _stock(db, _location(db, other, "Juniper Main"), "milk")
    db.commit()

    everywhere = OrgContext(tenant_id=tenant.id)
    harbor_only = OrgContext(tenant_id=tenant.id, location_ids=frozenset({harbor.id}))
    (milk,) = inventory_items(db, everywhere)
    assert [i.id for i in inventory_items(db, harbor_only)] == [milk.id]
    assert len(item_locations(db, everywhere, milk.id)) == 2
    assert [s.location_id for s in item_locations(db, harbor_only, milk.id)] == [harbor.id]


# --- T1.13-b: readers use the split rows -------------------------------------------------------


def test_screens_read_the_item_and_its_settings_not_the_legacy_columns(db, tenant, location):
    """What the item screens (T1.14) will change directly is what every reader shows."""
    from app.orchestration.views import stock_views

    milk = _stock(db, location, "milk", reorder_point_base=Decimal("1000"))
    _stock(db, location, "apples", dimension=Dimension.COUNT, stock_unit="each")
    db.commit()
    milk.inventory_item.name = "Barista milk"
    milk.inventory_item.display_unit = "ml"
    milk.item_location.reorder_point_base = Decimal("2500")
    db.commit()
    db.expire_all()

    org = OrgContext(tenant_id=tenant.id)
    views = stock_views(db, org)
    assert [v.name for v in views] == ["Apples", "Barista milk"]  # sorted by the item's name
    shown = views[1]
    assert (shown.stock_unit, shown.reorder_point_base) == ("ml", Decimal("2500"))
    assert db.get(StockItem, milk.id)._name == "Milk"  # the legacy column was not consulted

    milk = db.get(StockItem, milk.id)
    milk.item_location.is_active = False
    db.commit()
    assert [v.name for v in stock_views(db, org)] == ["Apples"]


def test_writing_a_stock_item_fills_the_split_row_and_the_legacy_column(db, tenant, location):
    milk = _stock(db, location, "milk")
    db.commit()
    milk.par_level_base = Decimal("6000")
    milk.dimension = Dimension.WEIGHT
    db.commit()
    db.expire_all()
    milk = db.get(StockItem, milk.id)
    assert milk.item_location.par_level_base == milk._par_level_base == Decimal("6000")
    assert milk.inventory_item.base_unit == "g"  # derived fields follow


def test_no_product_query_filters_or_sorts_on_a_legacy_column():
    """Queries go through the split tables (app/catalog.py), never StockItem's legacy columns."""
    import re
    from pathlib import Path

    fields = "name|category|dimension|stock_unit|tracks_expiry|reorder_point_base|par_level_base"
    pattern = re.compile(rf"StockItem\.(_?(?:{fields}|is_active))\b")
    offenders = [
        f"{path}:{number}"
        for path in Path("app").rglob("*.py")
        if path.as_posix() not in {"app/models.py", "app/catalog.py"}
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []
