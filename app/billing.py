"""The subscription follows the business (T4.02, ADR-0090; SAAS-B-18, SAAS-B-19, WF-25, C-44).

A plan billed per location (Complete) is billed for the business's active locations taking sales
from a POS; a plan billed per business (Standalone) is one unit. When a location changes its sales
mode at a cutover, the plan follows: from the cutover date the business moves to the narrowest
plan that fits (`app/transform/billing.py`), prorated by Stripe. Adding, archiving or restoring a
location only resyncs the quantity, unless the plan no longer fits. Before a business subscribes,
the change is made here at once; once it has a Stripe subscription, the change is asked of Stripe
and Stripe's webhook applies it. The work runs as the `sync_subscription` job, safe to repeat.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.audit import record_audit
from app.config import get_settings
from app.jobs import enqueue
from app.models import BillingPlan, Job, Location, Tenant
from app.orchestration.subscriptions import apply_location_limit
from app.plans import plan_of
from app.sales import source_of_record
from app.transform.billing import Offer, Use, narrowest, quantity, unfit
from app.transform.business_days import business_date

log = logging.getLogger(__name__)

JOB = "sync_subscription"
CUTOVER = "cutover"  # the reason that lets the plan move to a narrower one


class BillingNotConfigured(RuntimeError):
    """Stripe is needed and not configured, or the plan names no provider price."""


def stripe_client() -> Any:
    """The Stripe library with this deployment's key; refused when billing is not configured."""
    secret = get_settings().stripe_secret_key
    if not secret:
        raise BillingNotConfigured("STRIPE_SECRET_KEY is not set")
    import stripe

    stripe.api_key = secret
    return stripe


def offer(plan: BillingPlan) -> Offer:
    granted = plan.entitlement
    return Offer(
        code=plan.code,
        name=plan.name,
        billing_basis=plan.billing_basis,
        allowed_modes=frozenset(granted.allowed_modes or ()),
        max_locations=granted.max_locations,
    )


def offers(db: Session) -> list[BillingPlan]:
    """The plans a business may move to: active ones (E-08 keeps the rest for those on them)."""
    return list(db.scalars(select(BillingPlan).where(BillingPlan.active.is_(True))))


def _mode(db: Session, org: OrgContext, place: Location) -> str:
    today = business_date(datetime.now(UTC), place.timezone, place.business_day_cutoff_hour)
    record = source_of_record(db, org, place.id, today)
    return "connected" if record is not None and record.kind == "pos_adapter" else "standalone"


def use_of(db: Session, org: OrgContext) -> Use:
    """The business's active locations and the sales mode each takes today."""
    places = db.scalars(
        select_scoped(org, Location)
        .where(Location.is_active.is_(True), Location.archived_at.is_(None))
        .order_by(Location.id)
    )
    return Use(modes=tuple(_mode(db, org, place) for place in places))


def fit_reason(db: Session, org: OrgContext, plan: BillingPlan) -> str | None:
    """Why this business cannot take the plan now (its modes or locations), or None."""
    return unfit(offer(plan), use_of(db, org))


def invoices_read_today(db: Session, org: OrgContext) -> int:
    """Invoice photos queued for reading in the last 24 hours: the plan's costed limit (E-09)."""
    since = datetime.now(UTC) - timedelta(hours=24)
    return len(
        db.scalars(
            select_scoped(org, Job).where(Job.kind == "extract_document", Job.created_at >= since)
        ).all()
    )


def quantity_for(db: Session, org: OrgContext, plan: BillingPlan) -> int:
    return quantity(offer(plan), use_of(db, org))


def queue(
    db: Session, org: OrgContext, *, reason: str = "locations", at: datetime | None = None
) -> None:
    """Ask for the subscription to be brought in line, now or at a cutover's start."""
    key = f"{reason}:{at.isoformat()}" if at else reason
    enqueue(db, org, JOB, {"reason": reason}, dedupe_key=key, run_at=at)


def cutover_start(place: Location, cutover: date) -> datetime:
    """When a cutover business date starts at the location, in UTC."""
    local = datetime.combine(
        cutover, time(place.business_day_cutoff_hour), ZoneInfo(place.timezone)
    )
    return local.astimezone(UTC)


def queue_after_cutover(db: Session, org: OrgContext, place: Location, cutover: date) -> None:
    """The plan follows the mode from the cutover date (WF-25), not before it."""
    start = cutover_start(place, cutover)
    queue(db, org, reason=CUTOVER, at=start if start > datetime.now(UTC) else None)


