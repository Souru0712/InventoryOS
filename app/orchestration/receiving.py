"""Invoice photo pipeline: store -> extract -> match -> review -> apply."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped, update_scoped
from app.catalog import active_stock
from app.config import get_settings
from app.costs import set_cost
from app.extract.images import detect_media_type
from app.extract.invoice import (
    STORED_SUFFIX,
    SUPPORTED_MEDIA,
    ExtractionError,
    Extractor,
    get_extractor,
)
from app.ledger.posting import post_transaction
from app.models import (
    Document,
    DocumentLine,
    DocumentStatus,
    ExtractionCorrection,
    LineStatus,
    ProductIdentifier,
    StockAlias,
    StockBatch,
    StockItem,
)
from app.storage import StorageError, get_storage
from app.transform.ledger import Line, places
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


class AlreadyApplied(ValueError):
    """The delivery was received already, perhaps a moment ago by another request (AC-RCV-05)."""


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
    org: OrgContext,
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
            org.tenant_id,
            filename,
            content_type,
            media_type,
        )

    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalars(select_scoped(org, Document).where(Document.sha256 == digest)).first()
    if existing:
        log.info("tenant %s re-uploaded document %s", org.tenant_id, existing.id)
        return existing

    key = f"{org.tenant_id}/{digest}{STORED_SUFFIX[media_type]}"
    try:
        get_storage().put(key, content, media_type)
    except StorageError:
        log.exception("could not store an upload for tenant %s", org.tenant_id)
        raise UploadRejected("the photo could not be saved; please try again") from None

    document = Document(
        tenant_id=org.tenant_id,
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


def candidates_for(db: Session, org: OrgContext, location_id: int) -> list[Candidate]:
    aliases: dict[int, list[str]] = {}
    for alias in db.scalars(select_scoped(org, StockAlias)):
        aliases.setdefault(alias.stock_item_id, []).append(alias.alias)

    codes: dict[int, list[tuple[str, str]]] = {}
    for row in db.scalars(select_scoped(org, ProductIdentifier)):
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
        for item in db.scalars(active_stock(org).where(StockItem.location_id == location_id))
    ]


def alias_index(db: Session, org: OrgContext) -> dict[str, int]:
    return build_alias_index(
        [(alias.alias, alias.stock_item_id) for alias in db.scalars(select_scoped(org, StockAlias))]
    )


def parse_document(
    db: Session, org: OrgContext, document_id: int, extractor: Extractor | None = None
) -> Document:
    """Read the photo and match every line against this location's stock."""
    document = get_scoped(db, org, Document, document_id)
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
        candidates_for(db, org, document.location_id),
        alias_index(db, org),
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
    org: OrgContext,
    document_id: int,
    decisions: list[LineDecision],
    now: datetime | None = None,
    user_id: int | None = None,
) -> dict:
    """Receive the delivery: one receipt posting for every applied line (DR-25), plus the
    lots' dates, the latest costs and the supplier's wording."""
    now = now or datetime.now(UTC)
    document = get_scoped(db, org, Document, document_id)
    if document is None:
        raise ValueError(f"document {document_id} not found")
    # Claim the document before anything else: the status changes only if it is not applied
    # yet (DR-07). A second request waits on this row, then finds it applied and stops here,
    # so a double click or two people at once receive the delivery once (AC-RCV-05).
    claim = db.execute(
        update_scoped(org, Document)
        .where(Document.id == document.id, Document.status != DocumentStatus.APPLIED)
        .values(status=DocumentStatus.APPLIED, applied_at=now)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(claim, "rowcount", 0) or 0) != 1:
        raise AlreadyApplied(f"document {document_id} has already been applied")

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
    items = {item.id: item for item in db.scalars(select_scoped(org, StockItem))}

    applied = 0
    batches = 0
    movements: list[Line] = []
    for decision in decisions:
        line = lines.get(decision.line_id)
        if line is None:
            raise ValueError(f"line {decision.line_id} does not belong to document {document_id}")
        if not decision.apply:
            line.status = LineStatus.SKIPPED
            continue

        item = items.get(decision.stock_item_id or line.stock_item_id or -1)
        quantity = decision.qty_base if decision.qty_base is not None else line.received_qty_base
        if quantity is not None:
            quantity = places(Decimal(quantity))
        if item is None or item.inventory_item_id is None or quantity is None or quantity <= ZERO:
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

        cost = line_cost_per_base(
            line.line_total_cents,
            None if decision.line_total_cents is not None else line.unit_price_cents,
            line.parsed_qty,
            Decimal(quantity),
        )
        if cost is not None and cost >= ZERO:
            # The latest price paid is what replacing this stock costs, which is what waste costs.
            set_cost(
                db,
                org,
                location_id=item.location_id,
                inventory_item_id=item.inventory_item_id,
                cost=cost,
                source_type="receipt",
                source_id=str(document.id),
                effective_at=now,
            )
        movements.append(
            Line(
                item.location_id,
                item.inventory_item_id,
                "receipt",
                quantity,
                # What this delivery cost; the item's current cost when the invoice did not say.
                unit_cost=cost if cost is not None and cost >= ZERO else None,
            )
        )

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
        _learn_alias(db, org, document, line, item.id)
        applied += 1

    if movements:
        supplier = document.supplier or "supplier"
        post_transaction(
            db,
            org,
            kind="receipt",
            source_type="document",
            source_id=str(document.id),
            idempotency_key=f"receipt:document:{document.id}",
            lines=movements,
            occurred_at=now,
            reason=f"Received from {supplier}, invoice {document.invoice_number or document.id}",
        )
    # The row was changed in SQL above; the loaded copy follows it.
    document.status = DocumentStatus.APPLIED
    document.applied_at = now
    db.flush()

    # Nothing is sent to Square (C-34): a delivery changes InventoryOS stock only.
    return {
        "applied": applied,
        "batches": batches,
        "document_id": document.id,
    }


def _learn_alias(
    db: Session, org: OrgContext, document: Document, line: DocumentLine, stock_item_id: int
) -> None:
    """Remember supplier wording so the next invoice matches without a human."""
    alias = normalize_name(line.parsed_name)
    if not alias:
        return
    existing = db.scalars(select_scoped(org, StockAlias).where(StockAlias.alias == alias)).first()
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
