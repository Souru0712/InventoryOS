"""Scan a code, know the product, receive against it. Square still owns the quantity."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.access import OrgContext
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    IdentifierType,
    InventoryCount,
    InventoryEvent,
    Location,
    ProductIdentifier,
    StockBatch,
    StockItem,
    Tenant,
    TrackMode,
)
from app.orchestration.scanning import (
    CodeInUse,
    find_by_code,
    link_code,
    receive_scanned,
    suspicious,
)
from app.units import Dimension
from tests.accounts import confirm_email

COKE = "049000028911"  # a real-looking 12 digit UPC-A


@pytest.fixture
def coke(db, tenant, location):
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="coke-12oz",
        name="Coca-Cola 12oz",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.PROVIDER,
        provider_variation_id="VAR-COKE",
        qty_base=Decimal("34"),
        cost_per_base_cents=Decimal("58"),
        pack_size_base=Decimal("24"),
    )
    db.add(item)
    db.flush()
    db.add(
        ProductIdentifier(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            identifier_type=IdentifierType.UPC,
            value=COKE,
            source="square",
        )
    )
    db.commit()
    return item


# --- resolving ------------------------------------------------------------------------------


def test_a_scanned_code_finds_its_item(db, tenant, coke):
    assert find_by_code(db, OrgContext.system(tenant.id), COKE).stock_item_id == coke.id


def test_the_thirteen_digit_spelling_of_the_same_product_also_finds_it(db, tenant, coke):
    # The phone reports EAN-13 with a leading zero; Square stored the 12-digit UPC.
    assert find_by_code(db, OrgContext.system(tenant.id), "0" + COKE).stock_item_id == coke.id


def test_whitespace_from_the_scanner_does_not_stop_a_match(db, tenant, coke):
    assert find_by_code(db, OrgContext.system(tenant.id), f"  {COKE}\n").stock_item_id == coke.id


def test_an_unknown_code_finds_nothing(db, tenant, coke):
    assert find_by_code(db, OrgContext.system(tenant.id), "5012345678900") is None


def test_one_tenants_codes_are_invisible_to_another(db, tenant, coke):
    other = Tenant(name="Someone else")
    db.add(other)
    db.commit()
    assert find_by_code(db, OrgContext.system(other.id), COKE) is None


def test_a_misread_code_is_flagged_before_it_is_linked():
    assert suspicious("049000028912")  # check digit does not add up
    assert not suspicious(COKE)
    assert not suspicious("ABC123", "code_128")  # nothing to check


# --- linking --------------------------------------------------------------------------------


def test_linking_teaches_the_scanner(db, tenant, location, coke):
    water = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="water",
        name="Still water 500ml",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
        qty_base=Decimal("12"),
    )
    db.add(water)
    db.flush()
    identifier = link_code(db, OrgContext.system(tenant.id), water.id, " 5012345678900 ", "ean_13")
    db.commit()
    assert identifier.value == "5012345678900"
    assert identifier.identifier_type is IdentifierType.EAN
    assert find_by_code(db, OrgContext.system(tenant.id), "5012345678900").stock_item_id == water.id


def test_linking_the_same_code_to_the_same_item_twice_is_harmless(db, tenant, coke):
    first = link_code(db, OrgContext.system(tenant.id), coke.id, COKE)
    assert first.stock_item_id == coke.id
    assert db.query(ProductIdentifier).count() == 1


def test_one_code_cannot_point_at_two_products(db, tenant, location, coke):
    other = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="other",
        name="Something else",
        dimension=Dimension.COUNT,
        stock_unit="each",
        qty_base=Decimal("1"),
    )
    db.add(other)
    db.flush()
    with pytest.raises(CodeInUse):
        link_code(db, OrgContext.system(tenant.id), other.id, COKE)


# --- receiving ------------------------------------------------------------------------------


def test_receiving_raises_stock_and_leaves_a_trail(db, tenant, coke):
    result = receive_scanned(db, coke, Decimal("24"), user_id=None)
    db.commit()

    assert coke.qty_base == Decimal("58")
    assert result["qty_after_base"] == Decimal("58")
    # Nothing is sent to Square (C-34), so there is no push to report.
    assert "pushed_to_pos" not in result

    event = db.query(InventoryEvent).one()
    assert (event.action, event.source) == ("received", "barcode_scan")
    assert (event.delta_base, event.qty_after_base) == (Decimal("24"), Decimal("58"))
    # The receipt re-anchors the quantity, exactly as an invoice does.
    assert db.query(InventoryCount).filter(InventoryCount.source == "scan").count() == 1


def test_a_dated_scan_creates_the_batch_the_expiring_page_reads(db, tenant, coke):
    best_before = date.today() + timedelta(days=30)
    result = receive_scanned(db, coke, Decimal("24"), expires_on=best_before)
    db.commit()

    batch = db.query(StockBatch).one()
    assert result["dated"] is True
    assert (batch.received_qty_base, batch.expires_on, batch.source) == (
        Decimal("24"),
        best_before,
        "scan",
    )
    assert batch.cost_per_base_cents == Decimal("58")


def test_an_undated_scan_creates_no_batch(db, tenant, coke):
    receive_scanned(db, coke, Decimal("24"))
    db.commit()
    assert db.query(StockBatch).count() == 0


def test_receiving_nothing_is_refused(db, tenant, coke):
    for bad in (Decimal("0"), Decimal("-5")):
        with pytest.raises(ValueError):
            receive_scanned(db, coke, bad)


# --- through the browser --------------------------------------------------------------------


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def signed_up(client, name="Rosewood", email="owner@rosewood.test") -> int:
    client.post(
        "/signup", data={"business_name": name, "email": email, "password": "a-good-password"}
    )
    confirm_email(email)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.name == name).one()
        location = Location(
            tenant_id=tenant.id, provider="demo", provider_location_id=f"loc-{name}", name="Main"
        )
        db.add(location)
        db.flush()
        item = StockItem(
            tenant_id=tenant.id,
            location_id=location.id,
            slug="beans",
            name="House beans 1kg",
            dimension=Dimension.COUNT,
            stock_unit="each",
            track_mode=TrackMode.MANUAL,
            qty_base=Decimal("10"),
            pack_size_base=Decimal("12"),
        )
        db.add(item)
        db.commit()
        return item.id


def test_the_scan_page_renders(client):
    signed_up(client)
    response = client.get("/app/scan")
    assert response.status_code == 200
    assert "Start camera" in response.text
    assert "/static/vendor/zxing.min.js" in response.text


def test_an_unknown_code_offers_to_be_linked(client):
    signed_up(client)
    body = client.get("/app/scan/lookup", params={"code": COKE}).json()
    assert body["found"] is False
    assert body["code"] == COKE


def test_link_then_scan_resolves_the_product(client):
    item_id = signed_up(client)
    linked = client.post("/app/scan/link", data={"code": COKE, "stock_item_id": item_id})
    assert linked.status_code == 200
    assert linked.json()["item"]["name"] == "House beans 1kg"

    found = client.get("/app/scan/lookup", params={"code": COKE}).json()
    assert found["found"] is True
    assert found["item"]["qty_label"] == "10 each"


def test_scanning_and_receiving_moves_the_stock(client):
    item_id = signed_up(client)
    client.post("/app/scan/link", data={"code": COKE, "stock_item_id": item_id})
    response = client.post("/app/scan/receive", data={"stock_item_id": item_id, "quantity": "24"})
    assert response.status_code == 200
    assert response.json()["qty_label"] == "34 each"
    assert response.json()["qty_text"] == "34"


def test_receiving_by_the_pack_multiplies_by_the_pack_size(client):
    item_id = signed_up(client)
    response = client.post(
        "/app/scan/receive",
        data={"stock_item_id": item_id, "quantity": "2", "as_packs": "true"},
    )
    # Two packs of 12 on top of the 10 already there.
    assert response.json()["qty_label"] == "34 each"
    assert response.json()["qty_text"] == "34"


def test_a_dated_receipt_through_the_browser_creates_a_batch(client):
    item_id = signed_up(client)
    best_before = (date.today() + timedelta(days=14)).isoformat()
    body = client.post(
        "/app/scan/receive",
        data={"stock_item_id": item_id, "quantity": "6", "expires_on": best_before},
    ).json()
    assert body["dated"] is True
    with SessionLocal() as db:
        assert db.query(StockBatch).one().expires_on == date.fromisoformat(best_before)


def test_correcting_the_count_replaces_it_rather_than_adding(client):
    item_id = signed_up(client)
    body = client.post("/app/scan/count", data={"stock_item_id": item_id, "quantity": "7"}).json()
    assert body["qty_label"] == "7 each"
    assert body["qty_text"] == "7"


def test_a_quantity_that_is_not_a_number_is_refused(client):
    item_id = signed_up(client)
    response = client.post("/app/scan/receive", data={"stock_item_id": item_id, "quantity": "lots"})
    assert response.status_code == 400


def test_one_tenant_cannot_scan_into_another_tenants_stock(client):
    item_id = signed_up(client, "A", "a@rosewood.test")
    client.post("/logout")
    signed_up(client, "B", "b@rosewood.test")
    for path, payload in [
        ("/app/scan/link", {"code": COKE, "stock_item_id": item_id}),
        ("/app/scan/receive", {"stock_item_id": item_id, "quantity": "5"}),
        ("/app/scan/count", {"stock_item_id": item_id, "quantity": "5"}),
    ]:
        assert client.post(path, data=payload).status_code == 404, path


# --- reading a date from a photo, through the browser ----------------------------------------


def test_the_expiry_route_reads_a_photo(client, monkeypatch):
    import app.orchestration.scanning as scanning_mod
    from app.extract.expiry import StubExpiryReader

    signed_up(client)
    monkeypatch.setattr(
        scanning_mod,
        "get_reader",
        lambda: StubExpiryReader(
            {
                "text": "24 OCT 2026",
                "label": "BEST BEFORE",
                "date_iso": "2026-10-24",
                "legible": True,
                "confidence": 1.0,
            }
        ),
    )
    response = client.post(
        "/app/scan/expiry", files={"photo": ("date.jpg", b"bytes", "image/jpeg")}
    )
    body = response.json()
    assert response.status_code == 200
    assert body["found"] is True
    assert body["date"] == "2026-10-24"
    assert body["date_type"] == "best_before"
    assert body["needs_confirmation"] is False


def test_an_unreadable_photo_asks_for_manual_entry(client, monkeypatch):
    import app.orchestration.scanning as scanning_mod
    from app.extract.expiry import StubExpiryReader

    signed_up(client)
    monkeypatch.setattr(
        scanning_mod, "get_reader", lambda: StubExpiryReader({"legible": False, "confidence": 0.0})
    )
    body = client.post(
        "/app/scan/expiry", files={"photo": ("date.jpg", b"bytes", "image/jpeg")}
    ).json()
    assert body["found"] is False
    assert body["needs_confirmation"] is True


def test_an_empty_photo_is_refused(client):
    signed_up(client)
    response = client.post("/app/scan/expiry", files={"photo": ("date.jpg", b"", "image/jpeg")})
    assert response.status_code == 400


def test_a_scanned_date_reaches_the_batch_with_its_confidence(client):
    item_id = signed_up(client)
    client.post(
        "/app/scan/receive",
        data={
            "stock_item_id": item_id,
            "quantity": "6",
            "expires_on": "2026-10-24",
            "date_type": "use_by",
            "date_confidence": "0.97",
        },
    )
    with SessionLocal() as db:
        batch = db.query(StockBatch).one()
        assert batch.date_type == "use_by"
        assert batch.date_confidence == Decimal("0.9700")


def test_a_receipt_with_no_date_records_no_date_metadata(client):
    item_id = signed_up(client)
    client.post(
        "/app/scan/receive",
        data={
            "stock_item_id": item_id,
            "quantity": "6",
            "date_type": "use_by",
            "date_confidence": "0.9",
        },
    )
    with SessionLocal() as db:
        assert db.query(StockBatch).count() == 0
