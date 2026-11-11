"""Confirming an email address, resetting a forgotten password, changing a password (T1.08).

Emailed links open a page whose button posts the token, so a mail scanner that fetches every
link cannot use one up. Asking for a reset says the same whether or not the account exists.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access.system import user_by_email
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.notify.email import render_pair, send_email
from app.security import (
    issue_token,
    login_user,
    redeem_token,
    require_user,
    set_password,
    token_is_live,
    verify_password,
)
from app.throttle import (
    LINK_IP,
    RESEND_ACCOUNT,
    RESET_ACCOUNT,
    RESET_IP,
    client_ip,
    hit,
    locked_for,
    retry_after,
)
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

SUBJECTS = {
    "verify_email": "Confirm your email for StockPulse",
    "reset_password": "Reset your StockPulse password",
}
PAGES = {"verify_email": "/verify-email", "reset_password": "/password/reset"}


def send_link(db: Session, user: User, purpose: str) -> bool:
    """Email a fresh single-use link; earlier ones for the same purpose stop working."""
    token = issue_token(db, user, purpose)
    link = f"{get_settings().app_base_url.rstrip('/')}{PAGES[purpose]}?token={token}"
    html, text = render_pair(f"email/{purpose}", link=link, email=user.email)
    return send_email(db, user.tenant_id, user.email, SUBJECTS[purpose], html, purpose, text)


# --- confirming the address ---------------------------------------------------------------------


@router.get("/verify-email", response_class=HTMLResponse)
def verify_email_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    live = bool(token) and token_is_live(db, token, "verify_email")
    return templates.TemplateResponse(
        request,
        "auth/verify_email.html",
        {"title": "Confirm your email", "live": live, "token": token},
    )


@router.post("/verify-email")
def verify_email(request: Request, token: str = Form(""), db: Session = Depends(get_db)):
    ip = client_ip(request)
    wait = locked_for(db, LINK_IP, ip)
    user = None if wait else (redeem_token(db, token, "verify_email") if token else None)
    if user is None:
        if wait is None:
            hit(db, LINK_IP, ip)
            db.commit()
        return templates.TemplateResponse(
            request,
            "auth/verify_email.html",
            {"title": "Confirm your email", "live": False, "token": ""},
            status_code=429 if wait else 400,
            headers=retry_after(wait) if wait else None,
        )
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    db.commit()
    log.info("user %s confirmed their email", user.id)
    return RedirectResponse("/app?verified=1", status_code=303)


@router.post("/app/verify-email/resend")
def resend_verification(db: Session = Depends(get_db), user: User = Depends(require_user)):
    if user.email_verified_at is not None:
        return RedirectResponse("/app/settings", status_code=303)
    if locked_for(db, RESEND_ACCOUNT, user.email):
        return RedirectResponse("/app/settings?verification=wait", status_code=303)
    hit(db, RESEND_ACCOUNT, user.email)
    send_link(db, user, "verify_email")
    db.commit()
    return RedirectResponse("/app/settings?verification=sent", status_code=303)


# --- a forgotten password -----------------------------------------------------------------------


@router.get("/password/forgot", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request, "auth/forgot_password.html", {"title": "Forgot your password?", "sent": False}
    )


@router.post("/password/forgot", response_class=HTMLResponse)
def forgot_password(request: Request, email: str = Form(""), db: Session = Depends(get_db)):
    ip = client_ip(request)
    # A few emails an hour per address asking and per account asked about; past that, nothing
    # is sent, and the page still reads the same.
    if email.strip() and not locked_for(db, RESET_IP, ip):
        hit(db, RESET_IP, ip)
        if not locked_for(db, RESET_ACCOUNT, email):
            hit(db, RESET_ACCOUNT, email)
            user = user_by_email(db, email)
            if user is not None and user.is_active:
                send_link(db, user, "reset_password")
        db.commit()
    # The same answer either way: the page must not tell anyone which emails have accounts.
    return templates.TemplateResponse(
        request,
        "auth/forgot_password.html",
        {"title": "Check your email", "sent": True, "email": email.strip()},
    )


@router.get("/password/reset", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    live = bool(token) and token_is_live(db, token, "reset_password")
    return templates.TemplateResponse(
        request,
        "auth/reset_password.html",
        {"title": "Choose a new password", "live": live, "token": token},
    )


@router.post("/password/reset")
def reset_password(
    request: Request,
    token: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    context = {"title": "Choose a new password", "live": True, "token": token}
    ip = client_ip(request)
    wait = locked_for(db, LINK_IP, ip)
    if wait or not token or not token_is_live(db, token, "reset_password"):
        context["live"] = False
        if wait is None:
            hit(db, LINK_IP, ip)
            db.commit()
        return templates.TemplateResponse(
            request,
            "auth/reset_password.html",
            context,
            status_code=429 if wait else 400,
            headers=retry_after(wait) if wait else None,
        )
    try:
        user = redeem_token(db, token, "reset_password")
        if user is None:
            raise ValueError("That link has expired.")
        set_password(db, user, password)
    except ValueError as exc:
        db.rollback()
        context["error"] = str(exc)
        return templates.TemplateResponse(
            request, "auth/reset_password.html", context, status_code=400
        )
    # The reset email reached them, which proves the address too.
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    request.session.clear()
    db.commit()
    log.info("user %s reset their password; every session ended", user.id)
    return RedirectResponse("/login?reset=1", status_code=303)


# --- changing a password while signed in --------------------------------------------------------


@router.post("/app/settings/password")
def change_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """End every other session, keep this browser signed in on a fresh one (AC-SAAS-04)."""
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse("/app/settings?password=wrong", status_code=303)
    try:
        set_password(db, user, new_password)
    except ValueError:
        db.rollback()
        return RedirectResponse("/app/settings?password=short", status_code=303)
    login_user(request, db, user)
    db.commit()
    log.info("user %s changed their password; other sessions ended", user.id)
    return RedirectResponse("/app/settings?password=changed", status_code=303)
