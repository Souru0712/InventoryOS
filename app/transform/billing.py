"""Which plan a business's use needs, and how many units it is billed for (T4.02, ADR-0090). Pure.

A plan fits a business when its entitlements cover the sales modes its active locations use and
how many active locations it has (E-02). When the plan must follow the mode (a cutover, WF-25,
C-44), the business moves to the narrowest plan that fits: the fewest modes, then the fewest
locations. A plan billed per location counts the active locations taking sales from a POS, at
least one (SAAS-B-18); a plan billed per business is one unit. Prices are the provider's (E-01).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

PER_LOCATION = "per_location"
CONNECTED = "connected"
MODE_WORDS = {"connected": "sales from a POS", "standalone": "sales entered by hand or imported"}


@dataclass(frozen=True)
class Offer:
    """A sellable plan as far as fitting and billing go."""

    code: str
    name: str
    billing_basis: str
    allowed_modes: frozenset[str]
    max_locations: int | None


@dataclass(frozen=True)
class Use:
    """What a business uses today: its active locations and the sales mode of each."""

    modes: tuple[str, ...]

    @property
    def locations(self) -> int:
        return len(self.modes)

    @property
    def connected(self) -> int:
        return sum(1 for mode in self.modes if mode == CONNECTED)


def unfit(offer: Offer, use: Use) -> str | None:
    """Why the plan does not cover this use, as a sentence; None when it does."""
    missing = sorted(set(use.modes) - offer.allowed_modes)
    if missing:
        wanted = " and ".join(MODE_WORDS.get(mode, mode) for mode in missing)
        return f"{offer.name} does not cover {wanted}, which a location of yours takes."
    if offer.max_locations is not None and use.locations > offer.max_locations:
        plural = "location" if offer.max_locations == 1 else "locations"
        return (
            f"{offer.name} covers {offer.max_locations} {plural}; you have {use.locations} active."
        )
    return None


def narrowest(offers: Iterable[Offer], use: Use) -> Offer | None:
    """The plan that covers the use and as little else as possible; None when none does."""
    fitting = [offer for offer in offers if unfit(offer, use) is None]
    return min(
        fitting,
        key=lambda offer: (
            len(offer.allowed_modes),
            offer.max_locations if offer.max_locations is not None else float("inf"),
            offer.code,
        ),
        default=None,
    )


def quantity(offer: Offer, use: Use) -> int:
    """Units billed: POS-connected active locations (at least one), or one per business."""
    if offer.billing_basis == PER_LOCATION:
        return max(1, use.connected)
    return 1
