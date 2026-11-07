"""Password hashing and session-backed auth. Tenant isolation starts here."""

from __future__ import annotations

import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped
from app.access.permissions import known, role_permissions
from app.access.system import memberships_of
from app.db import get_db
from app.models import Location, Membership, Tenant, User
from app.observability import bind

SESSION_USER_KEY = "uid"
# The membership the person is acting through, so a switch survives across requests.
SESSION_MEMBERSHIP_KEY = "mid"
SESSION_STATE_KEY = "oauth_state"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def login_user(request: Request, user: User) -> None:
    request.session[SESSION_USER_KEY] = user.id
    # A business chosen by whoever used this browser before is not this person's choice.
    request.session.pop(SESSION_MEMBERSHIP_KEY, None)


def logout_user(request: Request) -> None:
    request.session.clear()


def new_state() -> str:
    return secrets.token_urlsafe(32)


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user and user.is_active:
        bind(user_id=user.id)
        return user
    return None


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="login required",
            headers={"Location": "/login"},
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
    chosen = request.session.get(SESSION_MEMBERSHIP_KEY)
    membership = next((m for m, _name in options if m.id == chosen), options[0][0])
    request.session[SESSION_MEMBERSHIP_KEY] = membership.id
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


def email_taken(db: Session, email: str) -> bool:
    return db.scalars(select(User).where(User.email == email.lower())).first() is not None
