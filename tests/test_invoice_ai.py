"""The invoice reader judged the way a store owner judges it: how much is left to fix."""

import base64
import contextlib
import io
import json
from datetime import date
from decimal import Decimal

import pytest
from PIL import Image

from app.config import get_settings
from app.extract.images import MAX_EDGE, prepare_for_model
from app.extract.invoice import (
    ClaudeInvoiceExtractor,
    ExtractionError,
    StubExtractor,
    validate_payload,
)
from app.models import ExtractionCorrection, StockItem, TrackMode
from app.orchestration.extraction_quality import correction_stats
from app.orchestration.receiving import (
    LineDecision,
    UploadRejected,
    apply_document,
    parse_document,
    save_upload,
)
from app.units import Dimension, to_base
from tests.sample_files import jpeg

INVOICE = {
    "supplier": "Northside Supply",
    "invoice_number": "NS-4471",
    "invoice_date": "2026-09-04",
    "currency": "USD",
    "total_cents": 6750,
    "lines": [
        {
            "raw_text": "PASTRY FLOUR 25KG SACK",
            "product_name": "Pastry Flour",
            "quantity": 2,
            "unit": "sack",
            "pack_size": None,
            "unit_price_cents": 3375,
            "line_total_cents": 6750,
            "expiry_date": None,
        },
    ],
}


def photo(width: int, height: int, colour=(210, 180, 140)) -> bytes:
    """A JPEG the size a phone camera produces, with enough detail to resist compression."""
    image = Image.new("RGB", (width, height), colour)
    pixels = image.load()
    for x in range(0, width, 3):
        for y in range(0, height, 3):
            pixels[x, y] = ((x * 7) % 256, (y * 13) % 256, (x + y) % 256)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


# --- what actually goes over the wire ----------------------------------------------------------


def test_a_phone_photo_is_shrunk_to_what_the_model_reads():
    original = photo(4000, 3000)
    sent, media_type = prepare_for_model(original, "image/jpeg")

    assert media_type == "image/jpeg"
    assert len(sent) < len(original) / 2, "a 4000px photo must not go over the wire as it came"
    with Image.open(io.BytesIO(sent)) as shrunk:
        assert max(shrunk.size) == MAX_EDGE


def test_a_sideways_photo_is_turned_the_right_way_up():
    """Phones record rotation in EXIF; the model reads pixels, so the rotation has to be applied."""
    image = Image.new("RGB", (400, 800), (200, 200, 200))
    exif = image.getexif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)

    sent, _media = prepare_for_model(buffer.getvalue(), "image/jpeg")
    with Image.open(io.BytesIO(sent)) as upright:
        assert upright.size == (800, 400)


def test_a_small_scan_is_not_blown_up_to_fill_the_limit():
    original = photo(600, 800)
    sent, _media = prepare_for_model(original, "image/jpeg")

    with Image.open(io.BytesIO(sent)) as kept:
        assert kept.size == (600, 800)
    assert len(sent) <= len(original)


def test_a_pdf_is_never_touched():
    pdf = b"%PDF-1.4 not really a pdf"
    assert prepare_for_model(pdf, "application/pdf") == (pdf, "application/pdf")


def test_something_that_is_not_an_image_passes_through_rather_than_failing():
    junk = b"this is not an image at all"
    assert prepare_for_model(junk, "image/jpeg") == (junk, "image/jpeg")


# --- what comes back -----------------------------------------------------------------------


def test_a_well_formed_extraction_passes_validation():
    validate_payload(INVOICE)


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({**INVOICE, "lines": [{**INVOICE["lines"][0], "quantity": "two"}]}, "quantity"),
        ({key: value for key, value in INVOICE.items() if key != "total_cents"}, "total_cents"),
        ({**INVOICE, "lines": [{**INVOICE["lines"][0], "vat_rate": 20}]}, "vat_rate"),
        ({**INVOICE, "lines": "none"}, "lines"),
    ],
)
def test_a_malformed_extraction_is_refused_rather_than_applied(payload, expected):
    with pytest.raises(ExtractionError, match=expected):
        validate_payload(payload)


