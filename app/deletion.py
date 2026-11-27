"""Deleting a business: on an Owner's request, or when retention ends (T4.06, ADR-0093;
ADM-FR-12, SAAS-D-03..05, PD-07/C-44, AC-ADM-05).

An Owner asks, re-entering their password; the deletion falls due after a window of
`deletion_window_days` (7) during which any Owner can cancel it, and every Owner is told. A
business read-only for `retention_days` (90) is scheduled the same way by the nightly sweep, and
subscribing again cancels that. When it falls due, the `delete_business` job cancels the Stripe
subscription, revokes the POS connection, deletes the business's files and every row it owns
(`erase_business`), with the people whose only business it was, keeps the business's own row
emptied as a tombstone, and confirms to the Owners. Audit events name actor ids only.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, members, select_scoped
from app.access.system import erase_business, lapsed_tenants
from app.audit import record_audit
from app.config import get_settings
from app.connections import NotConnected, disconnect
from app.entitlements import Access, access, grace_days
from app.jobs import enqueue
from app.models import DataExport, Document, Membership, SalesImport, Tenant, User
from app.notify.email import render_pair, send_email
from app.observability import log_context
from app.security import verify_password
from app.storage import get_storage
from app.throttle import DELETE_ACCOUNT, clear, hit, locked_for
from app.transform.retention import OWNER, RETENTION, read_only_since, retention_over

log = logging.getLogger(__name__)

JOB = "delete_business"
TOMBSTONE = "Deleted business"


class DeletionRefused(ValueError):
    """A deletion request the rules refuse; the message says why."""


def window() -> timedelta:
    return timedelta(days=get_settings().deletion_window_days)


def _tenant(db: Session, org: OrgContext) -> Tenant:
    tenant = get_scoped(db, org, Tenant, org.tenant_id)
    if tenant is None:
        raise ValueError(f"tenant {org.tenant_id} not found")
    return tenant


def owners(db: Session, org: OrgContext) -> list[User]:
    """The business's active Owners, the people deletion notices go to."""
    owner_ids = {
        membership.user_id
        for membership in db.scalars(
            select_scoped(org, Membership).where(
                Membership.role == "owner", Membership.status == "active"
            )
        )
    }
    return [user for user in members(db, org) if user.id in owner_ids]


def _schedule(db: Session, org: OrgContext, tenant: Tenant, reason: str, by: int | None) -> None:
    now = datetime.now(UTC)
    tenant.deletion_requested_at, tenant.deletion_due_at = now, now + window()
    tenant.deletion_reason, tenant.deletion_requested_by = reason, by
    db.flush()
    enqueue(
        db,
        org,
        JOB,
        {},
        dedupe_key=f"deletion:{tenant.deletion_due_at.isoformat()}",
        run_at=tenant.deletion_due_at,
    )
    _tell(db, org, "deletion_scheduled", tenant, reason=reason)


def request_deletion(
    db: Session, org: OrgContext, user: User, password: str, *, ip: str | None = None
) -> Tenant:
    """An Owner asks for the business to be deleted, re-entering their password."""
    tenant = _tenant(db, org)
    if tenant.deletion_due_at is not None:
        return tenant
    subject = f"user:{user.id}"
    if locked_for(db, DELETE_ACCOUNT, subject):
        raise DeletionRefused("Too many attempts. Wait a few minutes and try again.")
    if not verify_password(password, user.password_hash):
        hit(db, DELETE_ACCOUNT, subject)
        raise DeletionRefused("That password is not right.")
    clear(db, DELETE_ACCOUNT, subject)
    _schedule(db, org, tenant, OWNER, user.id)
    record_audit(
        db,
        org,
        "data.deletion_requested",
        "Asked for the business to be deleted",
        subject_type="tenant",
        subject_id=tenant.id,
        after={"due": tenant.deletion_due_at.isoformat() if tenant.deletion_due_at else None},
        ip=ip,
    )
    return tenant


