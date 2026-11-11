"""Inviting people into a business, changing what they may do, and taking access away (T1.10).

The rules (who may grant or remove which role, the last Owner) are pure, in
app/transform/team.py; this module applies them to rows. Invitations are single-use links bound
to one email address for seven days (SAAS-A-08); only their hash is stored.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access import OrgContext, get_scoped, select_scoped
from app.access.system import invitation_by_token_hash, user_by_email
from app.config import get_settings
from app.models import (
    Invitation,
    InvitationLocation,
    Location,
    Membership,
    MembershipLocation,
    Tenant,
    User,
)
from app.notify.email import render_pair, send_email
from app.security import revoke_membership
from app.transform.team import (
    OWNER,
    ROLE_LABELS,
    TeamRuleError,
    check_change,
    check_grant,
    invitee_email,
    known_role,
    scope_for,
)

log = logging.getLogger(__name__)

INVITATION_LIFETIME = timedelta(days=7)


class NotFound(LookupError):
    """A member, invitation or location that is not this business's: the route answers 404."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def status_of(invitation: Invitation, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if invitation.accepted_at is not None:
        return "accepted"
    if invitation.revoked_at is not None:
        return "revoked"
    return "expired" if now >= invitation.expires_at else "pending"


def open_invitations(db: Session, org: OrgContext) -> list[Invitation]:
    """Invitations nobody accepted or withdrew, newest first: pending, then lapsed ones to resend."""
    return list(
        db.scalars(
            select_scoped(org, Invitation)
            .where(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))
            .order_by(Invitation.created_at.desc(), Invitation.id.desc())
        )
    )


def _locations(db: Session, org: OrgContext, location_ids: list[int]) -> frozenset[int]:
    for location_id in location_ids:
        if get_scoped(db, org, Location, location_id) is None:
            raise NotFound("location not found")
    return frozenset(location_ids)


def _active_owners(db: Session, org: OrgContext) -> int:
    query = select_scoped(org, Membership).where(
        Membership.role == OWNER, Membership.status == "active"
    )
    return int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)


def _member(db: Session, org: OrgContext, membership_id: int) -> Membership:
    membership = get_scoped(db, org, Membership, membership_id)
    if membership is None:
        raise NotFound("member not found")
    return membership


def _set_scope(row: Membership | Invitation, scope: frozenset[int]) -> None:
    """Every location (an empty scope), or exactly the listed ones."""
    row.all_locations = not scope
    if isinstance(row, Membership):
        row.locations = [
            MembershipLocation(tenant_id=row.tenant_id, location_id=lid) for lid in sorted(scope)
        ]
    else:
        row.locations = [
            InvitationLocation(tenant_id=row.tenant_id, location_id=lid) for lid in sorted(scope)
        ]


# --- inviting -----------------------------------------------------------------------------------


def invite(
    db: Session,
    org: OrgContext,
    inviter: User,
    email: str,
    role: str,
    all_locations: bool,
    location_ids: list[int],
) -> tuple[Invitation, str]:
    """A new pending invitation and its token (to email once). Re-inviting an address replaces
    its earlier pending invitation, so only the latest link works."""
    scope = _locations(db, org, location_ids)
    email = invitee_email(email)
    role = known_role(role)
    check_grant(role, can_manage_owners=org.can("owner.manage"))
    scope = scope_for(role, all_locations, scope)
    person = user_by_email(db, email)
    if person is not None:
        current = db.scalars(
            select_scoped(org, Membership).where(Membership.user_id == person.id)
        ).first()
        if current is not None and current.status == "active":
            raise TeamRuleError("That person is already on the team.")

    now = datetime.now(UTC)
    for earlier in open_invitations(db, org):
        if earlier.email == email:
            earlier.revoked_at = now
    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        tenant_id=org.tenant_id,
        email=email,
        role=role,
        invited_by_user_id=inviter.id,
        token_hash=_hash(token),
        created_at=now,
        expires_at=now + INVITATION_LIFETIME,
    )
    _set_scope(invitation, scope)
    db.add(invitation)
    db.flush()
    log.info("invitation %s created as %s", invitation.id, role)
    return invitation, token


