"""Deliveries received by hand (T2.11-a; RCV-FR-01, -05..07, -09..12): /app/deliveries.

A delivery is a draft until posted: add what was invoiced, arrived and accepted, then post. A
posted line is corrected, never edited. Invoice photos keep their own screens (/app/receiving).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.db import get_db
from app.models import InventoryItem, Location, ReceivingLine, Supplier, Tenant
from app.orchestration.views import tenant_currency
from app.receivings import (
    NotFound,
    add_line,
    correct_line,
    discrepancies,
    get_receiving,
    list_receivings,
    post_receiving,
    remove_line,
    start_receiving,
)
from app.security import require_permission, require_tenant, require_writable
from app.transform.ledger import BelowZero
from app.transform.receivings import ReceivingRuleError
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
BASE = "/app/deliveries"


def _act(request: Request, db: Session, action, done: str, back: str) -> RedirectResponse:
    try:
        action()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except (ReceivingRuleError, BelowZero) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, done)
    return RedirectResponse(back, status_code=303)


@router.get(BASE, response_class=HTMLResponse)
def deliveries_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("receiving.record")),
):
    return templates.TemplateResponse(
        request,
        "app/deliveries.html",
        {
            "title": "Deliveries",
            "tenant": tenant,
            "message": pop_flash(request),
            "receivings": list_receivings(db, org),
            "places": list(db.scalars(select_scoped(org, Location).order_by(Location.id))),
            "suppliers": [
                s
                for s in db.scalars(select_scoped(org, Supplier).order_by(Supplier.name))
                if s.is_active
            ],
        },
    )


@router.post(BASE)
def new_delivery(
    request: Request,
    location_id: int = Form(...),
    supplier_id: int | None = Form(None),
    note: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("receiving.record")),
):
    try:
        row = start_receiving(db, org, location_id=location_id, supplier_id=supplier_id, note=note)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    db.commit()
    return RedirectResponse(f"{BASE}/{row.id}", status_code=303)


@router.get(BASE + "/{receiving_id}", response_class=HTMLResponse)
def delivery_page(
    request: Request,
    receiving_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("receiving.record")),
):
    try:
        receiving = get_receiving(db, org, receiving_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    items = list(
        db.scalars(
            select_scoped(org, InventoryItem)
            .where(InventoryItem.is_active.is_(True))
            .order_by(InventoryItem.name)
        )
    )
    supplier = (
        get_scoped(db, org, Supplier, receiving.supplier_id) if receiving.supplier_id else None
    )
    return templates.TemplateResponse(
        request,
        "app/delivery.html",
        {
            "title": f"Delivery {receiving.id}",
            "tenant": tenant,
            "message": pop_flash(request),
            "receiving": receiving,
            "supplier": supplier,
            "items": items,
            "names": {i.id: i for i in items},
            "currency": tenant_currency(db, org),
            "issues": {
                line.id: discrepancies(line).describe(line.unit) for line in receiving.lines
            },
        },
    )


@router.post(BASE + "/{receiving_id}/lines")
def new_line(
    request: Request,
    receiving_id: int,
    inventory_item_id: int = Form(...),
    accepted: str = Form(""),
    unit: str = Form(""),
    invoiced: str = Form(""),
    arrived: str = Form(""),
    unit_price: str = Form(""),
    supplier_item_id: int | None = Form(None),
    expires_on: str = Form(""),
    lot_code: str = Form(""),
    reject_reason: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("receiving.record")),
):
    back = f"{BASE}/{receiving_id}"
    try:
        expiry = date.fromisoformat(expires_on) if expires_on else None
    except ValueError:
        flash(request, "Enter a valid date.", "error")
        return RedirectResponse(back, status_code=303)
    return _act(
        request,
        db,
        lambda: add_line(
            db,
            org,
            receiving_id,
            inventory_item_id=inventory_item_id,
            accepted=accepted,
            unit=unit,
            invoiced=invoiced or None,
            arrived=arrived or None,
            unit_price=unit_price or None,
            supplier_item_id=supplier_item_id,
            expires_on=expiry,
            lot_code=lot_code,
            reject_reason=reject_reason,
        ),
        "Line added.",
        back,
    )


@router.post(BASE + "/{receiving_id}/post")
def post_delivery(
    request: Request,
    receiving_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("receiving.post")),
):
    return _act(
        request,
        db,
        lambda: post_receiving(db, org, receiving_id),
        "Posted. The delivery is in stock.",
        f"{BASE}/{receiving_id}",
    )


def _line_receiving(db: Session, org: OrgContext, line_id: int) -> int:
    line = get_scoped(db, org, ReceivingLine, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="not found")
    return line.receiving_id


@router.post(BASE + "/lines/{line_id}/remove")
def drop_line(
    request: Request,
    line_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("receiving.record")),
):
    receiving_id = _line_receiving(db, org, line_id)
    return _act(
        request,
        db,
        lambda: remove_line(db, org, line_id),
        "Line removed.",
        f"{BASE}/{receiving_id}",
    )


@router.post(BASE + "/lines/{line_id}/correct")
def fix_line(
    request: Request,
    line_id: int,
    accepted: str = Form(""),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("receiving.correct")),
):
    receiving_id = _line_receiving(db, org, line_id)
    return _act(
        request,
        db,
        lambda: correct_line(db, org, line_id, accepted=accepted, reason=reason),
        "Corrected. The old line stays, marked corrected.",
        f"{BASE}/{receiving_id}",
    )
