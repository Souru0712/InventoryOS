"""Asking for the business to be deleted, and taking it back (T4.06, ADR-0093; ADM-FR-12,
SAAS-D-04, AC-ADM-05). Owners only (`account.delete`), the password re-entered. Both work in every
subscription state (listed in tests/test_lifecycle.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.config import get_settings
from app.db import get_db
from app.deletion import DeletionRefused, cancel_deletion, request_deletion
from app.models import Tenant, User
from app.security import require_permission, require_tenant, require_user
from app.throttle import client_ip
from app.web.flash import flash
from app.web.templating import templates

router = APIRouter()
PAGE = "/app/settings/deletion"


def _page(request: Request, tenant: Tenant, error: str | None = None, status_code: int = 200):
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "app/deletion.html",
        {
            "title": "Delete the business",
            "tenant": tenant,
            "error": error,
            "window_days": settings.deletion_window_days,
            "retention_days": settings.retention_days,
        },
        status_code=status_code,
    )


@router.get(PAGE, response_class=HTMLResponse)
def deletion_page(
    request: Request,
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("account.delete")),
):
    return _page(request, tenant)


@router.post(PAGE)
def ask_for_deletion(
    request: Request,
    password: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("account.delete")),
):
    try:
        request_deletion(db, org, user, password, ip=client_ip(request))
    except DeletionRefused as exc:
        db.commit()  # the failed attempt is counted
        return _page(request, tenant, str(exc), status_code=400)
    db.commit()
    flash(request, "The business will be deleted after the waiting period. Every Owner was told.")
    return RedirectResponse(PAGE, status_code=303)


@router.post(PAGE + "/cancel")
def keep_business(
    request: Request,
    db: Session = Depends(get_db),
    org: OrgContext = Depends(require_permission("account.delete")),
):
    if cancel_deletion(db, org, ip=client_ip(request)):
        db.commit()
        flash(request, "The deletion is cancelled. Nothing was deleted.")
    return RedirectResponse(PAGE, status_code=303)