def cancel_deletion(
    db: Session, org: OrgContext, *, ip: str | None = None, by_subscribing: bool = False
) -> bool:
    """Take a scheduled deletion back; False when none was scheduled."""
    tenant = _tenant(db, org)
    if tenant.deletion_due_at is None or tenant.deleted_at is not None:
        return False
    was = {"due": tenant.deletion_due_at.isoformat(), "reason": tenant.deletion_reason}
    tenant.deletion_requested_at = tenant.deletion_due_at = None
    tenant.deletion_reason = tenant.deletion_requested_by = None
    db.flush()
    record_audit(
        db,
        org,
        "data.deletion_cancelled",
        "Subscribed again: deletion cancelled" if by_subscribing else "Cancelled the deletion",
        subject_type="tenant",
        subject_id=tenant.id,
        before=was,
        after={"due": None},
        ip=ip,
    )
    return True


def sweep_retention(db: Session, now: datetime | None = None) -> list[int]:
    """Schedule the deletion of every business read-only past its retention (SAAS-D-03)."""
    now = now or datetime.now(UTC)
    retention = timedelta(days=get_settings().retention_days)
    scheduled = []
    for tenant in lapsed_tenants(db):
        since = read_only_since(
            tenant.subscription_status.value,
            trial_ends_at=tenant.trial_ends_at,
            current_period_end=tenant.current_period_end,
            past_due_since=tenant.past_due_since,
            last_event_at=tenant.stripe_event_at,
            grace=timedelta(days=grace_days()),
        )
        if not retention_over(since, now, retention):
            continue
        with log_context(tenant_id=tenant.id):
            _schedule(db, OrgContext.system(tenant.id), tenant, RETENTION, None)
            log.info("retention over for tenant %s: deletion scheduled", tenant.id)
        scheduled.append(tenant.id)
    return scheduled


def run_deletion(db: Session, org: OrgContext, now: datetime | None = None) -> bool:
    """The job's work: delete the business once its deletion is due. Safe to repeat."""
    now = now or datetime.now(UTC)
    tenant = _tenant(db, org)
    due = tenant.deletion_due_at
    if tenant.deleted_at is not None or due is None or due > now:
        return False
    if tenant.deletion_reason == RETENTION and access(tenant, now) is Access.FULL:
        cancel_deletion(db, org, by_subscribing=True)
        return False

    _cancel_subscription(tenant)
    try:
        disconnect(db, org)
    except NotConnected:
        pass
    told = [user.email for user in owners(db, org)]
    storage = get_storage()
    for key in _files(db, org):
        storage.delete(key)
    counts = erase_business(db, tenant.id)
    tenant.name, tenant.address, tenant.contact_email = TOMBSTONE, None, None
    tenant.stripe_customer_id = tenant.stripe_subscription_id = None
    tenant.stripe_subscription_item_id = None
    tenant.deleted_at = now
    db.flush()
    log.info("tenant %s deleted: %d rows", tenant.id, sum(counts.values()))
    for email in told:
        html, text = render_pair("email/deletion_done", day=now)
        send_email(
            db, tenant.id, email, "Your Vontry business is deleted", html, "deletion_done", text
        )
    # The confirmation's own log rows hold the Owners' addresses: they go too (SAAS-D-05).
    erase_business(db, tenant.id)
    return True


def _files(db: Session, org: OrgContext) -> list[str]:
    keys = [doc.storage_key for doc in db.scalars(select_scoped(org, Document))]
    keys += [row.storage_key for row in db.scalars(select_scoped(org, SalesImport))]
    keys += [
        row.storage_key for row in db.scalars(select_scoped(org, DataExport)) if row.storage_key
    ]
    return keys


def _cancel_subscription(tenant: Tenant) -> None:
    """Stop billing before the business is gone; Stripe already done with it is fine."""
    if not tenant.stripe_subscription_id:
        return
    from app.billing import stripe_client

    stripe: Any = stripe_client()
    try:
        stripe.Subscription.cancel(tenant.stripe_subscription_id)
    except Exception as exc:
        if getattr(exc, "code", None) != "resource_missing":
            raise


def _tell(db: Session, org: OrgContext, kind: str, tenant: Tenant, **context: object) -> None:
    link = f"{get_settings().app_base_url}/app/settings/deletion"
    for user in owners(db, org):
        html, text = render_pair(
            f"email/{kind}", business=tenant.name, due=tenant.deletion_due_at, link=link, **context
        )
        send_email(
            db,
            org.tenant_id,
            user.email,
            f"{tenant.name} will be deleted",
            html,
            kind,
            text,
        )
