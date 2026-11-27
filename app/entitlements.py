"""What a customer has paid for, decided in one place.

The pricing page is marketing; this module is the product. Every entitlement question - can they
change stock, add another location, use a capability beyond the core - is answered here, so no
route ever asks about a plan on its own (SAAS-B-09).

The shape is: account -> subscription status -> access, and account -> plan -> entitlement set
(app/plans.py, ADR-0089). Core operations need only an active subscription and are never gated
by plan (E-05, C-41); the entitlement set holds the location allowance, the capability flags
beyond the core and the usage limits, all as data.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.models import SubscriptionStatus, Tenant
from app.plans import plan_of

log = logging.getLogger(__name__)

# A trial runs a month on the plan the business chose, without a card (C-44).
TRIAL_DAYS = 30
# A card expires and nobody notices for a week. Stripe keeps retrying; so do we, with access.
GRACE_DAYS = 7


class Access(str, enum.Enum):
    """How much of the product is open right now, whatever plan it is."""

    FULL = "full"
    # Everything stays visible and Square keeps syncing, but nothing can be changed.
    READ_ONLY = "read_only"


@dataclass(frozen=True)
class EntitlementSet:
    """One business's entitlements, read from its plan (E-02)."""

    plan: str
    plan_name: str
    allowed_modes: frozenset[str]
    max_locations: int | None  # None means as many as they have
    capability_flags: frozenset[str]
    limits: Mapping[str, int]


# What it means when an operation is refused. Capability flags beyond the core are named by the
# plan catalogue; one without a sentence here is refused with the generic one.
DENIAL_REASONS = {
    "inventory_write": "Your subscription has lapsed, so stock cannot be changed. Everything is still here to read.",
    "add_location": "Your plan covers no more locations. Archive one, or change plan to add another.",
    "notifications": "Your subscription has lapsed, so alerts and the weekly report are paused.",
}

# Subscription-level questions: answered by access alone, whatever the plan (E-05).
WRITING = {"inventory_write", "add_location"}
SUBSCRIPTION = WRITING | {"notifications"}


class NotEntitled(PermissionError):
    """The account cannot do this. Carries the sentence a person should be shown."""

    def __init__(self, capability: str):
        self.capability = capability
        super().__init__(DENIAL_REASONS.get(capability, "That is not part of your plan."))


def entitlements(tenant: Tenant) -> EntitlementSet:
    """The business's entitlement set, from its plan in the catalogue."""
    plan = plan_of(tenant)
    granted = plan.entitlement
    return EntitlementSet(
        plan=plan.code,
        plan_name=plan.name,
        allowed_modes=frozenset(granted.allowed_modes or ()),
        max_locations=granted.max_locations,
        capability_flags=frozenset(granted.capability_flags or ()),
        limits=dict(granted.limits or {}),
    )


def trial_days_left(tenant: Tenant, now: datetime | None = None) -> int | None:
    """Whole days remaining, or None when this is not a trial."""
    if tenant.subscription_status is not SubscriptionStatus.TRIALING:
        return None
    ends = tenant.trial_ends_at
    if ends is None:
        return None
    now = now or datetime.now(UTC)
    return max(0, (ends - now).days + (1 if (ends - now).seconds else 0))


def grace_ends(tenant: Tenant) -> datetime | None:
    """When a failed payment stops being forgiven."""
    since = tenant.past_due_since
    return since + timedelta(days=GRACE_DAYS) if since else None


def access(tenant: Tenant, now: datetime | None = None) -> Access:
    """Full access or read-only, computed from the dates rather than from a stale flag.

    The status column is kept current by Stripe and by the nightly job, but a trial that ran out
    an hour ago must close on its own, without waiting for either.
    """
    now = now or datetime.now(UTC)
    status = tenant.subscription_status

    if status is SubscriptionStatus.ACTIVE:
        return Access.FULL
    if status is SubscriptionStatus.TRIALING:
        ends = tenant.trial_ends_at
        return Access.FULL if ends is None or now < ends else Access.READ_ONLY
    if status is SubscriptionStatus.PAST_DUE:
        # Stripe is still retrying the card; the shop keeps working while it does.
        until = grace_ends(tenant)
        return Access.FULL if until is None or now < until else Access.READ_ONLY
    # expired, canceled, paused
    return Access.READ_ONLY


