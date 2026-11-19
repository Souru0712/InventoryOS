"""Business settings screens (T2.19; ADM-FR-01, -03..06): /app/settings/business and
/app/settings/alerts.

The business page (Owner, Administrator) holds the profile, the weekly summary's schedule and the
defaults, and links the settings that live with what they configure: locations, the team,
categories, waste reasons, modifiers. The alert page (also Managers) holds the expiry window, the
alert email's hour and the review thresholds, and shows who receives which email.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext
from app.access.scoped import team
from app.business_settings import (
    DAYS,
    save_alerts,
    save_defaults,
    save_profile,
    save_schedule,
    settings_for,
)
from app.db import get_db
from app.models import Tenant
from app.orchestration.reporting import recipients
from app.security import require_permission, require_tenant, require_writable
from app.throttle import client_ip
from app.transform.products import MODE_LABELS
from app.transform.settings import SYSTEM_LABELS, SettingsRuleError
from app.web.flash import flash, pop_flash
from app.web.templating import templates

router = APIRouter()
BUSINESS = "/app/settings/business"
ALERTS = "/app/settings/alerts"


def _done(request: Request, db: Session, back: str, action) -> RedirectResponse:
    try:
        changed = action()
    except SettingsRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(back, status_code=303)
    db.commit()
    flash(request, "Saved." if changed is not False else "Nothing changed.")
    return RedirectResponse(back, status_code=303)


@router.get(BUSINESS, response_class=HTMLResponse)
def business_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    row = settings_for(db, org)
    db.commit()  # the first read makes the row
    return templates.TemplateResponse(
        request,
        "app/business_settings.html",
        {
            "title": "Business settings",
            "tenant": tenant,
            "message": pop_flash(request),
            "settings": row,
            "days": DAYS,
            "systems": SYSTEM_LABELS,
            "modes": MODE_LABELS,
        },
    )


@router.post(BUSINESS + "/profile")
def business_profile(
    request: Request,
    name: str = Form(""),
    address: str = Form(""),
    contact_email: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    return _done(
        request,
        db,
        BUSINESS,
        lambda: save_profile(
            db,
            org,
            name=name,
            address=address,
            contact_email=contact_email,
            ip=client_ip(request),
        ),
    )


@router.post(BUSINESS + "/schedule")
def business_schedule(
    request: Request,
    day: int = Form(0),
    hour: int = Form(7),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    return _done(
        request,
        db,
        BUSINESS,
        lambda: save_schedule(db, org, day=day, hour=hour, ip=client_ip(request)),
    )


@router.post(BUSINESS + "/defaults")
def business_defaults(
    request: Request,
    unit_system: str = Form("metric"),
    production_mode: str = Form("made_to_order"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    return _done(
        request,
        db,
        BUSINESS,
        lambda: save_defaults(
            db, org, unit_system=unit_system, production_mode=production_mode, ip=client_ip(request)
        ),
    )


@router.get(ALERTS, response_class=HTMLResponse)
def alerts_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("alerts.configure")),
):
    row = settings_for(db, org)
    db.commit()
    weekly = set(recipients(db, org))
    people = [
        {
            "email": user.email,
            "role": membership.role,
            "stock": user.wants_stock_alerts,
            "expiry": user.wants_expiry_alerts,
            "weekly": user.email in weekly,
        }
        for membership, user in team(db, org)
        if membership.status == "active" and user.is_active
    ]
    return templates.TemplateResponse(
        request,
        "app/alert_settings.html",
        {
            "title": "Alert settings",
            "tenant": tenant,
            "message": pop_flash(request),
            "settings": row,
            "people": people,
        },
    )


@router.post(ALERTS)
def save_alert_settings(
    request: Request,
    expiry_alert_days: str = Form(""),
    digest_hour: str = Form(""),
    count_review: str = Form(""),
    count_review_percent: str = Form(""),
    adjustment_review: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("alerts.configure")),
):
    return _done(
        request,
        db,
        ALERTS,
        lambda: save_alerts(
            db,
            org,
            expiry_alert_days=expiry_alert_days,
            digest_hour=digest_hour,
            count_review=count_review,
            count_review_percent=count_review_percent,
            adjustment_review=adjustment_review,
            ip=client_ip(request),
        ),
    )
