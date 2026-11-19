"""Onboarding screens (T2.20-b; SAAS §5): step 0, skipping a checklist step, and the one-screen
opening count at /app/onboarding/count.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.access import OrgContext, select_scoped
from app.db import get_db
from app.models import InventoryItem, Location, StockItem, Tenant
from app.onboarding import NotFound, answer_payments, opening_count, set_skipped
from app.security import location_filter, require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.transform.onboarding import OnboardingRuleError
from app.web.flash import flash, pop_flash
from app.web.inputs import posted_form
from app.web.templating import templates

router = APIRouter()
COUNT = "/app/onboarding/count"
NEXT = {
    "pos_supported": "Next, connect Square from the checklist: your sales will arrive by themselves.",
    "none": "You're set up to enter each day's sales by hand; the checklist shows the rest.",
    "none_wants_pos": (
        "Noted: we'll let you know about a POS. Meanwhile, enter each day's sales by hand."
    ),
    "pos_unsupported": "Noted. Until we support it, import its sales file each week.",
}


@router.post("/app/onboarding/payments")
def payments(
    request: Request,
    answer: str = Form(""),
    pos_name: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    """Step 0: "How do you take payments today?" (WF-01)."""
    try:
        row = answer_payments(db, org, answer=answer, pos_name=pos_name, ip=client_ip(request))
    except OnboardingRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse("/app", status_code=303)
    db.commit()
    flash(request, NEXT[row.payment_answer or "none"])
    return RedirectResponse("/app", status_code=303)


@router.post("/app/onboarding/skip")
def skip(
    request: Request,
    step: str = Form(""),
    skip: str = Form("yes"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    """Skip a checklist step, or take it back up."""
    try:
        set_skipped(db, org, step, skip == "yes")
    except OnboardingRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return RedirectResponse("/app", status_code=303)


@router.get(COUNT, response_class=HTMLResponse)
def count_page(
    request: Request,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("count.record")),
):
    places = list(db.scalars(select_scoped(org, Location).order_by(Location.id)))
    place = next((p for p in places if p.id == location), places[0] if places else None)
    rows = []
    if place is not None:
        stock = {
            s.inventory_item_id: s
            for s in db.scalars(
                select_scoped(org, StockItem).where(StockItem.location_id == place.id)
            )
        }
        rows = [
            (item, stock[item.id])
            for item in db.scalars(
                select_scoped(org, InventoryItem)
                .where(InventoryItem.is_active.is_(True))
                .order_by(InventoryItem.name)
            )
            if item.id in stock
        ]
    return templates.TemplateResponse(
        request,
        "app/opening_count.html",
        {
            "title": "Count your stock",
            "tenant": tenant,
            "message": pop_flash(request),
            "places": places,
            "place": place,
            "rows": rows,
        },
    )


@router.post(COUNT)
def post_count_page(
    request: Request,
    location_id: int = Form(...),
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("count.record")),
):
    entries: dict[int, tuple[str, str]] = {}
    for key, value in form.multi_items():
        if key.startswith("qty_") and key[4:].isdigit() and isinstance(value, str):
            entries[int(key[4:])] = (value, str(form.get(f"unit_{key[4:]}") or ""))
    back = f"{COUNT}?location={location_id}"
    try:
        done = opening_count(db, org, location_id=location_id, entries=entries)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except OnboardingRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    if not done:
        flash(request, "Enter at least one quantity.", "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(
        request,
        f"Counted {len(done)} item{'s' if len(done) != 1 else ''}. "
        "Stock from here on is measured against this count.",
    )
    return RedirectResponse("/app", status_code=303)
