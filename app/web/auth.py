"""Signup, login, logout. One workspace per signup."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.entitlements import start_trial
from app.models import Tenant, User
from app.security import email_taken, hash_password, login_user, logout_user, verify_password
from app.web.templating import templates

router = APIRouter()

MIN_PASSWORD_LENGTH = 10


@router.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse(request, "auth/signup.html", {"title": "Start free"})


@router.post("/signup")
def signup(
    request: Request,
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
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
            },
            status_code=400,
        )

    tenant = start_trial(Tenant(name=business_name.strip() or "My business"))
    db.add(tenant)
    db.flush()
    user = User(tenant_id=tenant.id, email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()

    login_user(request, user)
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
    user = db.scalars(select(User).where(User.email == email.strip().lower())).first()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"title": "Log in", "error": "Email or password is not right.", "email": email},
            status_code=401,
        )
    login_user(request, user)
    return RedirectResponse("/app", status_code=303)


@router.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/", status_code=303)
