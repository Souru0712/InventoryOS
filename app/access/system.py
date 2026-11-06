"""The only queries that cross organizations, each named for the one job that needs it (C-33).

Signing in, routing a provider's webhook or a Stripe event to its account, and the scheduler's
per-organization loops start before any OrgContext exists. Everything they find is then worked
on through an OrgContext for that one organization.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Plan, ProviderConnection, SubscriptionStatus, Tenant, User


def user_by_email(db: Session, email: str) -> User | None:
    """The account an email signs in to."""
    return db.scalars(select(User).where(User.email == email.strip().lower())).first()


def connection_for_merchant(
    db: Session, provider: str, merchant_id: str
) -> ProviderConnection | None:
    """The active connection a provider's webhook names by merchant."""
    return db.scalars(
        select(ProviderConnection).where(
            ProviderConnection.provider == provider,
            ProviderConnection.merchant_id == merchant_id,
            ProviderConnection.status == "active",
        )
    ).first()


def connected_tenant_ids(db: Session) -> list[int]:
    """Organizations with an active provider connection, for the scheduled sync."""
    return list(
        db.scalars(
            select(ProviderConnection.tenant_id).where(ProviderConnection.status == "active")
        )
    )


def subscribed_tenants(db: Session) -> list[Tenant]:
    """Every organization that has not cancelled, for the nightly and weekly jobs."""
    return list(db.scalars(select(Tenant).where(Tenant.plan != Plan.CANCELED)))


def trialing_tenants(db: Session) -> list[Tenant]:
    """Every organization in its trial, so trials end on their date."""
    return list(
        db.scalars(select(Tenant).where(Tenant.subscription_status == SubscriptionStatus.TRIALING))
    )


def tenant_by_id(db: Session, tenant_id: int) -> Tenant | None:
    """The organization a Stripe event names by our own id."""
    return db.get(Tenant, tenant_id)


def tenant_by_stripe_id(
    db: Session, *, subscription_id: str = "", customer_id: str = ""
) -> Tenant | None:
    """The organization holding a Stripe subscription or customer id."""
    if subscription_id:
        return db.scalars(
            select(Tenant).where(Tenant.stripe_subscription_id == subscription_id)
        ).first()
    if customer_id:
        return db.scalars(select(Tenant).where(Tenant.stripe_customer_id == customer_id)).first()
    return None
