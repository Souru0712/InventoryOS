"""What a customer has paid for, decided in one place.

The pricing page is marketing; this module is the product. Every feature question - can they add
another location, download the reorder CSV, set a custom reorder point, receive a delivery at all -
is answered here, from the subscription state, so no route ever asks `if plan == "pro"` on its own.

The shape is: account -> subscription status -> plan -> capabilities -> access.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.models import Plan, SubscriptionStatus, Tenant

log = logging.getLogger(__name__)

# A trial runs a month on the full product, so the features worth paying for are the ones they use.
TRIAL_DAYS = 30
TRIAL_PLAN = "pro"
# A card expires and nobody notices for a week. Stripe keeps retrying; so do we, with access.
GRACE_DAYS = 7

STARTER, PRO = "starter", "pro"
PLAN_IDS = (STARTER, PRO)


class Access(str, enum.Enum):
    """How much of the product is open right now, whatever plan it is."""

    FULL = "full"
    # Everything stays visible and Square keeps syncing, but nothing can be changed.
    READ_ONLY = "read_only"


@dataclass(frozen=True)
class Capabilities:
    """One plan's limits. Add a field here, not an `if` somewhere else."""

    locations: int | None  # None means unlimited
    history_days: int
    reorder_csv: bool
    custom_thresholds: bool


CAPABILITIES: dict[str, Capabilities] = {
    STARTER: Capabilities(locations=1, history_days=0, reorder_csv=False, custom_thresholds=False),
    PRO: Capabilities(locations=None, history_days=30, reorder_csv=True, custom_thresholds=True),
}

# Every capability a route can ask about, and what it means if it is missing.
DENIAL_REASONS = {
    "inventory_write": "Your subscription has lapsed, so stock cannot be changed. Everything is still here to read.",
    "add_location": "Starter covers one location. Pro covers as many as you have.",
    "reorder_csv": "The reorder list as a CSV is part of Pro.",
    "custom_thresholds": "Custom reorder points and par levels are part of Pro.",
    "history_30_day": "30 days of health history is part of Pro.",
    "notifications": "Your subscription has lapsed, so alerts and the weekly report are paused.",
}

WRITING = {"inventory_write", "add_location", "custom_thresholds"}


class NotEntitled(PermissionError):
    """The account cannot do this. Carries the sentence a person should be shown."""

    def __init__(self, capability: str):
        self.capability = capability
        super().__init__(DENIAL_REASONS.get(capability, "That is not part of your plan."))


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def effective_plan(tenant: Tenant) -> str:
    """The plan whose capabilities apply now. A trial is given the full product on purpose."""
    if tenant.subscription_status is SubscriptionStatus.TRIALING:
        return TRIAL_PLAN
    plan = getattr(tenant.plan, "value", tenant.plan)
    return plan if plan in PLAN_IDS else STARTER


def capabilities(tenant: Tenant) -> Capabilities:
    return CAPABILITIES[effective_plan(tenant)]


def trial_days_left(tenant: Tenant, now: datetime | None = None) -> int | None:
    """Whole days remaining, or None when this is not a trial."""
    if tenant.subscription_status is not SubscriptionStatus.TRIALING:
        return None
    ends = _aware(tenant.trial_ends_at)
    if ends is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, (ends - now).days + (1 if (ends - now).seconds else 0))


def grace_ends(tenant: Tenant) -> datetime | None:
    """When a failed payment stops being forgiven."""
    since = _aware(tenant.past_due_since)
    return since + timedelta(days=GRACE_DAYS) if since else None