class FakeAnthropic:
    """Captures how the client was built and what was sent, so a test can check both."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.built_with: dict = {}
        self.sent: dict = {}
        self.messages = self

    def stream(self, **kwargs):
        self.sent = kwargs
        content = [type("Block", (), {"type": "text", "text": json.dumps(self.payload)})()]
        response = type("Response", (), {"content": content, "stop_reason": "end_turn"})()
        stream = type("Stream", (), {"get_final_message": lambda _self: response})()
        return contextlib.nullcontext(stream)


@pytest.fixture
def anthropic_client(monkeypatch):
    def build(payload: dict) -> FakeAnthropic:
        client = FakeAnthropic(payload)

        def construct(**kwargs):
            client.built_with = kwargs
            return client

        monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key", raising=False)
        monkeypatch.setattr("anthropic.Anthropic", construct)
        return client

    return build


def test_the_model_is_told_the_owners_receiving_rules(anthropic_client):
    """C-45: a line marked as returned by hand is left out. C-46: shipped beats ordered."""
    client = anthropic_client(INVOICE)
    ClaudeInvoiceExtractor().extract(photo(400, 300), "1/abc.jpg")
    system = client.sent["system"]
    assert "marked by hand as a return" in system
    assert "use the shipped one" in system


def test_the_client_gives_up_after_a_minute_of_silence(anthropic_client):
    """The SDK default of 600 s held a worker for ten minutes on a stalled call (C8)."""
    client = anthropic_client(INVOICE)
    ClaudeInvoiceExtractor().extract(photo(400, 300), "1/abc.jpg")
    assert client.built_with["timeout"] <= 60


def test_the_resized_image_is_what_is_sent_and_the_reply_is_checked(anthropic_client):
    client = anthropic_client(INVOICE)
    original = photo(4000, 3000)

    extract = ClaudeInvoiceExtractor().extract(original, "1/abc.jpg")

    block = client.sent["messages"][0]["content"][0]
    sent_bytes = base64.standard_b64decode(block["source"]["data"])
    assert len(sent_bytes) < len(original), "the full-size photo must not be uploaded"
    assert extract.supplier == "Northside Supply"
    assert extract.lines[0].qty == Decimal("2")


def test_a_reply_that_does_not_match_the_schema_fails_the_document(anthropic_client):
    anthropic_client({"supplier": "Northside", "lines": [{"product_name": "Flour"}]})
    with pytest.raises(ExtractionError):
        ClaudeInvoiceExtractor().extract(photo(400, 300), "1/abc.jpg")


# --- the door ---------------------------------------------------------------------------------


def test_a_file_type_we_cannot_read_is_refused_at_upload(db, tenant, location):
    with pytest.raises(UploadRejected, match="cannot be read"):
        save_upload(
            db, tenant.id, location.id, "invoice.docx", b"not a photo", "application/msword"
        )


def test_a_file_over_the_size_limit_is_refused(db, tenant, location, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1, raising=False)
    with pytest.raises(UploadRejected, match="larger than"):
        save_upload(db, tenant.id, location.id, "invoice.jpg", b"x" * 1_200_000, "image/jpeg")


# --- what the reviewer changed -----------------------------------------------------------------


@pytest.fixture
def flour(db, tenant, location) -> StockItem:
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="pastry-flour",
        name="Pastry flour",
        dimension=Dimension.WEIGHT,
        stock_unit="kg",
        track_mode=TrackMode.DERIVED,
        qty_base=to_base(5, "kg"),
        receive_unit="kg",
        pack_size_base=to_base(25, "kg"),
    )
    db.add(item)
    db.commit()
    return item


@pytest.fixture
def parsed(db, tenant, location, flour):
    document = save_upload(
        db, tenant.id, location.id, "note.jpg", jpeg("a delivery note"), "image/jpeg"
    )
    db.commit()
    parse_document(db, document.id, StubExtractor(INVOICE))
    db.commit()
    return document


def test_accepting_what_the_reader_proposed_records_no_corrections(db, tenant, parsed, flour):
    line = parsed.lines[0]
    apply_document(db, parsed.id, [LineDecision(line.id, flour.id, line.received_qty_base)])
    db.commit()

    assert db.query(ExtractionCorrection).count() == 0


def test_every_change_the_reviewer_makes_is_recorded_with_both_values(db, tenant, parsed, flour):
    line = parsed.lines[0]
    other = StockItem(
        tenant_id=tenant.id,
        location_id=parsed.location_id,
        slug="bread-flour",
        name="Bread flour",
        dimension=Dimension.WEIGHT,
        stock_unit="kg",
        track_mode=TrackMode.DERIVED,
        qty_base=Decimal("0"),
        receive_unit="kg",
    )
    db.add(other)
    db.commit()

    apply_document(
        db,
        parsed.id,
        [
            LineDecision(
                line.id,
                other.id,
                to_base(45, "kg"),
                expires_on=date(2026, 12, 1),
                line_total_cents=7000,
            )
        ],
        user_id=1,
    )
    db.commit()

    corrections = {row.field: row for row in db.query(ExtractionCorrection)}
    assert set(corrections) == {"product_match", "quantity", "expiry", "cost"}
    assert corrections["product_match"].ai_value == str(flour.id)
    assert corrections["product_match"].corrected_value == str(other.id)
    # Quantities are recorded in base units: 50 kg is 50000 g.
    assert corrections["quantity"].ai_value == "50000"
    assert corrections["quantity"].corrected_value == "45000"
    assert corrections["cost"].corrected_value == "7000"
    assert corrections["expiry"].ai_value is None
    assert corrections["expiry"].corrected_value == "2026-12-01"
    assert corrections["quantity"].document_line_id == line.id


def test_a_corrected_line_total_is_what_the_stock_is_costed_at(db, tenant, parsed, flour):
    line = parsed.lines[0]
    apply_document(
        db, parsed.id, [LineDecision(line.id, flour.id, to_base(50, "kg"), line_total_cents=10000)]
    )
    db.commit()

    # 100.00 for 50 kg is 2.00 per kg, in cents per gram.
    assert flour.cost_per_base_cents == Decimal("0.2")


def test_the_statistics_say_how_much_was_left_to_fix(db, tenant, parsed, flour):
    line = parsed.lines[0]
    apply_document(db, parsed.id, [LineDecision(line.id, flour.id, to_base(45, "kg"))])
    db.commit()

    stats = correction_stats(db, tenant.id)
    assert stats["documents"] == 1 and stats["lines"] == 1
    assert stats["by_field"]["quantity"]["count"] == 1
    assert stats["clean_rate"] == 0.0, "the only line was corrected"
    assert stats["suppliers"][0]["supplier"] == "Northside Supply"


def test_statistics_are_empty_before_anything_is_received(db, tenant):
    assert correction_stats(db, tenant.id)["documents"] == 0
