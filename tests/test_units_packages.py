"""T1.15: units, packages and conversions (INV-FR-04, -05, -11..14; DR-10, DR-11)."""

import random
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import InventoryCount, InventoryItem, ItemPackage, StockItem
from app.transform.conversions import (
    Conversion,
    ConversionMissing,
    Package,
    check_package,
    to_base,
)
from app.units import UNITS, UNITS_BY_DIMENSION, Dimension, UnitError

D = Decimal


# --- the resolver -------------------------------------------------------------------------------


def test_ac_inv_02_same_dimension_and_packages_convert_exactly():
    assert to_base(D(2), "lb", dimension=Dimension.WEIGHT) == D("907.1847")
    case = {"case": Package("case", D(6), "gal")}
    assert to_base(D(1), "case", dimension=Dimension.VOLUME, packages=case) == D("22712.4707")


def test_ac_inv_03_another_dimension_needs_the_items_own_conversion():
    with pytest.raises(ConversionMissing, match="no conversion between volume and weight"):
        to_base(D(120), "ml", dimension=Dimension.WEIGHT, item="Carrots")
    juice = [Conversion(D(1), "lb", D(340), "ml")]
    assert to_base(D(120), "ml", dimension=Dimension.WEIGHT, conversions=juice) == D("160.0914")


def test_the_press_yield_fixture_works_both_ways():
    """1 lb of ginger presses to 225 ml of juice (the JuJu kitchen's measure)."""
    ginger = [Conversion(D(1), "lb", D(225), "ml")]
    assert to_base(D(225), "ml", dimension=Dimension.WEIGHT, conversions=ginger) == D("453.5924")
    assert to_base(D(1), "lb", dimension=Dimension.VOLUME, conversions=ginger) == D("225.0000")


def test_packages_nest_and_mistakes_are_refused():
    packages = {
        "bottle": Package("bottle", D(330), "ml"),
        "case": Package("case", D(24), "bottle"),
    }
    assert to_base(D(2), "case", dimension=Dimension.VOLUME, packages=packages) == D("15840.0000")
    loop = {"box": Package("box", D(2), "box")}
    with pytest.raises(UnitError, match="in terms of itself"):
        to_base(D(1), "box", dimension=Dimension.COUNT, packages=loop)
    with pytest.raises(UnitError, match="already a unit"):
        check_package(Package("L", D(1), "ml"), {})
    with pytest.raises(UnitError, match="not a unit"):
        to_base(D(1), "handful", dimension=Dimension.COUNT)


def test_tablespoons_and_teaspoons_are_built_in():
    assert to_base(D(3), "tsp", dimension=Dimension.VOLUME) == to_base(
        D(1), "tbsp", dimension=Dimension.VOLUME
    )
    assert to_base(D(1), "tbsp", dimension=Dimension.VOLUME) == D("14.7868")


@pytest.mark.parametrize("seed", range(5))
def test_round_trips_within_a_dimension_lose_nothing_but_the_last_place(seed):
    rng = random.Random(seed)  # noqa: S311 (seeded test data, not secrets)
    for _ in range(200):
        dimension = rng.choice(list(Dimension))
        unit = rng.choice(UNITS_BY_DIMENSION[dimension])
        qty = D(rng.randint(1, 10_000_000)) / D(1000)
        base = to_base(qty, unit.code, dimension=dimension)
        back = base / unit.to_base
        assert abs(back - qty) * unit.to_base <= D("0.00005")  # half the last stored place


@pytest.mark.parametrize("seed", range(5))
def test_round_trips_across_dimensions_go_through_the_conversion_and_back(seed):
    rng = random.Random(seed)  # noqa: S311 (seeded test data, not secrets)
    for _ in range(200):
        link = Conversion(D(rng.randint(1, 5000)) / 10, "g", D(rng.randint(1, 5000)) / 10, "ml")
        qty = D(rng.randint(1, 100_000)) / D(100)
        grams = to_base(qty, "ml", dimension=Dimension.WEIGHT, conversions=[link])
        millilitres = to_base(grams, "g", dimension=Dimension.VOLUME, conversions=[link])
        ratio = max(link.to_quantity / link.from_quantity, link.from_quantity / link.to_quantity)
        assert abs(millilitres - qty) <= D("0.0001") * (1 + ratio)


# --- in the product -----------------------------------------------------------------------------

PASSWORD = "a-good-password"


@pytest.fixture
def owner(db, tenant, location):
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": "owner@rosewood.test", "password": PASSWORD})
    yield client
    client.__exit__(None, None, None)


def new_item(client, name: str, unit: str, location_id: int, **extra) -> int:
    form = {"name": name, "location_ids": [location_id], "display_unit": unit, **extra}
    client.post("/app/catalog/new", data=form)
    with SessionLocal() as db:
        return db.scalars(select(InventoryItem.id).where(InventoryItem.name == name)).one()


