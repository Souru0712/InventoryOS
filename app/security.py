"""Password hashing and server-side sessions. Tenant isolation starts here."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped
from app.access.permissions import known, role_permissions
from app.access.system import memberships_of
from app.config import get_settings
from app.db import get_db
from app.models import AuthToken, Location, Membership, Tenant, User, UserSession
from app.observability import bind

# The only sign-in state in the cookie: a random token naming a row in `sessions` (SAAS-A-06).
SESSION_TOKEN_KEY = "sid"  # noqa: S105 (the name of a cookie field, not a secret)
SESSION_STATE_KEY = "oauth_state"
# Recording every request would write on every page; this often is enough for the idle timeout.
TOUCH_EVERY = timedelta(minutes=5)
# How long an emailed link works (SAAS-A-05: reset links expire within the hour).
TOKEN_LIFETIME = {"verify_email": timedelta(hours=48), "reset_password": timedelta(hours=1)}
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _session_for(db: Session, token: str | None) -> UserSession | None:
    if not token:
        return None
    return db.scalars(select(UserSession).where(UserSession.token_hash == _hash(token))).first()


def login_user(request: Request, db: Session, user: User) -> UserSession:
    """Start a new server-side session. The token is new at every sign-in (no fixation), and
    whatever this browser held before, the earlier session and its form token, is ended."""
    old = _session_for(db, request.session.get(SESSION_TOKEN_KEY))
    now = datetime.now(UTC)
    if old is not None and old.revoked_at is None:
        old.revoked_at = now
    token = secrets.token_urlsafe(32)
    days = get_settings().session_max_days
    session = UserSession(
        token_hash=_hash(token),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=days),
    )
    db.add(session)
    request.session.clear()
    request.session[SESSION_TOKEN_KEY] = token
    return session


def logout_user(request: Request, db: Session) -> None:
    """End this browser's session on the server, then forget it in the cookie."""
    row = _session_for(db, request.session.get(SESSION_TOKEN_KEY))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
    request.session.clear()


def revoke_sessions(db: Session, user_id: int) -> int:
    """End every session a person holds: "log out all devices", and a changed password (T1.08)."""
    result = db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    return int(getattr(result, "rowcount", 0) or 0)


def revoke_membership(db: Session, membership: Membership) -> None:
    """Take a member's access to one business away, ending the sessions acting for it now
    (STF-FR-06). Their access ends on their next request either way; this ends the session too."""
    membership.status = "revoked"
    db.execute(
        update(UserSession)
        .where(UserSession.membership_id == membership.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def issue_token(db: Session, user: User, purpose: str) -> str:
    """A new single-use link token for user. Earlier unused links for the same purpose stop
    working, so only the latest email opens anything."""
    now = datetime.now(UTC)
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    db.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=_hash(token),
            created_at=now,
            expires_at=now + TOKEN_LIFETIME[purpose],
        )
    )
    return token


def redeem_token(db: Session, token: str, purpose: str) -> User | None:
    """The person a live link belongs to, marking the link used; None when the link is unknown,
    used, expired or for something else. Found by hash, so nothing compares the token itself."""
    row = db.scalars(select(AuthToken).where(AuthToken.token_hash == _hash(token))).first()
    now = datetime.now(UTC)
    if row is None or row.purpose != purpose or row.used_at is not None or now >= row.expires_at:
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    row.used_at = now
    return user


def token_is_live(db: Session, token: str, purpose: str) -> bool:
    """Whether a link would still work, without using it (to show the form it leads to)."""
    row = db.scalars(select(AuthToken).where(AuthToken.token_hash == _hash(token))).first()
    return (
        row is not None
        and row.purpose == purpose
        and row.used_at is None
        and datetime.now(UTC) < row.expires_at
    )


