"""Locations a business runs, with or without a POS, and its business profile (ADM-FR-01, -02).

Every member manages them until roles exist (T1.06 gives this to Owner and Administrator).
A location that came from Square keeps Square's name, time zone and currency; the rest is ours.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.db import get_db
from app.entitlements import location_allowance
from app.models import Location, Tenant
from app.security import require_permission, require_tenant, require_writable
from app.transform.business_days import (
    EVERY_DAY,
    WEEKDAYS,
    is_time_zone,
    open_day_names,
    open_days_mask,
    time_zones,
)
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

CURRENCY = re.compile(r"^[A-Z]{3}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Invalid(ValueError):
    """A form value the owner has to correct; its message is shown to them."""


def _locations(db: Session, org: OrgContext) -> list[Location]:
    return list(db.scalars(select_scoped(org, Location).order_by(Location.name)))


def _owned(db: Session, org: OrgContext, location_id: int) -> Location:
    location = get_scoped(db, org, Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="location not found")
    return location


def _room_for_another(db: Session, org: OrgContext, tenant: Tenant) -> bool:
    """The plan covers one more active location (DOMAIN_MODEL: active count <= entitlement)."""
    allowance = location_allowance(tenant)
    if allowance is None:
        return True
    active = [location for location in _locations(db, org) if location.is_active]
    return len(active) < allowance


def _allowance_message(tenant: Tenant) -> str:
    allowance = location_allowance(tenant)
    plural = "location" if allowance == 1 else "locations"
    return f"Your plan covers {allowance} {plural}. Archive one or upgrade to add another."


def _form(
    name: str, address: str, timezone: str, currency: str, cutoff: int, open_days: list[str]
) -> dict:
    """Check what the owner typed; raise Invalid with a sentence they can act on."""
    name = name.strip()
    if not name:
        raise Invalid("Give the location a name.")
    timezone = timezone.strip()
    if not is_time_zone(timezone):
        raise Invalid(f"{timezone or 'That'} is not a time zone; pick one like America/New_York.")
    currency = currency.strip().upper()
    if not CURRENCY.match(currency):
        raise Invalid("Currency is a three-letter code such as USD.")
    if not 0 <= cutoff <= 23:
        raise Invalid("The business day starts at an hour between midnight and 11 p.m.")
    try:
        mask = open_days_mask(open_days)
    except ValueError as exc:
        raise Invalid("Pick the days the location opens.") from exc
    return {
        "name": name[:200],
        "address": address.strip()[:300] or None,
        "timezone": timezone,
        "currency": currency,
        "business_day_cutoff_hour": cutoff,
        "open_days": mask,
    }


def _render_form(
    request: Request,
    tenant: Tenant,
    location: Location | None,
    values: dict,
    error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "app/location_form.html",
        {
            "title": "Edit location" if location else "Add location",
            "tenant": tenant,
            "location": location,
            "values": values,
            "weekdays": WEEKDAYS,
            "time_zones": sorted(time_zones()),
            "error": error,
        },
        status_code=status_code,
    )


def _values(location: Location) -> dict:
    return {
        "name": location.name,
        "address": location.address or "",
        "timezone": location.timezone,
        "currency": location.currency,
        "business_day_cutoff_hour": location.business_day_cutoff_hour,
        "open_days": open_day_names(location.open_days),
    }


@router.get("/app/locations", response_class=HTMLResponse)
def locations_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("location.manage")),
):
    return templates.TemplateResponse(
        request,
        "app/locations.html",
        {
            "title": "Locations",
            "tenant": tenant,
            "locations": _locations(db, org),
            "allowance": location_allowance(tenant),
            "room": _room_for_another(db, org, tenant),
            "open_day_names": open_day_names,
        },
    )


@router.get("/app/locations/new", response_class=HTMLResponse)
def new_location_form(
    request: Request,
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("location.manage")),
):
    defaults = {
        "name": "",
        "address": "",
        "timezone": "",
        "currency": "USD",
        "business_day_cutoff_hour": 0,
        "open_days": open_day_names(EVERY_DAY),
    }
    return _render_form(request, tenant, None, defaults)


@router.post("/app/locations/new")
def create_location(
    request: Request,
    name: str = Form(""),
    address: str = Form(""),
    timezone: str = Form(""),
    currency: str = Form("USD"),
    business_day_cutoff_hour: int = Form(0),
    open_days: list[str] = Form([]),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("location.manage")),
):
    raw = {
        "name": name,
        "address": address,
        "timezone": timezone,
        "currency": currency,
        "business_day_cutoff_hour": business_day_cutoff_hour,
        "open_days": tuple(open_days),
    }
    try:
        values = _form(name, address, timezone, currency, business_day_cutoff_hour, open_days)
    except Invalid as exc:
        return _render_form(request, tenant, None, raw, str(exc), status_code=400)
    if not _room_for_another(db, org, tenant):
        return _render_form(request, tenant, None, raw, _allowance_message(tenant), 400)

    location = Location(tenant_id=org.tenant_id, provider=None, provider_location_id=None, **values)
    db.add(location)
    db.commit()
    log.info("location %s added", location.id)
    return RedirectResponse("/app/locations?saved=1", status_code=303)


@router.get("/app/locations/{location_id}", response_class=HTMLResponse)
def edit_location_form(
    request: Request,
    location_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("location.manage")),
):
    location = _owned(db, org, location_id)
    return _render_form(request, tenant, location, _values(location))


@router.post("/app/locations/{location_id}")
def save_location(
    request: Request,
    location_id: int,
    name: str = Form(""),
    address: str = Form(""),
    timezone: str = Form(""),
    currency: str = Form("USD"),
    business_day_cutoff_hour: int = Form(0),
    open_days: list[str] = Form([]),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("location.manage")),
):
    location = _owned(db, org, location_id)
    from_pos = location.provider_location_id is not None
    if from_pos:
        # Square's sync owns these; the form shows them but does not send them back.
        name, timezone, currency = location.name, location.timezone, location.currency
    try:
        values = _form(name, address, timezone, currency, business_day_cutoff_hour, open_days)
    except Invalid as exc:
        raw = _values(location) | {
            "address": address,
            "business_day_cutoff_hour": business_day_cutoff_hour,
            "open_days": tuple(open_days),
        }
        if not from_pos:
            raw |= {"name": name, "timezone": timezone, "currency": currency}
        return _render_form(request, tenant, location, raw, str(exc), status_code=400)

    for key, value in values.items():
        setattr(location, key, value)
    db.commit()
    return RedirectResponse("/app/locations?saved=1", status_code=303)


@router.post("/app/locations/{location_id}/archive")
def archive_location(
    location_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("location.manage")),
):
    """Stop tracking a location. Nothing is deleted: its items and history stay (DR-24)."""
    location = _owned(db, org, location_id)
    if location.archived_at is None:
        location.archived_at = datetime.now(UTC)
        location.is_active = False
        db.commit()
        log.info("location %s archived", location.id)
    return RedirectResponse("/app/locations?archived=1", status_code=303)


@router.post("/app/locations/{location_id}/restore")
def restore_location(
    location_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("location.manage")),
):
    location = _owned(db, org, location_id)
    if location.archived_at is None:
        return RedirectResponse("/app/locations", status_code=303)
    if not _room_for_another(db, org, tenant):
        return RedirectResponse("/app/locations?error=limit", status_code=303)
    location.archived_at = None
    location.is_active = True
    db.commit()
    log.info("location %s restored", location.id)
    return RedirectResponse("/app/locations?restored=1", status_code=303)


@router.post("/app/settings/business")
def save_business_profile(
    business_name: str = Form(""),
    address: str = Form(""),
    contact_email: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("settings.manage")),
):
    """The business profile (ADM-FR-01): its name, address and account contact."""
    name = business_name.strip()
    email = contact_email.strip().lower()
    if not name:
        return RedirectResponse("/app/settings?error=name", status_code=303)
    if email and not EMAIL.match(email):
        return RedirectResponse("/app/settings?error=email", status_code=303)
    tenant.name = name[:200]
    tenant.address = address.strip()[:300] or None
    tenant.contact_email = email[:255] or None
    db.commit()
    return RedirectResponse("/app/settings?saved=business", status_code=303)
