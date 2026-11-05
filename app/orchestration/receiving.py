"""Invoice photo pipeline: store -> extract -> match -> review -> apply."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.extract.images import detect_media_type
from app.extract.invoice import (
    STORED_SUFFIX,
    SUPPORTED_MEDIA,
    ExtractionError,
    Extractor,
    get_extractor,
)
from app.models import (
    Document,
    DocumentLine,
    DocumentStatus,
    ExtractionCorrection,
    InventoryCount,
    LineStatus,
    ProductIdentifier,
    StockAlias,
    StockBatch,
    StockItem,
    TrackMode,
)
from app.storage import StorageError, get_storage
from app.transform.receiving import (
    Candidate,
    MatchResult,
    ParsedLine,
    build_alias_index,
    line_cost_per_base,
    match_document,
    normalize_name,
)

log = logging.getLogger(__name__)

ZERO = Decimal("0")


class UploadRejected(ValueError):
    pass


@dataclass
class LineDecision:
    line_id: int
    stock_item_id: int | None
    qty_base: Decimal | None
    apply: bool = True
    # One date for the whole line: 48 bottles from one delivery share a best-before.
    expires_on: date | None = None
    # What the line actually cost, when the reviewer corrects the reader.
    line_total_cents: int | None = None


# What a reviewer can change, and what each correction is called in the statistics.
CORRECTION_FIELDS = ("product_match", "quantity", "cost", "expiry", "supplier", "invoice_number")


def record_correction(
    db: Session,
    document: Document,
    field: str,
    ai_value: object,
    corrected_value: object,
    line_id: int | None = None,
    user_id: int | None = None,
) -> ExtractionCorrection | None:
    """Keep one change the reviewer made, if it really is a change."""
    before, after = _as_text(ai_value), _as_text(corrected_value)
    if before == after:
        return None
    correction = ExtractionCorrection(
        tenant_id=document.tenant_id,
        document_id=document.id,
        document_line_id=line_id,
        field=field,
        ai_value=before,
        corrected_value=after,
        corrected_by=user_id,
    )
    db.add(correction)
    db.flush()
    return correction


def _as_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        # 24 and 24.0000 are the same quantity, and not a correction; and no exponent notation.
        return format(value.normalize(), "f")
    return str(value)[:300]


def save_upload(
    db: Session,
    tenant_id: int,
    location_id: int,
    filename: str,
    content: bytes,
    content_type: str,
    uploaded_by: int | None = None,
    kind: str = "invoice",
) -> Document:
    """Store the photo as the receiving record, deduplicated by content hash.

    `content_type` is what the browser claimed. It is only logged: what is stored, and later
    served, is decided by the bytes themselves (S3, S12).
    """
    settings = get_settings()
    limit = settings.max_upload_mb * 1024 * 1024
    if not content:
        raise UploadRejected("the uploaded file is empty")
    if len(content) > limit:
        raise UploadRejected(f"file is larger than the {settings.max_upload_mb} MB limit")

    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_MEDIA:
        raise UploadRejected(
            f"{suffix or 'that file type'} cannot be read; use {', '.join(sorted(SUPPORTED_MEDIA))}"
        )
    media_type = detect_media_type(content)
    if media_type is None:
        raise UploadRejected(
            f"that file is not a readable photo or PDF; use {', '.join(sorted(SUPPORTED_MEDIA))}"
        )
    if media_type != content_type:
        log.info(
            "tenant %s upload %r was sent as %s but is %s",
            tenant_id,
            filename,
            content_type,
            media_type,
        )

    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalars(
        select(Document).where(Document.tenant_id == tenant_id, Document.sha256 == digest)
    ).first()
    if existing:
        log.info("tenant %s re-uploaded document %s", tenant_id, existing.id)
        return existing

    key = f"{tenant_id}/{digest}{STORED_SUFFIX[media_type]}"
    try:
        get_storage().put(key, content, media_type)
    except StorageError:
        log.exception("could not store an upload for tenant %s", tenant_id)
        raise UploadRejected("the photo could not be saved; please try again") from None

    document = Document(
        tenant_id=tenant_id,
        location_id=location_id,
        uploaded_by=uploaded_by,
        kind=kind,
        storage_key=key,
        content_type=media_type,
        sha256=digest,
        byte_size=len(content),
    )
    db.add(document)
    db.flush()
    return document


def candidates_for(db: Session, tenant_id: int, location_id: int) -> list[Candidate]:
    aliases: dict[int, list[str]] = {}
    for alias in db.scalars(select(StockAlias).where(StockAlias.tenant_id == tenant_id)):
        aliases.setdefault(alias.stock_item_id, []).append(alias.alias)

    codes: dict[int, list[tuple[str, str]]] = {}
    for row in db.scalars(
        select(ProductIdentifier).where(ProductIdentifier.tenant_id == tenant_id)
    ):
        codes.setdefault(row.stock_item_id, []).append(
            (row.identifier_type.value.upper(), row.value)
        )

    return [
        Candidate(
            stock_item_id=item.id,
            name=item.name,
            dimension=item.dimension,
            stock_unit=item.stock_unit,
            receive_unit=item.receive_unit or "each",
            # A scanned barcode is stronger evidence than a merchant's own SKU, so try it first.
            codes=tuple(sorted(codes.get(item.id, ()), key=lambda code: code[0] == "SKU")),
            category=item.category,
            pack_size_base=Decimal(item.pack_size_base)
            if item.pack_size_base is not None
            else None,
            aliases=tuple(aliases.get(item.id, ())),
        )
        for item in db.scalars(
            select(StockItem).where(
                StockItem.tenant_id == tenant_id,
                StockItem.location_id == location_id,
                StockItem.is_active.is_(True),
            )
        )
    ]


def alias_index(db: Session, tenant_id: int) -> dict[str, int]:
    return build_alias_index(
        [
            (alias.alias, alias.stock_item_id)
            for alias in db.scalars(select(StockAlias).where(StockAlias.tenant_id == tenant_id))
        ]
    )


def parse_document(db: Session, document_id: int, extractor: Extractor | None = None) -> Document:
    """Read the photo and match every line against this location's stock."""
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError(f"document {document_id} not found")
    if document.status is DocumentStatus.APPLIED:
        # Its lines are the receiving record the stock batches point at; re-reading would erase it.
        raise ValueError(f"document {document_id} is already applied and cannot be re-read")

    extractor = extractor or get_extractor()
    try:
        content = get_storage().get(document.storage_key)
    except StorageError as exc:
        document.status = DocumentStatus.FAILED
        document.error = "The photo could not be read back from storage."
        db.flush()
        log.error("document %s photo unreadable at %s: %s", document_id, document.storage_key, exc)
        raise ExtractionError(document.error) from exc
    try:
        extract = extractor.extract(content, document.storage_key)
    except ExtractionError as exc:
        document.status = DocumentStatus.FAILED
        document.error = str(exc)
        db.flush()
        log.error("extraction failed for document %s: %s", document_id, exc)
        raise

    document.supplier = extract.supplier
    document.invoice_number = extract.invoice_number
    document.invoice_date = extract.invoice_date
    document.total_cents = extract.total_cents
    document.currency = extract.currency or document.currency
    document.extractor = extract.extractor
    document.raw_extract_json = _dump(extract.raw)

    for stale in list(document.lines):
        db.delete(stale)
    db.flush()

    results = match_document(
        extract.lines,
        candidates_for(db, document.tenant_id, document.location_id),
        alias_index(db, document.tenant_id),
    )
    for result in results:
        document.lines.append(_to_row(document, result))

    document.status = DocumentStatus.PARSED if results else DocumentStatus.NEEDS_REVIEW
    if any(not result.is_auto for result in results) or not results:
        document.status = DocumentStatus.NEEDS_REVIEW
    db.flush()
    return document