def access(tenant: Tenant, now: datetime | None = None) -> Access:
    """Full access or read-only, computed from the dates rather than from a stale flag.

    The status column is kept current by Stripe and by the nightly job, but a trial that ran out
    an hour ago must close on its own, without waiting for either.
    """
    now = now or datetime.now(timezone.utc)
    status = tenant.subscription_status

    if status is SubscriptionStatus.ACTIVE:
        return Access.FULL
    if status is SubscriptionStatus.TRIALING:
        ends = _aware(tenant.trial_ends_at)
        return Access.FULL if ends is None or now < ends else Access.READ_ONLY
    if status is SubscriptionStatus.PAST_DUE:
        # Stripe is still retrying the card; the shop keeps working while it does.
        until = grace_ends(tenant)
        return Access.FULL if until is None or now < until else Access.READ_ONLY
    # expired, canceled, paused
    return Access.READ_ONLY


def allows(tenant: Tenant, capability: str, now: datetime | None = None) -> bool:
    """The one question every route asks."""
    if capability in WRITING and access(tenant, now) is not Access.FULL:
        return False

    limits = capabilities(tenant)
    if capability == "reorder_csv":
        return limits.reorder_csv
    if capability == "custom_thresholds":
        return limits.custom_thresholds
    if capability == "history_30_day":
        return limits.history_days >= 30
    if capability == "notifications":
        return access(tenant, now) is Access.FULL
    if capability in ("inventory_write", "add_location"):
        return True
    log.warning("asked about an unknown capability %r", capability)
    return False


def require(tenant: Tenant, capability: str, now: datetime | None = None) -> None:
    """Raise NotEntitled, with the sentence to show, rather than returning a bare False."""
    if not allows(tenant, capability, now):
        raise NotEntitled(capability)


def location_allowance(tenant: Tenant) -> int | None:
    """How many locations this account may track. None means as many as they have."""
    return capabilities(tenant).locations


def history_days(tenant: Tenant) -> int:
    return capabilities(tenant).history_days


def state(tenant: Tenant, now: datetime | None = None) -> dict:
    """Everything a page needs to say where the account stands, in one call."""
    now = now or datetime.now(timezone.utc)
    current = access(tenant, now)
    days_left = trial_days_left(tenant, now)
    status = tenant.subscription_status
    return {
        "status": status.value,
        "plan": effective_plan(tenant),
        "access": current.value,
        "read_only": current is Access.READ_ONLY,
        "trial_days_left": days_left,
        "grace_ends_at": grace_ends(tenant),
        "message": _message(status, current, days_left, tenant, now),
    }


def _message(
    status: SubscriptionStatus, current: Access, days_left: int | None, tenant: Tenant, now: datetime
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
            return "We could not take payment. Update your card to start receiving and counting again."
        if status is SubscriptionStatus.PAUSED:
            return "Your subscription is paused. Your inventory is still here and still syncing."
        return "Your subscription has ended. Your inventory is still here and still syncing; subscribe to change it again."
    return None


def start_trial(tenant: Tenant, now: datetime | None = None) -> Tenant:
    """Every new account starts on the full product, for TRIAL_DAYS."""
    now = now or datetime.now(timezone.utc)
    tenant.subscription_status = SubscriptionStatus.TRIALING
    tenant.plan = Plan(TRIAL_PLAN)
    tenant.trial_started_at = now
    tenant.trial_ends_at = now + timedelta(days=TRIAL_DAYS)
    return tenant


# --- private modules --------------------------------------------------------------------------

def snack_box_tenants() -> frozenset[int]:
    """Accounts allowed to reach the snack box module, from the environment. Empty means nobody."""
    raw = get_settings().snack_box_tenant_ids or ""
    return frozenset(int(part) for part in raw.replace(" ", "").split(",") if part.isdigit())


def owns_snack_box(tenant: Tenant | int | None) -> bool:
    """Whether this account runs the operator's own snack box shop.

    Deliberately not a capability and not in CAPABILITIES: no plan includes it, no subscription can
    buy it, and an account that is not on the list is never told it exists.
    """
    tenant_id = getattr(tenant, "id", tenant)
    return isinstance(tenant_id, int) and tenant_id in snack_box_tenants()
