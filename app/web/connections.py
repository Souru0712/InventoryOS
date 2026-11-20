"""The POS connection screen (T3.02; SQ-04, POS-FR-01): /app/connection.

Status, the POS's locations, the last successful sync and the last error; reconnect and
disconnect. Provider-neutral: everything goes through `app/connections.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.connections import NotConnected, current, disconnect, name_of, sync_health
from app.db import get_db
from app.models import Location, SyncRun, Tenant, User
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
    locations = list(
        db.scalars(
            select_scoped(org, Location)
            .where(Location.provider == PROVIDER)
            .order_by(Location.name)
        )
    )
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
            "locations": locations,
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
        flash(request, "Square is disconnected. Your history is kept.")
    else:
        flash(
            request,
            "Disconnected here, but Square could not be reached to revoke access: remove the app "
            "under Apps in your Square Dashboard.",
            "error",
        )
    return RedirectResponse(PAGE, status_code=303)
