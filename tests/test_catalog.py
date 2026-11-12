"""T1.14: item and category screens (INV-FR-01, -02, -03, -06, -10; DR-24; AC-INV-01)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (
    AuditEvent,
    InventoryCategory,
    InventoryCount,
    InventoryItem,
    ItemLocation,
    Location,
    Membership,
    StockBatch,
    StockItem,
    User,
)
from app.security import hash_password

PASSWORD = "a-good-password"


@pytest.fixture
def owner(db, tenant, location):
    """The owner of Rosewood Coffee (one location, Main), signed in."""
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": "owner@rosewood.test", "password": PASSWORD})
    yield client
    client.__exit__(None, None, None)


@pytest.fixture
def harbor(db, tenant):
    place = Location(tenant_id=tenant.id, name="Harbor", timezone="UTC")
    db.add(place)
    db.commit()
    return place


def person(db, tenant, role: str) -> TestClient:
    user = User(
        tenant_id=tenant.id,
        email=f"{role}@rosewood.test",
        password_hash=hash_password(PASSWORD),
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
    db.commit()
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": user.email, "password": PASSWORD})
    return client


def add(client: TestClient, name: str, locations: list[int], **fields):
    form = {"name": name, "location_ids": locations, "display_unit": "l", **fields}
    return client.post("/app/catalog/new", data=form, follow_redirects=False)


def the_item(name: str) -> InventoryItem:
    with SessionLocal() as db:
        return db.scalars(select(InventoryItem).where(InventoryItem.name == name)).one()


def stock_of(item_id: int) -> list[StockItem]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(StockItem)
                .where(StockItem.inventory_item_id == item_id)
                .order_by(StockItem.location_id)
            )
        )


def actions() -> list[str]:
    with SessionLocal() as db:
        return [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id))]


# --- AC-INV-01: create, edit, deactivate --------------------------------------------------------


def test_an_item_is_created_once_and_stocked_at_the_chosen_locations(owner, location, harbor):
    made = add(
        owner,
        "Oat milk",
        [location.id, harbor.id],
        quantity="6",
        reorder_point="2",
        category="Dairy",
        item_type="raw",
        notes="Barista edition",
    )
    item = the_item("Oat milk")
    assert made.status_code == 303 and made.headers["location"] == f"/app/catalog/{item.id}"
    assert (item.display_unit, item.base_unit, item.category, item.notes) == (
        "l",
        "ml",
        "Dairy",
        "Barista edition",
    )
    rows = stock_of(item.id)
    assert [r.location_id for r in rows] == [location.id, harbor.id]
    assert all(r.qty_base == Decimal("6000") and r.reorder_point_base == 2000 for r in rows)
    with SessionLocal() as db:
        counts = db.scalars(select(InventoryCount).where(InventoryCount.source == "opening")).all()
        assert len(counts) == 2
    page = owner.get(f"/app/catalog/{item.id}").text
    assert "Main" in page and "Harbor" in page and "Oat milk" in page
    assert actions() == ["item.created"]


def test_names_are_unique_whatever_the_case(owner, location):
    add(owner, "Oat milk", [location.id])
    again = add(owner, "OAT  MILK", [location.id])
    assert again.status_code == 400 and "already an item called" in again.text


def test_editing_an_item_changes_it_everywhere_it_is_shown(owner, location):
    add(owner, "Oat milk", [location.id])
    item = the_item("Oat milk")
    saved = owner.post(
        f"/app/catalog/{item.id}",
        data={
            "name": "Oat drink",
            "item_type": "resale",
            "category": "Plant milks",
            "display_unit": "ml",
            "notes": "",
            "shelf_life_days": "10",
            "tracks_expiry": "yes",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    item = the_item("Oat drink")
    assert (item.item_type, item.category, item.display_unit) == ("resale", "Plant milks", "ml")
    assert (item.default_shelf_life_days, item.tracks_expiry, item.tracks_batches) == (
        10,
        True,
        True,
    )
    assert "Oat drink" in owner.get("/app").text  # the stock list reads the item
    assert actions()[-1] == "item.updated"


def test_how_an_item_is_measured_is_locked_once_stock_has_moved(owner, location):
    add(owner, "Flour", [location.id], quantity="0")
    item = the_item("Flour")
    to_weight = {"name": "Flour", "item_type": "raw", "display_unit": "kg"}
    assert owner.post(f"/app/catalog/{item.id}", data=to_weight).status_code == 200
    assert the_item("Flour").base_unit == "g"  # nothing had moved yet

    owner.post(f"/app/items/{stock_of(item.id)[0].id}/count", data={"quantity": "3"})
    back = owner.post(f"/app/catalog/{item.id}", data={**to_weight, "display_unit": "l"})
    assert back.status_code == 400 and "stock history" in back.text
    assert the_item("Flour").base_unit == "g"


def test_levels_are_set_per_location_in_the_items_unit(owner, location, harbor):
    add(owner, "Oat milk", [location.id, harbor.id])
    item = the_item("Oat milk")
    setting = stock_of(item.id)[1].item_location_id
    owner.post(
        f"/app/catalog/{item.id}/settings/{setting}",
        data={"reorder_point": "3", "par_level": "12", "min_level": "1", "storage_area": "Walk-in"},
    )
    with SessionLocal() as db:
        saved = db.get(ItemLocation, setting)
        assert (saved.reorder_point_base, saved.par_level_base, saved.min_level_base) == (
            Decimal("3000"),
            Decimal("12000"),
            Decimal("1000"),
        )
        assert saved.storage_area == "Walk-in"
    bad = owner.post(
        f"/app/catalog/{item.id}/settings/{setting}", data={"reorder_point": "5", "par_level": "2"}
    )
    assert bad.status_code == 400 and "Par is the most" in bad.text
    assert actions()[-1] == "item.levels_changed"


def test_a_deactivated_item_keeps_its_history_and_leaves_pick_lists(owner, location):
    add(owner, "Oat milk", [location.id], quantity="4")
    item = the_item("Oat milk")
    stock = stock_of(item.id)[0]
    assert "Oat milk" in owner.get("/app").text
    owner.post(f"/app/catalog/{item.id}/deactivate")
    assert not the_item("Oat milk").is_active
    assert "Oat milk" not in owner.get("/app").text  # the stock list
    assert "Oat milk" not in owner.get("/app/expiring").text  # its item picker
    with SessionLocal() as db:
        assert db.get(StockItem, stock.id).qty_base == Decimal("4000")
        assert db.scalars(
            select(InventoryCount).where(InventoryCount.stock_item_id == stock.id)
        ).all()
    assert "Oat milk" in owner.get("/app/catalog", params={"status": "inactive"}).text
    owner.post(f"/app/catalog/{item.id}/activate")
    assert "Oat milk" in owner.get("/app").text
    assert actions()[-2:] == ["item.deactivated", "item.activated"]


def test_an_item_can_be_stocked_at_another_location(owner, location, harbor):
    add(owner, "Oat milk", [location.id], quantity="4")
    item = the_item("Oat milk")
    owner.post(f"/app/catalog/{item.id}/stock-at", data={"location_id": harbor.id})
    owner.post(f"/app/catalog/{item.id}/stock-at", data={"location_id": harbor.id})
    rows = stock_of(item.id)
    assert [(r.location_id, r.qty_base) for r in rows] == [
        (location.id, Decimal("4000")),
        (harbor.id, Decimal("0")),
    ]


def test_the_old_add_item_address_leads_to_the_new_form(owner):
    moved = owner.get("/app/items/new", follow_redirects=False)
    assert moved.status_code == 303 and moved.headers["location"] == "/app/catalog/new"


# --- INV-FR-10: search and filters --------------------------------------------------------------


def test_items_are_found_by_name_category_supplier_status_level_and_date(
    owner, db, location, harbor
):
    add(
        owner,
        "Oat milk",
        [location.id],
        quantity="1",
        reorder_point="2",
        category="Dairy",
        supplier="Valley Farms",
    )
    add(owner, "Flour", [harbor.id], display_unit="kg", quantity="10", category="Dry goods")
    add(owner, "Butter", [location.id], display_unit="kg", quantity="5", category="Dairy")
    butter = stock_of(the_item("Butter").id)[0]
    db.add(
        StockBatch(
            tenant_id=butter.tenant_id,
            stock_item_id=butter.id,
            received_qty_base=Decimal("5000"),
            expires_on=date.today() + timedelta(days=3),
        )
    )
    db.commit()
    dairy = the_item("Oat milk").category_id

    def names(**params):
        page = owner.get("/app/catalog", params=params).text
        return [n for n in ("Butter", "Flour", "Oat milk") if f">{n}</a>" in page]

    assert names() == ["Butter", "Flour", "Oat milk"]
    assert names(q="oat") == ["Oat milk"]
    assert names(category=dairy) == ["Butter", "Oat milk"]
    assert names(supplier="valley") == ["Oat milk"]
    assert names(low="1") == ["Oat milk"]
    assert names(expiring="1") == ["Butter"]
    assert names(location=harbor.id) == ["Flour"]
    assert owner.get("/app/catalog", params={"category": 99999}).status_code == 404


# --- INV-FR-03: categories ----------------------------------------------------------------------


def test_categories_are_created_renamed_merged_archived_and_restored(owner, location):
    owner.post("/app/catalog/categories", data={"name": "Dairy"})
    assert (
        "already a category" in owner.post("/app/catalog/categories", data={"name": "dairy"}).text
    )
    add(owner, "Oat milk", [location.id], category="Milks")
    with SessionLocal() as db:
        ids = {c.name: c.id for c in db.scalars(select(InventoryCategory))}
    owner.post(f"/app/catalog/categories/{ids['Milks']}/rename", data={"name": "Milk"})
    assert the_item("Oat milk").category == "Milk"
    owner.post(f"/app/catalog/categories/{ids['Milks']}/merge", data={"into_id": ids["Dairy"]})
    assert the_item("Oat milk").category == "Dairy"
    with SessionLocal() as db:
        assert db.get(InventoryCategory, ids["Milks"]).archived_at is not None
    owner.post(f"/app/catalog/categories/{ids['Dairy']}/archive")
    form = owner.get("/app/catalog/new").text
    assert '<option value="Dairy">' not in form  # archived: not offered
    assert the_item("Oat milk").category == "Dairy"  # but kept on its items
    owner.post(f"/app/catalog/categories/{ids['Dairy']}/restore")
    assert '<option value="Dairy">' in owner.get("/app/catalog/new").text
    assert [a for a in actions() if a.startswith("item.categor")] == [
        "item.category_created",
        "item.category_renamed",
        "item.categories_merged",
        "item.category_archived",
        "item.category_restored",
    ]


def test_typing_a_category_name_reuses_it_in_any_case(owner, location):
    add(owner, "Oat milk", [location.id], category="Dairy")
    add(owner, "Butter", [location.id], category=" dairy ")
    assert the_item("Oat milk").category_id == the_item("Butter").category_id


# --- who may do what ----------------------------------------------------------------------------


def test_everyone_can_look_and_only_item_editors_can_change(owner, db, tenant, location):
    add(owner, "Oat milk", [location.id])
    item = the_item("Oat milk")
    employee = person(db, tenant, "employee")
    assert employee.get("/app/catalog").status_code == 200
    page = employee.get(f"/app/catalog/{item.id}")
    assert page.status_code == 200 and 'action="/app/catalog/' not in page.text
    assert (
        employee.post("/app/catalog/new", data={"name": "X", "display_unit": "each"}).status_code
        == 403
    )
    assert employee.post(f"/app/catalog/{item.id}/deactivate").status_code == 403
    employee.__exit__(None, None, None)


def test_packs_lead_time_and_codes_are_kept_from_the_old_form(owner, location):
    """What the old add form set, and receiving and scanning rely on, is still set."""
    add(
        owner,
        "Cold brew",
        [location.id],
        display_unit="each",
        receive_unit="each",
        pack_size="12",
        lead_time_days="4",
        upc="012345678905",
        sku="CB-12",
    )
    stock = stock_of(the_item("Cold brew").id)[0]
    assert (stock.receive_unit, stock.pack_size_base, stock.lead_time_days) == (
        "each",
        Decimal("12"),
        4,
    )
    from app.models import ProductIdentifier

    with SessionLocal() as db:
        values = {c.value for c in db.scalars(select(ProductIdentifier))}
    assert values == {"012345678905", "CB12"}  # stored the way scans read them
    refused = add(owner, "Tea", [location.id], display_unit="each", pack_size="0")
    assert refused.status_code == 400 and "pack size above zero" in refused.text
