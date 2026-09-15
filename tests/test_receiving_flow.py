"""End to end: photo of an invoice raises the right stock in the right units."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.extract.invoice import StubExtractor
from app.models import Document, DocumentStatus, InventoryCount, LineStatus, StockAlias, StockItem, TrackMode
from app.orchestration.receiving import LineDecision, apply_document, parse_document, save_upload
from app.units import Dimension, to_base

INVOICE = {
    "supplier": "Northside Supply",
    "invoice_number": "NS-4471",
    "invoice_date": "2026-09-04",
    "currency": "USD",
    "total_cents": 18640,
    "lines": [
        {"raw_text": "NORTHSIDE FULL CREAM MLK 6 x 1L", "product_name": "Northside Full Cream Milk",
         "quantity": 4, "unit": "case", "pack_size": 1, "unit_price_cents": 570, "line_total_cents": 2280},
        {"raw_text": "PASTRY FLOUR 25KG SACK", "product_name": "Pastry Flour",
         "quantity": 2, "unit": "sack", "pack_size": None, "unit_price_cents": 3375, "line_total_cents": 6750},
        {"raw_text": "TAKEAWAY CUP 12OZ BOX/500", "product_name": "Takeaway cups 12oz",
         "quantity": 3, "unit": "box", "pack_size": None, "unit_price_cents": 5500, "line_total_cents": 16500},
        {"raw_text": "DISHWASHER TABLETS 100PK", "product_name": "Dishwasher tablets",
         "quantity": 1, "unit": "pack", "pack_size": None, "unit_price_cents": 2400, "line_total_cents": 2400},
    ],
}


@pytest.fixture
def stock(db, tenant, location):
    items = [
        StockItem(tenant_id=tenant.id, location_id=location.id, slug="whole-milk", name="Whole milk",
                  dimension=Dimension.VOLUME, stock_unit="l", track_mode=TrackMode.DERIVED,
                  qty_base=to_base(10, "l"), reorder_point_base=to_base(20, "l"),
                  receive_unit="l", pack_size_base=to_base(6, "l")),
        StockItem(tenant_id=tenant.id, location_id=location.id, slug="pastry-flour", name="Pastry flour",
                  dimension=Dimension.WEIGHT, stock_unit="kg", track_mode=TrackMode.DERIVED,
                  qty_base=to_base(5, "kg"), reorder_point_base=to_base(10, "kg"),
                  receive_unit="kg", pack_size_base=to_base(25, "kg")),
        StockItem(tenant_id=tenant.id, location_id=location.id, slug="cups", name="Takeaway cups 12oz",
                  dimension=Dimension.COUNT, stock_unit="each", track_mode=TrackMode.MANUAL,
                  qty_base=Decimal("120"), reorder_point_base=Decimal("300"),
                  receive_unit="each", pack_size_base=Decimal("500")),
    ]
    db.add_all(items)
    db.commit()
    return {item.slug: item for item in items}


def upload(db, tenant, location) -> Document:
    document = save_upload(
        db, tenant.id, location.id, "delivery.jpg", b"not-a-real-photo", "image/jpeg"
    )
    db.commit()
    return document


def test_upload_is_deduplicated_by_content(db, tenant, location):
    first = upload(db, tenant, location)
    second = upload(db, tenant, location)
    assert first.id == second.id
    assert db.query(Document).count() == 1


def test_parse_matches_what_it_recognises_and_flags_the_rest(db, tenant, location, stock):
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor(INVOICE))
    db.commit()

    assert document.supplier == "Northside Supply"
    assert document.invoice_number == "NS-4471"
    assert document.status is DocumentStatus.NEEDS_REVIEW

    by_name = {line.parsed_name: line for line in document.lines}
    # Weight: "2 sacks" becomes 50 kg via the pack size on the item.
    assert by_name["Pastry Flour"].received_qty_base == to_base(50, "kg")
    assert by_name["Pastry Flour"].status is LineStatus.MATCHED
    # Count: "3 boxes" becomes 1500 cups.
    assert by_name["Takeaway cups 12oz"].received_qty_base == Decimal("1500.0000")
    # The supplier calls milk something else, so a human confirms it once.
    assert by_name["Northside Full Cream Milk"].stock_item_id is None
    assert by_name["Dishwasher tablets"].stock_item_id is None
    assert by_name["Dishwasher tablets"].status is LineStatus.UNMATCHED


def test_applying_a_delivery_raises_stock_and_files_the_record(db, tenant, location, stock):
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor(INVOICE))
    db.commit()

    decisions = []
    for line in document.lines:
        if line.stock_item_id:
            decisions.append(LineDecision(line.id, line.stock_item_id, line.received_qty_base))
        elif "Milk" in line.parsed_name:
            # What the reviewer does in the UI: pick the item, confirm the quantity.
            decisions.append(LineDecision(line.id, stock["whole-milk"].id, to_base(24, "l")))
        else:
            decisions.append(LineDecision(line.id, None, None, apply=False))

    result = apply_document(db, document.id, decisions, now=datetime(2026, 9, 4, tzinfo=timezone.utc))
    db.commit()

    assert result["applied"] == 3
    assert stock["whole-milk"].qty_base == to_base(34, "l")
    assert stock["pastry-flour"].qty_base == to_base(55, "kg")
    assert stock["cups"].qty_base == Decimal("1620.0000")
    assert document.status is DocumentStatus.APPLIED

    # Receiving re-anchors derived stock so depletion restarts from a known quantity.
    counts = db.query(InventoryCount).filter(InventoryCount.source == "receipt").all()
    assert len(counts) == 3
    assert stock["whole-milk"].last_counted_at == datetime(2026, 9, 4, tzinfo=timezone.utc)


def test_supplier_wording_is_learned_so_the_next_invoice_is_automatic(db, tenant, location, stock):
    first = upload(db, tenant, location)
    parse_document(db, first.id, StubExtractor(INVOICE))
    db.commit()
    milk_line = next(line for line in first.lines if "Milk" in line.parsed_name)
    apply_document(db, first.id, [LineDecision(milk_line.id, stock["whole-milk"].id, to_base(24, "l"))])
    db.commit()

    alias = db.query(StockAlias).one()
    assert alias.alias == "northside full cream milk"
    assert alias.stock_item_id == stock["whole-milk"].id
    assert alias.supplier == "Northside Supply"

    second = save_upload(db, tenant.id, location.id, "next.jpg", b"another-photo", "image/jpeg")
    db.commit()
    parse_document(db, second.id, StubExtractor(INVOICE))
    db.commit()

    milk_again = next(line for line in second.lines if "Milk" in line.parsed_name)
    assert milk_again.stock_item_id == stock["whole-milk"].id
    assert milk_again.received_qty_base == to_base(24, "l")
    assert milk_again.status is LineStatus.MATCHED


def test_an_applied_delivery_is_never_re_read_over_its_record(db, tenant, location, stock):
    """Its lines are what the stock went up by; Postgres would refuse deleting them anyway."""
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor(INVOICE))
    db.commit()
    flour = next(line for line in document.lines if line.parsed_name == "Pastry Flour")
    apply_document(db, document.id, [LineDecision(flour.id, stock["pastry-flour"].id, to_base(50, "kg"))])
    db.commit()

    with pytest.raises(ValueError, match="already applied"):
        parse_document(db, document.id, StubExtractor({"lines": []}))
    assert len(document.lines) == len(INVOICE["lines"])


def test_unchecked_lines_are_skipped_not_applied(db, tenant, location, stock):
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor(INVOICE))
    db.commit()
    before = stock["whole-milk"].qty_base

    apply_document(
        db, document.id, [LineDecision(line.id, None, None, apply=False) for line in document.lines]
    )
    db.commit()

    assert stock["whole-milk"].qty_base == before
    assert all(line.status is LineStatus.SKIPPED for line in document.lines)


def test_an_unreadable_photo_leaves_the_document_for_a_human(db, tenant, location, stock):
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor({"lines": []}))
    db.commit()
    assert document.status is DocumentStatus.NEEDS_REVIEW
    assert document.lines == []


# --- dated deliveries ---------------------------------------------------------------------

from datetime import date  # noqa: E402

from app.models import StockBatch  # noqa: E402

DATED = {
    "supplier": "Harbour Dairy Co.",
    "invoice_number": "40118",
    "invoice_date": "2026-09-06",
    "currency": "GBP",
    "total_cents": 4440,
    "lines": [
        {"raw_text": "Oat milk 1L (barista) 4 cases of 6 x 1L", "product_name": "Oat milk 1L",
         "quantity": 4, "unit": "case", "pack_size": 1, "unit_price_cents": 1110,
         "line_total_cents": 4440, "expiry_date": "2026-09-20"},
    ],
}


@pytest.fixture
def oat_milk(db, tenant, location):
    item = StockItem(tenant_id=tenant.id, location_id=location.id, slug="oat-milk", name="Oat milk 1L",
                     dimension=Dimension.VOLUME, stock_unit="l", track_mode=TrackMode.DERIVED,
                     qty_base=to_base(6, "l"), receive_unit="l", pack_size_base=to_base(6, "l"))
    db.add(item)
    db.commit()
    return item


def receive(db, document, decisions=None):
    decisions = decisions or [
        LineDecision(line.id, line.stock_item_id, line.received_qty_base) for line in document.lines
    ]
    result = apply_document(db, document.id, decisions, now=datetime(2026, 9, 6, tzinfo=timezone.utc))
    db.commit()
    return result


def test_the_printed_best_before_is_read_off_the_invoice(db, tenant, location, oat_milk):
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor(DATED))
    db.commit()
    assert document.lines[0].expires_on == date(2026, 9, 20)


def test_a_dated_line_becomes_one_batch_for_the_whole_quantity(db, tenant, location, oat_milk):
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor(DATED))
    db.commit()

    result = receive(db, document)

    batch = db.query(StockBatch).one()
    assert result["batches"] == 1
    assert batch.stock_item_id == oat_milk.id
    assert batch.received_qty_base == to_base(24, "l")
    assert batch.expires_on == date(2026, 9, 20)
    assert batch.source == "receipt"
    assert batch.document_line_id == document.lines[0].id
    # 4440 pence for 24,000 ml.
    assert batch.cost_per_base_cents == Decimal("0.1850")
    # Stock still rises exactly as before; the batch is a label on it, not a second ledger.
    assert oat_milk.qty_base == to_base(30, "l")


def test_the_invoice_price_becomes_the_items_cost(db, tenant, location, oat_milk):
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor(DATED))
    db.commit()
    receive(db, document)
    assert oat_milk.cost_per_base_cents == Decimal("0.1850")


def test_the_reviewer_can_correct_the_date(db, tenant, location, oat_milk):
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor(DATED))
    db.commit()
    line = document.lines[0]
    receive(db, document, [LineDecision(line.id, oat_milk.id, line.received_qty_base,
                                        expires_on=date(2026, 9, 18))])
    assert db.query(StockBatch).one().expires_on == date(2026, 9, 18)


def test_an_undated_delivery_creates_no_batch(db, tenant, location, stock):
    document = upload(db, tenant, location)
    parse_document(db, document.id, StubExtractor(INVOICE))
    db.commit()
    result = receive(db, document, [
        LineDecision(line.id, line.stock_item_id, line.received_qty_base)
        for line in document.lines if line.stock_item_id
    ])
    assert result["batches"] == 0
    assert db.query(StockBatch).count() == 0
