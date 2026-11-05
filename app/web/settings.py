"""Account settings: which emails this person gets, and why one might have stopped."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.config import get_settings
from app.db import get_db
from app.entitlements import state
from app.models import Tenant, User
from app.notify.email import suppression
from app.security import require_tenant, require_user
from app.web.inputs import posted_form
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

# (form field, attribute, what it is, what it means)
EMAIL_SETTINGS = (
    (
        "stock_alerts",
        "wants_stock_alerts",
        "Inventory alerts",
        "Something is low, out, or has not been counted in a while.",
    ),
    (
        "expiry_alerts",
        "wants_expiry_alerts",
        "Expiration alerts",
        "Stock that will not sell before its date, and stock already past it.",
    ),
    (
        "weekly_report",
        "wants_weekly_report",
        "Weekly report",
        "Sales, stock value and what to order, once a week.",
    ),
)


@router.get("/app/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
):
    return templates.TemplateResponse(
        request,
        "app/settings.html",
        {
            "title": "Settings",
            "tenant": tenant,
            "user": user,
            "email_settings": EMAIL_SETTINGS,
            "suppressed": suppression(db, user.email),
            "support_email": get_settings().support_email,
            "account": state(tenant),
            "billing_configured": bool(get_settings().stripe_secret_key),
        },
    )


@router.post("/app/settings/email")
def save_email_settings(
    request: Request,
    form: FormData = Depends(posted_form),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
):
    """Checkboxes only post when ticked, so an absent field means the person turned it off."""
    for field, attribute, _label, _description in EMAIL_SETTINGS:
        setattr(user, attribute, form.get(field) is not None)
    db.commit()
    log.info("email preferences updated for %s", user.email)
    return RedirectResponse("/app/settings?saved=1", status_code=303)
