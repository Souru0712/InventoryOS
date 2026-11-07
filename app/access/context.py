"""Who is asking, for which organization and which of its locations (C-33)."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Membership


@dataclass(frozen=True, kw_only=True)
class OrgContext:
    """The scope every query runs in. Built here, never from a form or a URL.

    location_ids None means every location of the organization. A set limits location-scoped
    rows to those locations (DR-35); a membership scoped to some locations sets it.
    """

    tenant_id: int
    user_id: int | None = None
    location_ids: frozenset[int] | None = None

    @classmethod
    def for_membership(cls, membership: Membership) -> OrgContext:
        """A signed-in member's scope: their business, and its locations they may work at."""
        locations = (
            None
            if membership.all_locations
            else frozenset(link.location_id for link in membership.locations)
        )
        return cls(
            tenant_id=membership.tenant_id, user_id=membership.user_id, location_ids=locations
        )

    @classmethod
    def system(cls, tenant_id: int) -> OrgContext:
        """The product acting for one organization: scheduled jobs, webhooks, the command line."""
        return cls(tenant_id=tenant_id)
