"""Count session screens (T1.23-b; REC-FR-01, -02, -09, WF-09, AC-REC-01, -05).

Start a count, count on a phone in storage order (packages plus loose), post it once, and read
past counts with their totals and line detail. Counting takes `count.record`; anyone who can see
inventory may read the history, with amounts only for roles with cost access.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped, team
from app.access.permissions import VIEW_COSTS
from app.counts import NotFound, count_items, get_count, post_count, record_line, start_count
from app.counts import summaries as count_summaries
from app.db import get_db
from app.items import categories
from app.models import InventoryItem, ItemLocation, Location, Tenant
from app.security import require_permission, require_tenant, require_writable
from app.transform.counts import REASONS, SCOPES, CountRuleError
from app.transform.items import ItemRuleError
from app.units import UNITS_BY_DIMENSION
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()


def _count(db: Session, org: OrgContext, count_id: int):
    try:
        return get_count(db, org, count_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="count not found") from exc


@router.get("/app/counts", response_class=HTMLResponse)
def counts_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    locations = sorted(
        (loc for loc in db.scalars(select_scoped(org, Location)) if loc.archived_at is None),
        key=lambda loc: loc.name,
    )
    settings = list(db.scalars(select_scoped(org, ItemLocation)))
    areas = sorted({s.storage_area for s in settings if s.storage_area})
    items = sorted(
        db.scalars(select_scoped(org, InventoryItem).where(InventoryItem.is_active.is_(True))),
        key=lambda item: item.name.casefold(),
    )
    return templates.TemplateResponse(
        request,
        "app/counts.html",
        {
            "title": "Counts",
            "tenant": tenant,
            "summaries": count_summaries(db, org),
            "locations": locations,
            "scopes": SCOPES,
            "categories": categories(db, org),
            "areas": areas,
            "items": items,
            "costs": org.can(VIEW_COSTS),
            "currency": locations[0].currency if locations else "USD",
            "message": pop_flash(request),
        },
    )


@router.post("/app/counts")
def start_count_route(
    request: Request,
    location_id: int = Form(...),
    scope: str = Form("full"),
    category_id: str = Form(""),
    area: str = Form(""),
    item_id: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("count.record")),
):
    """Open a session: everything at a location, a category, a storage area or one item."""
    ref = {"category": category_id, "area": area, "item": item_id}.get(scope)
    try:
        count = start_count(db, org, location_id=location_id, scope=scope, scope_ref=ref)
    except NotFound as exc:
        # Another business's location, category or item reaches nothing (C-33).
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CountRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse("/app/counts", status_code=303)
    db.commit()
    return RedirectResponse(f"/app/counts/{count.id}", status_code=303)


@router.get("/app/counts/{count_id}", response_class=HTMLResponse)
def count_page(
    request: Request,
    count_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    count = _count(db, org, count_id)
    location = get_scoped(db, org, Location, count.location_id)
    entries = count_items(db, org, count)
    lines = {line.inventory_item_id: line for line in count.lines}
    names = {entry.item.id: entry.item for entry in entries}
    # Items counted but since moved out of the session's scope still show in its record.
    missing = [
        line.inventory_item_id for line in count.lines if line.inventory_item_id not in names
    ]
    if missing:
        names.update(
            {
                item.id: item
                for item in db.scalars(
                    select_scoped(org, InventoryItem).where(InventoryItem.id.in_(missing))
                )
            }
        )
    people = {user.id: user.email for _membership, user in team(db, org)}
    return templates.TemplateResponse(
        request,
        "app/count_session.html",
        {
            "title": f"Count {count.id}",
            "tenant": tenant,
            "count": count,
            "location": location,
            "entries": entries,
            "lines": lines,
            "items": names,
            "units": {
                d: [(u.code, u.label) for u in UNITS_BY_DIMENSION[d]] for d in UNITS_BY_DIMENSION
            },
            "reasons": REASONS,
            "scopes": SCOPES,
            "people": people,
            "costs": org.can(VIEW_COSTS),
            "currency": location.currency if location else "USD",
            "may_count": org.can("count.record"),
            "totals": next(
                (s for s in count_summaries(db, org, count.location_id) if s.count.id == count.id),
                None,
            ),
            "message": pop_flash(request),
        },
    )


@router.post("/app/counts/{count_id}/lines")
def record_line_route(
    request: Request,
    count_id: int,
    item_id: int = Form(...),
    pack_quantity: str = Form(""),
    pack: str = Form(""),
    quantity: str = Form(""),
    unit: str = Form(""),
    reason: str = Form(""),
    next_item: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("count.record")),
):
    """One item's count: packages plus loose (REC-FR-02). Counting it again replaces it."""
    count = _count(db, org, count_id)
    parts = [(pack_quantity, pack)] if pack and pack_quantity.strip() else []
    if quantity.strip() or not parts:
        parts.append((quantity, unit))
    try:
        record_line(db, org, count.id, item_id, parts, reason=reason or None)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="item not in this count") from exc
    except (CountRuleError, ItemRuleError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(f"/app/counts/{count_id}#item-{item_id}", status_code=303)
    db.commit()
    anchor = f"#item-{next_item}" if next_item.isdigit() else "#post"
    return RedirectResponse(f"/app/counts/{count_id}{anchor}", status_code=303)


@router.post("/app/counts/{count_id}/post")
def post_count_route(
    request: Request,
    count_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("count.record")),
):
    """Post the session once: variances become movements; large ones go to review."""
    count = _count(db, org, count_id)
    try:
        post_count(db, org, count.id)
    except CountRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(f"/app/counts/{count_id}", status_code=303)
    db.commit()
    count = _count(db, org, count_id)
    moved = [line for line in count.lines if line.movement_id is not None]
    review = sum(1 for line in count.lines if line.needs_review)
    message = f"Count {count.id} posted: {len(count.lines)} counted, {len(moved)} adjusted"
    if review:
        message += f", {review} sent for review"
    flash(request, message + ".")
    return RedirectResponse(f"/app/counts/{count_id}", status_code=303)
