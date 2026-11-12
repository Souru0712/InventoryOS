"""The signed-in product: stock, alerts, counts, reports and the Square connection."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped, update_scoped
from app.audit import record_audit
from app.config import get_settings
from app.db import get_db
from app.entitlements import require
from app.items import entered_base
from app.ledger.posting import post_count
from app.models import (
    Location,
    Promotion,
    ProviderConnection,
    StockBatch,
    StockItem,
    SyncRun,
    SyncStatus,
    Tenant,
    User,
)
from app.orchestration.alerts import proposals_for
from app.orchestration.health import current as current_health
from app.orchestration.reporting import weekly_report
from app.orchestration.sync import NotConnected, run_sync
from app.orchestration.views import (
    expiry_coverage,
    expiry_positions,
    stock_views,
    tenant_currency,
    velocity_map,
)
from app.providers.square import oauth
from app.security import (
    SESSION_STATE_KEY,
    location_filter,
    new_state,
    require_permission,
    require_tenant,
    require_user,
    require_verified,
    require_writable,
)
from app.throttle import client_ip
from app.transform.alerts import counts_as_waste_risk
from app.transform.expiry import ItemExpiry
from app.transform.inventory import days_of_cover, suggest_order_base, valuation_cents
from app.transform.items import ItemRuleError
from app.transform.ledger import LedgerRuleError
from app.units import UnitError, format_amount, format_change, to_base
from app.web.flash import flash, pop_flash
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _rows(db: Session, org: OrgContext, location_id: int | None = None) -> list[dict]:
    """One display row per stock item, with the numbers the owner needs."""
    items = stock_views(db, org, location_id)
    velocity = velocity_map(db, org)
    alerts = {a.stock_item_id: a for a in proposals_for(db, org)}

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
                # Below zero needs a count before anything else (DR-08, AC-INV-10).
                "status": "count_required"
                if item.qty_base < 0
                else (alert.kind if alert else "ok"),
            }
        )
    rows.sort(key=lambda row: (row["status"] == "ok", row["cover"] is None, row["cover"] or 0))
    return rows


# Most urgent first: gone off, will not sell in time, sell next, fine.
STATUS_ORDER = {"expired": 0, "at_risk": 1, "sell_first": 2, "no_signal": 3, "on_track": 4}


def _expiry_summary(expiries: list[ItemExpiry], horizon_days: int) -> dict:
    """Page rows plus the totals the email leads with, from the same rule the alerts use."""
    rows: list[dict[str, Any]] = []
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


def _locations(db: Session, org: OrgContext) -> list[Location]:
    return list(
        db.scalars(
            select_scoped(org, Location).where(Location.is_active.is_(True)).order_by(Location.name)
        )
    )


@router.get("/app", response_class=HTMLResponse)
def dashboard(
    request: Request,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    rows = _rows(db, org, location)
    connection = db.scalars(
        select_scoped(org, ProviderConnection).where(ProviderConnection.status == "active")
    ).first()
    last_run = db.scalars(
        select_scoped(org, SyncRun).order_by(SyncRun.started_at.desc()).limit(1)
    ).first()
    sync = _sync_status(db, org, connection, last_run)

    by_dimension: dict[str, dict] = {}
    for row in rows:
        key = row["item"].dimension.value
        bucket = by_dimension.setdefault(key, {"count": 0, "value_cents": 0, "at_risk": 0})
        bucket["count"] += 1
        bucket["value_cents"] += row["value_cents"]
        if row["status"] != "ok":
            bucket["at_risk"] += 1

    expiries = expiry_positions(db, org, location_id=location)
    expiry = _expiry_summary(expiries, get_settings().expiry_horizon_days) if expiries else None

    return templates.TemplateResponse(
        request,
        "app/dashboard.html",
        {
            "title": "Inventory",
            "message": pop_flash(request),
            "tenant": tenant,
            "rows": rows,
            "locations": _locations(db, org),
            "selected_location": location,
            "connection": connection,
            "last_run": last_run,
            "sync": sync,
            "by_dimension": by_dimension,
            "total_value": sum(row["value_cents"] for row in rows),
            "at_risk": sum(1 for row in rows if row["status"] != "ok"),
            "expiry": expiry,
            "currency": tenant_currency(db, org),
            "health": current_health(db, org, location),
        },
    )


def _sync_status(
    db: Session, org: OrgContext, connection: ProviderConnection | None, last_run: SyncRun | None
) -> dict:
    """What the merchant needs to know: is it connected, when did it last work, what broke.

    A failure is only worth showing while it is still the latest word; a later success clears it.
    """
    last_success = db.scalars(
        select_scoped(org, SyncRun)
        .where(SyncRun.status == SyncStatus.SUCCESS)
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()
    failed = last_run if last_run is not None and last_run.status is SyncStatus.FAILED else None
    return {
        "connected": connection is not None,
        "last_success_at": last_success.finished_at if last_success else None,
        "error": (failed.error or "")[:200] if failed else None,
        "failed_at": failed.finished_at if failed else None,
    }


@router.get("/app/expiring", response_class=HTMLResponse)
def expiring(
    request: Request,
    location: int | None = Depends(location_filter),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("inventory.view")),
):
    horizon = get_settings().expiry_horizon_days
    summary = _expiry_summary(expiry_positions(db, org, location_id=location), horizon)
    return templates.TemplateResponse(
        request,
        "app/expiring.html",
        {
            "title": "Expiring",
            "tenant": tenant,
            "horizon": horizon,
            "items": stock_views(db, org, location),
            "currency": tenant_currency(db, org),
            "locations": _locations(db, org),
            "selected_location": location,
            "today": datetime.now(UTC).date(),
            "coverage": expiry_coverage(db, org, location),
            **summary,
        },
    )


@router.post("/app/expiring/label")
def label_stock(
    stock_item_id: int = Form(...),
    quantity: str = Form(...),
    expires_on: str = Form(...),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("count.record")),
):
    """Date stock that is already on the shelf. The quantity does not change; it gains a date."""
    item = get_scoped(db, org, StockItem, stock_item_id)
    if item is None:
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
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    """Say whether an item can expire at all, so the coverage figure counts the right things."""
    item = get_scoped(db, org, StockItem, stock_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="stock item not found")
    item.tracks_expiry = perishable.lower() in ("yes", "true", "1")
    db.commit()
    return RedirectResponse("/app/expiring?coverage=1", status_code=303)


@router.post("/app/expiring/{batch_id}/remove")
def remove_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("count.record")),
):
    """Undo a wrongly entered date. Stock is untouched: a batch is only a date on it."""
    batch = get_scoped(db, org, StockBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    # A markdown decided against this lot stays on record; it keeps its own end date.
    db.execute(
        update_scoped(org, Promotion)
        .where(Promotion.stock_batch_id == batch.id)
        .values(stock_batch_id=None)
    )
    db.delete(batch)
    db.commit()
    return RedirectResponse("/app/expiring?removed=1", status_code=303)


@router.get("/app/items/new")
def new_item_moved(org: OrgContext = Depends(require_permission("item.edit"))):
    """The add-item form moved to the item screens (T1.14)."""
    return RedirectResponse("/app/catalog/new", status_code=303)


@router.post("/app/items/{item_id}/count")
def record_count(
    item_id: int,
    request: Request,
    quantity: str = Form(...),
    unit: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("count.record")),
):
    """A physical count is an observation (DR-12): the difference from the recorded quantity
    is posted as a count variance, never set directly. It may be entered in any unit or package
    of the item; one that cannot convert is refused (DR-11)."""
    item = get_scoped(db, org, StockItem, item_id)
    if item is None or item.inventory_item is None:
        raise HTTPException(status_code=404, detail="stock item not found")

    try:
        counted = entered_base(item.inventory_item, quantity, unit or item.stock_unit)
    except ItemRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now = datetime.now(UTC)
    count = uuid4().hex
    try:
        posted = post_count(
            db,
            org,
            location_id=item.location_id,
            inventory_item_id=item.inventory_item.id,
            counted=counted,
            source_type="count",
            source_id=count,
            idempotency_key=f"count:{count}",
            occurred_at=now,
            reason=note.strip()[:300] or "Counted on the inventory screen",
        )
    except LedgerRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item.last_counted_at = now
    # Nothing is sent to Square (C-34).
    db.commit()
    shown = format_amount(item.qty_base, item.stock_unit)
    if posted is None:
        flash(request, f"Counted {item.name}: {shown}, as recorded. Nothing to post.")
    else:
        variance = format_change(posted.movements[0].qty_base, item.stock_unit)
        flash(request, f"Counted {item.name}: {shown}. {variance} posted as a count variance.")
    return RedirectResponse("/app", status_code=303)


@router.post("/app/items/{item_id}/thresholds")
def update_thresholds(
    item_id: int,
    reorder_point: str = Form("0"),
    par_level: str = Form(""),
    lead_time_days: int = Form(2),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("item.edit")),
):
    require(tenant, "custom_thresholds")
    item = get_scoped(db, org, StockItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="stock item not found")
    try:
        reorder_base = to_base(reorder_point or "0", item.stock_unit)
        par_base = to_base(par_level, item.stock_unit) if par_level.strip() else None
        _check_thresholds(reorder_base, par_base, lead_time_days)
    except (UnitError, ValueError, ArithmeticError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item.reorder_point_base = reorder_base
    item.par_level_base = par_base
    item.lead_time_days = lead_time_days
    db.commit()
    return RedirectResponse("/app", status_code=303)


def _check_thresholds(reorder_base: Decimal, par_base: Decimal | None, lead_time_days: int) -> None:
    """The database refuses negative thresholds (T1.01); say so before it has to."""
    if reorder_base < 0 or (par_base is not None and par_base < 0):
        raise ValueError("Reorder point and par level cannot be negative.")
    if lead_time_days < 0:
        raise ValueError("Lead time cannot be negative.")


@router.get("/app/report", response_class=HTMLResponse)
def report(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("reports.financial")),
):
    items = {item.id: item for item in stock_views(db, org)}
    return templates.TemplateResponse(
        request,
        "app/report.html",
        {
            "title": "Weekly report",
            "tenant": tenant,
            "report": weekly_report(db, org),
            "items": items,
        },
    )


@router.post("/app/sync")
def manual_sync(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("mapping.manage")),
):
    try:
        run_sync(db, org)
    except NotConnected:
        return RedirectResponse("/app?error=not_connected", status_code=303)
    except Exception:
        log.exception("manual sync failed for tenant %s", tenant.id)
        return RedirectResponse("/app?error=sync_failed", status_code=303)
    return RedirectResponse("/app", status_code=303)


@router.get("/connect/square")
def connect_square(
    request: Request,
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("pos.manage")),
    _verified: User = Depends(require_verified),
):
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
    org: OrgContext = Depends(require_permission("pos.manage")),
    _verified: User = Depends(require_verified),
):
    expected = request.session.pop(SESSION_STATE_KEY, None)
    if error:
        raise HTTPException(status_code=400, detail=f"square returned an error: {error}")
    if not code or not state or state != expected:
        raise HTTPException(status_code=400, detail="the connection link is invalid or expired")

    token = oauth.exchange_code(code)
    existing = db.scalars(
        select_scoped(org, ProviderConnection).where(
            ProviderConnection.provider == "square",
            ProviderConnection.merchant_id == token["merchant_id"],
        )
    ).first()
    connection = existing or ProviderConnection(
        tenant_id=org.tenant_id, provider="square", merchant_id=token["merchant_id"]
    )
    connection.access_token_enc = oauth.encrypt(token["access_token"])
    connection.refresh_token_enc = (
        oauth.encrypt(token["refresh_token"]) if token["refresh_token"] else None
    )
    connection.expires_at = token["expires_at"]
    was_active = existing is not None and existing.status == "active"
    connection.status = "active"
    db.add(connection)
    db.flush()
    record_audit(
        db,
        org,
        "connection.connected",
        "Reconnected Square" if was_active else "Connected Square",
        subject_type="provider_connection",
        subject_id=connection.id,
        after={"provider": "square", "merchant": token["merchant_id"]},
        ip=client_ip(request),
    )
    db.commit()

    try:
        run_sync(db, org)
    except Exception:
        log.exception("first sync after connect failed for tenant %s", org.tenant_id)
        return RedirectResponse("/app?error=first_sync_failed", status_code=303)
    return RedirectResponse("/app?connected=1", status_code=303)
