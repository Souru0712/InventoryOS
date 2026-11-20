"""Supplier screens (T2.10; PRC-FR-01..03, -08): /app/suppliers.

Anyone who sees reorder suggestions sees suppliers; changing them takes supplier.manage. Prices
are money: shown only to a role with cost access.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.db import get_db
from app.models import InventoryItem, Tenant
from app.orchestration.views import tenant_currency
from app.security import require_permission, require_tenant, require_writable
from app.suppliers import (
    NotFound,
    SupplierFields,
    add_supplier_item,
    create_supplier,
    get_supplier,
    get_supplier_item,
    list_suppliers,
    purchases,
    set_supplier_active,
    update_supplier,
    update_supplier_item,
)
from app.transform.business_days import WEEKDAYS, open_day_names
from app.transform.suppliers import METHOD_LABELS, SupplierRuleError
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
BASE = "/app/suppliers"


def _act(request: Request, db: Session, action, done: str, back: str) -> RedirectResponse:
    try:
        action()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except SupplierRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, done)
    return RedirectResponse(back, status_code=303)


def _fields(
    name: str,
    contact_name: str,
    phone: str,
    email: str,
    ordering_method: str,
    delivery_days: list[str],
    lead_time_days: str,
    minimum_order: str,
    notes: str,
) -> SupplierFields:
    return SupplierFields(
        name=name,
        contact_name=contact_name,
        phone=phone,
        email=email,
        ordering_method=ordering_method,
        delivery_days=tuple(delivery_days),
        lead_time_days=lead_time_days,
        minimum_order=minimum_order,
        notes=notes,
    )


@router.get(BASE, response_class=HTMLResponse)
def suppliers_page(
    request: Request,
    show: str = "",
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("reorder.view")),
):
    return templates.TemplateResponse(
        request,
        "app/suppliers.html",
        {
            "title": "Suppliers",
            "tenant": tenant,
            "message": pop_flash(request),
            "suppliers": list_suppliers(db, org, inactive=show == "all"),
            "show": show,
            "methods": METHOD_LABELS,
            "weekdays": WEEKDAYS,
            "day_names": open_day_names,
        },
    )


@router.post(BASE)
def new_supplier(
    request: Request,
    name: str = Form(""),
    contact_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    ordering_method: str = Form("email"),
    delivery_days: list[str] = Form([]),
    lead_time_days: str = Form("2"),
    minimum_order: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("supplier.manage")),
):
    fields = _fields(
        name,
        contact_name,
        phone,
        email,
        ordering_method,
        delivery_days,
        lead_time_days,
        minimum_order,
        notes,
    )
    try:
        row = create_supplier(db, org, fields)
    except SupplierRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(BASE, status_code=303)
    db.commit()
    flash(request, f"{row.name} added.")
    return RedirectResponse(f"{BASE}/{row.id}", status_code=303)


@router.get(BASE + "/{supplier_id}", response_class=HTMLResponse)
def supplier_page(
    request: Request,
    supplier_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("reorder.view")),
):
    try:
        supplier = get_supplier(db, org, supplier_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    items = list(db.scalars(select_scoped(org, InventoryItem).order_by(InventoryItem.name)))
    return templates.TemplateResponse(
        request,
        "app/supplier.html",
        {
            "title": supplier.name,
            "tenant": tenant,
            "message": pop_flash(request),
            "supplier": supplier,
            "items": items,
            "names": {i.id: i for i in items},
            "deliveries": purchases(db, org, supplier),
            "methods": METHOD_LABELS,
            "weekdays": WEEKDAYS,
            "chosen_days": set(open_day_names(supplier.delivery_days)),
            "currency": tenant_currency(db, org),
        },
    )


@router.post(BASE + "/{supplier_id}")
def edit_supplier(
    request: Request,
    supplier_id: int,
    name: str = Form(""),
    contact_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    ordering_method: str = Form("email"),
    delivery_days: list[str] = Form([]),
    lead_time_days: str = Form("2"),
    minimum_order: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("supplier.manage")),
):
    fields = _fields(
        name,
        contact_name,
        phone,
        email,
        ordering_method,
        delivery_days,
        lead_time_days,
        minimum_order,
        notes,
    )
    return _act(
        request,
        db,
        lambda: update_supplier(db, org, supplier_id, fields),
        "Saved.",
        f"{BASE}/{supplier_id}",
    )


@router.post(BASE + "/{supplier_id}/active")
def supplier_active(
    request: Request,
    supplier_id: int,
    active: str = Form("no"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("supplier.manage")),
):
    on = active == "yes"
    return _act(
        request,
        db,
        lambda: set_supplier_active(db, org, supplier_id, on),
        "Brought back." if on else "Deactivated. Its deliveries and history stay.",
        f"{BASE}/{supplier_id}",
    )


@router.post(BASE + "/{supplier_id}/items")
def new_supplier_item(
    request: Request,
    supplier_id: int,
    inventory_item_id: int = Form(...),
    purchase_unit: str = Form(""),
    pack_quantity: str = Form("1"),
    pack_unit: str = Form(""),
    code: str = Form(""),
    name: str = Form(""),
    price: str = Form(""),
    preferred: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("supplier.manage")),
):
    return _act(
        request,
        db,
        lambda: add_supplier_item(
            db,
            org,
            supplier_id,
            inventory_item_id=inventory_item_id,
            purchase_unit=purchase_unit,
            pack_quantity=pack_quantity,
            pack_unit=pack_unit,
            code=code,
            name=name,
            price=price,
            preferred=preferred == "yes",
        ),
        "Item added.",
        f"{BASE}/{supplier_id}",
    )


@router.post(BASE + "/items/{supplier_item_id}")
def edit_supplier_item(
    request: Request,
    supplier_item_id: int,
    purchase_unit: str = Form(""),
    pack_quantity: str = Form("1"),
    pack_unit: str = Form(""),
    code: str = Form(""),
    name: str = Form(""),
    price: str = Form(""),
    preferred: str = Form(""),
    active: str = Form("yes"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("supplier.manage")),
):
    try:
        row = get_supplier_item(db, org, supplier_item_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    return _act(
        request,
        db,
        lambda: update_supplier_item(
            db,
            org,
            supplier_item_id,
            purchase_unit=purchase_unit,
            pack_quantity=pack_quantity,
            pack_unit=pack_unit,
            code=code,
            name=name,
            price=price,
            preferred=preferred == "yes",
            active=active == "yes",
        ),
        "Saved.",
        f"{BASE}/{row.supplier_id}",
    )
