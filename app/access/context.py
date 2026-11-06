"""Who is asking, for which organization and which of its locations (C-33)."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import User


@dataclass(frozen=True, kw_only=True)
class OrgContext:
    """The scope every query runs in. Built here, never from a form or a URL.

    location_ids None means every location of the organization. A set limits location-scoped
    rows to those locations (DR-35); memberships with a location scope set it (T1.05).
    """

    tenant_id: int
    user_id: int | None = None
    location_ids: frozenset[int] | None = None

    @classmethod
    def for_user(cls, user: User) -> OrgContext:
        """A signed-in user's scope. Until memberships (T1.05) every user owns one organization."""
        return cls(tenant_id=user.tenant_id, user_id=user.id)

    @classmethod
    def system(cls, tenant_id: int) -> OrgContext:
        """The product acting for one organization: scheduled jobs, webhooks, the command line."""
        return cls(tenant_id=tenant_id)
