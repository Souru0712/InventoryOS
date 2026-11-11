"""The team page and the invitation link (T1.10; STF-FR-01, -06, -07; SAAS-A-08).

Owners and Administrators invite people by email with a role and a location scope, change a
member's role, and remove members. The emailed link opens a page whose button posts the token;
the person joins as the invited address only, signing in or creating the account on the spot.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped, team
from app.access.system import user_by_email
from app.db import get_db
from app.models import Invitation, Location, Tenant, User
from app.security import (
    MIN_PASSWORD_LENGTH,
    current_user,
    hash_password,
    login_user,
    require_permission,
    require_tenant,
    require_user,
    require_verified,
    require_writable,
    verify_password,
)
from app.team import (
    NotFound,
    accept,
    business_name,
    change_role,
    invite,
    live_invitation,
    open_invitations,
    remove,
    send_invitation,
    status_of,
    withdraw,
)
from app.throttle import (
    INVITE_ACCOUNT,
    LINK_IP,
    LOGIN_ACCOUNT,
    LOGIN_IP,
    clear,
    client_ip,
    hit,
    locked_for,
    retry_after,
)
from app.transform.team import DEFAULT_ROLE, ROLE_LABELS, TeamRuleError
from app.web.flash import flash, pop_flash
from app.web.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

TEAM = "/app/team"


def _back() -> RedirectResponse:
    return RedirectResponse(TEAM, status_code=303)


@router.get(TEAM, response_class=HTMLResponse)
def team_page(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    user: User = Depends(require_user),
    org: OrgContext = Depends(require_permission("team.manage")),
):
    locations = list(db.scalars(select_scoped(org, Location).order_by(Location.name)))
    roles = {
        key: label
        for key, label in ROLE_LABELS.items()
        if key != "owner" or org.can("owner.manage")
    }
    return templates.TemplateResponse(
        request,
        "app/team.html",
        {
            "title": "Team",
            "tenant": tenant,
            "user": user,
            "members": [(m, person) for m, person in team(db, org) if m.status == "active"],
            "invitations": [(i, status_of(i)) for i in open_invitations(db, org)],
            "locations": locations,
            "location_names": {location.id: location.name for location in locations},
            "roles": roles,
            "role_labels": ROLE_LABELS,
            "default_role": DEFAULT_ROLE,
            "can_manage_owners": org.can("owner.manage"),
            "message": pop_flash(request),
        },
    )


@router.post(f"{TEAM}/invite")
def invite_member(
    request: Request,
    email: str = Form(""),
    role: str = Form(DEFAULT_ROLE),
    scope: str = Form("all"),
    location_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
    user: User = Depends(require_verified),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("team.manage")),
):
    """Email someone a link to join (AC-STF-01). Waits for a confirmed address (SAAS-A-02)."""
    sender = str(user.id)
    if locked_for(db, INVITE_ACCOUNT, sender):
        flash(request, "That's a lot of invitations for one hour. Try again later.", "error")
        return _back()
    try:
        invitation, token = invite(db, org, user, email, role, scope != "some", location_ids)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeamRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return _back()
    hit(db, INVITE_ACCOUNT, sender)
    send_invitation(db, invitation, token, tenant.name)
    db.commit()
    flash(request, "Invitation sent. The link works for 7 days.")
    return _back()


@router.post(TEAM + "/invitations/{invitation_id}/withdraw")
def withdraw_invitation(
    request: Request,
    invitation_id: int,
    db: Session = Depends(get_db),
    org: OrgContext = Depends(require_permission("team.manage")),
):
    try:
        withdraw(db, org, invitation_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    flash(request, "Invitation withdrawn. Its link no longer works.")
    return _back()


@router.post(TEAM + "/{membership_id}/role")
def change_member_role(
    request: Request,
    membership_id: int,
    role: str = Form(""),
    scope: str = Form("all"),
    location_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_writable),
    org: OrgContext = Depends(require_permission("team.manage")),
):
    """A new role or scope, in force from the member's next request (STF-FR-06)."""
    try:
        change_role(db, org, membership_id, role, scope != "some", location_ids)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeamRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return _back()
    db.commit()
    flash(request, "Access updated.")
    return _back()


