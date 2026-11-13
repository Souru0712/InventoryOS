"""Count sessions' rules (T1.23; REC-FR-01..08, DR-12, PD-12, WF-09). Pure.

A count is an observation: what was on the shelf at an instant. What the ledger expected at that
instant is the sum of the movements that had happened by then; the difference is a variance,
posted as a `count_variance` movement. A line whose variance is worth $25 or more, or is 20% or
more of what was expected, is posted and then reviewed by a Manager (R1 post-then-review, PD-12).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

SCOPES: Mapping[str, str] = {
    "full": "Everything at the location",
    "category": "One category",
    "area": "One storage area",
    "item": "One item",
}
COUNT_SCOPES = tuple(SCOPES)
COUNT_STATUSES = ("open", "posted")

# REC-FR-06.
REASONS: Mapping[str, str] = {
    "unrecorded_waste": "Unrecorded waste",
    "spillage": "Spillage or over-pour",
    "receiving_error": "Receiving error",
    "recipe_error": "Recipe error",
    "theft_suspected": "Theft suspected",
    "count_error": "Count error",
    "unknown": "Unknown",
}
VARIANCE_REASONS = tuple(REASONS)

# USER_ROLES_AND_PERMISSIONS §4 defaults: |value| >= $25 or |qty| >= 20% of expected. A business
# setting later (ADM-FR-05).
REVIEW_CENTS = 2500
REVIEW_SHARE = Decimal("0.20")


class CountRuleError(ValueError):
    """A count the rules refuse; nothing is written."""


def check_scope(scope: str, scope_ref: str | None) -> str | None:
    """What the session covers, tidied; a full count names nothing else."""
    if scope not in SCOPES:
        raise CountRuleError("Pick what to count.")
    ref = " ".join((scope_ref or "").split())[:120] or None
    if scope == "full":
        return None
    if ref is None:
        raise CountRuleError(
            "Pick which "
            + {"category": "category", "area": "storage area", "item": "item"}[scope]
            + " to count."
        )
    return ref


def check_parts(parts: list[Decimal]) -> Decimal:
    """The parts of one entry ("3 cases + 4 each"), each already in base units: none below zero,
    at least one given; their total."""
    if not parts:
        raise CountRuleError("Enter what is on the shelf, zero if it is empty.")
    if any(part < 0 for part in parts):
        raise CountRuleError("A count cannot be below zero.")
    return sum(parts, Decimal("0"))


def check_reason(reason: str | None) -> str | None:
    if reason in (None, ""):
        return None
    if reason not in REASONS:
        raise CountRuleError("Pick a reason from the list.")
    return reason


def variance_cents(variance: Decimal, unit_cost_cents: Decimal) -> int:
    """The variance at cost, in whole cents, signed: a shortfall is negative."""
    return int((variance * unit_cost_cents).quantize(Decimal("1")))


def needs_review(variance: Decimal, expected: Decimal, cents: int) -> bool:
    """Worth $25 or more either way, or 20% or more of a positive expected quantity."""
    if variance == 0:
        return False
    if abs(cents) >= REVIEW_CENTS:
        return True
    return expected > 0 and abs(variance) >= REVIEW_SHARE * expected
