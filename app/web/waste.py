"""Waste screens (T2.07; WST-FR-01..10, AC-WST-01..08): /app/waste.

Logging is phone-first: choose what was wasted, then quantity, reason, save (three taps). Anyone
who may log waste sees their own recent entries and may void them for 30 minutes. The log and
report read the ledger only (DR-33); costs show only to a role with cost access and financial
reports.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, members, select_scoped
from app.access.permissions import VIEW_COSTS
from app.db import get_db
from app.ledger.posting import held_batches
from app.models import (
    InventoryItem,
    ItemLocation,
    Location,
    ProductVariation,
    StockBatch,
    Tenant,
)
from app.orchestration.views import tenant_currency
from app.security import location_filter, require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.transform.business_days import business_date
from app.transform.ledger import BelowZero
from app.transform.waste import VOID_WINDOW, WasteRuleError, report
from app.waste import (
    NotFound,
    add_reason,
    change_reason,
    ensure_reasons,
    found_short,
    logged,
    reasons,
    recent_entries,
    record_waste,
    void_waste,
)
from app.web.flash import flash, pop_flash
from app.web.templating import can, templates

router = APIRouter()
BASE = "/app/waste"


def _places(db: Session, org: OrgContext) -> list[Location]:
    return list(db.scalars(select_scoped(org, Location).order_by(Location.id)))


def _today(place: Location) -> date:
    return business_date(datetime.now(UTC), place.timezone, place.business_day_cutoff_hour)


@router.get(BASE, response_class=HTMLResponse)
def waste_page(
    request: Request,
    item: int | None = None,
    product: int | None = None,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("waste.record")),
):
    """Choose what was wasted; then quantity, reason, save."""
    places = _places(db, org)
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
    sizes = [
        v
        for v in db.scalars(select_scoped(org, ProductVariation).order_by(ProductVariation.id))
        if v.is_active and v.product.is_active
    ]
    sizes.sort(key=lambda v: (v.product.name, v.id))
    chosen_item = get_scoped(db, org, InventoryItem, item) if item else None
    chosen_size = get_scoped(db, org, ProductVariation, product) if product else None
    if (item and chosen_item is None) or (product and chosen_size is None):
        raise HTTPException(status_code=404, detail="not found")
    batches = []
    if chosen_item is not None:
        held = {h.batch_id: h for h in held_batches(db, org, place.id, chosen_item.id)}
        if held:
            batches = [
                (b, held[b.id].remaining)
                for b in db.scalars(
                    select_scoped(org, StockBatch).where(StockBatch.id.in_(list(held)))
                )
            ]
            batches.sort(key=lambda pair: (pair[0].expires_on, pair[0].id))
    now = datetime.now(UTC)
    recent = recent_entries(db, org, since=now - timedelta(hours=12))
    labels = {}
    for entry in recent:
        if entry.inventory_item_id is not None:
            thing = get_scoped(db, org, InventoryItem, entry.inventory_item_id)
            labels[entry.id] = thing.name if thing else "item"
        elif entry.product_variation_id is not None:
            sized = get_scoped(db, org, ProductVariation, entry.product_variation_id)
            labels[entry.id] = f"{sized.product.name} {sized.name}" if sized else "product"
    choices = reasons(db, org)
    if not choices:
        # The defaults are written once, the first time anyone opens waste (WST-FR-02).
        choices = ensure_reasons(db, org)
        db.commit()
    return templates.TemplateResponse(
        request,
        "app/waste.html",
        {
            "title": "Waste",
            "tenant": tenant,
            "message": pop_flash(request),
            "places": places,
            "place": place,
            "items": items,
            "sizes": sizes,
            "item": chosen_item,
            "size": chosen_size,
            "batches": batches,
            "reasons": choices,
            "recent": recent,
            "labels": labels,
            "void_until": now - VOID_WINDOW,
        },
    )


@router.post(BASE)
def log_waste(
    request: Request,
    location_id: int = Form(...),
    reason_id: int | None = Form(None),
    quantity: str = Form(""),
    unit: str = Form(""),
    inventory_item_id: int | None = Form(None),
    product_variation_id: int | None = Form(None),
    batch_id: int | None = Form(None),
    note: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("waste.record")),
):
    back = (
        BASE
        + (
            f"?item={inventory_item_id}"
            if inventory_item_id
            else f"?product={product_variation_id}"
        )
        + f"&location={location_id}"
    )
    try:
        record_waste(
            db,
            org,
            location_id=location_id,
            reason_id=reason_id,
            quantity=quantity,
            unit=unit,
            inventory_item_id=inventory_item_id,
            product_variation_id=product_variation_id,
            batch_id=batch_id,
            note=note,
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
    except (WasteRuleError, ArithmeticError) as exc:
        db.rollback()
        flash(
            request, str(exc) if isinstance(exc, WasteRuleError) else "Enter a quantity.", "error"
        )
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, "Waste logged. You can void it for 30 minutes.")
    return RedirectResponse(f"{BASE}?location={location_id}", status_code=303)


@router.post(BASE + "/{event_id}/void")
def void_entry(
    request: Request,
    event_id: int,
    reason: str = Form(""),
    back: str = Form(BASE),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("waste.record")),
):
    target = back if back in (BASE, BASE + "/log") else BASE
    try:
        void_waste(db, org, event_id, reason=reason)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except WasteRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(target, status_code=303)
    db.commit()
    flash(request, "Voided. The stock is back.")
    return RedirectResponse(target, status_code=303)


@router.get(BASE + "/log", response_class=HTMLResponse)
def waste_log(
    request: Request,
    since: str = "",
    until: str = "",
    reason: int | None = None,
    item: int | None = None,
    actor: int | None = None,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("reports.operational")),
):
    """Logged waste and what counts found short, for a period (WST-FR-08..10)."""
    places = _places(db, org)
    today = _today(places[0]) if places else datetime.now(UTC).date()
    try:
        start = date.fromisoformat(since) if since else today - timedelta(days=6)
        end = date.fromisoformat(until) if until else today
    except ValueError:
        start, end = today - timedelta(days=6), today
    entries = logged(
        db,
        org,
        since=start,
        until=end,
        location_id=location,
        reason_id=reason,
        item_id=item,
        actor_id=actor,
    )
    costs = can(request, VIEW_COSTS) and can(request, "reports.financial")
    return templates.TemplateResponse(
        request,
        "app/waste_log.html",
        {
            "title": "Waste log",
            "tenant": tenant,
            "message": pop_flash(request),
            "entries": entries,
            "summary": report(loss for entry in entries for loss in entry.losses),
            "short": found_short(db, org, since=start, until=end, location_id=location),
            "filters": {
                "since": start,
                "until": end,
                "reason": reason,
                "item": item,
                "actor": actor,
                "location": location,
            },
            "places": places,
            "reasons": reasons(db, org, archived=True),
            "items": list(
                db.scalars(select_scoped(org, InventoryItem).order_by(InventoryItem.name))
            ),
            "people": {user.id: user.email for user in members(db, org)},
            "costs": costs,
            "currency": tenant_currency(db, org),
            "void_until": datetime.now(UTC) - VOID_WINDOW,
        },
    )


@router.post(BASE + "/reasons")
def new_reason(
    request: Request,
    label: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    try:
        add_reason(db, org, label, ip=client_ip(request))
    except WasteRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(BASE + "/log#reasons", status_code=303)
    db.commit()
    flash(request, "Reason added.")
    return RedirectResponse(BASE + "/log#reasons", status_code=303)


@router.post(BASE + "/reasons/{reason_id}")
def edit_reason(
    request: Request,
    reason_id: int,
    label: str = Form(""),
    active: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    try:
        change_reason(
            db,
            org,
            reason_id,
            label=label or None,
            active={"yes": True, "no": False}.get(active),
            ip=client_ip(request),
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except WasteRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(BASE + "/log#reasons", status_code=303)
    db.commit()
    flash(request, "Reason saved.")
    return RedirectResponse(BASE + "/log#reasons", status_code=303)
