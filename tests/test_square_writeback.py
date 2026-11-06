"""Nothing InventoryOS does writes to Square (C-34, T0.08).

Write-back returns in R1.1 through the outbox. The push functions are kept for it and refuse to
run until then; the tests at the bottom pin what they send when it is switched on.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.extract.invoice import StubExtractor
from app.models import ProviderConnection, StockItem, TrackMode
from app.orchestration import sync
from app.orchestration.receiving import LineDecision, apply_document, parse_document, save_upload
from app.units import Dimension
from tests.sample_files import jpeg

INVOICE = {
    "supplier": "Northside Supply",
    "invoice_number": "NS-9001",
    "currency": "USD",
    "total_cents": 1200,
    "lines": [
        {
            "raw_text": "COCA-COLA 12OZ x24",
            "product_name": "Coca-Cola 12oz",
            "quantity": 24,
            "unit": "each",
            "pack_size": None,
            "unit_price_cents": 50,
            "line_total_cents": 1200,
        },
    ],
}


class FakeSquare:
    """Square's inventory ledger, recording every write it is sent."""

    def __init__(self, ledger: dict):
        self.ledger = ledger
        self.changes: list[dict] = []
        self.keys: list[str] = []
        self.calls = 0

    def __call__(self, _token):  # stands in for SquareClient(token)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def apply_inventory_changes(self, changes: list[dict], idempotency_key: str) -> dict:
        self.calls += 1
        if idempotency_key in self.keys:
            return {}  # Square drops a repeat of the same key
        self.keys.append(idempotency_key)
        for change in changes:
            self.changes.append(change)
            body = change[change["type"].lower()]
            at = (body["catalog_object_id"], body["location_id"])
            quantity = Decimal(body["quantity"])
            if change["type"] == "PHYSICAL_COUNT":
                self.ledger[at] = quantity
            else:
                self.ledger[at] = self.ledger.get(at, Decimal("0")) + quantity
        return {}


@pytest.fixture
def square(db, tenant, location, monkeypatch):
    """A connected tenant, one Square-tracked item, and a fake Square holding 24 of it."""
    db.add(
        ProviderConnection(
            tenant_id=tenant.id,
            provider="square",
            merchant_id="M-1",
            access_token_enc="enc",
            status="active",
        )
    )
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="coke",
        name="Coca-Cola 12oz",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.PROVIDER,
        provider_variation_id="VAR-COKE",
        receive_unit="each",
        qty_base=Decimal("24"),
    )
    db.add(item)
    db.commit()
    fake = FakeSquare({("VAR-COKE", location.provider_location_id): Decimal("24")})
    monkeypatch.setattr(sync, "SquareClient", fake)
    monkeypatch.setattr(sync, "_access_token", lambda _db, _connection: "token")
    return fake, item


@pytest.fixture
def write_back_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "square_write_back", True, raising=False)


@pytest.fixture
def signed_in():
    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        yield client


def shelf(fake: FakeSquare, location) -> Decimal:
    return fake.ledger[("VAR-COKE", location.provider_location_id)]


def receive_by_invoice(db, tenant, location, item, quantity: str) -> dict:
    document = save_upload(
        db,
        tenant.id,
        location.id,
        f"note-{quantity}.jpg",
        jpeg(f"delivery of {quantity}"),
        "image/jpeg",
    )
    db.commit()
    parse_document(db, document.id, StubExtractor(INVOICE))
    db.commit()
    line = document.lines[0]
    result = apply_document(db, document.id, [LineDecision(line.id, item.id, Decimal(quantity))])
    db.commit()
    return result


# --- R1: nothing is written to Square ---------------------------------------------------------


def test_write_back_is_off_by_default():
    assert get_settings().square_write_back is False


def test_receiving_an_invoice_sends_nothing_to_square(db, tenant, location, square):
    """Replaces audit probe R3: a delivery used to be pushed, and a failed push was swallowed."""
    fake, item = square

    receive_by_invoice(db, tenant, location, item, "24")

    assert fake.calls == 0
    assert shelf(fake, location) == Decimal("24")
    db.refresh(item)
    assert item.qty_base == Decimal("48")


def test_receiving_by_scan_sends_nothing_to_square(db, location, square):
    from app.orchestration.scanning import receive_scanned

    fake, item = square
    receive_scanned(db, item, Decimal("6"))
    db.commit()

    assert fake.calls == 0
    db.refresh(item)
    assert item.qty_base == Decimal("30")


