"""Signup, login, logout, and switching between the businesses a person belongs to."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access.system import memberships_of, user_by_email
from app.db import get_db
from app.entitlements import PLAN_IDS, start_trial
from app.mfa import start_challenge
from app.models import Membership, Tenant, User
from app.security import (
    MIN_PASSWORD_LENGTH,
    choose_membership,
    email_taken,
    hash_password,
    login_user,
    logout_user,
    require_user,
    revoke_sessions,
    verify_password,
)
from app.throttle import LOGIN_ACCOUNT, LOGIN_IP, clear, client_ip, hit, locked_for, retry_after
from app.web.accounts import send_link
from app.web.templating import templates

router = APIRouter()

TOO_MANY = "Too many attempts. Wait a few minutes and try again, or reset your password."


@router.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, plan: str = ""):
    return templates.TemplateResponse(
        request,
        "auth/signup.html",
        {"title": "Start free", "plan": plan if plan in PLAN_IDS else ""},
    )


@router.post("/signup")
def signup(
    request: Request,
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    plan: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if len(password) < MIN_PASSWORD_LENGTH:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {
                "title": "Start free",
                "error": f"Use at least {MIN_PASSWORD_LENGTH} characters for your password.",
                "business_name": business_name,
                "email": email,
                "plan": plan if plan in PLAN_IDS else "",
            },
            status_code=400,
        )
    if email_taken(db, email):
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {
                "title": "Start free",
                "error": "That email already has an account. Log in instead.",
                "business_name": business_name,
                "email": email,
                "plan": plan if plan in PLAN_IDS else "",
            },
            status_code=400,
        )

    tenant = start_trial(Tenant(name=business_name.strip() or "My business"))
    db.add(tenant)
    db.flush()
    user = User(tenant_id=tenant.id, email=email, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    # Whoever creates a business owns it (WF-01).
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role="owner"))
    login_user(request, db, user)
    db.flush()
    send_link(db, user, "verify_email")
    db.commit()
    # Someone who picked a plan on the way in goes straight to paying for it; everyone else
    # starts their trial in the app. The account exists either way, so nothing is orphaned
    # in Stripe and nobody types their details twice.
    if plan in PLAN_IDS:
        return templates.TemplateResponse(
            request, "auth/to_checkout.html", {"title": "One more step", "plan": plan}
        )
    return RedirectResponse("/app", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "auth/login.html", {"title": "Log in"})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    waits = [locked_for(db, LOGIN_ACCOUNT, email), locked_for(db, LOGIN_IP, ip)]
    wait = max((w for w in waits if w is not None), default=None)
    if wait is not None:
        # The same answer whether or not the account exists, so it reveals nothing (SAAS-A-03).
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"title": "Log in", "error": TOO_MANY, "email": email},
            status_code=429,
            headers=retry_after(wait),
        )
    user = user_by_email(db, email)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        hit(db, LOGIN_ACCOUNT, email)
        hit(db, LOGIN_IP, ip)
        db.commit()
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"title": "Log in", "error": "Email or password is not right.", "email": email},
            status_code=401,
        )
    if user.mfa_enabled_at is not None:
        # Half signed in: whatever this browser held before ends, and a code finishes it
        # (SAAS-A-07). The account's count clears only then.
        logout_user(request, db)
        start_challenge(request, user)
        db.commit()
        return RedirectResponse("/login/code", status_code=303)
    # The account's count starts again; the address's does not, or one good login from an
    # attacker's own account would wipe the guesses made at everyone else's.
    clear(db, LOGIN_ACCOUNT, email)
    login_user(request, db, user)
    db.commit()
    return RedirectResponse("/app", status_code=303)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    """A POST, so a link or an image on another page cannot sign anyone out (S4)."""
    logout_user(request, db)
    db.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/app/sessions/end-all")
def log_out_everywhere(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """End every session this person has, on every device, this one included (SAAS-A-04)."""
    revoke_sessions(db, user.id)
    request.session.clear()
    db.commit()
    return RedirectResponse("/login", status_code=303)


@router.post("/app/organizations/switch")
def switch_business(
    request: Request,
    membership_id: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Act for another business this person belongs to (SAAS-A-09); every page follows."""
    if not any(membership.id == membership_id for membership, _ in memberships_of(db, user.id)):
        raise HTTPException(status_code=404, detail="business not found")
    choose_membership(request, db, membership_id)
    return RedirectResponse("/app", status_code=303)
