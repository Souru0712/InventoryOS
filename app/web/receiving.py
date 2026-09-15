"""Receiving: photograph the invoice, confirm the matches, stock goes up."""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.extract.invoice import ExtractionError
from app.models import Document, DocumentStatus, Location, StockItem, Tenant, User
from app.orchestration.receiving import (
    LineDecision,
    UploadRejected,
    apply_document,
    parse_document,
    save_upload,
)
from app.security import require_tenant, require_user
from app.storage import StorageError, get_storage
from app.units import UnitError, to_base
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _owned(db: Session, tenant_id: int, document_id: int) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="document not found")
    return document


@router.get("/app/receiving", response_class=HTMLResponse)
def receiving_index(
    request: Request, db: Session = Depends(get_db), tenant: Tenant = Depends(require_tenant)
):
    documents = list(
        db.scalars(
            select(Document)
            .where(Document.tenant_id == tenant.id)
            .order_by(Document.uploaded_at.desc())
            .limit(50)
        )
    )
    locations = list(
        db.scalars(
            select(Location).where(Location.tenant_id == tenant.id, Location.is_active.is_(True))
        )
    )
    return templates.TemplateResponse(
        request,
        "app/receiving.html",
        {
            "title": "Receiving",
            "tenant": tenant,
            "documents": documents,
            "locations": locations,
        },
    )


@router.post("/app/receiving")
async def upload_invoice(
    location_id: int = Form(...),
    kind: str = Form("invoice"),
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
):
    location = db.get(Location, location_id)
    if location is None or location.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="location not found")

    try:
        document = save_upload(
            db,
            tenant.id,
            location.id,
            photo.filename or "invoice.jpg",
            await photo.read(),
            photo.content_type or "image/jpeg",
            uploaded_by=user.id,
            kind=kind,
        )
    except UploadRejected as exc:
        return RedirectResponse(f"/app/receiving?error={exc}", status_code=303)
    db.commit()

    try:
        parse_document(db, document.id)
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
):
    document = _owned(db, tenant.id, document_id)
    items = list(
        db.scalars(
            select(StockItem).where(
                StockItem.tenant_id == tenant.id,
                StockItem.location_id == document.location_id,
                StockItem.is_active.is_(True),
            )
        )
    )
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
    document_id: int, db: Session = Depends(get_db), tenant: Tenant = Depends(require_tenant)
):
    document = _owned(db, tenant.id, document_id)
    try:
        content = get_storage().get(document.storage_key)
    except StorageError:
        log.exception("photo for document %s is missing from storage", document.id)
        raise HTTPException(status_code=404, detail="photo not found") from None
    return Response(content, media_type=document.content_type, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/app/receiving/{document_id}/reparse")
def reparse(
    document_id: int, db: Session = Depends(get_db), tenant: Tenant = Depends(require_tenant)
):
    document = _owned(db, tenant.id, document_id)
    if document.status == DocumentStatus.APPLIED:
        return RedirectResponse(f"/app/receiving/{document.id}?error=already_applied", status_code=303)
    try:
        parse_document(db, document.id)
        db.commit()
    except ExtractionError:
        db.commit()
    return RedirectResponse(f"/app/receiving/{document.id}", status_code=303)


@router.post("/app/receiving/{document_id}/apply")
async def apply_receipt(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    """Apply the reviewed lines. The form posts one field group per line."""
    document = _owned(db, tenant.id, document_id)
    if document.status == DocumentStatus.APPLIED:
        return RedirectResponse(f"/app/receiving/{document.id}?error=already_applied", status_code=303)

    form = await request.form()
    items = {
        item.id: item
        for item in db.scalars(select(StockItem).where(StockItem.tenant_id == tenant.id))
    }

    decisions: list[LineDecision] = []
    for line in document.lines:
        prefix = f"line_{line.id}"
        if form.get(f"{prefix}_action") != "apply":
            decisions.append(LineDecision(line.id, None, None, apply=False))
            continue

        raw_item = form.get(f"{prefix}_item") or ""
        stock_item_id = int(raw_item) if raw_item.isdigit() else None
        item = items.get(stock_item_id or -1)
        if item is None:
            decisions.append(LineDecision(line.id, None, None, apply=False))
            continue

        raw_qty = (form.get(f"{prefix}_qty") or "").strip()
        try:
            qty_base = to_base(Decimal(raw_qty), item.stock_unit) if raw_qty else None
        except (InvalidOperation, UnitError, ValueError):
            qty_base = None

        raw_date = (form.get(f"{prefix}_expires") or "").strip()
        try:
            expires_on = date.fromisoformat(raw_date) if raw_date else None
        except ValueError:
            expires_on = None
        # The form always posts the field, so an emptied date means the reviewer removed it.
        line.expires_on = expires_on
        decisions.append(LineDecision(line.id, item.id, qty_base, apply=True, expires_on=expires_on))

    result = apply_document(db, document.id, decisions)
    db.commit()
    return RedirectResponse(
        f"/app/receiving/{document.id}?applied={result['applied']}&pushed={result['pushed_to_pos']}"
        f"&batches={result['batches']}",
        status_code=303,
    )
