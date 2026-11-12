"""The exceptions screen (T1.19; REC-FR-11..16, WF-27): what needs a person, and acting on it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.db import get_db
from app.exceptions import NotFound, acknowledge, dismiss, listing
from app.models import Tenant
from app.orchestration.views import tenant_currency
from app.security import require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.transform.exceptions import ExceptionRuleError
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()

# Where each kind is fixed. Fixing clears the condition; the next evaluation resolves it.
FIX_LINKS = {
    "out_of_stock": "/app/receiving",
    "low_stock": "/app/reorder",
    "stale_count": "/app",
    "negative_balance": "/app",
    "batch_shortfall": "/app",
    "waste_risk": "/app/expiring",
    "batch_expired_unresolved": "/app/expiring",
}


@router.get("/app/exceptions", response_class=HTMLResponse)
def exceptions_page(
    request: Request,
    status: str = "open",
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("exception.view")),
):
    closed = status == "closed"
    return templates.TemplateResponse(
        request,
        "app/exceptions.html",
        {
            "title": "Exceptions",
            "tenant": tenant,
            "rows": listing(db, org, closed=closed),
            "closed": closed,
            "fix_links": FIX_LINKS,
            "currency": tenant_currency(db, org),
            "message": pop_flash(request),
        },
    )


def _act(request: Request, done: str, action) -> RedirectResponse:
    try:
        action()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="exception not found") from exc
    except ExceptionRuleError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/app/exceptions", status_code=303)
    flash(request, done)
    return RedirectResponse("/app/exceptions", status_code=303)


@router.post("/app/exceptions/{exception_id}/acknowledge")
def acknowledge_exception(
    exception_id: int,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("exception.resolve")),
):
    """Say someone is on it. It stays on the list until fixed or dismissed."""

    def act() -> None:
        acknowledge(db, org, exception_id, ip=client_ip(request))
        db.commit()

    return _act(request, "Acknowledged. It stays here until it is fixed or dismissed.", act)


@router.post("/app/exceptions/{exception_id}/dismiss")
def dismiss_exception(
    exception_id: int,
    request: Request,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("exception.resolve")),
):
    """Close it without a fix, saying why (AC-REC-08). No quantity changes."""

    def act() -> None:
        dismiss(db, org, exception_id, reason, ip=client_ip(request))
        db.commit()

    return _act(request, "Dismissed.", act)
