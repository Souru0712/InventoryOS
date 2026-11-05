"""What StockPulse sends Square: a delivery adjusts, an observation counts.

Square owns the count for its own SKUs. Anything the app worked out - a previous count plus a
delivery, or a derived weight - must never be sent as a physical count, because a count replaces
whatever Square holds, including the sale it rang up a minute ago.
"""

from decimal import Decimal

import pytest

from app.extract.invoice import StubExtractor
from app.models import ProviderConnection, StockItem, TrackMode
from app.orchestration import sync
from app.orchestration.receiving import LineDecision, apply_document, parse_document, save_upload
from app.units import Dimension

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
    """Square's inventory ledger, with only the two operations StockPulse ever sends it."""

    def __init__(self, ledger: dict):
        self.ledger = ledger
        self.changes: list[dict] = []
        self.keys: list[str] = []

    def __call__(self, _token):  # stands in for SquareClient(token)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def apply_inventory_changes(self, changes: list[dict], idempotency_key: str) -> dict:
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


def shelf(fake: FakeSquare, location) -> Decimal:
    return fake.ledger[("VAR-COKE", location.provider_location_id)]


def receive_by_invoice(db, tenant, location, item, quantity: str) -> dict:
    document = save_upload(
        db,
        tenant.id,
        location.id,
        f"note-{quantity}.jpg",
        f"delivery of {quantity}".encode(),
        "image/jpeg",
    )
    db.commit()
    parse_document(db, document.id, StubExtractor(INVOICE))
    db.commit()
    line = document.lines[0]
    result = apply_document(db, document.id, [LineDecision(line.id, item.id, Decimal(quantity))])
    db.commit()
    return result


def test_a_delivery_cannot_erase_a_sale_that_happened_just_before_it(db, tenant, location, square):
    """Square 24, a sale takes it to 20, an invoice brings 24. Square must end at 44, not 48."""
    fake, item = square
    fake.ledger[("VAR-COKE", location.provider_location_id)] = Decimal("20")

    result = receive_by_invoice(db, tenant, location, item, "24")

    assert result["pushed_to_pos"] == 1
    assert [change["type"] for change in fake.changes] == ["ADJUSTMENT"]
    assert fake.changes[0]["adjustment"]["quantity"] == "24"
    assert shelf(fake, location) == Decimal("44")


def test_a_physical_count_sends_the_number_someone_read_off_the_shelf(db, tenant, location, square):
    fake, item = square
    fake.ledger[("VAR-COKE", location.provider_location_id)] = Decimal("20")
    item.qty_base = Decimal("37")
    db.flush()

    assert sync.push_counts_to_square(db, tenant.id, [item.id]) == 1
    assert fake.changes[0]["type"] == "PHYSICAL_COUNT"
    assert fake.changes[0]["physical_count"]["quantity"] == "37"
    assert shelf(fake, location) == Decimal("37")


def test_receiving_by_scan_and_by_invoice_take_the_same_pathway(db, tenant, location, square):
    from app.orchestration.scanning import receive_scanned

    fake, item = square
    receive_scanned(db, item, Decimal("6"))
    db.commit()
    receive_by_invoice(db, tenant, location, item, "24")

    assert [change["type"] for change in fake.changes] == ["ADJUSTMENT", "ADJUSTMENT"]
    assert [change["adjustment"]["quantity"] for change in fake.changes] == ["6", "24"]
    assert shelf(fake, location) == Decimal("54")


def test_the_same_receipt_pushed_twice_is_only_counted_once(db, tenant, location, square):
    """The idempotency key is the receipt, so a retried push is Square's to drop."""
    fake, item = square
    for _attempt in range(2):
        sync.push_adjustments_to_square(db, tenant.id, {item.id: Decimal("24")}, "receipt-7")
    assert shelf(fake, location) == Decimal("48")


def test_a_derived_weight_is_never_pushed_to_square_at_all(db, tenant, location, square):
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
