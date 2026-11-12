"""Receiving: photograph the invoice, confirm the matches, stock goes up."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.access import OrgContext, get_scoped, select_scoped
from app.catalog import active_stock
from app.db import get_db
from app.extract.invoice import SUPPORTED_MEDIA, ExtractionError
from app.models import Document, DocumentStatus, Location, StockItem, Tenant, User
from app.orchestration.extraction_quality import correction_stats
from app.orchestration.receiving import (
    LineDecision,
    UploadRejected,
    apply_document,
    parse_document,
    record_correction,
    save_upload,
)
from app.security import (
    require_permission,
    require_tenant,
    require_user,
    require_verified,
    require_writable,
)
from app.storage import StorageError, get_storage
from app.units import UnitError, to_base
from app.web.inputs import posted_form
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _owned(db: Session, org: OrgContext, document_id: int) -> Document:
    document = get_scoped(db, org, Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return document


def _field(form: FormData, name: str) -> str:
    """A text field's value; a file posted under a text field's name counts as empty."""
    value = form.get(name)
    return value if isinstance(value, str) else ""


def _cents(raw: object) -> int | None:
    """A price typed as 12.50 or 12,50; blank means the reviewer left the reader's figure alone."""
    text = str(raw or "").strip().replace(",", ".").lstrip("$")
    if not text:
        return None
    try:
        return int((Decimal(text) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


@router.get("/app/receiving", response_class=HTMLResponse)
def receiving_index(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("receiving.record")),
):
    documents = list(
        db.scalars(select_scoped(org, Document).order_by(Document.uploaded_at.desc()).limit(50))
    )
    locations = list(db.scalars(select_scoped(org, Location).where(Location.is_active.is_(True))))
    return templates.TemplateResponse(
        request,
        "app/receiving.html",
        {
            "title": "Receiving",
            "tenant": tenant,
            "documents": documents,
            "locations": locations,
            "quality": correction_stats(db, org),
        },
    )


@router.post("/app/receiving")
def upload_invoice(
    location_id: int = Form(...),
    kind: str = Form("invoice"),
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("receiving.record")),
    _verified: User = Depends(require_verified),
):
    location = get_scoped(db, org, Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="location not found")

    try:
        document = save_upload(
            db,
            org,
            location.id,
            photo.filename or "invoice.jpg",
            photo.file.read(),
            photo.content_type or "image/jpeg",
            uploaded_by=user.id,
            kind=kind,
        )
    except UploadRejected as exc:
        return RedirectResponse(f"/app/receiving?error={exc}", status_code=303)
    db.commit()

    try:
        parse_document(db, org, document.id)
        db.commit()
    except ExtractionError as exc:
        db.commit()
        log.warning("document %s could not be read: %s", document.id, exc)
    except Exception:
        db.rollback()
        log.exception("parsing document %s failed", document.id)

    return RedirectResponse(f"/app/receiving/{document.id}", status_code=303)


@router.get("/app/receiving/{document_id}", response_class=HTMLResponse)
def review_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("receiving.record")),
):
    document = _owned(db, org, document_id)
    items = list(db.scalars(active_stock(org).where(StockItem.location_id == document.location_id)))
    return templates.TemplateResponse(
        request,
        "app/receiving_review.html",
        {
            "title": f"Invoice {document.invoice_number or document.id}",
            "tenant": tenant,
            "document": document,
            "lines": sorted(document.lines, key=lambda line: line.line_no),
            "items": {item.id: item for item in items},
            "item_list": sorted(items, key=lambda item: item.name),
        },
    )


@router.get("/app/receiving/{document_id}/image")
def document_image(
    document_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("receiving.record")),
):
    document = _owned(db, org, document_id)
    try:
        content = get_storage().get(document.storage_key)
    except StorageError:
        log.exception("photo for document %s is missing from storage", document.id)
        raise HTTPException(status_code=404, detail="photo not found") from None
    # The type comes from the allow-list by stored suffix, never from what the uploader sent:
    # records stored before T0.06 kept the uploader's Content-Type, which could be text/html (S3).
    suffix = PurePosixPath(document.storage_key).suffix.lower()
    media_type = SUPPORTED_MEDIA.get(suffix)
    disposition = "inline"
    if media_type is None:
        media_type, disposition = "application/octet-stream", "attachment"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'{disposition}; filename="document-{document.id}{suffix}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/app/receiving/{document_id}/reparse")
def reparse(
    document_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("receiving.record")),
    _verified: User = Depends(require_verified),
):
    document = _owned(db, org, document_id)
    if document.status == DocumentStatus.APPLIED:
        return RedirectResponse(
            f"/app/receiving/{document.id}?error=already_applied", status_code=303
        )
    try:
        parse_document(db, org, document.id)
        db.commit()
    except ExtractionError:
        db.commit()
    return RedirectResponse(f"/app/receiving/{document.id}", status_code=303)


@router.post("/app/receiving/{document_id}/apply")
def apply_receipt(
    document_id: int,
    request: Request,
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("receiving.post")),
):
    """Apply the reviewed lines. The form posts one field group per line."""
    document = _owned(db, org, document_id)
    if document.status == DocumentStatus.APPLIED:
        return RedirectResponse(
            f"/app/receiving/{document.id}?error=already_applied", status_code=303
        )

    items = {item.id: item for item in db.scalars(select_scoped(org, StockItem))}

    decisions: list[LineDecision] = []
    for line in document.lines:
        prefix = f"line_{line.id}"
        if form.get(f"{prefix}_action") != "apply":
            decisions.append(LineDecision(line.id, None, None, apply=False))
            continue

        raw_item = _field(form, f"{prefix}_item")
        stock_item_id = int(raw_item) if raw_item.isdigit() else None
        item = items.get(stock_item_id or -1)
        if item is None:
            decisions.append(LineDecision(line.id, None, None, apply=False))
            continue

        raw_qty = _field(form, f"{prefix}_qty").strip()
        try:
            qty_base = to_base(Decimal(raw_qty), item.stock_unit) if raw_qty else None
        except (InvalidOperation, UnitError, ValueError):
            qty_base = None

        raw_date = _field(form, f"{prefix}_expires").strip()
        try:
            expires_on = date.fromisoformat(raw_date) if raw_date else None
        except ValueError:
            expires_on = None

        decisions.append(
            LineDecision(
                line.id,
                item.id,
                qty_base,
                apply=True,
                expires_on=expires_on,
                # Only a role that may set costs changes what the invoice says a line cost.
                line_total_cents=(
                    _cents(form.get(f"{prefix}_total")) if org.can("receiving.edit_costs") else None
                ),
            )
        )

    # The header the reader filled in is editable too: a wrong supplier teaches the wrong alias.
    supplier = _field(form, "supplier").strip() or None
    invoice_number = _field(form, "invoice_number").strip() or None
    record_correction(db, document, "supplier", document.supplier, supplier, user_id=user.id)
    record_correction(
        db, document, "invoice_number", document.invoice_number, invoice_number, user_id=user.id
    )
    document.supplier, document.invoice_number = supplier, invoice_number

    result = apply_document(db, org, document.id, decisions, user_id=user.id)
    db.commit()
    return RedirectResponse(
        f"/app/receiving/{document.id}?applied={result['applied']}&batches={result['batches']}",
        status_code=303,
    )
