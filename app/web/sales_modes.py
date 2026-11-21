"""Where a location's sales come from, and switching it (T3.10; WF-25, POS-FR-19, STD-FR-11).

Owner only: the switch changes what the business is billed for (PD-39, T4.02), so it takes
`subscription.manage`. A preview first says which counted sales stop counting and which start.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.connections import current, name_of
from app.db import get_db
from app.models import Tenant
from app.sales_modes import (
    ModeRuleError,
    NotFound,
    label,
    preview,
    switch,
)
from app.security import require_permission, require_tenant
from app.throttle import client_ip
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
PROVIDER = "square"
CHOICES = {
    "pos": ("pos_adapter", PROVIDER),
    "hand": ("manual", None),
    "file": ("file_import", None),
}


def _day(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _choice(raw: str | None) -> tuple[str, str | None]:
    if raw not in CHOICES:
        raise HTTPException(status_code=404, detail="no such source")
    return CHOICES[raw]


@router.get("/app/locations/{location_id}/sales-source", response_class=HTMLResponse)
def sales_source_page(
    request: Request,
    location_id: int,
    to: str = "hand",
    cutover: str | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("subscription.manage")),
):
    kind, provider = _choice(to)
    error, shown = None, None
    try:
        shown = preview(db, org, location_id, kind, provider, _day(cutover))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="location not found") from exc
    except ModeRuleError as exc:
        error = str(exc)
    connection = current(db, org, PROVIDER)
    return templates.TemplateResponse(
        request,
        "app/sales_source.html",
        {
            "title": "Where sales come from",
            "tenant": tenant,
            "message": pop_flash(request),
            "location_id": location_id,
            "to": to,
            "error": error,
            "preview": shown,
            "target": label(kind, provider),
            "provider": name_of(PROVIDER),
            "pos_ready": connection is not None and connection.status == "active",
        },
    )


@router.post("/app/locations/{location_id}/sales-source")
def sales_source_switch(
    request: Request,
    location_id: int,
    to: str = Form("hand"),
    cutover: str = Form(""),
    db: Session = Depends(get_db),
    org: OrgContext = Depends(require_permission("subscription.manage")),
):
    kind, provider = _choice(to)
    day = _day(cutover)
    back = f"/app/locations/{location_id}/sales-source?to={to}"
    try:
        if day is None:
            raise ModeRuleError("Choose the first business date.")
        shown = switch(db, org, location_id, kind, provider, day, ip=client_ip(request))
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="location not found") from exc
    except ModeRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(
        request,
        f"{shown.location.name} takes its sales from {label(kind, provider)} from {day:%d %b %Y}.",
    )
    return RedirectResponse(back, status_code=303)
