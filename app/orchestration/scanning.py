"""Barcode scanning: a code identifies the product; the ledger owns the quantity.

A scan resolves the code locally and raises stock the same way receiving a delivery does: a
receipt posted to the ledger (T1.18). Nothing is sent to Square (C-34).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.extract.expiry import ExpiryReader, ExpiryReadError, get_reader
from app.ledger.posting import post_transaction
from app.models import (
    IdentifierType,
    Location,
    ProductIdentifier,
    StockBatch,
    StockItem,
)
from app.transform.barcodes import canonical, has_valid_check_digit, is_numeric, lookup_keys
from app.transform.dates import parse_printed_date
from app.transform.ledger import Line, places

log = logging.getLogger(__name__)

ZERO = Decimal("0")


class CodeInUse(ValueError):
    """The code already points at a different item; one code identifies one product."""


@dataclass(frozen=True)
class ScanHit:
    item: StockItem
    value: str
    identifier_type: str
    matched_existing: bool


def find_by_code(
    db: Session, org: OrgContext, raw: str, symbology: str | None = None
) -> ProductIdentifier | None:
    """Resolve a scanned code locally. Much faster than asking Square on every scan."""
    keys = lookup_keys(raw, symbology)
    if not keys:
        return None
    rows = {
        row.value: row
        for row in db.scalars(
            select_scoped(org, ProductIdentifier).where(ProductIdentifier.value.in_(keys))
        )
    }
    # keys are ordered most-canonical first, so the first hit is the best one.
    for key in keys:
        if key in rows:
            return rows[key]
    return None


def link_code(
    db: Session,
    org: OrgContext,
    stock_item_id: int,
    raw: str,
    symbology: str | None = None,
    source: str = "scan",
) -> ProductIdentifier:
    """Attach a code to an item so the next scan resolves without a human."""
    value, kind = canonical(raw, symbology)
    if not value:
        raise ValueError("nothing to link: the code is empty")

    existing = db.scalars(
        select_scoped(org, ProductIdentifier).where(ProductIdentifier.value == value)
    ).first()
    if existing is not None:
        if existing.stock_item_id != stock_item_id:
            raise CodeInUse(f"{value} already identifies another item")
        return existing

    identifier = ProductIdentifier(
        tenant_id=org.tenant_id,
        stock_item_id=stock_item_id,
        identifier_type=IdentifierType(kind),
        value=value,
        source=source,
    )
    db.add(identifier)
    db.flush()
    return identifier


def suspicious(raw: str, symbology: str | None = None) -> bool:
    """A numeric retail barcode whose check digit disagrees with the rest was misread."""
    value, _ = canonical(raw, symbology)
    if not value or not is_numeric(value) or len(value) not in (8, 12, 13, 14):
        return False
    return not has_valid_check_digit(value)


# Above this, the reading is offered as already correct; below it, a person is asked.
CONFIDENT_ENOUGH = Decimal("0.95")


@dataclass(frozen=True)
class DateReading:
    """What a photo of a printed date came to, and whether it can be trusted unasked."""

    value: date | None
    date_type: str
    confidence: Decimal
    raw_text: str
    note: str | None
    legible: bool

    @property
    def is_expiry(self) -> bool:
        return self.date_type != "packed"

    @property
    def needs_confirmation(self) -> bool:
        """A packing date always gets asked about: it is not a date to sell against."""
        return self.value is None or self.confidence < CONFIDENT_ENOUGH or not self.is_expiry


def read_expiry_photo(
    image: bytes,
    media_type: str,
    prefer: str = "mdy",
    today: date | None = None,
    reader: ExpiryReader | None = None,
) -> DateReading:
    """Read a date off a photo, then check the reader's own reading against the parser's."""
    reader = reader or get_reader()
    try:
        read = reader.read(image, media_type)
    except ExpiryReadError as exc:
        log.info("could not read a date photo: %s", exc)
        return DateReading(None, "unknown", ZERO, "", str(exc), False)

    printed = " ".join(part for part in (read.label, read.text) if part).strip()
    if not printed:
        return DateReading(None, "unknown", ZERO, "", "nothing legible in the photo", read.legible)

    parsed = parse_printed_date(printed, today, prefer, read.confidence)
    theirs = _iso(read.date_iso)

    if parsed is None:
        if theirs is None:
            return DateReading(
                None, "unknown", ZERO, printed, "no date found in the photo", read.legible
            )
        # The reader saw a date in a shape the parser does not know; trust it less for that.
        return DateReading(
            theirs,
            "unknown",
            (Decimal(str(read.confidence)) * Decimal("0.7")).quantize(Decimal("0.01")),
            printed,
            "read from the photo but not in a familiar format",
            read.legible,
        )

    confidence = parsed.confidence
    note = parsed.note
    if theirs is not None and theirs != parsed.value:
        # Two readings of the same print, disagreeing. Keep the deterministic one and ask.
        confidence = (confidence * Decimal("0.6")).quantize(Decimal("0.01"))
        note = "the photo could be read two ways"
    elif theirs == parsed.value and not read.legible:
        note = note or "the print is faint"

    return DateReading(parsed.value, parsed.date_type, confidence, printed, note, read.legible)


def _iso(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def receive_scanned(
    db: Session,
    org: OrgContext,
    item: StockItem,
    qty_base: Decimal,
    expires_on: date | None = None,
    date_type: str = "unknown",
    date_confidence: Decimal | None = None,
    now: datetime | None = None,
) -> dict:
    """Receive scanned stock: one receipt posted to the ledger, and the lot dated if known."""
    if qty_base is None or places(qty_base) <= ZERO:
        raise ValueError("received quantity must be above zero")
    if item.inventory_item_id is None:
        raise ValueError("that stock item is not linked to an item")
    now = now or datetime.now(UTC)
    received = places(qty_base)
    # A scan has no document; each one is its own receipt.
    scan = uuid4().hex
    post_transaction(
        db,
        org,
        kind="receipt",
        source_type="scan",
        source_id=scan,
        idempotency_key=f"receipt:scan:{scan}",
        lines=[Line(item.location_id, item.inventory_item_id, "receipt", received)],
        occurred_at=now,
        reason="Received by barcode scan",
    )

    batch = None
    if expires_on is not None:
        batch = StockBatch(
            tenant_id=item.tenant_id,
            stock_item_id=item.id,
            received_qty_base=received,
            expires_on=expires_on,
            date_type=date_type,
            date_confidence=date_confidence,
            cost_per_base_cents=item.cost_per_base_cents or None,
            source="scan",
            received_at=now,
        )
        db.add(batch)

    db.flush()
    return {"qty_after_base": item.qty_base, "dated": batch is not None}


def location_name(db: Session, org: OrgContext, item: StockItem) -> str:
    location = get_scoped(db, org, Location, item.location_id)
    return location.name if location else ""