def set_password(db: Session, user: User, password: str) -> None:
    """Change the password and end every session the person has (AC-SAAS-04)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use at least {MIN_PASSWORD_LENGTH} characters for your password.")
    user.password_hash = hash_password(password)
    revoke_sessions(db, user.id)


def new_state() -> str:
    return secrets.token_urlsafe(32)


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """The person this browser's session belongs to, if it is live: not revoked, not past its
    absolute end, not idle too long, and the account still active."""
    row = _session_for(db, request.session.get(SESSION_TOKEN_KEY))
    now = datetime.now(UTC)
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    if (
        row is None
        or row.revoked_at is not None
        or now >= row.expires_at
        or now - row.last_seen_at >= idle
    ):
        request.session.pop(SESSION_TOKEN_KEY, None)
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    if now - row.last_seen_at >= TOUCH_EVERY:
        row.last_seen_at = now
        db.commit()
    request.state.auth_session = row
    request.state.email_verified = user.email_verified_at is not None
    bind(user_id=user.id)
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="login required",
            headers={"Location": "/login"},
        )
    return user


def require_verified(user: User = Depends(require_user)) -> User:
    """Admits only a person who confirmed their email: connecting a POS, reading invoices and
    photos with AI, and inviting staff wait for it (SAAS-A-02, AC-SAAS-02)."""
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=403,
            detail="Confirm your email address first: use the link we sent, or ask for a new "
            "one from Settings.",
        )
    return user


def require_membership(
    request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> Membership:
    """The business this request acts for: the one the person switched to, else their first.

    Only an active membership counts (SAAS-T-03); a revoked one is as if it never existed.
    """
    options = memberships_of(db, user.id)
    if not options:
        raise HTTPException(status_code=403, detail="no workspace for this account")
    chosen = request.state.auth_session.membership_id
    membership = next((m for m, _name in options if m.id == chosen), options[0][0])
    # For the switcher in the page header: (membership id, business name, is it this one).
    request.state.businesses = [(m.id, name, m.id == membership.id) for m, name in options]
    # For templates: menus and cost columns follow the role (USER_ROLES_AND_PERMISSIONS §5).
    request.state.permissions = role_permissions(membership.role)
    bind(tenant_id=membership.tenant_id)
    return membership


def require_org(membership: Membership = Depends(require_membership)) -> OrgContext:
    """The member's scope; every product query in the request runs in it (C-33)."""
    return OrgContext.for_membership(membership)


def require_permission(key: str):
    """A dependency that admits only members whose role holds key, and gives the route its
    OrgContext. Every product route declares one (a test enforces it); deny by default."""
    known(key)

    def check(org: OrgContext = Depends(require_org)) -> OrgContext:
        if not org.can(key):
            raise HTTPException(status_code=403, detail="Your role does not allow this.")
        return org

    check.permission = key  # type: ignore[attr-defined]  # read by the declaration test
    return check


def require_tenant(org: OrgContext = Depends(require_org), db: Session = Depends(get_db)) -> Tenant:
    tenant = get_scoped(db, org, Tenant, org.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=403, detail="no workspace for this account")
    return tenant


def location_filter(
    location: int | None = None,
    db: Session = Depends(get_db),
    org: OrgContext = Depends(require_org),
) -> int | None:
    """An optional ?location= narrowing a page; a location outside the organization is not found."""
    if location is not None and get_scoped(db, org, Location, location) is None:
        raise HTTPException(status_code=404, detail="location not found")
    return location


def require_writable(
    tenant: Tenant = Depends(require_tenant),
) -> Tenant:
    """A tenant that may still change its inventory: the trial is live, or the bill is paid.

    A read-only account keeps every page and every number; this guards only what writes.
    """
    from app.entitlements import require

    require(tenant, "inventory_write")
    return tenant


def choose_membership(request: Request, db: Session, membership_id: int) -> None:
    """Remember on the server which business this session acts for."""
    request.state.auth_session.membership_id = membership_id
    db.commit()


def email_taken(db: Session, email: str) -> bool:
    return db.scalars(select(User).where(User.email == email.lower())).first() is not None
