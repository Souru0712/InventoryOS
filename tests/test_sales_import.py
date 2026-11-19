"""T2.13: sales file import (STD-FR-03, AC-SAL-04)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.daily_sales import save_day
from app.items import NewItem, create_item
from app.models import InventoryTransaction, SalesTransaction
from app.products import NewVariation, create_product
from app.recipes import NewLine, Owner, save_recipe
from app.sales_import import post, preview, save_mapping, teach, upload
from app.transform.imports import ColumnMap, ImportRuleError

D = Decimal
TODAY = datetime.now(UTC).date()
DAYS = [TODAY - timedelta(days=n) for n in range(6, -1, -1)]


@pytest.fixture
def shop(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    beans = create_item(
        db,
        org,
        NewItem(
            name="Beans",
            location_ids=[location.id],
            dimension="weight",
            display_unit="g",
            quantity="100000",
        ),
    )
    sizes = []
    for n in range(1, 19):
        product = create_product(db, org, name=f"Drink {n}", variations=[NewVariation("Regular")])
        sizes.append(product.variations[0])
    save_recipe(db, org, Owner("variation", sizes[0].id), [NewLine(beans.id, "18", "g")])
    db.commit()
    return org, sizes


def _csv(names, days=DAYS, qty=3):
    lines = ["Date,Item name,Qty"]
    for day in days:
        lines += [f"{day.isoformat()},{name},{qty}" for name in names]
    return ("\r\n".join(lines) + "\r\n").encode()


NAMES = [f"Drink {n}" for n in range(1, 19)] + ["Mystery tea", "Seasonal special"]


def _sale_postings(db):
    return db.scalars(select(InventoryTransaction).where(InventoryTransaction.kind == "sale")).all()


def test_ac_sal_04_preview_posts_matched_lists_unmatched_and_reimport_adds_nothing(
    db, shop, location
):
    org, sizes = shop
    first = upload(db, org, location_id=location.id, filename="week.csv", content=_csv(NAMES))
    shown = preview(db, org, first.id)  # the header is guessed: Date, Item name, Qty
    assert len(shown.days) == 7 and shown.rows == 140
    assert sorted(shown.unmatched) == ["Mystery tea", "Seasonal special"]
    assert shown.days[TODAY][sizes[0].id] == D(3)
    assert post(db, org, first.id) == 7
    db.commit()
    assert len(_sale_postings(db)) == 1  # only today's drink has a recipe in effect
    again = upload(db, org, location_id=location.id, filename="week.csv", content=_csv(NAMES))
    assert post(db, org, again.id) == 0
    db.commit()
    assert len(_sale_postings(db)) == 1  # no duplicate consumption
    origins = {t.origin for t in db.scalars(select(SalesTransaction))}
    assert origins == {"file_import"}


def test_a_learned_name_is_matched_on_the_next_import(db, shop, location):
    org, sizes = shop
    row = upload(db, org, location_id=location.id, filename="a.csv", content=_csv(["Mystery tea"]))
    assert list(preview(db, org, row.id).unmatched) == ["Mystery tea"]
    teach(db, org, "mystery TEA", sizes[3].id)
    later = upload(
        db, org, location_id=location.id, filename="b.csv", content=_csv(["Mystery tea"])
    )
    assert preview(db, org, later.id).unmatched == {}


def test_columns_are_chosen_once_and_remembered(db, shop, location):
    org, _sizes = shop
    odd = b"Sold on;What;How many\r\n" + f"{TODAY.isoformat()};Drink 1;2\r\n".encode()
    row = upload(db, org, location_id=location.id, filename="odd.csv", content=odd)
    with pytest.raises(ImportRuleError, match="Choose which columns"):
        preview(db, org, row.id)
    save_mapping(db, org, row.id, ColumnMap("Sold on", "What", "How many"))
    assert preview(db, org, row.id).days
    second = upload(db, org, location_id=location.id, filename="odd2.csv", content=odd)
    assert preview(db, org, second.id).days  # the same header reuses the saved columns


def test_a_day_entered_another_way_is_skipped(db, shop, location):
    org, sizes = shop
    save_day(db, org, location_id=location.id, day=TODAY, quantities={sizes[1].id: D(5)})
    row = upload(
        db,
        org,
        location_id=location.id,
        filename="t.csv",
        content=_csv(["Drink 2"], days=[TODAY - timedelta(days=1), TODAY]),
    )
    shown = preview(db, org, row.id)
    assert shown.blocked == {TODAY: "manual"}
    assert post(db, org, row.id) == 1  # only yesterday


def test_only_csv_files_are_read(db, shop, location):
    org, _sizes = shop
    with pytest.raises(ImportRuleError, match="Excel"):
        upload(db, org, location_id=location.id, filename="a.xlsx", content=b"PK\x03\x04rest")
    with pytest.raises(ImportRuleError, match="not a CSV"):
        upload(db, org, location_id=location.id, filename="a.csv", content=b"\x00\x01\x02")


def test_importing_through_the_pages(db, shop, location):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        uploaded = client.post(
            "/app/sales/import",
            data={"location_id": location.id},
            files={"file": ("week.csv", _csv(NAMES[:3], days=[TODAY]), "text/csv")},
        )
        assert "Preview" in uploaded.text and "Drink 1" in uploaded.text
        import_id = uploaded.url.path.rsplit("/", 1)[-1]
        done = client.post(f"/app/sales/import/{import_id}/post")
        assert "Imported." in done.text
