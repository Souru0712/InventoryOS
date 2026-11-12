"""Stock screens for one item (T1.22): its movement history, and deliberate adjustments.

History: anyone who can see inventory (INV-FR-20); values only with cost access. Adjusting takes
`adjustment.create`, and taking stock below zero `adjustment.override_negative` (DR-08).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, team
from app.access.permissions import VIEW_COSTS
from app.adjustments import NotFound as NotStocked
from app.adjustments import adjust
from app.db import get_db
from app.items import NotFound, entered_base, item_row
from app.ledger.history import history
from app.models import Tenant
from app.security import require_permission, require_tenant, require_writable
from app.transform.adjustments import REASONS, AdjustmentRuleError
from app.transform.items import ItemRuleError
from app.transform.ledger import MOVEMENT_TYPES, BelowZero
from app.units import UNITS_BY_DIMENSION, format_change
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
BASE = "/app/catalog/{inventory_item_id}"

KIND_LABELS = {
    "opening": "Opening balance",
    "receipt": "Received",
    "sale": "Sold",
    "production": "Production",
    "waste": "Waste",
    "count": "Count",
    "adjustment": "Adjustment",
    "usage": "Used",
    "transfer": "Transfer",
    "pos_correction": "POS correction",
    "reversal": "Reversal",
}


def _row(db: Session, org: OrgContext, inventory_item_id: int):
    try:
        return item_row(db, org, inventory_item_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="item not found") from exc


def _place(row, location_id: int | None):
    """The location asked for, or the first one the item is stocked at."""
    places = row.places
    if location_id is None:
        return places[0]
    for place in places:
        if place.location.id == location_id:
            return place
    raise HTTPException(status_code=404, detail="item not stocked there")


def _date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw.strip() else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="dates are YYYY-MM-DD") from exc


@router.get(BASE + "/history", response_class=HTMLResponse)
def history_page(
    request: Request,
    inventory_item_id: int,
    location: int | None = None,
    type: str = "",
    since: str = "",
    until: str = "",
    actor: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    row = _row(db, org, inventory_item_id)
    place = _place(row, location)
    if type and type not in MOVEMENT_TYPES:
        raise HTTPException(status_code=400, detail="unknown movement type")
    people = {user.id: user.email for _membership, user in team(db, org)}
    rows = history(
        db,
        org,
        place.location,
        row.item.id,
        movement_type=type or None,
        since=_date(since),
        until=_date(until),
        actor_user_id=actor,
    )
    return templates.TemplateResponse(
        request,
        "app/stock_history.html",
        {
            "title": f"{row.item.name}: history",
            "tenant": tenant,
            "row": row,
            "item": row.item,
            "place": place,
            "rows": rows,
            "people": people,
            "kind_labels": KIND_LABELS,
            "movement_types": sorted(MOVEMENT_TYPES),
            "filters": {"type": type, "since": since, "until": until, "actor": actor},
            "costs": org.can(VIEW_COSTS),
            "currency": place.location.currency,
            "message": pop_flash(request),
        },
    )


def _form(request, db, org, tenant, row, place, error=None, values=None, status=200):
    item = row.item
    units = [(u.code, u.label) for u in UNITS_BY_DIMENSION[item.dimension]]
    units += [(package.name, package.name) for package in item.packages]
    return templates.TemplateResponse(
        request,
        "app/stock_adjust.html",
        {
            "title": f"Adjust {item.name}",
            "tenant": tenant,
            "row": row,
            "item": item,
            "place": place,
            "units": units,
            "reasons": REASONS,
            "may_override": org.can("adjustment.override_negative"),
            "values": values or {"unit": item.display_unit, "direction": "remove"},
            "error": error,
        },
        status_code=status,
    )


@router.get(BASE + "/adjust", response_class=HTMLResponse)
def adjust_page(
    request: Request,
    inventory_item_id: int,
    location: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("adjustment.create")),
):
    row = _row(db, org, inventory_item_id)
    return _form(request, db, org, tenant, row, _place(row, location))


@router.post(BASE + "/adjust")
def post_adjustment(
    request: Request,
    inventory_item_id: int,
    location_id: int = Form(...),
    direction: str = Form("remove"),
    quantity: str = Form(""),
    unit: str = Form(""),
    reason: str = Form(""),
    note: str = Form(""),
    override: str = Form(""),
    override_reason: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("adjustment.create")),
):
    """Post a correction with its reason (WF-10); refused below zero without an override."""
    row = _row(db, org, inventory_item_id)
    place = _place(row, location_id)
    values = {
        "direction": direction,
        "quantity": quantity,
        "unit": unit,
        "reason": reason,
        "note": note,
        "override": override,
        "override_reason": override_reason,
    }
    try:
        amount = entered_base(row.item, quantity or "0", unit or row.item.display_unit)
        if amount <= 0:
            raise AdjustmentRuleError("Enter a quantity above zero, then pick add or remove.")
        posted = adjust(
            db,
            org,
            inventory_item_id=row.item.id,
            location_id=place.location.id,
            qty_base=-amount if direction == "remove" else amount,
            reason=reason,
            note=note,
            override=override == "yes",
            override_reason=override_reason,
        )
    except BelowZero:
        db.rollback()
        message = "That would take recorded stock below zero."
        if org.can("adjustment.override_negative"):
            message += " Tick the override and say why, or count the item first."
        else:
            message += " Ask a Manager, or count the item first."
        return _form(request, db, org, tenant, row, place, message, values, status=400)
    except (AdjustmentRuleError, ItemRuleError) as exc:
        db.rollback()
        return _form(request, db, org, tenant, row, place, str(exc), values, status=400)
    except NotStocked as exc:
        raise HTTPException(status_code=404, detail="item not stocked there") from exc
    db.commit()
    flash(
        request,
        f"Adjusted {row.item.name} at {place.location.name} by "
        f"{format_change(posted.qty_base, row.item.display_unit)}.",
    )
    return RedirectResponse(
        f"/app/catalog/{row.item.id}/history?location={place.location.id}", status_code=303
    )
