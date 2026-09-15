"""Barcode scanning: a code identifies the product, Square still owns the quantity.

The scan never invents a Square number. It resolves the code locally, raises stock the same
way receiving a delivery does, and sends Square a delta.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extract.expiry import ExpiryReader, ExpiryReadError, get_reader
from app.models import (
    IdentifierType,
    InventoryCount,
    InventoryEvent,
    Location,
    ProductIdentifier,
    StockBatch,
    StockItem,
    TrackMode,
)
from app.transform.barcodes import canonical, has_valid_check_digit, is_numeric, lookup_keys
from app.transform.dates import parse_printed_date

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
    db: Session, tenant_id: int, raw: str, symbology: str | None = None
) -> ProductIdentifier | None:
    """Resolve a scanned code locally. Much faster than asking Square on every scan."""
    keys = lookup_keys(raw, symbology)
    if not keys:
        return None
    rows = {
        row.value: row
        for row in db.scalars(
            select(ProductIdentifier).where(
                ProductIdentifier.tenant_id == tenant_id, ProductIdentifier.value.in_(keys)
            )
        )
    }
    # keys are ordered most-canonical first, so the first hit is the best one.
    for key in keys:
        if key in rows:
            return rows[key]
    return None


def link_code(
    db: Session,
    tenant_id: int,
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
        select(ProductIdentifier).where(
            ProductIdentifier.tenant_id == tenant_id, ProductIdentifier.value == value
        )
    ).first()
    if existing is not None:
        if existing.stock_item_id != stock_item_id:
            raise CodeInUse(f"{value} already identifies another item")
        return existing

    identifier = ProductIdentifier(
        tenant_id=tenant_id,
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
        return (
            self.value is None
            or self.confidence < CONFIDENT_ENOUGH
            or not self.is_expiry
        )


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
            return DateReading(None, "unknown", ZERO, printed, "no date found in the photo", read.legible)
        # The reader saw a date in a shape the parser does not know; trust it less for that.
        return DateReading(
            theirs, "unknown", (Decimal(str(read.confidence)) * Decimal("0.7")).quantize(Decimal("0.01")),
            printed, "read from the photo but not in a familiar format", read.legible,
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


def record_event(
    db: Session,
    item: StockItem,
    action: str,
    source: str,
    qty_after_base: Decimal,
    delta_base: Decimal | None = None,
    user_id: int | None = None,
    detail: str | None = None,
    pushed_to_pos: bool = False,
) -> InventoryEvent:
    event = InventoryEvent(
        tenant_id=item.tenant_id,
        stock_item_id=item.id,
        user_id=user_id,
        action=action,
        source=source,
        delta_base=delta_base,
        qty_after_base=qty_after_base,
        detail=(detail or None) and detail[:500],
        pushed_to_pos=pushed_to_pos,
    )
    db.add(event)
    return event


def receive_scanned(
    db: Session,
    item: StockItem,
    qty_base: Decimal,
    user_id: int | None = None,
    expires_on: date | None = None,
    date_type: str = "unknown",
    date_confidence: Decimal | None = None,
    now: datetime | None = None,
) -> dict:
    """Raise stock for a scanned receipt, date the lot, and push the delta to Square."""
    if qty_base is None or qty_base <= ZERO:
        raise ValueError("received quantity must be above zero")
    now = now or datetime.now(timezone.utc)

    item.qty_base = Decimal(item.qty_base or 0) + Decimal(qty_base)
    db.add(
        InventoryCount(
            tenant_id=item.tenant_id,
            stock_item_id=item.id,
            qty_base=item.qty_base,
            source="scan",
            note="received by barcode scan",
            counted_at=now,
        )
    )
    # A receipt re-anchors derived stock, exactly as an invoice does.
    item.last_counted_at = now

    batch = None
    if expires_on is not None:
        batch = StockBatch(
            tenant_id=item.tenant_id,
            stock_item_id=item.id,
            received_qty_base=Decimal(qty_base),
            expires_on=expires_on,
            date_type=date_type,
            date_confidence=date_confidence,
            cost_per_base_cents=item.cost_per_base_cents or None,
            source="scan",
            received_at=now,
        )
        db.add(batch)

    pushed = _push(db, item, Decimal(qty_base))
    record_event(
        db,
        item,
        action="received",
        source="barcode_scan",
        qty_after_base=item.qty_base,
        delta_base=Decimal(qty_base),
        user_id=user_id,
        detail=f"best before {expires_on:%Y-%m-%d}" if expires_on else None,
        pushed_to_pos=pushed,
    )
    db.flush()
    return {"qty_after_base": item.qty_base, "pushed_to_pos": pushed, "dated": batch is not None}


def _push(db: Session, item: StockItem, delta_base: Decimal) -> bool:
    """Square owns the count for its own SKUs; anything else is ours alone."""
    if item.track_mode is not TrackMode.PROVIDER:
        return False
    from app.orchestration.sync import NotConnected, adjust_stock_in_square

    try:
        return adjust_stock_in_square(db, item.tenant_id, item, delta_base)
    except NotConnected:
        log.info("tenant %s is not connected; scan kept locally", item.tenant_id)
    except Exception:
        log.exception("failed to adjust square stock for item %s", item.id)
    return False


def location_name(db: Session, item: StockItem) -> str:
    location = db.get(Location, item.location_id)
    return location.name if location else ""