def stock_row(item_id: int) -> StockItem:
    with SessionLocal() as db:
        return db.scalars(select(StockItem).where(StockItem.inventory_item_id == item_id)).one()


def last_count() -> Decimal:
    with SessionLocal() as db:
        return (
            db.scalars(select(InventoryCount).order_by(InventoryCount.id.desc())).first().qty_base
        )


def test_a_count_can_be_entered_in_any_unit_or_package(owner, location):
    flour = new_item(owner, "Flour", "kg", location.id)
    owner.post(f"/app/items/{stock_row(flour).id}/count", data={"quantity": "2", "unit": "lb"})
    assert last_count() == D("907.1847")

    milk = new_item(owner, "Milk", "gal", location.id)
    owner.post(
        f"/app/catalog/{milk}/packages", data={"name": "case", "quantity": "6", "unit": "gal"}
    )
    owner.post(f"/app/items/{stock_row(milk).id}/count", data={"quantity": "1", "unit": "case"})
    assert last_count() == D("22712.4707")


def test_a_count_in_another_measure_is_refused_until_the_conversion_exists(owner, location):
    carrots = new_item(owner, "Carrots", "lb", location.id)
    stock = stock_row(carrots).id
    refused = owner.post(f"/app/items/{stock}/count", data={"quantity": "120", "unit": "ml"})
    assert refused.status_code == 400
    assert "no conversion between volume and weight" in refused.text
    owner.post(
        f"/app/catalog/{carrots}/conversions",
        data={"from_quantity": "1", "from_unit": "lb", "to_quantity": "340", "to_unit": "ml"},
    )
    owner.post(f"/app/items/{stock}/count", data={"quantity": "120", "unit": "ml"})
    assert last_count() == D("160.0914")
    again = owner.post(
        f"/app/catalog/{carrots}/conversions",
        data={"from_quantity": "1", "from_unit": "kg", "to_quantity": "700", "to_unit": "ml"},
    )
    assert again.status_code == 400 and "already converts between" in again.text


def test_the_delivery_package_sets_what_receiving_and_scanning_use(owner, location):
    milk = new_item(owner, "Milk", "l", location.id, pack_size="6")
    with SessionLocal() as db:
        (pack,) = db.scalars(select(ItemPackage)).all()
        assert (pack.name, pack.quantity, pack.unit, pack.is_purchase) == ("pack", D(6), "l", True)
    assert stock_row(milk).pack_size_base == D("6000")

    owner.post(
        f"/app/catalog/{milk}/packages", data={"name": "crate", "quantity": "4", "unit": "pack"}
    )
    assert stock_row(milk).pack_size_base == D("6000")  # still delivered by the pack
    with SessionLocal() as db:
        crate = db.scalars(select(ItemPackage).where(ItemPackage.name == "crate")).one()
    owner.post(f"/app/catalog/{milk}/packages/{crate.id}/purchase")
    assert stock_row(milk).pack_size_base == D("24000")

    refused = owner.post(f"/app/catalog/{milk}/packages/{pack.id}/remove")
    assert refused.status_code == 400 and "measured in pack" in refused.text
    owner.post(f"/app/catalog/{milk}/packages/{crate.id}/remove")
    assert stock_row(milk).pack_size_base is None  # no delivery package any more
    page = owner.get(f"/app/catalog/{milk}").text
    assert "6000 ml" in page  # what one pack is, exactly


def test_a_package_in_another_measure_resolves_once_the_conversion_exists(owner, location):
    ginger = new_item(owner, "Ginger juice", "ml", location.id)
    bag = {"name": "bag", "quantity": "1", "unit": "lb", "purchase": "yes"}
    refused = owner.post(f"/app/catalog/{ginger}/packages", data=bag)
    assert refused.status_code == 400 and "no conversion between weight and volume" in refused.text
    owner.post(
        f"/app/catalog/{ginger}/conversions",
        data={"from_quantity": "1", "from_unit": "lb", "to_quantity": "225", "to_unit": "ml"},
    )
    owner.post(f"/app/catalog/{ginger}/packages", data=bag)
    assert stock_row(ginger).pack_size_base == D("225.0000")


def test_how_an_item_is_measured_still_locks_once_stock_has_moved(owner, location):
    flour = new_item(owner, "Flour", "kg", location.id, quantity="5")
    changed = owner.post(
        f"/app/catalog/{flour}", data={"name": "Flour", "item_type": "raw", "display_unit": "l"}
    )
    assert changed.status_code == 400 and "stock history" in changed.text
    assert UNITS["kg"].dimension is Dimension.WEIGHT
