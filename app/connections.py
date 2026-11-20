"""POS connections: connect, refresh, disconnect, revoked (T3.02, ADR-0076; POS-FR-01, SQ-01..04).

The only writer of `provider_connections`. Provider-neutral: a POS is reached through
`adapter_for()` and its normalized `Credentials`, and tokens are kept encrypted
(`app/crypto.py`). A merchant is actively connected to one business at most (DR-22, AC-POS-11).
Disconnecting revokes at the provider, stops ingestion and keeps history (SQ-04). A provider that
stops honouring the authorization marks the connection revoked, opens a critical `sync_failure`
and emails the Owners (AC-POS-09).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.access.scoped import team
from app.access.system import connection_for_merchant
from app.audit import record_audit
from app.config import get_settings
from app.crypto import CryptoUnavailable, decrypt, encrypt
from app.exceptions import clear, mark_notified, record_exception
from app.jobs import enqueue
from app.models import ExceptionRecord, ProviderConnection, SyncRun, SyncStatus, Tenant
from app.notify.email import render_pair, send_email
from app.providers import adapter_for
from app.providers.base import AdapterError, Credentials
from app.transform.exceptions import CRITICAL, OPEN_STATUSES, Finding
from app.transform.team import OWNER

log = logging.getLogger(__name__)

ACTIVE, REVOKED, DISCONNECTED = "active", "revoked", "disconnected"
# Refreshed this long before expiry, so a run never starts on a token about to lapse.
REFRESH_MARGIN = timedelta(days=7)
SUBJECT = "provider_connection"
REVOKED_EMAIL = "connection_revoked_email"
NAMES = {"square": "Square"}


class NotConnected(RuntimeError):
    """No active connection to read from: the business must connect or reconnect."""


class MerchantTaken(RuntimeError):
    """The merchant is actively connected to another business (DR-22); nothing is stored."""


def name_of(provider: str) -> str:
    return NAMES.get(provider, provider.title())


def active_connection(db: Session, org: OrgContext, provider: str = "square") -> ProviderConnection:
    connection = db.scalars(
        select_scoped(org, ProviderConnection)
        .where(ProviderConnection.provider == provider, ProviderConnection.status == ACTIVE)
        .order_by(ProviderConnection.id)
    ).first()
    if connection is None:
        raise NotConnected(f"tenant {org.tenant_id} has no active {provider} connection")
    return connection


def current(db: Session, org: OrgContext, provider: str = "square") -> ProviderConnection | None:
    """The connection the business sees: its active one, else the latest it had."""
    rows = list(
        db.scalars(
            select_scoped(org, ProviderConnection)
            .where(ProviderConnection.provider == provider)
            .order_by(ProviderConnection.id.desc())
        )
    )
    return next((row for row in rows if row.status == ACTIVE), rows[0] if rows else None)


def sync_health(
    db: Session, org: OrgContext, connection: ProviderConnection | None, last_run: SyncRun | None
) -> dict:
    """What the merchant needs to know: is it connected, when did it last work, what broke.

    A failure is only worth showing while it is still the latest word; a later success clears it.
    """
    last_success = db.scalars(
        select_scoped(org, SyncRun)
        .where(SyncRun.status == SyncStatus.SUCCESS)
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()
    failed = last_run if last_run is not None and last_run.status is SyncStatus.FAILED else None
    return {
        "connected": connection is not None and connection.status == ACTIVE,
        "last_success_at": last_success.finished_at if last_success else None,
        "error": (failed.error or "")[:200] if failed else None,
        "failed_at": failed.finished_at if failed else None,
    }


def _finding(connection: ProviderConnection) -> Finding:
    return Finding(
        type="sync_failure",
        subject_type=SUBJECT,
        subject_id=str(connection.id),
        title=f"{name_of(connection.provider)} no longer accepts the connection: reconnect it",
        severity=CRITICAL,
    )


def _store(connection: ProviderConnection, credentials: Credentials) -> None:
    connection.access_token_enc = encrypt(credentials.access_token)
    connection.refresh_token_enc = (
        encrypt(credentials.refresh_token) if credentials.refresh_token else None
    )
    connection.expires_at = credentials.expires_at


def _held(connection: ProviderConnection) -> Credentials:
    if connection.access_token_enc is None:
        raise NotConnected(f"connection {connection.id} holds no token")
    return Credentials(
        access_token=decrypt(connection.access_token_enc),
        refresh_token=decrypt(connection.refresh_token_enc)
        if connection.refresh_token_enc
        else None,
        expires_at=connection.expires_at,
        external_merchant_id=connection.merchant_id,
    )


def connect(
    db: Session,
    org: OrgContext,
    provider: str,
    credentials: Credentials,
    *,
    ip: str | None = None,
) -> ProviderConnection:
    """Store a fresh authorization: a new connection, or the business's own row for that merchant
    made active again. `MerchantTaken` when another business holds the merchant."""
    merchant = credentials.external_merchant_id
    holder = connection_for_merchant(db, provider, merchant)
    if holder is not None and holder.tenant_id != org.tenant_id:
        # The token just obtained is dropped, never revoked: at Square a revocation ends every
        # token the merchant granted, the other business's included.
        raise MerchantTaken(f"{provider} merchant is connected to another business")

    existing = db.scalars(
        select_scoped(org, ProviderConnection).where(
            ProviderConnection.provider == provider, ProviderConnection.merchant_id == merchant
        )
    ).first()
    before = existing.status if existing else None
    connection = existing or ProviderConnection(
        tenant_id=org.tenant_id, provider=provider, merchant_id=merchant
    )
    _store(connection, credentials)
    connection.status = ACTIVE
    db.add(connection)
    try:
        db.flush()
    except IntegrityError as exc:
        # Another business connected the same merchant a moment ago (the unique active index).
        db.rollback()
        raise MerchantTaken(f"{provider} merchant is connected to another business") from exc

    # Reconnecting is the fix for a revoked connection's exception.
    clear(db, org, _finding(connection).key)
    name = name_of(provider)
    record_audit(
        db,
        org,
        "connection.connected",
        f"Reconnected {name}" if before is not None else f"Connected {name}",
        subject_type=SUBJECT,
        subject_id=connection.id,
        before={"status": before} if before else None,
        after={"provider": provider, "merchant": merchant, "status": ACTIVE},
        ip=ip,
    )
    return connection


def credentials(
    db: Session, org: OrgContext, connection: ProviderConnection, now: datetime | None = None
) -> Credentials:
    """The connection's credentials, refreshed first when they are about to lapse. A provider
    that no longer honours them marks the connection revoked and raises `NotConnected` (SQ-01)."""
    if connection.status != ACTIVE:
        raise NotConnected(f"connection {connection.id} is {connection.status}")
    held = _held(connection)
    now = now or datetime.now(UTC)
    if held.expires_at is None or held.expires_at - REFRESH_MARGIN >= now:
        return held
    if held.refresh_token is None and held.expires_at > now:
        return held  # nothing to refresh with, and still good until it expires
    log.info("refreshing the %s token of connection %s", connection.provider, connection.id)
    try:
        fresh = adapter_for(connection.provider).refresh(held)
    except AdapterError as exc:
        if exc.revoked:
            mark_revoked(db, org, connection, now=now)
            raise NotConnected(f"connection {connection.id} was revoked") from exc
        raise
    if fresh.refresh_token is None:
        # Square may answer a refresh without a new refresh token: the one held still works.
        fresh = replace(fresh, refresh_token=held.refresh_token)
    _store(connection, fresh)
    db.flush()
    return fresh


def mark_revoked(
    db: Session, org: OrgContext, connection: ProviderConnection, now: datetime | None = None
) -> bool:
    """The provider no longer honours this connection: stop using it and tell a person
    (AC-POS-09). False when it was not active, and nothing changes."""
    if connection.status != ACTIVE:
        return False
    now = now or datetime.now(UTC)
    connection.status = REVOKED
    db.flush()
    record_exception(db, org, _finding(connection), now=now)
    record_audit(
        db,
        org,
        "connection.revoked",
        f"{name_of(connection.provider)} stopped accepting the connection",
        subject_type=SUBJECT,
        subject_id=connection.id,
        before={"status": ACTIVE},
        after={"status": REVOKED},
    )
    enqueue(
        db,
        org,
        REVOKED_EMAIL,
        {"connection_id": connection.id},
        dedupe_key=f"connection:{connection.id}",
    )
    log.warning("connection %s was revoked by %s", connection.id, connection.provider)
    return True


def revoked_by_provider(db: Session, provider: str, merchant_id: str) -> int | None:
    """A provider's signed notice that the merchant revoked the authorization; the business it
    belonged to, or None when no business holds that merchant."""
    connection = connection_for_merchant(db, provider, merchant_id)
    if connection is None:
        return None
    mark_revoked(db, OrgContext.system(connection.tenant_id), connection)
    return connection.tenant_id


def disconnect(
    db: Session, org: OrgContext, provider: str = "square", *, ip: str | None = None
) -> bool:
    """Let go of the business's active connection: revoke it at the provider, stop ingestion,
    keep history (SQ-04). True when the provider confirmed the revocation; `NotConnected` when
    there is nothing to disconnect."""
    connection = active_connection(db, org, provider)
    confirmed = True
    try:
        adapter_for(provider).revoke(_held(connection))
    except AdapterError as exc:
        # Already revoked there is as good as revoked; otherwise the person is told to remove
        # the app at the provider. The connection is let go of here either way.
        confirmed = exc.revoked
    except CryptoUnavailable:
        confirmed = False
    if not confirmed:
        log.warning("could not revoke connection %s at %s", connection.id, provider)
    connection.status = DISCONNECTED
    connection.access_token_enc = None
    connection.refresh_token_enc = None
    connection.expires_at = None
    db.flush()
    clear(db, org, _finding(connection).key)
    record_audit(
        db,
        org,
        "connection.disconnected",
        f"Disconnected {name_of(provider)}",
        subject_type=SUBJECT,
        subject_id=connection.id,
        before={"status": ACTIVE},
        after={"status": DISCONNECTED, "revoked_at_provider": confirmed},
        ip=ip,
    )
    return confirmed


def email_owners(db: Session, org: OrgContext, connection_id: int) -> int:
    """Tell each active Owner that the connection ended and how to restore it (AC-POS-09); the
    number of emails sent. Once per revocation: the open exception remembers it was sent."""
    connection = get_scoped(db, org, ProviderConnection, connection_id)
    if connection is None or connection.status != REVOKED:
        return 0  # reconnected or disconnected since: nothing left to say
    exception = db.scalars(
        select_scoped(org, ExceptionRecord).where(
            ExceptionRecord.type == "sync_failure",
            ExceptionRecord.subject_type == SUBJECT,
            ExceptionRecord.subject_id == str(connection.id),
            ExceptionRecord.status.in_(OPEN_STATUSES),
        )
    ).first()
    if exception is None or exception.notified_at is not None:
        return 0
    tenant = get_scoped(db, org, Tenant, org.tenant_id)
    business = tenant.name if tenant else "your business"
    provider = name_of(connection.provider)
    link = f"{get_settings().app_base_url.rstrip('/')}/app/connection"
    html, text = render_pair(
        "email/connection_revoked", business=business, provider=provider, link=link
    )
    subject = f"{provider} is no longer connected to {business}"
    sent = 0
    for membership, user in team(db, org):
        if membership.role == OWNER and membership.status == "active" and user.is_active:
            sent += send_email(
                db, org.tenant_id, user.email, subject, html, "connection_revoked", text
            )
    mark_notified([exception], datetime.now(UTC))
    return sent