def test_counting_by_scan_sends_nothing_to_square(db, square, signed_in):
    fake, item = square

    response = signed_in.post("/app/scan/count", data={"stock_item_id": item.id, "quantity": "37"})

    assert response.status_code == 200
    assert "pushed_to_pos" not in response.json()
    assert fake.calls == 0
    db.refresh(item)
    assert item.qty_base == Decimal("37")


def test_a_count_from_the_dashboard_sends_nothing_to_square(db, square, signed_in):
    fake, item = square

    response = signed_in.post(
        f"/app/items/{item.id}/count", data={"quantity": "40"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert fake.calls == 0
    db.refresh(item)
    assert item.qty_base == Decimal("40")


def test_the_push_functions_refuse_while_write_back_is_off(db, tenant, square):
    """A call before R1.1 is a bug: it fails loudly instead of quietly not writing."""
    fake, item = square
    with pytest.raises(sync.WriteBackDisabled):
        sync.push_counts_to_square(db, tenant.id, [item.id])
    with pytest.raises(sync.WriteBackDisabled):
        sync.push_adjustments_to_square(db, tenant.id, {item.id: Decimal("24")})
    assert fake.calls == 0


# --- R1.1: what the kept push functions send once write-back is switched on -------------------


def test_an_adjustment_cannot_erase_a_sale_that_happened_just_before_it(
    db, tenant, location, square, write_back_on
):
    """Square 24, a sale takes it to 20, a delivery brings 24. Square must end at 44, not 48."""
    fake, item = square
    fake.ledger[("VAR-COKE", location.provider_location_id)] = Decimal("20")

    assert sync.push_adjustments_to_square(db, tenant.id, {item.id: Decimal("24")}) == 1

    assert [change["type"] for change in fake.changes] == ["ADJUSTMENT"]
    assert fake.changes[0]["adjustment"]["quantity"] == "24"
    assert shelf(fake, location) == Decimal("44")


def test_a_physical_count_sends_the_number_someone_read_off_the_shelf(
    db, tenant, location, square, write_back_on
):
    fake, item = square
    fake.ledger[("VAR-COKE", location.provider_location_id)] = Decimal("20")
    item.qty_base = Decimal("37")
    db.flush()

    assert sync.push_counts_to_square(db, tenant.id, [item.id]) == 1
    assert fake.changes[0]["type"] == "PHYSICAL_COUNT"
    assert fake.changes[0]["physical_count"]["quantity"] == "37"
    assert shelf(fake, location) == Decimal("37")


def test_the_same_receipt_pushed_twice_is_only_counted_once(
    db, tenant, location, square, write_back_on
):
    """The idempotency key is the receipt, so a retried push is Square's to drop."""
    fake, item = square
    for _attempt in range(2):
        sync.push_adjustments_to_square(db, tenant.id, {item.id: Decimal("24")}, "receipt-7")
    assert shelf(fake, location) == Decimal("48")


def test_a_derived_weight_is_never_pushed_to_square_at_all(
    db, tenant, location, square, write_back_on
):
    """Flour is calculated from recipes; sending it as a count would be inventing an observation."""
    fake, _item = square
    flour = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="flour",
        name="Pastry flour",
        dimension=Dimension.WEIGHT,
        stock_unit="kg",
        track_mode=TrackMode.DERIVED,
        provider_variation_id="VAR-FLOUR",
        qty_base=Decimal("45000"),
    )
    db.add(flour)
    db.flush()

    assert sync.push_counts_to_square(db, tenant.id, [flour.id]) == 0
    assert sync.push_adjustments_to_square(db, tenant.id, {flour.id: Decimal("25000")}) == 0
    assert fake.changes == []


def test_a_failed_square_read_during_a_scan_is_recorded_and_shown(
    db, tenant, square, signed_in, monkeypatch
):
    """The one remaining Square call outside a sync used to fail into the log only."""
    from app.models import IdentifierType, ProductIdentifier, SyncRun, SyncStatus

    _fake, item = square
    db.add(
        ProductIdentifier(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            identifier_type=IdentifierType.UPC,
            value="049000050103",
            source="manual",
        )
    )
    db.commit()

    def unreachable(*_args, **_kwargs):
        raise ConnectionError("square timed out")

    monkeypatch.setattr(sync, "refresh_item_from_square", unreachable)
    response = signed_in.get("/app/scan/lookup", params={"code": "049000050103"})

    assert response.status_code == 200
    assert response.json()["square_unreachable"] is True
    run = db.query(SyncRun).filter(SyncRun.tenant_id == tenant.id).one()
    assert (run.kind, run.status) == ("item", SyncStatus.FAILED)
    assert "square timed out" in run.error
