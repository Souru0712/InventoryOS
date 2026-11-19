"""T2.20-a: item import, quick-add and the onboarding checklist (INV-FR-08, AC-INV-12, CMD-FR-11)."""

import pytest
from sqlalchemy import func, select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.item_import import import_items, preview_items, quick_add, upload_items
from app.items import NewItem, create_item
from app.models import InventoryItem, Location
from app.onboarding import checklist
from app.transform.item_import import ItemImportError, column_map, item_rows


@pytest.fixture
def org(tenant):
    return OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))


def _items(db) -> int:
    return db.scalar(select(func.count()).select_from(InventoryItem))


def _file(rows: list[str]) -> bytes:
    return (
        "\r\n".join(["Name,Unit,Category,Quantity,Reorder point,Cost", *rows]) + "\r\n"
    ).encode()


def test_ac_inv_12_150_rows_with_3_invalid_import_147_and_say_why(db, tenant, location, org):
    rows = [f"Item {n},kg,Dry goods,{n},2,1.50" for n in range(1, 148)]
    rows += [
        "Saffron,pinch,Spices,1,0,9",  # not a unit
        "Item 7,kg,Dry goods,1,0,1",  # the same name twice in the file
        "Rice,kg,Dry goods,lots,0,1",  # not a quantity
    ]
    digest = upload_items(db, org, _file(rows))
    shown = preview_items(db, org, digest, location.id)
    db.rollback()
    assert len(shown) == 150
    assert _items(db) == 0  # the preview kept nothing
    refused = {r.name: r.error for r in shown if r.error}
    assert set(refused) == {"Saffron", "Item 7", "Rice"}
    assert "not a unit" in refused["Saffron"] and "twice" in refused["Item 7"]
    assert refused["Rice"]

    done = import_items(db, org, digest, location.id)
    db.commit()
    assert sum(1 for r in done if r.error is None) == 147
    assert [r.line for r in done if r.error] == [149, 150, 151]
    assert _items(db) == 147


def test_columns_go_by_their_common_names():
    header = ["Item name", "UOM", "On hand", "Par level", "Unit cost", "Vendor"]
    assert set(column_map(header)) == {"name", "unit", "quantity", "par_level", "cost", "supplier"}
    [row] = item_rows(header, [["Oat milk", "L", "6", "12", "2.10", "Northside"]])
    assert (row.unit, row.dimension, row.values["quantity"]) == ("l", "volume", "6")
    with pytest.raises(ItemImportError, match="item name"):
        column_map(["Unit", "Quantity"])


def test_spreadsheets_and_binary_files_are_refused(db, tenant, org):
    with pytest.raises(ItemImportError, match="Excel"):
        upload_items(db, org, b"PK\x03\x04rest")
    with pytest.raises(ItemImportError, match="not a CSV"):
        upload_items(db, org, b"\x00\x01")


def test_an_existing_name_is_refused_with_the_rest_imported(db, tenant, location, org):
    create_item(
        db,
        org,
        NewItem(name="Flour", location_ids=[location.id], dimension="weight", display_unit="kg"),
    )
    db.commit()
    rows = item_rows(["name", "unit"], [["Flour", "kg"], ["Sugar", "kg"]])
    done = quick_add(db, org, rows, location.id)
    db.commit()
    assert [(r.name, r.error is None) for r in done] == [("Flour", False), ("Sugar", True)]
    assert "already" in done[0].error


def test_the_checklist_follows_what_the_business_has(db, tenant):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    for place in db.scalars(select(Location).where(Location.tenant_id == tenant.id)):
        db.delete(place)
    db.commit()
    steps = {s.key: s.done for s in checklist(db, org)}
    assert list(steps)[:2] == ["location", "items"] and not any(steps.values())
    place = Location(tenant_id=tenant.id, name="Main", timezone="UTC")
    db.add(place)
    db.flush()
    create_item(
        db,
        org,
        NewItem(
            name="Milk",
            location_ids=[place.id],
            dimension="volume",
            display_unit="l",
            reorder_point="2",
        ),
    )
    db.commit()
    steps = {s.key: s.done for s in checklist(db, org)}
    assert steps["location"] and steps["items"] and steps["alerts"]  # a reorder point is set
    assert not steps["count"] and not steps["suppliers"]


def test_importing_and_quick_adding_through_the_pages(db, tenant, location):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        page = client.get("/app")
        assert "Getting set up" in page.text and "Add your items" in page.text
        checked = client.post(
            "/app/items/import",
            data={"location_id": location.id},
            files={
                "file": (
                    "items.csv",
                    _file(["Basil,g,Herbs,100,20,0.05", "Bad,zz,,1,0,1"]),
                    "text/csv",
                )
            },
        )
        assert "1 row ready, 1 with something to fix" in checked.text
        digest = checked.url.path.rsplit("/", 1)[-1]
        done = client.post(f"/app/items/import/{digest}", data={"location_id": location.id})
        assert "1 item added" in done.text and "zz is not a unit" in done.text
        quick = client.post(
            "/app/items/quick-add",
            data={"location_id": location.id, "name_1": "Mint", "unit_1": "g", "qty_1": "50"},
        )
        assert "1 item added" in quick.text
        assert client.get("/app/items/import/template.csv").text.startswith("Name,Unit")
