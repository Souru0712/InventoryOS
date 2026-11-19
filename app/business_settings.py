"""Business settings (T2.19; ADM-FR-01, -03..06, AC-ADM-02).

The only writer of `business_settings`, and of the tenant's profile, report schedule and expiry
window from the settings screens. Every change is audited (`settings.*`). The rules are in
`app/transform/settings.py`; what reads a setting reads it when it runs, so a change takes
effect on the next evaluation, count or sale.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import changes, record_audit
from app.models import BusinessSettings, Tenant
from app.transform.settings import (
    SYSTEM_LABELS,
    Alerts,
    check_alerts,
    check_defaults,
    check_profile,
    check_schedule,
)

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def settings_for(db: Session, org: OrgContext) -> BusinessSettings:
    """The business's settings row, made with the defaults the first time it is read."""
    row = db.scalars(select_scoped(org, BusinessSettings)).first()
    if row is None:
        row = BusinessSettings(tenant_id=org.tenant_id)
        db.add(row)
        db.flush()
    return row


def _tenant(db: Session, org: OrgContext) -> Tenant:
    tenant = get_scoped(db, org, Tenant, org.tenant_id)
    if tenant is None:
        raise LookupError(org.tenant_id)
    return tenant


def _audit(
    db: Session, org: OrgContext, action: str, summary: str, was: dict, now: dict, ip: str | None
) -> bool:
    before, after = changes(was, now)
    if not after:
        return False
    record_audit(
        db,
        org,
        f"settings.{action}",
        f"{summary}: {', '.join(after)}",
        subject_type="tenant",
        subject_id=org.tenant_id,
        before=before,
        after=after,
        ip=ip,
    )
    return True


def save_profile(
    db: Session,
    org: OrgContext,
    *,
    name: str,
    address: str,
    contact_email: str,
    ip: str | None = None,
) -> bool:
    """ADM-FR-01: the business's name, address and contact email. True when something changed."""
    profile = check_profile(name, address, contact_email)
    tenant = _tenant(db, org)
    was = {"name": tenant.name, "address": tenant.address, "contact email": tenant.contact_email}
    tenant.name, tenant.address, tenant.contact_email = (
        profile.name,
        profile.address,
        profile.contact_email,
    )
    now = {"name": tenant.name, "address": tenant.address, "contact email": tenant.contact_email}
    return _audit(db, org, "profile_changed", "Changed the business profile", was, now, ip)


def save_schedule(
    db: Session, org: OrgContext, *, day: int, hour: int, ip: str | None = None
) -> bool:
    """When the weekly summary goes out, on the business's clock (the T2.19 business reason)."""
    day, hour = check_schedule(day, hour)
    tenant = _tenant(db, org)
    was = {"weekly summary": f"{DAYS[tenant.report_day]} {tenant.report_hour:02d}:00"}
    tenant.report_day, tenant.report_hour = day, hour
    now = {"weekly summary": f"{DAYS[day]} {hour:02d}:00"}
    return _audit(
        db, org, "schedule_changed", "Changed when the weekly summary goes out", was, now, ip
    )


def _alert_state(tenant: Tenant, row: BusinessSettings) -> dict:
    return {
        "expiry warning days": tenant.expiry_alert_days,
        "alert email hour": "as found" if row.digest_hour is None else f"{row.digest_hour:02d}:00",
        "count review amount": row.count_review_cents,
        "count review share": row.count_review_percent,
        "adjustment review amount": row.adjustment_review_cents,
    }


def save_alerts(
    db: Session,
    org: OrgContext,
    *,
    expiry_alert_days: str,
    digest_hour: str,
    count_review: str,
    count_review_percent: str,
    adjustment_review: str,
    ip: str | None = None,
) -> Alerts:
    """ADM-FR-05: the expiry window, the alert email's hour and the review thresholds; the next
    evaluation, count and adjustment read them (AC-ADM-02)."""
    checked = check_alerts(
        expiry_alert_days, digest_hour, count_review, count_review_percent, adjustment_review
    )
    tenant = _tenant(db, org)
    row = settings_for(db, org)
    was = _alert_state(tenant, row)
    tenant.expiry_alert_days = checked.expiry_alert_days
    row.digest_hour = checked.digest_hour
    row.count_review_cents = checked.count_review_cents
    row.count_review_percent = checked.count_review_percent
    row.adjustment_review_cents = checked.adjustment_review_cents
    row.updated_at = datetime.now(UTC)
    _audit(
        db, org, "alerts_changed", "Changed the alert settings", was, _alert_state(tenant, row), ip
    )
    db.flush()
    return checked


def save_defaults(
    db: Session,
    org: OrgContext,
    *,
    unit_system: str,
    production_mode: str,
    ip: str | None = None,
) -> bool:
    """ADM-FR-03 and -06: the units offered first, and how a new product is made."""
    system, mode = check_defaults(unit_system, production_mode)
    row = settings_for(db, org)
    was = {"units": SYSTEM_LABELS[row.unit_system], "new products": row.default_production_mode}
    row.unit_system, row.default_production_mode = system, mode
    row.updated_at = datetime.now(UTC)
    now = {"units": SYSTEM_LABELS[system], "new products": mode}
    changed = _audit(db, org, "defaults_changed", "Changed the defaults", was, now, ip)
    db.flush()
    return changed


def alert_email_key(db: Session, org: OrgContext, now: datetime) -> str | None:
    """Whether the alert email may go out now, as the job's dedupe key: as soon as something is
    found when no hour is set, else once a day in that hour on the business's clock (ADM-FR-05)."""
    hour = settings_for(db, org).digest_hour
    if hour is None:
        return "digest"
    tenant = _tenant(db, org)
    try:
        local = now.astimezone(ZoneInfo(tenant.timezone or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        local = now.astimezone(UTC)
    return f"digest:{local.date().isoformat()}" if local.hour == hour else None