def send_invitation(db: Session, invitation: Invitation, token: str, business: str) -> bool:
    link = f"{get_settings().app_base_url.rstrip('/')}/invitations/accept?token={token}"
    html, text = render_pair(
        "email/invitation",
        link=link,
        business=business,
        role=ROLE_LABELS[invitation.role],
        email=invitation.email,
    )
    subject = f"Join {business} on StockPulse"
    return send_email(db, invitation.tenant_id, invitation.email, subject, html, "invitation", text)


def withdraw(db: Session, org: OrgContext, invitation_id: int) -> None:
    invitation = get_scoped(db, org, Invitation, invitation_id)
    if invitation is None:
        raise NotFound("invitation not found")
    if invitation.accepted_at is None and invitation.revoked_at is None:
        invitation.revoked_at = datetime.now(UTC)
        log.info("invitation %s withdrawn", invitation.id)


# --- accepting ----------------------------------------------------------------------------------


def live_invitation(db: Session, token: str) -> Invitation | None:
    """The pending invitation a link opens, or None (unknown, used, withdrawn or expired)."""
    invitation = invitation_by_token_hash(db, _hash(token)) if token else None
    if invitation is None or status_of(invitation) != "pending":
        return None
    return invitation


def business_name(db: Session, invitation: Invitation) -> str:
    tenant = get_scoped(db, OrgContext.system(invitation.tenant_id), Tenant, invitation.tenant_id)
    return tenant.name if tenant is not None else ""


def accept(db: Session, invitation: Invitation, user: User) -> Membership:
    """Give user the invitation's access, using the invitation up. The address must be the one
    invited: the link was sent there, which also proves the address is theirs (AC-STF-01)."""
    if user.email != invitation.email or status_of(invitation) != "pending":
        raise TeamRuleError("This invitation is for someone else.")
    org = OrgContext.system(invitation.tenant_id)
    now = datetime.now(UTC)
    membership = db.scalars(
        select_scoped(org, Membership).where(Membership.user_id == user.id)
    ).first()
    if membership is None:
        membership = Membership(tenant_id=invitation.tenant_id, user_id=user.id)
        db.add(membership)
    if membership.status != "active":
        # A new member, or one who was removed and is invited back: the invitation decides.
        # Someone already active keeps what they have: an old link never changes a role.
        membership.role = invitation.role
        membership.status = "active"
        _set_scope(membership, frozenset(link.location_id for link in invitation.locations))
    invitation.accepted_at = now
    if user.email_verified_at is None:
        user.email_verified_at = now
    db.flush()
    log.info("invitation %s accepted by user %s", invitation.id, user.id)
    return membership


# --- changing and removing ------------------------------------------------------------------------


def change_role(
    db: Session,
    org: OrgContext,
    membership_id: int,
    role: str,
    all_locations: bool,
    location_ids: list[int],
) -> Membership:
    """A member's new role and scope; they apply from the member's next request."""
    membership = _member(db, org, membership_id)
    scope = _locations(db, org, location_ids)
    if membership.status != "active":
        raise TeamRuleError("That person is no longer on the team. Invite them again.")
    role = known_role(role)
    check_change(
        current_role=membership.role,
        new_role=role,
        is_self=membership.user_id == org.user_id,
        can_manage_owners=org.can("owner.manage"),
        active_owners=_active_owners(db, org),
    )
    membership.role = role
    _set_scope(membership, scope_for(role, all_locations, scope))
    log.info("membership %s is now %s", membership.id, role)
    return membership


def remove(db: Session, org: OrgContext, membership_id: int) -> None:
    """Take a member's access away; their sessions for this business end now (STF-FR-06)."""
    membership = _member(db, org, membership_id)
    if membership.status != "active":
        return
    check_change(
        current_role=membership.role,
        new_role=None,
        is_self=membership.user_id == org.user_id,
        can_manage_owners=org.can("owner.manage"),
        active_owners=_active_owners(db, org),
    )
    revoke_membership(db, membership)
    log.info("membership %s revoked", membership.id)
