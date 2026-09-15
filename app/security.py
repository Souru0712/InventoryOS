"""Password hashing and session-backed auth. Tenant isolation starts here."""
from __future__ import annotations

import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Customer, Tenant, User

SESSION_USER_KEY = "uid"
SESSION_STATE_KEY = "oauth_state"
SESSION_CUSTOMER_KEY = "cid"


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
    return user if user and user.is_active else None


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="login required",
            headers={"Location": "/login"},
        )
    return user


def require_tenant(
    user: User = Depends(require_user), db: Session = Depends(get_db)
) -> Tenant:
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=403, detail="no workspace for this account")
    return tenant


def email_taken(db: Session, email: str) -> bool:
    return db.scalars(select(User).where(User.email == email.lower())).first() is not None


def login_customer(request: Request, customer: Customer) -> None:
    """A shopper signs in beside, never instead of, a store login in the same browser."""
    request.session[SESSION_CUSTOMER_KEY] = customer.id


def logout_customer(request: Request) -> None:
    request.session.pop(SESSION_CUSTOMER_KEY, None)


def current_customer(tenant_id: int, request: Request, db: Session = Depends(get_db)) -> Customer | None:
    customer_id = request.session.get(SESSION_CUSTOMER_KEY)
    if not customer_id:
        return None
    customer = db.get(Customer, customer_id)
    # A shopper at one store is nobody at another, whatever the cookie says.
    if customer is None or not customer.is_active or customer.tenant_id != tenant_id:
        return None
    return customer


def require_customer(tenant_id: int, customer: Customer | None = Depends(current_customer)) -> Customer:
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="sign in required",
            headers={"Location": f"/shop/{tenant_id}/login"},
        )
    return customer