@router.post(TEAM + "/{membership_id}/remove")
def remove_member(
    request: Request,
    membership_id: int,
    db: Session = Depends(get_db),
    org: OrgContext = Depends(require_permission("team.manage")),
):
    """End a member's access now, sessions included (AC-STF-04). Never waits on billing:
    taking access away must always work."""
    try:
        remove(db, org, membership_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TeamRuleError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return _back()
    db.commit()
    flash(request, "Removed from the team. Their access has ended.")
    return _back()


# --- the emailed link ---------------------------------------------------------------------------


def _accept_page(
    request: Request,
    db: Session,
    invitation: Invitation | None,
    user: User | None,
    token: str,
    *,
    error: str | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
):
    context: dict = {"title": "Join a business", "token": token, "error": error, "state": "gone"}
    if invitation is not None:
        if user is not None:
            state = "join" if user.email == invitation.email else "other"
        else:
            account = user_by_email(db, invitation.email)
            if account is None:
                state = "create"
            else:
                state = "log_in_first" if account.mfa_enabled_at else "sign_in"
        context |= {
            "state": state,
            "business": business_name(db, invitation),
            "role": ROLE_LABELS[invitation.role],
            "email": invitation.email,
            "min_password": MIN_PASSWORD_LENGTH,
        }
    return templates.TemplateResponse(
        request, "auth/accept_invitation.html", context, status_code=status_code, headers=headers
    )


@router.get("/invitations/accept", response_class=HTMLResponse)
def invitation_page(
    request: Request,
    token: str = "",
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    return _accept_page(request, db, live_invitation(db, token), user, token)


@router.post("/invitations/accept")
def accept_invitation(
    request: Request,
    token: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    """Join as the invited address: signed in already, by its password, or as a new account."""
    ip = client_ip(request)
    wait = locked_for(db, LINK_IP, ip)
    invitation = None if wait else live_invitation(db, token)
    if invitation is None:
        if wait is None:
            hit(db, LINK_IP, ip)
            db.commit()
        return _accept_page(
            request,
            db,
            None,
            user,
            "",
            status_code=429 if wait else 400,
            headers=retry_after(wait) if wait else None,
        )

    if user is not None:
        if user.email != invitation.email:
            return _accept_page(request, db, invitation, user, token, status_code=403)
        membership = accept(db, invitation, user)
        request.state.auth_session.membership_id = membership.id
        db.commit()
        return RedirectResponse("/app?joined=1", status_code=303)

    account = user_by_email(db, invitation.email)
    if account is not None and account.mfa_enabled_at is not None:
        # A password alone never signs in an account with two-step sign-in (SAAS-A-07).
        return _accept_page(request, db, invitation, None, token, status_code=403)
    if account is not None:
        # Signing in, so it counts like the sign-in form (SAAS-A-03).
        waits = [locked_for(db, LOGIN_ACCOUNT, account.email), locked_for(db, LOGIN_IP, ip)]
        login_wait = max((w for w in waits if w is not None), default=None)
        if login_wait is not None:
            return _accept_page(
                request,
                db,
                invitation,
                None,
                token,
                error="Too many attempts. Wait a few minutes and try again.",
                status_code=429,
                headers=retry_after(login_wait),
            )
        if not account.is_active or not verify_password(password, account.password_hash):
            hit(db, LOGIN_ACCOUNT, account.email)
            hit(db, LOGIN_IP, ip)
            db.commit()
            return _accept_page(
                request,
                db,
                invitation,
                None,
                token,
                error="That password is not right.",
                status_code=401,
            )
        clear(db, LOGIN_ACCOUNT, account.email)
    else:
        if len(password) < MIN_PASSWORD_LENGTH:
            return _accept_page(
                request,
                db,
                invitation,
                None,
                token,
                error=f"Use at least {MIN_PASSWORD_LENGTH} characters for your password.",
                status_code=400,
            )
        account = User(
            tenant_id=invitation.tenant_id,
            email=invitation.email,
            password_hash=hash_password(password),
            email_verified_at=datetime.now(UTC),
        )
        db.add(account)
        db.flush()
        log.info("user %s created from invitation %s", account.id, invitation.id)

    membership = accept(db, invitation, account)
    session = login_user(request, db, account)
    session.membership_id = membership.id
    db.commit()
    return RedirectResponse("/app?joined=1", status_code=303)
