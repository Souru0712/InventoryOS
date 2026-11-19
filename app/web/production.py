"""Production screen (T2.09; WF-11, PRD-FR-03..07): /app/production.

Phone-first: choose what is being made and how many times the recipe, see what it takes and what
is on hand, confirm with what was actually made, then read the label (code and use-by). The
recorder may void for 30 minutes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.db import get_db
from app.models import InventoryItem, Location, ProductionOrder, StockBatch, Tenant
from app.production import NotFound, makeable, propose, recent_runs, record_run, void_run
from app.security import location_filter, require_permission, require_tenant, require_writable
from app.transform.production import VOID_WINDOW, ProductionRuleError
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
BASE = "/app/production"


def _multiplier(raw: str) -> Decimal:
    try:
        value = Decimal(raw.strip() or "1")
    except InvalidOperation as exc:
        raise ProductionRuleError("The multiplier is a number, such as 1.5.") from exc
    return value


@router.get(BASE, response_class=HTMLResponse)
def production_page(
    request: Request,
    item: int | None = None,
    times: str = "1",
    made: int | None = None,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("production.record")),
):
    places = list(db.scalars(select_scoped(org, Location).order_by(Location.id)))
    if not places:
        raise HTTPException(status_code=404, detail="no location")
    place = next((p for p in places if p.id == location), places[0])
    proposal = None
    problem = None
    if item is not None:
        if get_scoped(db, org, InventoryItem, item) is None:
            raise HTTPException(status_code=404, detail="not found")
        try:
            proposal = propose(
                db,
                org,
                location_id=place.id,
                inventory_item_id=item,
                multiplier=_multiplier(times),
            )
        except NotFound as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except ProductionRuleError as exc:
            problem = str(exc)
    finished = get_scoped(db, org, ProductionOrder, made) if made else None
    if made and finished is None:
        raise HTTPException(status_code=404, detail="not found")
    label = None
    if finished is not None:
        batch = (
            get_scoped(db, org, StockBatch, finished.output_batch_id)
            if finished.output_batch_id
            else None
        )
        thing = get_scoped(db, org, InventoryItem, finished.inventory_item_id)
        label = {"order": finished, "batch": batch, "item": thing}
    now = datetime.now(UTC)
    runs = recent_runs(db, org, since=now - timedelta(hours=12))
    names = {
        i.id: i
        for i in db.scalars(
            select_scoped(org, InventoryItem).where(
                InventoryItem.id.in_({r.inventory_item_id for r in runs})
            )
        )
    }
    return templates.TemplateResponse(
        request,
        "app/production.html",
        {
            "title": "Production",
            "tenant": tenant,
            "message": pop_flash(request),
            "places": places,
            "place": place,
            "makeable": makeable(db, org, place.id),
            "proposal": proposal,
            "problem": problem,
            "times": times,
            "label": label,
            "runs": runs,
            "names": names,
            "void_until": now - VOID_WINDOW,
        },
    )


@router.post(BASE)
def make(
    request: Request,
    location_id: int = Form(...),
    inventory_item_id: int = Form(...),
    times: str = Form("1"),
    actual_yield: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("production.record")),
):
    back = f"{BASE}?item={inventory_item_id}&location={location_id}&times={times}"
    try:
        order = record_run(
            db,
            org,
            location_id=location_id,
            inventory_item_id=inventory_item_id,
            multiplier=_multiplier(times),
            actual_yield=actual_yield,
            note=note,
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except ProductionRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    return RedirectResponse(f"{BASE}?made={order.id}&location={location_id}", status_code=303)


@router.post(BASE + "/{order_id}/void")
def void(
    request: Request,
    order_id: int,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("production.record")),
):
    try:
        void_run(db, org, order_id, reason=reason)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except ProductionRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(BASE, status_code=303)
    db.commit()
    flash(request, "Voided. The inputs are back and the batch holds nothing.")
    return RedirectResponse(BASE, status_code=303)
