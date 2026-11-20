"""Direct usage screen (T2.14; STD-FR-04): /app/usage.

Ingredients used without a sale, with a reason. Recorded by anyone who may enter sales and
usage (`sales.record`, PD-37). The week's usage shows beside what sales used.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.access import OrgContext, select_scoped
from app.db import get_db
from app.models import InventoryItem, ItemLocation, Location, Tenant
from app.security import location_filter, require_permission, require_tenant, require_writable
from app.transform.business_days import business_date
from app.transform.ledger import BelowZero
from app.transform.usage import MAX_LINES, REASONS, UsageRuleError
from app.units import UNITS
from app.usage import NotFound, Use, consumption, new_token, record_usage, records
from app.web.flash import flash, pop_flash
from app.web.inputs import posted_form
from app.web.templating import templates

router = APIRouter()
BASE = "/app/usage"
ROWS = 4


def _today(place: Location) -> date:
    return business_date(datetime.now(UTC), place.timezone, place.business_day_cutoff_hour)


@router.get(BASE, response_class=HTMLResponse)
def usage_page(
    request: Request,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("sales.record")),
):
    places = list(db.scalars(select_scoped(org, Location).order_by(Location.id)))
    if not places:
        raise HTTPException(status_code=404, detail="no location")
    place = next((p for p in places if p.id == location), places[0])
    stocked = {
        row.inventory_item_id
        for row in db.scalars(
            select_scoped(org, ItemLocation).where(ItemLocation.location_id == place.id)
        )
    }
    items = [
        i
        for i in db.scalars(
            select_scoped(org, InventoryItem)
            .where(InventoryItem.is_active.is_(True))
            .order_by(InventoryItem.name)
        )
        if i.id in stocked
    ]
    names = {
        i.id: i
        for i in db.scalars(select_scoped(org, InventoryItem).where(InventoryItem.id.in_(stocked)))
    }
    until = _today(place)
    since = until - timedelta(days=6)
    return templates.TemplateResponse(
        request,
        "app/usage.html",
        {
            "title": "Usage",
            "tenant": tenant,
            "message": pop_flash(request),
            "places": places,
            "place": place,
            "items": items,
            "names": names,
            "reasons": REASONS,
            "rows": range(1, ROWS + 1),
            "units": sorted(UNITS),
            "token": new_token(),
            "since": since,
            "records": records(db, org, since=since, until=until, location_id=place.id),
            "used": consumption(db, org, since=since, until=until, location_id=place.id),
        },
    )


@router.post(BASE)
def record(
    request: Request,
    location_id: int = Form(...),
    reason: str = Form(""),
    note: str = Form(""),
    token: str = Form(""),
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("sales.record")),
):
    back = f"{BASE}?location={location_id}"
    uses = []
    try:
        for n in range(1, MAX_LINES + 1):
            item = str(form.get(f"item_{n}") or "").strip()
            quantity = str(form.get(f"qty_{n}") or "")
            if item and quantity.strip():
                uses.append(Use(int(item), quantity, str(form.get(f"unit_{n}") or "").strip()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bad item") from exc
    try:
        posted = record_usage(
            db, org, location_id=location_id, uses=uses, reason=reason, note=note, token=token
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except BelowZero:
        db.rollback()
        flash(
            request,
            "That is more than the recorded stock. Count it first, or ask a Manager.",
            "error",
        )
        return RedirectResponse(back, status_code=303)
    except UsageRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, "Usage recorded." if posted else "That usage was already recorded.")
    return RedirectResponse(back, status_code=303)
