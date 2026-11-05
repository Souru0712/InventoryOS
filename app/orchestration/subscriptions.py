"""Moving an account between subscription states, and keeping the limits true.

Stripe drives most of this through webhooks; the rest is time passing, which nobody sends an
event for. The nightly job is what makes a trial end when it ends, rather than when someone
happens to open the app.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entitlements import location_allowance
from app.models import Location, Plan, SubscriptionStatus, Tenant

log = logging.getLogger(__name__)


def expire_due_trials(db: Session, now: datetime | None = None) -> list[int]:
    """End every trial whose time is up. Returns the tenants that changed."""
    now = now or datetime.now(timezone.utc)
    expired = []
    for tenant in db.scalars(
        select(Tenant).where(Tenant.subscription_status == SubscriptionStatus.TRIALING)
    ):
        ends = tenant.trial_ends_at
        if ends is None:
            continue
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)
        if ends <= now:
            tenant.subscription_status = SubscriptionStatus.EXPIRED
            # The floor everyone falls back to; what they actually buy decides the rest.
            tenant.plan = Plan.STARTER
            expired.append(tenant.id)
            log.info("trial ended for tenant %s", tenant.id)
    db.flush()
    return expired


def apply_location_limit(db: Session, tenant: Tenant) -> int:
    """Keep only as many locations active as the plan covers, oldest first.

    Nothing is deleted: a Starter account that connects three Square locations keeps all three
    on record, and tracking the other two is what upgrading turns back on.
    """
    allowance = location_allowance(tenant)
    locations = list(
        db.scalars(select(Location).where(Location.tenant_id == tenant.id).order_by(Location.id))
    )
    changed = 0
    for index, location in enumerate(locations):
        wanted = allowance is None or index < allowance
        if location.is_active is not wanted:
            location.is_active = wanted
            changed += 1
            log.info(
                "location %s for tenant %s %s by the plan limit",
                location.id,
                tenant.id,
                "enabled" if wanted else "set aside",
            )
    db.flush()
    return changed


def start_subscription(
    db: Session,
    tenant: Tenant,
    plan: str,
    subscription_id: str | None = None,
    customer_id: str | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> Tenant:
    """A payment went through: the account is on a plan it has paid for."""
    tenant.plan = Plan(plan)
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    tenant.past_due_since = None
    if subscription_id:
        tenant.stripe_subscription_id = subscription_id
    if customer_id:
        tenant.stripe_customer_id = customer_id
    tenant.current_period_start = period_start
    tenant.current_period_end = period_end
    apply_location_limit(db, tenant)
    db.flush()
    log.info("tenant %s is now active on %s", tenant.id, plan)
    return tenant


def change_plan(db: Session, tenant: Tenant, plan: str) -> Tenant:
    """An upgrade or a downgrade. A downgrade can take locations out of use, so the limit reruns."""
    tenant.plan = Plan(plan)
    apply_location_limit(db, tenant)
    db.flush()
    log.info("tenant %s moved to %s", tenant.id, plan)
    return tenant


def mark_past_due(db: Session, tenant: Tenant, now: datetime | None = None) -> Tenant:
    """A payment failed. The grace period runs from the first failure, not from each retry."""
    now = now or datetime.now(timezone.utc)
    if tenant.subscription_status is not SubscriptionStatus.PAST_DUE:
        tenant.past_due_since = now
    tenant.subscription_status = SubscriptionStatus.PAST_DUE
    db.flush()
    log.warning("tenant %s is past due since %s", tenant.id, tenant.past_due_since)
    return tenant


def end_subscription(
    db: Session, tenant: Tenant, status: SubscriptionStatus = SubscriptionStatus.CANCELED
) -> Tenant:
    """Canceled or paused: read-only from here, with everything still visible and still syncing."""
    tenant.subscription_status = status
    tenant.past_due_since = None
    db.flush()
    log.info("tenant %s subscription %s", tenant.id, status.value)
    return tenant
