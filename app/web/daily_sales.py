"""Daily sales entry (T2.12; STD-FR-02, -05, PD-37): /app/sales/daily.

A tablet-friendly grid: every product size, most sold first, with how many were sold on the
chosen business day. Managers and above enter it; Employees too when the Owner allows (PD-37).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import record_audit
from app.daily_sales import entered, save_day
from app.db import get_db
from app.models import Location, ProductVariation, Tenant
from app.sales import NotFound, daily_sales
from app.security import location_filter, require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.transform.business_days import business_date
from app.transform.sales import SalesRuleError
from app.web.flash import flash, pop_flash
from app.web.inputs import posted_form
from app.web.templating import templates

router = APIRouter()
BASE = "/app/sales/daily"


@router.get(BASE, response_class=HTMLResponse)
def daily_page(
    request: Request,
    day: str = "",
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("sales.record")),
):
    places = list(db.scalars(select_scoped(org, Location).order_by(Location.id)))
    if not places:
        raise HTTPException(status_code=404, detail="no location")
    place = next((p for p in places if p.id == location), places[0])
    yesterday = business_date(
        datetime.now(UTC), place.timezone, place.business_day_cutoff_hour
    ) - timedelta(days=1)
    try:
        chosen = date.fromisoformat(day) if day else yesterday
    except ValueError:
        chosen = yesterday
    sizes = [
        v
        for v in db.scalars(select_scoped(org, ProductVariation))
        if v.is_active and v.product.is_active
    ]
    sold: dict[int, Decimal] = defaultdict(Decimal)
    for sale in daily_sales(db, org, chosen - timedelta(days=28), chosen, place.id):
        if sale.product_variation_id is not None:
            sold[sale.product_variation_id] += sale.units
    sizes.sort(key=lambda v: (-sold.get(v.id, 0), v.product.name, v.name))
    return templates.TemplateResponse(
        request,
        "app/sales_daily.html",
        {
            "title": "Daily sales",
            "tenant": tenant,
            "message": pop_flash(request),
            "places": places,
            "place": place,
            "day": chosen,
            "sizes": sizes,
            "values": entered(db, org, place.id, chosen),
        },
    )


@router.post(BASE)
def save(
    request: Request,
    location_id: int = Form(...),
    day: str = Form(""),
    reason: str = Form(""),
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("sales.record")),
):
    try:
        chosen = date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bad day") from exc
    back = f"{BASE}?location={location_id}&day={chosen.isoformat()}"
    quantities: dict[int, Decimal] = {}
    try:
        for key, value in form.multi_items():
            if key.startswith("qty_") and isinstance(value, str) and value.strip():
                quantities[int(key[4:])] = Decimal(value.strip())
    except (ValueError, InvalidOperation):
        flash(request, "Enter whole numbers sold, or leave a size empty.", "error")
        return RedirectResponse(back, status_code=303)
    try:
        saved = save_day(
            db, org, location_id=location_id, day=chosen, quantities=quantities, reason=reason
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except SalesRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, "Saved. What the day used has left stock." if saved else "Nothing changed.")
    return RedirectResponse(back, status_code=303)


@router.post(BASE + "/settings")
def entry_settings(
    request: Request,
    employees: str = Form("no"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    """PD-37: whether Employees may enter daily sales."""
    allowed = employees == "yes"
    if allowed != tenant.employees_enter_sales:
        business = get_scoped(db, org, Tenant, tenant.id)
        if business is not None:
            business.employees_enter_sales = allowed
            record_audit(
                db,
                org,
                "settings.sales_entry_changed",
                "Employees may enter daily sales" if allowed else "Only Managers enter daily sales",
                subject_type="tenant",
                subject_id=tenant.id,
                after={"employees enter sales": allowed},
                ip=client_ip(request),
            )
    db.commit()
    flash(request, "Saved.")
    return RedirectResponse(BASE, status_code=303)