def plan_after(db: Session, org: OrgContext, location_id: int, mode: str) -> BillingPlan | None:
    """The plan the business would move to if this location took `mode`; None if it stays."""
    tenant = _tenant(db, org)
    places = db.scalars(
        select_scoped(org, Location)
        .where(Location.is_active.is_(True), Location.archived_at.is_(None))
        .order_by(Location.id)
    )
    use = Use(modes=tuple(mode if p.id == location_id else _mode(db, org, p) for p in places))
    target = _target(db, plan_of(tenant), use, CUTOVER)
    return None if target is None or target.id == plan_of(tenant).id else target


def _tenant(db: Session, org: OrgContext) -> Tenant:
    tenant = get_scoped(db, org, Tenant, org.tenant_id)
    if tenant is None:
        raise ValueError(f"tenant {org.tenant_id} not found")
    return tenant


def _target(db: Session, current: BillingPlan, use: Use, reason: str) -> BillingPlan | None:
    """A cutover moves to the narrowest plan that fits; anything else keeps a plan that fits."""
    if reason != CUTOVER and unfit(offer(current), use) is None:
        return current
    by_code = {plan.code: plan for plan in offers(db)}
    chosen = narrowest((offer(plan) for plan in by_code.values()), use)
    return by_code[chosen.code] if chosen else None


def sync(db: Session, org: OrgContext, reason: str = "locations") -> dict:
    """Bring the plan and the billed quantity in line with the business (the job's work)."""
    tenant = _tenant(db, org)
    current = plan_of(tenant)
    use = use_of(db, org)
    target = _target(db, current, use, reason)
    if target is None:
        log.warning("no plan fits tenant %s (%d locations)", tenant.id, use.locations)
        return {"changed": False, "reason": "no plan fits"}
    units = quantity(offer(target), use)

    if not tenant.stripe_subscription_id:
        if target.id == current.id:
            return {"changed": False}
        tenant.billing_plan = target
        apply_location_limit(db, tenant)
        _audit(db, org, current, target, units, asked_of_stripe=False)
        return {"changed": True, "plan": target.code}

    if target.id == current.id and units == tenant.billed_quantity:
        return {"changed": False}
    if not target.provider_price_ref:
        raise BillingNotConfigured(f"plan {target.code!r} names no provider price")
    stripe = stripe_client()
    item = tenant.stripe_subscription_item_id or _first_item(stripe, tenant)
    stripe.Subscription.modify(
        tenant.stripe_subscription_id,
        items=[{"id": item, "price": target.provider_price_ref, "quantity": units}],
        proration_behavior="create_prorations",
    )
    _audit(db, org, current, target, units, asked_of_stripe=True)
    return {"changed": True, "plan": target.code, "quantity": units}


def _first_item(stripe: Any, tenant: Tenant) -> str:
    subscription = stripe.Subscription.retrieve(tenant.stripe_subscription_id)
    items = (subscription.get("items") or {}).get("data") or []
    if not items:
        raise BillingNotConfigured(f"subscription {tenant.stripe_subscription_id} has no item")
    tenant.stripe_subscription_item_id = items[0]["id"]
    return str(items[0]["id"])


def _audit(
    db: Session,
    org: OrgContext,
    current: BillingPlan,
    target: BillingPlan,
    units: int,
    *,
    asked_of_stripe: bool,
) -> None:
    moved = target.id != current.id
    summary = (
        f"Plan follows the locations: {current.name} to {target.name}"
        if moved
        else f"Billed locations now {units}"
    )
    record_audit(
        db,
        org,
        "billing.plan_follows" if moved else "billing.quantity",
        summary + (" (asked of Stripe)" if asked_of_stripe else ""),
        subject_type="tenant",
        subject_id=org.tenant_id,
        before={"plan": current.code},
        after={"plan": target.code, "quantity": units},
    )


def set_price(db: Session, plan: BillingPlan, price_id: str) -> BillingPlan:
    """Point a plan at a Stripe price and copy its amount for display (E-01, AC-SAAS-12).

    The price must be active and recurring. New checkouts use it at once; subscriptions already
    on the old price keep it (E-08).
    """
    price = stripe_client().Price.retrieve(price_id)
    recurring = price.get("recurring") or {}
    if not price.get("active") or not recurring.get("interval"):
        raise ValueError(f"{price_id} is not an active recurring price")
    plan.provider_price_ref = price_id
    plan.display_amount_cents = price.get("unit_amount")
    plan.display_currency = str(price.get("currency") or "").upper()[:3] or None
    plan.display_interval = str(recurring["interval"])[:10]
    db.flush()
    log.info("plan %s now sells at Stripe price %s", plan.code, price_id)
    return plan