def allows(tenant: Tenant, capability: str, now: datetime | None = None) -> bool:
    """The one question every route asks.

    Changing stock, adding a location and receiving alerts need an active subscription; anything
    else is a capability flag of the business's plan. The location count itself is checked
    against `location_allowance()` where locations are added.
    """
    if capability in SUBSCRIPTION:
        return access(tenant, now) is Access.FULL
    return capability in entitlements(tenant).capability_flags


def require(tenant: Tenant, capability: str, now: datetime | None = None) -> None:
    """Raise NotEntitled, with the sentence to show, rather than returning a bare False."""
    if not allows(tenant, capability, now):
        raise NotEntitled(capability)


def location_allowance(tenant: Tenant) -> int | None:
    """How many locations this account may track. None means as many as they have."""
    return entitlements(tenant).max_locations


def usage_limit(tenant: Tenant, name: str) -> int | None:
    """A usage limit with a real cost (E-09), such as invoices read a day; None when unlimited."""
    return entitlements(tenant).limits.get(name)


def state(tenant: Tenant, now: datetime | None = None) -> dict:
    """Everything a page needs to say where the account stands, in one call."""
    now = now or datetime.now(UTC)
    current = access(tenant, now)
    days_left = trial_days_left(tenant, now)
    status = tenant.subscription_status
    granted = entitlements(tenant)
    return {
        "status": status.value,
        "plan": granted.plan,
        "plan_name": granted.plan_name,
        "max_locations": granted.max_locations,
        "access": current.value,
        "read_only": current is Access.READ_ONLY,
        "trial_days_left": days_left,
        "grace_ends_at": grace_ends(tenant),
        "message": _message(status, current, days_left, tenant, now),
    }


def _message(
    status: SubscriptionStatus,
    current: Access,
    days_left: int | None,
    tenant: Tenant,
    now: datetime,
) -> str | None:
    """What to say at the top of the page, or nothing when everything is in order."""
    if status is SubscriptionStatus.TRIALING and current is Access.FULL:
        if days_left is None:
            return None
        if days_left <= 7:
            return f"{days_left} day{'s' if days_left != 1 else ''} left of your trial."
        return None
    if status is SubscriptionStatus.PAST_DUE and current is Access.FULL:
        until = grace_ends(tenant)
        days = max(0, (until - now).days) if until else GRACE_DAYS
        return (
            f"Your last payment did not go through. Update your card within {days} "
            f"day{'s' if days != 1 else ''} to keep receiving and counting stock."
        )
    if current is Access.READ_ONLY:
        if status is SubscriptionStatus.TRIALING:
            return "Your trial has ended. Your inventory is still here and still syncing; subscribe to change it again."
        if status is SubscriptionStatus.PAST_DUE:
            return (
                "We could not take payment. Update your card to start receiving and counting again."
            )
        if status is SubscriptionStatus.PAUSED:
            return "Your subscription is paused. Your inventory is still here and still syncing."
        return "Your subscription has ended. Your inventory is still here and still syncing; subscribe to change it again."
    return None


def start_trial(tenant: Tenant, now: datetime | None = None) -> Tenant:
    """A new account tries its plan for TRIAL_DAYS (PD-56: the plan it chose, not a bigger one)."""
    now = now or datetime.now(UTC)
    tenant.subscription_status = SubscriptionStatus.TRIALING
    tenant.trial_started_at = now
    tenant.trial_ends_at = now + timedelta(days=TRIAL_DAYS)
    return tenant
