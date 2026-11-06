"""Password hashing and session-backed auth. Tenant isolation starts here."""

from __future__ import annotations

import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped
from app.db import get_db
from app.models import Location, Tenant, User
from app.observability import bind

SESSION_USER_KEY = "uid"
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
        bind(tenant_id=user.tenant_id, user_id=user.id)
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


def require_tenant(user: User = Depends(require_user), db: Session = Depends(get_db)) -> Tenant:
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=403, detail="no workspace for this account")
    return tenant


def require_org(user: User = Depends(require_user)) -> OrgContext:
    """The signed-in user's scope; every product query in the request runs in it (C-33)."""
    return OrgContext.for_user(user)


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