def _to_row(document: Document, result: MatchResult) -> DocumentLine:
    line: ParsedLine = result.line
    return DocumentLine(
        tenant_id=document.tenant_id,
        line_no=line.line_no,
        raw_text=line.raw_text[:500],
        parsed_name=line.name[:300],
        parsed_qty=line.qty,
        parsed_unit=line.unit,
        parsed_pack_size=line.pack_size,
        unit_price_cents=line.unit_price_cents,
        line_total_cents=line.line_total_cents,
        expires_on=line.expires_on,
        stock_item_id=result.stock_item_id,
        match_confidence=result.confidence,
        match_reason=(result.problem or result.reason)[:200],
        received_qty_base=result.received_qty_base,
        status=LineStatus.MATCHED if result.is_auto else LineStatus.UNMATCHED,
    )


def apply_document(
    db: Session,
    document_id: int,
    decisions: list[LineDecision],
    now: datetime | None = None,
    user_id: int | None = None,
) -> dict:
    """Receive the delivery: raise stock, date it, cost it, anchor derived items, learn the wording."""
    now = now or datetime.now(UTC)
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError(f"document {document_id} not found")
    if document.status is DocumentStatus.APPLIED:
        # Applying twice would raise stock twice and tell Square the delivery arrived twice.
        raise ValueError(f"document {document_id} has already been applied")

    lines = {line.id: line for line in document.lines}
    # What the reader produced, captured before the reviewer's answers overwrite any of it.
    proposed = {
        line.id: (
            line.stock_item_id,
            line.received_qty_base,
            line.expires_on,
            line.line_total_cents,
        )
        for line in document.lines
    }
    items = {
        item.id: item
        for item in db.scalars(select(StockItem).where(StockItem.tenant_id == document.tenant_id))
    }

    applied = 0
    batches = 0
    received: dict[int, Decimal] = {}
    for decision in decisions:
        line = lines.get(decision.line_id)
        if line is None:
            raise ValueError(f"line {decision.line_id} does not belong to document {document_id}")
        if not decision.apply:
            line.status = LineStatus.SKIPPED
            continue

        item = items.get(decision.stock_item_id or line.stock_item_id or -1)
        quantity = decision.qty_base if decision.qty_base is not None else line.received_qty_base
        if item is None or quantity is None or quantity <= ZERO:
            line.status = LineStatus.UNMATCHED
            continue

        was_item, was_qty, was_expiry, was_total = proposed[line.id]
        record_correction(db, document, "product_match", was_item, item.id, line.id, user_id)
        record_correction(db, document, "quantity", was_qty, quantity, line.id, user_id)
        if decision.line_total_cents is not None:
            record_correction(
                db, document, "cost", was_total, decision.line_total_cents, line.id, user_id
            )
            line.line_total_cents = decision.line_total_cents

        item.qty_base = Decimal(item.qty_base or 0) + Decimal(quantity)
        db.add(
            InventoryCount(
                tenant_id=document.tenant_id,
                stock_item_id=item.id,
                qty_base=item.qty_base,
                source="receipt",
                note=f"received from {document.supplier or 'supplier'} "
                f"invoice {document.invoice_number or document.id}",
                counted_at=now,
            )
        )
        # A receipt re-anchors derived stock, so depletion restarts from a known quantity.
        item.last_counted_at = now

        cost = line_cost_per_base(
            line.line_total_cents,
            None if decision.line_total_cents is not None else line.unit_price_cents,
            line.parsed_qty,
            Decimal(quantity),
        )
        if cost is not None:
            # The latest price paid is what replacing this stock costs, which is what waste costs.
            item.cost_per_base_cents = cost

        expires_on = decision.expires_on if decision.expires_on is not None else line.expires_on
        record_correction(db, document, "expiry", was_expiry, expires_on, line.id, user_id)
        if expires_on is not None:
            db.add(
                StockBatch(
                    tenant_id=document.tenant_id,
                    stock_item_id=item.id,
                    received_qty_base=Decimal(quantity),
                    expires_on=expires_on,
                    date_type="best_before",
                    cost_per_base_cents=cost,
                    source="receipt",
                    document_line_id=line.id,
                    received_at=now,
                )
            )
            batches += 1

        line.stock_item_id = item.id
        line.received_qty_base = Decimal(quantity)
        line.expires_on = expires_on
        line.status = LineStatus.APPLIED
        _learn_alias(db, document, line, item.id)
        received[item.id] = received.get(item.id, ZERO) + Decimal(quantity)
        applied += 1

    document.status = DocumentStatus.APPLIED
    document.applied_at = now
    db.flush()

    pushed = 0
    # What the delivery added, never what we think the shelf now holds: a sale between the
    # invoice and this moment must survive. Same pathway as receiving by barcode scan.
    provider_deltas = {
        item_id: delta
        for item_id, delta in received.items()
        if items[item_id].track_mode == TrackMode.PROVIDER
    }
    if provider_deltas:
        from app.orchestration.sync import NotConnected, push_adjustments_to_square

        try:
            pushed = push_adjustments_to_square(
                db, document.tenant_id, provider_deltas, f"receipt-{document.id}"
            )
        except NotConnected:
            log.warning("tenant %s is not connected; delivery not pushed", document.tenant_id)
        except Exception:
            log.exception("failed to adjust square stock for tenant %s", document.tenant_id)

    return {
        "applied": applied,
        "batches": batches,
        "pushed_to_pos": pushed,
        "document_id": document.id,
    }


def _learn_alias(db: Session, document: Document, line: DocumentLine, stock_item_id: int) -> None:
    """Remember supplier wording so the next invoice matches without a human."""
    alias = normalize_name(line.parsed_name)
    if not alias:
        return
    existing = db.scalars(
        select(StockAlias).where(
            StockAlias.tenant_id == document.tenant_id, StockAlias.alias == alias
        )
    ).first()
    if existing:
        existing.times_seen += 1
        existing.stock_item_id = stock_item_id
        return
    db.add(
        StockAlias(
            tenant_id=document.tenant_id,
            stock_item_id=stock_item_id,
            alias=alias,
            supplier=document.supplier,
        )
    )


def _dump(payload: dict) -> str:
    import json

    return json.dumps(payload, default=str)[:100000]
