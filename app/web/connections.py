"""The POS connection screen (T3.02; SQ-04, POS-FR-01): /app/connection.

Status, the POS's locations, the last successful sync and the last error; reconnect and
disconnect. Provider-neutral: everything goes through `app/connections.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.connections import NotConnected, current, disconnect, name_of, sync_health
from app.db import get_db
from app.models import Location, SyncRun, Tenant, User
from app.order_ingestion import order_summary
from app.pos_catalog import MappingError, NotFound, external_locations, summary, track_location
from app.sales_status import connection_health
from app.security import require_permission, require_tenant, require_verified
from app.throttle import client_ip
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
PAGE = "/app/connection"
PROVIDER = "square"
ERRORS = {
    "merchant_taken": "That Square account is already connected to another business. Disconnect "
    "it there first, or connect a different account.",
    "exchange_failed": "Square did not complete the connection. Try again.",
}


@router.get(PAGE, response_class=HTMLResponse)
def connection_page(
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("pos.manage")),
):
    connection = current(db, org, PROVIDER)
    last_run = db.scalars(
        select_scoped(org, SyncRun).order_by(SyncRun.started_at.desc()).limit(1)
    ).first()
    own = list(
        db.scalars(
            select_scoped(org, Location)
            .where(Location.archived_at.is_(None), Location.is_active.is_(True))
            .order_by(Location.name)
        )
    )
    active = connection is not None and connection.status == "active"
    return templates.TemplateResponse(
        request,
        "app/connection.html",
        {
            "title": "POS connection",
            "tenant": tenant,
            "message": pop_flash(request),
            "error": ERRORS.get(error or ""),
            "provider": name_of(PROVIDER),
            "connection": connection,
            "health": sync_health(db, org, connection, last_run),
            # Square's locations, each with the business's location tracking it (POS-FR-03).
            "places": external_locations(db, org, connection) if connection else [],
            "own_locations": own,
            "can_map": active,
            "catalog": summary(db, org, connection) if connection else None,
            "orders": order_summary(db, org, connection) if connection else None,
            "integration": connection_health(db, org, connection, datetime.now(UTC))
            if connection and connection.status in ("active", "revoked")
            else None,
        },
    )


KINDS = {
    "full": "Hourly sync",
    "catalog": "Catalog",
    "orders": "Orders",
    "reconciliation": "Daily check",
}


def _summary(run: SyncRun) -> str:
    """What a run did, in a few words (POS-FR-10's sync history)."""
    try:
        stats = json.loads(run.stats_json or "{}")
    except ValueError:
        return ""
    if run.kind == "reconciliation":
        checked, off = stats.get("checked") or [], stats.get("mismatched", 0)
        return f"{len(checked)} day(s) checked, {off} differed" if checked else "nothing to check"
    if run.kind == "orders":
        return f"{stats.get('fetched', 0)} order(s) read, {stats.get('sales', 0)} recorded" + (
            " (hourly re-read)" if stats.get("reread") else ""
        )
    return ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in stats.items() if v)[:160]


@router.get(PAGE + "/history", response_class=HTMLResponse)
def sync_history(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("pos.manage")),
):
    """Every recent read, check and failure, newest first, with what each did (POS-FR-10)."""
    runs = list(
        db.scalars(select_scoped(org, SyncRun).order_by(SyncRun.started_at.desc()).limit(50))
    )
    return templates.TemplateResponse(
        request,
        "app/sync_history.html",
        {
            "title": "Sync history",
            "tenant": tenant,
            "provider": name_of(PROVIDER),
            "runs": [(run, KINDS.get(run.kind, run.kind), _summary(run)) for run in runs],
        },
    )


@router.post(f"{PAGE}/disconnect")
def disconnect_pos(
    request: Request,
    db: Session = Depends(get_db),
    org: OrgContext = Depends(require_permission("pos.manage")),
    _verified: User = Depends(require_verified),
):
    try:
        confirmed = disconnect(db, org, PROVIDER, ip=client_ip(request))
    except NotConnected:
        flash(request, "Square is not connected.", "error")
        return RedirectResponse(PAGE, status_code=303)
    db.commit()
    if confirmed:
        flash(
            request,
            "Square is disconnected. Your history is kept. To enter sales by hand, choose where "
            "each location's sales come from on the Locations page.",
        )
    else:
        flash(
            request,
            "Disconnected here, but Square could not be reached to revoke access: remove the app "
            "under Apps in your Square Dashboard.",
            "error",
        )
    return RedirectResponse(PAGE, status_code=303)


@router.post(PAGE + "/locations/{external_location_id}")
def track_pos_location(
    request: Request,
    external_location_id: int,
    location_id: str = Form(""),
    db: Session = Depends(get_db),
    org: OrgContext = Depends(require_permission("pos.manage")),
):
    """Which of the business's locations tracks a Square location; none leaves it untracked."""
    try:
        chosen = int(location_id) if location_id.strip() else None
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="location not found") from exc
    try:
        changed = track_location(db, org, external_location_id, chosen, ip=client_ip(request))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="location not found") from exc
    except MappingError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(PAGE, status_code=303)
    db.commit()
    flash(request, "Saved." if changed else "Nothing changed.")
    return RedirectResponse(PAGE, status_code=303)
