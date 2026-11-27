"""The only queries that cross organizations, each named for the one job that needs it (C-33).

Signing in, routing a provider's webhook or a Stripe event to its account, and the scheduler's
per-organization loops start before any OrgContext exists. Everything they find is then worked
on through an OrgContext for that one organization.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Delete, delete, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (
    AuthToken,
    DataExport,
    Invitation,
    Job,
    Membership,
    ProviderConnection,
    SubscriptionStatus,
    Tenant,
    User,
    UserSession,
)


def user_by_email(db: Session, email: str) -> User | None:
    """The account an email signs in to."""
    return db.scalars(select(User).where(User.email == email.strip().lower())).first()


def invitation_by_token_hash(db: Session, token_hash: str) -> Invitation | None:
    """The invitation an emailed link names. Crosses organizations: the person following the
    link has no membership yet, so nothing else can say which business it is for."""
    return db.scalars(select(Invitation).where(Invitation.token_hash == token_hash)).first()


def memberships_of(db: Session, user_id: int) -> list[tuple[Membership, str]]:
    """The businesses a person may act for, oldest membership first, each with its name.

    Crosses organizations by nature: it is how a signed-in person picks one (SAAS-A-09).
    """
    rows = db.execute(
        select(Membership, Tenant.name)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(Membership.user_id == user_id, Membership.status == "active")
        .order_by(Membership.id)
    )
    return [(membership, name) for membership, name in rows]


def connection_for_merchant(
    db: Session, provider: str, merchant_id: str
) -> ProviderConnection | None:
    """The active connection a provider's webhook names by merchant: one at most (ADR-0076)."""
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
    """Every organization, for the nightly and weekly jobs. A lapsed one stays read-only and
    keeps being evaluated (E-07); what it may receive is decided by `allows()`."""
    return list(db.scalars(select(Tenant).order_by(Tenant.id)))


def expired_exports(db: Session, now: datetime) -> list[DataExport]:
    """Every business's exports whose download time is over, for the nightly sweep (T4.05)."""
    return list(
        db.scalars(
            select(DataExport).where(DataExport.status == "ready", DataExport.expires_at <= now)
        )
    )


def _deleted(db: Session, statement: Delete) -> int:
    return cast("CursorResult[Any]", db.execute(statement)).rowcount


def lapsed_tenants(db: Session) -> list[Tenant]:
    """Businesses without full access and with no deletion scheduled, for the retention sweep."""
    return list(
        db.scalars(
            select(Tenant).where(
                Tenant.deleted_at.is_(None),
                Tenant.deletion_due_at.is_(None),
                Tenant.subscription_status.in_(
                    [
                        SubscriptionStatus.EXPIRED,
                        SubscriptionStatus.CANCELED,
                        SubscriptionStatus.PAUSED,
                        SubscriptionStatus.PAST_DUE,
                    ]
                ),
            )
        )
    )


def erase_business(db: Session, tenant_id: int) -> dict[str, int]:
    """Delete every row a business owns, children before parents; the people whose only business
    it was go with it (SAAS-D-05). The business's own row is left for the caller to empty.

    Only `app/deletion.py` calls this, once a deletion has fallen due (T4.06, ADR-0093).
    """
    memberships = select(Membership.id).where(Membership.tenant_id == tenant_id)
    members = select(Membership.user_id).where(Membership.tenant_id == tenant_id)
    elsewhere = select(Membership.user_id).where(Membership.tenant_id != tenant_id)
    only_here = list(
        db.scalars(select(User.id).where(User.id.in_(members), User.id.not_in(elsewhere)))
    )
    counts: dict[str, int] = {}
    counts["sessions"] = _deleted(
        db,
        delete(UserSession).where(
            or_(UserSession.membership_id.in_(memberships), UserSession.user_id.in_(only_here))
        ),
    )
    counts["auth_tokens"] = _deleted(db, delete(AuthToken).where(AuthToken.user_id.in_(only_here)))
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in ("users", "tenants") or "tenant_id" not in table.c:
            continue
        counts[table.name] = _deleted(db, delete(table).where(table.c.tenant_id == tenant_id))
    counts["users"] = _deleted(db, delete(User).where(User.id.in_(only_here)))
    db.flush()
    return counts


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


def next_due_job(db: Session, now: datetime, served: Collection[int] = ()) -> Job | None:
    """The oldest job due to run, locked for this worker, from an organization not yet served
    this round, so one business's backlog cannot starve the others (SCL-03). Crosses
    organizations: the worker serves them all. Rows another worker holds are skipped."""
    query = select(Job).where(Job.status == "pending", Job.run_at <= now)
    if served:
        query = query.where(Job.tenant_id.not_in(sorted(served)))
    return db.scalars(
        query.order_by(Job.run_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).first()


def abandoned_jobs(db: Session, started_before: datetime) -> list[Job]:
    """Jobs left running by a worker that stopped mid-way, across organizations."""
    return list(
        db.scalars(
            select(Job)
            .where(Job.status == "running", Job.locked_at < started_before)
            .with_for_update(skip_locked=True)
        )
    )
