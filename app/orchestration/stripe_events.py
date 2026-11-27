"""Stripe's side of the subscription, translated into ours.

Stripe is the record of what was paid; app/entitlements.py is the record of what that buys. This
module is the join between them, and the only place that reads a Stripe payload.

Every handler is safe to run twice, because Stripe retries, and events arrive out of order often
enough to matter: an older `updated` landing after a `deleted` would otherwise bring a canceled
subscription back to life.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.access import OrgContext
from app.access.system import tenant_by_id, tenant_by_stripe_id
from app.audit import record_audit
from app.entitlements import grace_days
from app.models import SubscriptionStatus, Tenant
from app.orchestration.subscriptions import (
    apply_location_limit,
    change_plan,
    end_subscription,
    mark_past_due,
    start_subscription,
)
from app.plans import plan_by_code, plan_for_price, plan_of

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


def _moment(value: Any) -> datetime | None:
    """Stripe sends seconds since the epoch."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _subscription_plan(db: Session, subscription: dict) -> str | None:
    """The catalogue plan whose provider price the subscription is for (E-03)."""
    items = ((subscription.get("items") or {}).get("data")) or []
    for item in items:
        price = (item.get("price") or {}).get("id")
        plan = plan_for_price(db, price)
        if plan is not None:
            return plan.code
        if price:
            log.warning("stripe price %s is not one of ours", price)
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
    plan = _subscription_plan(db, subscription) or plan_of(tenant).code
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
        change_plan(db, tenant, plan)
        tenant.current_period_end = period_end or tenant.current_period_end
        mark_past_due(db, tenant)
        if subscription.get("status") == "unpaid":
            # Stripe has stopped retrying the card: no grace is left, so read-only (SAAS §3).
            spent = datetime.now(UTC) - timedelta(days=grace_days())
            if tenant.past_due_since is None or tenant.past_due_since > spent:
                tenant.past_due_since = spent
    else:
        tenant.current_period_end = period_end or tenant.current_period_end
        end_subscription(db, tenant, status)

    if subscription.get("id"):
        tenant.stripe_subscription_id = subscription["id"]
    # The one item a plan is billed through, and how many units Stripe bills (T4.02).
    items = ((subscription.get("items") or {}).get("data")) or []
    if items:
        tenant.stripe_subscription_item_id = (
            items[0].get("id") or tenant.stripe_subscription_item_id
        )
        if items[0].get("quantity") is not None:
            tenant.billed_quantity = int(items[0]["quantity"])
    apply_location_limit(db, tenant)
    db.flush()
    return status.value


def _plan_state(tenant: Tenant) -> dict:
    return {
        "plan": plan_of(tenant).code,
        "status": getattr(tenant.subscription_status, "value", tenant.subscription_status),
    }


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
    was = _plan_state(tenant)

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
            if plan and plan_by_code(db, plan) is not None:
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
        result["plan"] = plan_of(tenant).code

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

    now = _plan_state(tenant)
    if now != was:
        # Stripe acted, not a member: the product records it as the system (SEC-14).
        record_audit(
            db,
            OrgContext.system(tenant.id),
            "billing.subscription_changed",
            f"Plan {was['plan']} ({was['status']}) became {now['plan']} ({now['status']})",
            subject_type="subscription",
            subject_id=tenant.id,
            before=was,
            after=now,
        )
    _mark_seen(tenant, event)
    db.flush()
    log.info("stripe %s applied to tenant %s: %s", kind, tenant.id, result.get("status"))
    return result
