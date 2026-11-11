"""Who may give or take which access in a business (STF-FR-01, STF-FR-07, DR-27). Pure rules.

team.manage lets Owners and Administrators invite, re-role and remove members; only a role
holding owner.manage touches an Owner. A business always keeps one active Owner, and nobody
changes their own access from the team page, so nobody locks themselves out by mistake.
"""

from __future__ import annotations

import re

OWNER = "owner"
# The R1 presets in the order the team page offers them (USER_ROLES_AND_PERMISSIONS §2).
ROLE_LABELS = {
    "owner": "Owner",
    "administrator": "Administrator",
    "manager": "Manager",
    "employee": "Employee",
    "read_only": "Read-only",
}
# Roles that work across the whole business: their scope is never a list of locations.
ORGANIZATION_ROLES = frozenset({OWNER, "administrator"})
DEFAULT_ROLE = "employee"  # least privilege (USER_ROLES_AND_PERMISSIONS §1)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TeamRuleError(ValueError):
    """A team change the rules refuse; its message is shown to the person who tried it."""


def invitee_email(raw: str) -> str:
    """The address an invitation is bound to, as accounts store it (lowercase, trimmed)."""
    email = raw.strip().lower()
    if not EMAIL.match(email) or len(email) > 255:
        raise TeamRuleError("That email address does not look right.")
    return email


def known_role(role: str) -> str:
    if role not in ROLE_LABELS:
        raise TeamRuleError("Pick a role.")
    return role


def check_grant(role: str, *, can_manage_owners: bool) -> None:
    """Whether someone may invite a person into role."""
    if role == OWNER and not can_manage_owners:
        raise TeamRuleError("Only an Owner can add another Owner.")


def check_change(
    *,
    current_role: str,
    new_role: str | None,
    is_self: bool,
    can_manage_owners: bool,
    active_owners: int,
) -> None:
    """Whether a member's role may change to new_role; None means removing them."""
    touches_owner = current_role == OWNER or new_role == OWNER
    if touches_owner and not can_manage_owners:
        raise TeamRuleError("Only an Owner can change or remove an Owner.")
    if current_role == OWNER and new_role != OWNER and active_owners <= 1:
        raise TeamRuleError(
            "A business always keeps at least one Owner. Make someone else an Owner first."
        )
    if is_self:
        raise TeamRuleError(
            "You can't change your own access here. Ask another Owner or Administrator."
        )


def scope_for(role: str, all_locations: bool, location_ids: frozenset[int]) -> frozenset[int]:
    """The locations a role is limited to; empty means every location.

    Owners and Administrators always work across the business, and a location-limited role
    needs at least one location.
    """
    if role in ORGANIZATION_ROLES or all_locations:
        return frozenset()
    if not location_ids:
        raise TeamRuleError("Pick at least one location, or give access to all of them.")
    return location_ids
