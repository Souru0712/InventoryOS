"""Two-step sign-in pages (T1.11, SAAS-A-07): turning it on and off, and the code at sign-in.

Every code check counts against the person (MFA_ACCOUNT), so codes cannot be guessed through
any of these forms. Turning it on ends the person's other sessions, as a password change does.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.crypto import CryptoUnavailable
from app.db import get_db
from app.mfa import (
    challenged_user,
    check_code,
    disable,
    enable,
    enrolment_secret,
    qr_svg,
    recovery_codes_left,
    regenerate_recovery_codes,
)
from app.models import Tenant, User
from app.security import login_user, require_tenant, require_user, revoke_sessions, verify_password
from app.throttle import LOGIN_ACCOUNT, MFA_ACCOUNT, clear, hit, locked_for, retry_after
from app.transform.mfa import grouped
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

PAGE = "/app/settings/mfa"
TOO_MANY = "Too many codes tried. Wait a few minutes and try again."
WRONG = "That code didn't work. Check the time on your phone, or use a recovery code."


def _page(
    request: Request,
    db: Session,
    tenant: Tenant,
    user: User,
    *,
    codes: list[str] | None = None,
    error: str | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
):
    context: dict = {
        "title": "Two-step sign-in",
        "tenant": tenant,
        "user": user,
        "codes": codes,
        "error": error,
        "enabled": user.mfa_enabled_at is not None,
    }
    if user.mfa_enabled_at is not None:
        context["codes_left"] = recovery_codes_left(db, user)
    else:
        try:
            secret = enrolment_secret(db, user)
            db.commit()
            context |= {"qr": qr_svg(secret, user.email), "setup_key": grouped(secret)}
        except CryptoUnavailable:
            log.error("two-step sign-in cannot be set up: no usable ENCRYPTION_KEY")
            context["unavailable"] = True
    return templates.TemplateResponse(
        request, "app/mfa.html", context, status_code=status_code, headers=headers
    )


def _locked(db: Session, user: User) -> dict[str, str] | None:
    wait = locked_for(db, MFA_ACCOUNT, str(user.id))
    return retry_after(wait) if wait else None


def _wrong(db: Session, user: User) -> None:
    hit(db, MFA_ACCOUNT, str(user.id))
    db.commit()


@router.get(PAGE, response_class=HTMLResponse)
def mfa_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
):
    return _page(request, db, tenant, user)


@router.post(f"{PAGE}/enable")
def turn_on(
    request: Request,
    code: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
):
    """Confirm the authenticator with one code; show the recovery codes once."""
    if headers := _locked(db, user):
        return _page(request, db, tenant, user, error=TOO_MANY, status_code=429, headers=headers)
    try:
        codes = enable(db, user, code)
    except CryptoUnavailable:
        return _page(request, db, tenant, user, status_code=503)
    if codes is None:
        _wrong(db, user)
        return _page(request, db, tenant, user, error=WRONG, status_code=400)
    clear(db, MFA_ACCOUNT, str(user.id))
    # Any session that signed in without the code ends; this browser carries on in a new one.
    revoke_sessions(db, user.id)
    login_user(request, db, user)
    db.commit()
    return _page(request, db, tenant, user, codes=codes)


@router.post(f"{PAGE}/recovery-codes")
def new_codes(
    request: Request,
    code: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
):
    if headers := _locked(db, user):
        return _page(request, db, tenant, user, error=TOO_MANY, status_code=429, headers=headers)
    if check_code(db, user, code) is None:
        _wrong(db, user)
        return _page(request, db, tenant, user, error=WRONG, status_code=400)
    codes = regenerate_recovery_codes(db, user)
    db.commit()
    return _page(request, db, tenant, user, codes=codes)


@router.post(f"{PAGE}/disable")
def turn_off(
    request: Request,
    password: str = Form(""),
    code: str = Form(""),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
):
    """Needs the password and a code: a borrowed session alone cannot remove the second step."""
    if headers := _locked(db, user):
        return _page(request, db, tenant, user, error=TOO_MANY, status_code=429, headers=headers)
    if not verify_password(password, user.password_hash) or check_code(db, user, code) is None:
        _wrong(db, user)
        return _page(
            request,
            db,
            tenant,
            user,
            error="The password or the code was not right.",
            status_code=400,
        )
    disable(db, user)
    db.commit()
    return RedirectResponse(f"{PAGE}?off=1", status_code=303)


# --- the second step of signing in --------------------------------------------------------------


def _challenge(request: Request, error: str | None = None, status_code: int = 200, headers=None):
    return templates.TemplateResponse(
        request,
        "auth/mfa_challenge.html",
        {"title": "Two-step sign-in", "error": error},
        status_code=status_code,
        headers=headers,
    )


@router.get("/login/code", response_class=HTMLResponse)
def code_page(request: Request, db: Session = Depends(get_db)):
    if challenged_user(request, db) is None:
        return RedirectResponse("/login", status_code=303)
    return _challenge(request)


@router.post("/login/code")
def finish_sign_in(request: Request, code: str = Form(""), db: Session = Depends(get_db)):
    user = challenged_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if headers := _locked(db, user):
        return _challenge(request, TOO_MANY, 429, headers)
    try:
        kind = check_code(db, user, code)
    except CryptoUnavailable:
        log.error("user %s could not be checked: no usable ENCRYPTION_KEY", user.id)
        return _challenge(request, "Signing in is unavailable right now. Try again later.", 503)
    if kind is None:
        _wrong(db, user)
        return _challenge(request, WRONG, 401)
    clear(db, MFA_ACCOUNT, str(user.id))
    clear(db, LOGIN_ACCOUNT, user.email)
    login_user(request, db, user)
    db.commit()
    if kind == "recovery":
        return RedirectResponse(f"{PAGE}?used=recovery", status_code=303)
    return RedirectResponse("/app", status_code=303)
