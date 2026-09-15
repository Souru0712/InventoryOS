"""The signed-in product: stock, alerts, counts, reports and the Square connection."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import (
    InventoryCount,
    Location,
    Promotion,
    ProviderConnection,
    StockBatch,
    StockItem,
    SyncRun,
    Tenant,
    TrackMode,
    User,
)
from app.orchestration.alerts import proposals_for
from app.orchestration.health import current as current_health
from app.orchestration.reporting import weekly_report
from app.orchestration.scanning import CodeInUse, link_code
from app.orchestration.sync import NotConnected, push_counts_to_square, run_sync
from app.orchestration.views import (
    expiry_coverage,
    expiry_positions,
    refresh_derived_quantities,
    stock_views,
    tenant_currency,
    velocity_map,
)
from app.providers.square import oauth
from app.security import SESSION_STATE_KEY, new_state, require_tenant, require_user
from app.transform.alerts import counts_as_waste_risk
from app.transform.expiry import ItemExpiry
from app.transform.inventory import days_of_cover, suggest_order_base, valuation_cents
from app.units import Dimension, UnitError, to_base
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _rows(db: Session, tenant_id: int, location_id: int | None = None) -> list[dict]:
    """One display row per stock item, with the numbers the owner needs."""
    items = stock_views(db, tenant_id, location_id)
    velocity = velocity_map(db, tenant_id)
    alerts = {a.stock_item_id: a for a in proposals_for(db, tenant_id)}

    rows = []
    for item in items:
        speed = velocity.get(item.id, Decimal("0"))
        alert = alerts.get(item.id)
        rows.append(
            {
                "item": item,
                "velocity": speed,
                "cover": days_of_cover(item.qty_base, speed),
                "value_cents": valuation_cents(item.qty_base, item.cost_per_base_cents),
                "suggested": suggest_order_base(item, speed),
                "alert": alert,
                "status": alert.kind if alert else "ok",
            }
        )
    rows.sort(key=lambda row: (row["status"] == "ok", row["cover"] is None, row["cover"] or 0))
    return rows


# Most urgent first: gone off, will not sell in time, sell next, fine.
STATUS_ORDER = {"expired": 0, "at_risk": 1, "sell_first": 2, "no_signal": 3, "on_track": 4}


def _expiry_summary(expiries: list[ItemExpiry], horizon_days: int) -> dict:
    """Page rows plus the totals the email leads with, from the same rule the alerts use."""
    rows = []
    for expiry in expiries:
        for position in expiry.positions:
            if position.remaining_base <= 0:
                continue
            if position.expired:
                status = "expired"
            elif counts_as_waste_risk(position, horizon_days):
                status = "at_risk"
            else:
                status = position.status
            rows.append({"item": expiry.item, "position": position, "status": status})
    rows.sort(key=lambda r: (STATUS_ORDER.get(r["status"], 9), r["position"].batch.expires_on))
    risky = [r for r in rows if r["status"] == "at_risk"]
    expired = [r for r in rows if r["status"] == "expired"]
    return {
        "rows": rows,
        "at_risk_cents": sum(r["position"].at_risk_cents for r in risky),
        "at_risk_products": len({r["item"].id for r in risky}),
        "expired_cents": sum(r["position"].at_risk_cents for r in expired),
        "expired_products": len({r["item"].id for r in expired}),
    }


def _locations(db: Session, tenant_id: int) -> list[Location]:
    return list(
        db.scalars(
            select(Location)
            .where(Location.tenant_id == tenant_id, Location.is_active.is_(True))
            .order_by(Location.name)
        )
    )


@router.get("/app", response_class=HTMLResponse)
def dashboard(
    request: Request,
    location: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    rows = _rows(db, tenant.id, location)
    connection = db.scalars(
        select(ProviderConnection).where(
            ProviderConnection.tenant_id == tenant.id, ProviderConnection.status == "active"
        )
    ).first()
    last_run = db.scalars(
        select(SyncRun)
        .where(SyncRun.tenant_id == tenant.id)
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()

    by_dimension: dict[str, dict] = {}
    for row in rows:
        key = row["item"].dimension.value
        bucket = by_dimension.setdefault(key, {"count": 0, "value_cents": 0, "at_risk": 0})
        bucket["count"] += 1
        bucket["value_cents"] += row["value_cents"]
        if row["status"] != "ok":
            bucket["at_risk"] += 1

    expiries = expiry_positions(db, tenant.id, location_id=location)
    expiry = _expiry_summary(expiries, get_settings().expiry_horizon_days) if expiries else None

    return templates.TemplateResponse(
        request,
        "app/dashboard.html",
        {
            "title": "Inventory",
            "tenant": tenant,
            "rows": rows,
            "locations": _locations(db, tenant.id),
            "selected_location": location,
            "connection": connection,
            "last_run": last_run,
            "by_dimension": by_dimension,
            "total_value": sum(row["value_cents"] for row in rows),
            "at_risk": sum(1 for row in rows if row["status"] != "ok"),
            "expiry": expiry,
            "currency": tenant_currency(db, tenant.id),
            "health": current_health(db, tenant.id, location),
        },
    )


@router.get("/app/expiring", response_class=HTMLResponse)
def expiring(
    request: Request,
    location: int | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    horizon = get_settings().expiry_horizon_days
    summary = _expiry_summary(expiry_positions(db, tenant.id, location_id=location), horizon)
    return templates.TemplateResponse(
        request,
        "app/expiring.html",
        {
            "title": "Expiring",
            "tenant": tenant,
            "horizon": horizon,
            "items": stock_views(db, tenant.id, location),
            "currency": tenant_currency(db, tenant.id),
            "locations": _locations(db, tenant.id),
            "selected_location": location,
            "today": datetime.now(timezone.utc).date(),
            "coverage": expiry_coverage(db, tenant.id, location),
            **summary,
        },
    )


@router.post("/app/expiring/label")
def label_stock(
    stock_item_id: int = Form(...),
    quantity: str = Form(...),
    expires_on: str = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    """Date stock that is already on the shelf. The quantity does not change; it gains a date."""
    item = db.get(StockItem, stock_item_id)
    if item is None or item.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="stock item not found")
    try:
        qty_base = to_base(quantity, item.stock_unit)
        expiry = date.fromisoformat(expires_on)
    except (UnitError, ValueError, ArithmeticError):
        return RedirectResponse("/app/expiring?error=invalid", status_code=303)
    if qty_base <= 0:
        return RedirectResponse("/app/expiring?error=invalid", status_code=303)

    db.add(
        StockBatch(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            received_qty_base=qty_base,
            expires_on=expiry,
            source="label",
        )
    )
    db.commit()
    return RedirectResponse("/app/expiring?labelled=1", status_code=303)


@router.post("/app/expiring/perishable")
def set_perishable(
    stock_item_id: int = Form(...),
    perishable: str = Form("no"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    """Say whether an item can expire at all, so the coverage figure counts the right things."""
    item = db.get(StockItem, stock_item_id)
    if item is None or item.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="stock item not found")
    item.tracks_expiry = perishable.lower() in ("yes", "true", "1")
    db.commit()
    return RedirectResponse("/app/expiring?coverage=1", status_code=303)

@router.post("/app/expiring/{batch_id}/remove")
def remove_batch(
    batch_id: int, db: Session = Depends(get_db), tenant: Tenant = Depends(require_tenant)
):
    """Undo a wrongly entered date. Stock is untouched: a batch is only a date on it."""
    batch = db.get(StockBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    # A markdown decided against this lot stays on record; it keeps its own end date.
    db.execute(update(Promotion).where(Promotion.stock_batch_id == batch.id).values(stock_batch_id=None))
    db.delete(batch)
    db.commit()
    return RedirectResponse("/app/expiring?removed=1", status_code=303)


@router.get("/app/items/new", response_class=HTMLResponse)
def new_item_form(
    request: Request, db: Session = Depends(get_db), tenant: Tenant = Depends(require_tenant)
):
    return templates.TemplateResponse(
        request,
        "app/item_form.html",
        {"title": "Add stock item", "tenant": tenant, "locations": _locations(db, tenant.id), "item": None},
    )


@router.post("/app/items/new")
def create_item(
    request: Request,
    name: str = Form(...),
    location_id: int = Form(...),
    dimension: str = Form(...),
    stock_unit: str = Form(...),
    quantity: str = Form("0"),
    reorder_point: str = Form("0"),
    par_level: str = Form(""),
    cost_per_unit: str = Form("0"),
    retail_price: str = Form(""),
    receive_unit: str = Form("each"),
    pack_size: str = Form(""),
    supplier: str = Form(""),
    lead_time_days: int = Form(2),
    category: str = Form(""),
    sku: str = Form(""),
    upc: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    from app.load.upsert import slugify

    location = db.get(Location, location_id)
    if location is None or location.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="location not found")

    try:
        dimension_enum = Dimension(dimension)
        qty_base = to_base(quantity or "0", stock_unit)
        reorder_base = to_base(reorder_point or "0", stock_unit)
        par_base = to_base(par_level, stock_unit) if par_level.strip() else None
        pack_base = to_base(pack_size, stock_unit) if pack_size.strip() else None
        # Cost is entered per display unit; store it per base unit.
        cost_base = (Decimal(cost_per_unit or "0") * 100) / to_base("1", stock_unit)
        retail_base = (
            (Decimal(retail_price) * 100) / to_base("1", stock_unit)
            if retail_price.strip()
            else None
        )
    except (UnitError, ValueError, ArithmeticError) as exc:
        return templates.TemplateResponse(
            request,
            "app/item_form.html",
            {
                "title": "Add stock item",
                "tenant": tenant,
                "locations": _locations(db, tenant.id),
                "item": None,
                "error": str(exc),
            },
            status_code=400,
        )

    now = datetime.now(timezone.utc)
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug=slugify(name),
        name=name.strip(),
        category=category.strip() or None,
        dimension=dimension_enum,
        stock_unit=stock_unit,
        track_mode=TrackMode.DERIVED if dimension_enum is not Dimension.COUNT else TrackMode.MANUAL,
        qty_base=qty_base,
        reorder_point_base=reorder_base,
        par_level_base=par_base,
        cost_per_base_cents=cost_base,
        retail_per_base_cents=retail_base,
        receive_unit=receive_unit,
        pack_size_base=pack_base,
        supplier=supplier.strip() or None,
        lead_time_days=lead_time_days,
        last_counted_at=now,
    )
    db.add(item)
    db.flush()
    for raw in (upc, sku):
        if raw.strip():
            try:
                link_code(db, tenant.id, item.id, raw, source="manual")
            except CodeInUse:
                log.info("code %r already identifies another item for tenant %s", raw, tenant.id)
    db.add(
        InventoryCount(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            qty_base=qty_base,
            source="opening",
            note="opening count",
            counted_at=now,
        )
    )
    db.commit()
    return RedirectResponse("/app", status_code=303)


@router.post("/app/items/{item_id}/count")
def record_count(
    item_id: int,
    quantity: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    """A physical count re-anchors the item; derived quantities restart from here."""
    item = db.get(StockItem, item_id)
    if item is None or item.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="stock item not found")

    qty_base = to_base(quantity, item.stock_unit)
    now = datetime.now(timezone.utc)
    item.qty_base = qty_base
    item.last_counted_at = now
    db.add(
        InventoryCount(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            qty_base=qty_base,
            source="manual",
            note=note.strip()[:500] or None,
            counted_at=now,
        )
    )
    db.flush()

    if item.track_mode == TrackMode.PROVIDER:
        try:
            push_counts_to_square(db, tenant.id, [item.id])
        except NotConnected:
            log.info("tenant %s not connected; count kept locally", tenant.id)
        except Exception:
            log.exception("count push failed for item %s", item.id)
    db.commit()
    return RedirectResponse("/app", status_code=303)


@router.post("/app/items/{item_id}/thresholds")
def update_thresholds(
    item_id: int,
    reorder_point: str = Form("0"),
    par_level: str = Form(""),
    lead_time_days: int = Form(2),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
):
    item = db.get(StockItem, item_id)
    if item is None or item.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="stock item not found")
    item.reorder_point_base = to_base(reorder_point or "0", item.stock_unit)
    item.par_level_base = to_base(par_level, item.stock_unit) if par_level.strip() else None
    item.lead_time_days = lead_time_days
    db.commit()
    return RedirectResponse("/app", status_code=303)


@router.get("/app/report", response_class=HTMLResponse)
def report(
    request: Request, db: Session = Depends(get_db), tenant: Tenant = Depends(require_tenant)
):
    items = {item.id: item for item in stock_views(db, tenant.id)}
    return templates.TemplateResponse(
        request,
        "app/report.html",
        {
            "title": "Weekly report",
            "tenant": tenant,
            "report": weekly_report(db, tenant.id),
            "items": items,
        },
    )


@router.post("/app/sync")
def manual_sync(db: Session = Depends(get_db), tenant: Tenant = Depends(require_tenant)):
    try:
        run_sync(db, tenant.id)
    except NotConnected:
        return RedirectResponse("/app?error=not_connected", status_code=303)
    except Exception:
        log.exception("manual sync failed for tenant %s", tenant.id)
        return RedirectResponse("/app?error=sync_failed", status_code=303)
    return RedirectResponse("/app", status_code=303)


@router.post("/app/recalculate")
def recalculate(db: Session = Depends(get_db), tenant: Tenant = Depends(require_tenant)):
    refresh_derived_quantities(db, tenant.id)
    db.commit()
    return RedirectResponse("/app", status_code=303)


@router.get("/connect/square")
def connect_square(request: Request, tenant: Tenant = Depends(require_tenant)):
    state = new_state()
    request.session[SESSION_STATE_KEY] = state
    return RedirectResponse(oauth.authorize_url(state), status_code=303)


@router.get("/connect/square/callback")
def square_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    expected = request.session.pop(SESSION_STATE_KEY, None)
    if error:
        raise HTTPException(status_code=400, detail=f"square returned an error: {error}")
    if not code or not state or state != expected:
        raise HTTPException(status_code=400, detail="the connection link is invalid or expired")

    token = oauth.exchange_code(code)
    existing = db.scalars(
        select(ProviderConnection).where(
            ProviderConnection.tenant_id == user.tenant_id,
            ProviderConnection.provider == "square",
            ProviderConnection.merchant_id == token["merchant_id"],
        )
    ).first()
    connection = existing or ProviderConnection(
        tenant_id=user.tenant_id, provider="square", merchant_id=token["merchant_id"]
    )
    connection.access_token_enc = oauth.encrypt(token["access_token"])
    connection.refresh_token_enc = (
        oauth.encrypt(token["refresh_token"]) if token["refresh_token"] else None
    )
    connection.expires_at = token["expires_at"]
    connection.status = "active"
    db.add(connection)
    db.commit()

    try:
        run_sync(db, user.tenant_id)
    except Exception:
        log.exception("first sync after connect failed for tenant %s", user.tenant_id)
        return RedirectResponse("/app?error=first_sync_failed", status_code=303)
    return RedirectResponse("/app?connected=1", status_code=303)
