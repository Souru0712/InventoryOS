"""Stripe's side of the subscription, translated into ours.

Stripe is the record of what was paid; app/entitlements.py is the record of what that buys. This
module is the join between them, and the only place that reads a Stripe payload.

Every handler is safe to run twice, because Stripe retries, and events arrive out of order often
enough to matter: an older `updated` landing after a `deleted` would otherwise bring a canceled
subscription back to life.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.access.system import tenant_by_id, tenant_by_stripe_id
from app.config import get_settings
from app.entitlements import PLAN_IDS, STARTER
from app.models import SubscriptionStatus, Tenant
from app.orchestration.subscriptions import (
    apply_location_limit,
    end_subscription,
    mark_past_due,
    start_subscription,
)

log = logging.getLogger(__name__)

# Stripe's subscription statuses, in our words. Anything unknown is treated as lapsed rather
# than as paid: the safe direction when we are unsure is read-only, not free Pro.
STATUS_MAP = {
    "trialing": SubscriptionStatus.TRIALING,
    "active": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE,
    "unpaid": SubscriptionStatus.PAST_DUE,
    "incomplete": SubscriptionStatus.PAST_DUE,
    "paused": SubscriptionStatus.PAUSED,
    "canceled": SubscriptionStatus.CANCELED,
    "incomplete_expired": SubscriptionStatus.CANCELED,
}

HANDLED = (
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
    "invoice.payment_failed",
    "invoice.payment_succeeded",
)


def price_for_plan(plan: str) -> str | None:
    """The configured price id for a plan. Chosen here, never taken from the browser."""
    settings = get_settings()
    return {"starter": settings.stripe_price_starter, "pro": settings.stripe_price_pro}.get(plan)


def plan_for_price(price_id: str | None) -> str | None:
    """Which plan a Stripe price belongs to, so an upgrade in Stripe is understood here."""
    if not price_id:
        return None
    settings = get_settings()
    for plan, configured in (
        ("starter", settings.stripe_price_starter),
        ("pro", settings.stripe_price_pro),
    ):
        if configured and configured == price_id:
            return plan
    log.warning("stripe price %s is not one of ours", price_id)
    return None


def _moment(value: Any) -> datetime | None:
    """Stripe sends seconds since the epoch."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _subscription_plan(subscription: dict) -> str | None:
    items = ((subscription.get("items") or {}).get("data")) or []
    for item in items:
        plan = plan_for_price((item.get("price") or {}).get("id"))
        if plan:
            return plan
    return None


def _tenant_for(db: Session, payload: dict, event: dict) -> Tenant | None:
    """Find the account an event belongs to: by reference first, then by the ids we stored."""
    metadata = payload.get("metadata") or {}
    reference = metadata.get("tenant_id") or payload.get("client_reference_id")
    if reference:
        tenant = tenant_by_id(db, int(reference))
        if tenant:
            return tenant

    for key, value in (
        ("subscription_id", payload.get("subscription") or payload.get("id")),
        ("customer_id", payload.get("customer")),
    ):
        if not value or not isinstance(value, str):
            continue
        tenant = tenant_by_stripe_id(db, **{key: value})
        if tenant:
            return tenant

    log.warning("stripe %s names no account we know", event.get("type"))
    return None


def _is_stale(tenant: Tenant, event: dict) -> bool:
    """Drop an event older than the last one applied to this account."""
    created = _moment(event.get("created"))
    seen = tenant.stripe_event_at
    if created is None or seen is None:
        return False
    return created < seen


def _mark_seen(tenant: Tenant, event: dict) -> None:
    created = _moment(event.get("created"))
    if created:
        tenant.stripe_event_at = created


