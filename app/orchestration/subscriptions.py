"""Moving an account between subscription states, and keeping the limits true.

Stripe drives most of this through webhooks; the rest is time passing, which nobody sends an
event for. The nightly job is what makes a trial end when it ends, rather than when someone
happens to open the app.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.access.system import trialing_tenants
from app.entitlements import location_allowance
from app.models import BillingPlan, Location, SubscriptionStatus, Tenant
from app.plans import plan_by_code

log = logging.getLogger(__name__)


def expire_due_trials(db: Session, now: datetime | None = None) -> list[int]:
    """End every trial whose time is up. Returns the tenants that changed."""
    now = now or datetime.now(UTC)
    expired = []
    for tenant in trialing_tenants(db):
        ends = tenant.trial_ends_at
        if ends is None:
            continue
        if ends <= now:
            # The plan stays: an expired trial is read-only on the plan it tried (E-07).
            tenant.subscription_status = SubscriptionStatus.EXPIRED
            expired.append(tenant.id)
            log.info("trial ended for tenant %s", tenant.id)
    db.flush()
    return expired


def apply_location_limit(db: Session, tenant: Tenant) -> int:
    """Keep only as many locations active as the plan covers, oldest first.

    Nothing is deleted (E-07): a one-location plan that connects three Square locations keeps
    all three on record, and tracking the other two is what a plan covering them turns back on.
    """
    allowance = location_allowance(tenant)
    locations = list(
        db.scalars(
            select_scoped(OrgContext.system(tenant.id), Location)
            .where(Location.archived_at.is_(None))
            .order_by(Location.id)
        )
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
    tenant.billing_plan = _catalogue_plan(db, plan)
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    tenant.past_due_since = None
    if tenant.deletion_reason == "retention" and tenant.deleted_at is None:
        # Subscribing again keeps the business (SAAS-D-03); an Owner's own request stands.
        tenant.deletion_requested_at = tenant.deletion_due_at = None
        tenant.deletion_reason = tenant.deletion_requested_by = None
        log.info("tenant %s subscribed again: retention deletion cancelled", tenant.id)
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
    tenant.billing_plan = _catalogue_plan(db, plan)
    apply_location_limit(db, tenant)
    db.flush()
    log.info("tenant %s moved to %s", tenant.id, plan)
    return tenant


def _catalogue_plan(db: Session, code: str) -> BillingPlan:
    """The catalogue plan a billing event names; an unknown one is an error, never a guess."""
    plan = plan_by_code(db, code)
    if plan is None:
        raise ValueError(f"no plan {code!r} in the catalogue")
    return plan


def mark_past_due(db: Session, tenant: Tenant, now: datetime | None = None) -> Tenant:
    """A payment failed. The grace period runs from the first failure, not from each retry."""
    now = now or datetime.now(UTC)
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
