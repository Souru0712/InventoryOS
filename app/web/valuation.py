"""Inventory valuation (T1.24; ANL-FR-01, -02, INV-FR-24, AC-ANL-02) and current-cost overrides
(INV-FR-07). Values sit behind `reports.financial`; overriding a cost takes
`receiving.edit_costs`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.costs import NotFound, override_cost, value_stock
from app.db import get_db
from app.items import NotFound as ItemNotFound
from app.items import item_row
from app.models import Location, Tenant
from app.security import location_filter, require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.transform.costs import CostRuleError
from app.units import UnitError, to_base
from app.web.flash import flash
from app.web.templating import templates

router = APIRouter()


@router.get("/app/valuation", response_class=HTMLResponse)
def valuation_page(
    request: Request,
    as_of: str = "",
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("reports.financial")),
):
    try:
        day = date.fromisoformat(as_of) if as_of.strip() else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="dates are YYYY-MM-DD") from exc
    places = sorted(db.scalars(select_scoped(org, Location)), key=lambda loc: loc.name)
    result = value_stock(db, org, location_id=location, as_of_day=day)
    return templates.TemplateResponse(
        request,
        "app/valuation.html",
        {
            "title": "Inventory value",
            "tenant": tenant,
            "valuation": result,
            "as_of": day,
            "locations": places,
            "selected_location": location,
            "names": {place.id: place.name for place in places},
            "currency": places[0].currency if places else "USD",
        },
    )


@router.post("/app/catalog/{inventory_item_id}/cost")
def set_current_cost(
    request: Request,
    inventory_item_id: int,
    location_id: int = Form(...),
    price: str = Form(""),
    unit: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("receiving.edit_costs")),
):
    """Override the current cost at one location until the next receipt (INV-FR-07). Past
    figures keep the cost they were posted at (AC-ANL-01)."""
    try:
        row = item_row(db, org, inventory_item_id)
    except ItemNotFound as exc:
        raise HTTPException(status_code=404, detail="item not found") from exc
    if get_scoped(db, org, Location, location_id) is None:
        raise HTTPException(status_code=404, detail="location not found")
    back = f"/app/catalog/{inventory_item_id}"
    try:
        per_unit = Decimal(price.strip())
        cost = per_unit * 100 / to_base("1", unit or row.item.display_unit)
        override_cost(
            db,
            org,
            location_id=location_id,
            inventory_item_id=inventory_item_id,
            cost=cost,
            ip=client_ip(request),
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="item not stocked there") from exc
    except (InvalidOperation, UnitError, ArithmeticError):
        flash(request, "A cost is an amount like 4.50.", "error")
        return RedirectResponse(back, status_code=303)
    except CostRuleError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, f"Current cost of {row.item.name} updated. Past figures keep their cost.")
    return RedirectResponse(back, status_code=303)