def apply_subscription(db: Session, tenant: Tenant, subscription: dict) -> str:
    """Bring the account in line with one Stripe subscription object."""
    status = STATUS_MAP.get(subscription.get("status") or "", SubscriptionStatus.EXPIRED)
    plan = _subscription_plan(subscription) or (
        tenant.plan.value if tenant.plan.value in PLAN_IDS else STARTER
    )
    period_start = _moment(subscription.get("current_period_start"))
    period_end = _moment(subscription.get("current_period_end"))

    if status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
        start_subscription(
            db,
            tenant,
            plan,
            subscription_id=subscription.get("id"),
            customer_id=subscription.get("customer"),
            period_start=period_start,
            period_end=period_end,
        )
        # A Stripe trial is still a trial: it ends when Stripe says, not 30 days from signup.
        if status is SubscriptionStatus.TRIALING:
            tenant.subscription_status = SubscriptionStatus.TRIALING
            tenant.trial_ends_at = _moment(subscription.get("trial_end")) or tenant.trial_ends_at
    elif status is SubscriptionStatus.PAST_DUE:
        tenant.plan = type(tenant.plan)(plan)
        tenant.current_period_end = period_end or tenant.current_period_end
        mark_past_due(db, tenant)
    else:
        tenant.current_period_end = period_end or tenant.current_period_end
        end_subscription(db, tenant, status)

    if subscription.get("id"):
        tenant.stripe_subscription_id = subscription["id"]
    apply_location_limit(db, tenant)
    db.flush()
    return status.value


def handle_event(db: Session, event: dict) -> dict:
    """Apply one Stripe event. Returns what was done, for the log and for the tests."""
    kind = event.get("type") or ""
    payload = ((event.get("data") or {}).get("object")) or {}

    if kind not in HANDLED:
        return {"ignored": kind}

    tenant = _tenant_for(db, payload, event)
    if tenant is None:
        # Not an error worth retrying: Stripe will keep sending it either way.
        return {"ignored": "unknown account"}
    if _is_stale(tenant, event):
        log.info("dropped a stale stripe %s for tenant %s", kind, tenant.id)
        return {"ignored": "stale event", "tenant_id": tenant.id}

    result: dict = {"tenant_id": tenant.id, "event": kind}

    if kind == "checkout.session.completed":
        # The session carries who they are; the subscription events carry what they bought.
        tenant.stripe_customer_id = payload.get("customer") or tenant.stripe_customer_id
        subscription = payload.get("subscription")
        if isinstance(subscription, dict):
            result["status"] = apply_subscription(db, tenant, subscription)
        else:
            if isinstance(subscription, str):
                tenant.stripe_subscription_id = subscription
            plan = (payload.get("metadata") or {}).get("plan")
            if plan in PLAN_IDS:
                start_subscription(
                    db,
                    tenant,
                    plan,
                    subscription_id=tenant.stripe_subscription_id,
                    customer_id=tenant.stripe_customer_id,
                )
                result["status"] = SubscriptionStatus.ACTIVE.value
        log.info("tenant %s completed checkout", tenant.id)

    elif kind.startswith("customer.subscription."):
        if kind.endswith(".deleted"):
            tenant.current_period_end = (
                _moment(payload.get("current_period_end")) or tenant.current_period_end
            )
            end_subscription(db, tenant, SubscriptionStatus.CANCELED)
            result["status"] = SubscriptionStatus.CANCELED.value
        elif kind.endswith(".paused"):
            end_subscription(db, tenant, SubscriptionStatus.PAUSED)
            result["status"] = SubscriptionStatus.PAUSED.value
        else:
            # created, updated, resumed: the payload says what it now is.
            result["status"] = apply_subscription(db, tenant, payload)
        result["plan"] = tenant.plan.value

    elif kind == "invoice.payment_failed":
        # The grace period runs from when the payment actually failed, not from when we got
        # round to processing the event - a retry queue must not shorten anyone's week.
        mark_past_due(db, tenant, _moment(event.get("created")))
        result["status"] = SubscriptionStatus.PAST_DUE.value

    elif kind == "invoice.payment_succeeded":
        # A retry that worked. Only the subscription's own events may change the plan.
        if tenant.subscription_status is SubscriptionStatus.PAST_DUE:
            tenant.subscription_status = SubscriptionStatus.ACTIVE
            tenant.past_due_since = None
        tenant.current_period_start = (
            _moment(payload.get("period_start")) or tenant.current_period_start
        )
        tenant.current_period_end = _moment(payload.get("period_end")) or tenant.current_period_end
        result["status"] = tenant.subscription_status.value

    _mark_seen(tenant, event)
    db.flush()
    log.info("stripe %s applied to tenant %s: %s", kind, tenant.id, result.get("status"))
    return result
