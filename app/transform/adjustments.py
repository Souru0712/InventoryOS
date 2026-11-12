"""Manual adjustments' rules (T1.22; INV-FR-19, INV-FR-23, DR-08, WF-10, STF-FR-08). Pure.

An adjustment is a deliberate correction not covered by receiving, waste, production, sales or
counts. It always says why. It may not take stock below zero unless the person may override and
says why again (DR-08). One worth more than the review threshold posts, and an Owner or
Administrator reviews it afterwards (R1, USER_ROLES_AND_PERMISSIONS §4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

# INV-FR-19 (the task's list; WF-10 names internal use and returned to supplier instead).
REASONS: Mapping[str, str] = {
    "found": "Found",
    "correction": "Correction",
    "sample": "Sample",
    "transfer_in": "Transfer in from outside",
    "other": "Other",
}
ADJUSTMENT_REASONS = tuple(REASONS)
# |value| at or above which an adjustment is reviewed afterwards (USER_ROLES_AND_PERMISSIONS §4:
# $50 by default; a business setting later, ADM-FR-05).
REVIEW_CENTS = 5000


class AdjustmentRuleError(ValueError):
    """An adjustment the rules refuse; nothing is posted."""


@dataclass(frozen=True)
class Checked:
    qty_base: Decimal
    reason: str
    note: str | None
    override_reason: str | None


def _tidy(text: str | None, limit: int) -> str | None:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit] or None


def check_adjustment(
    qty_base: Decimal,
    reason: str,
    note: str | None,
    *,
    override: bool = False,
    override_reason: str | None = None,
) -> Checked:
    """The adjustment the rules accept, tidied, or AdjustmentRuleError."""
    if qty_base == 0:
        raise AdjustmentRuleError("Enter a quantity to add or remove.")
    if reason not in REASONS:
        raise AdjustmentRuleError("Pick a reason for the adjustment.")
    cleaned = _tidy(note, 300)
    if reason == "other" and cleaned is None:
        raise AdjustmentRuleError("Say what the adjustment is for.")
    why = _tidy(override_reason, 300) if override else None
    if override and why is None:
        raise AdjustmentRuleError("Say why stock may go below zero.")
    return Checked(qty_base, reason, cleaned, why)


def value_cents(qty_base: Decimal, unit_cost_cents: Decimal) -> int:
    """What an adjustment moved, at cost, in whole cents, whichever way it went."""
    return int((abs(qty_base) * unit_cost_cents).quantize(Decimal("1")))


def needs_review(qty_base: Decimal, unit_cost_cents: Decimal) -> bool:
    return value_cents(qty_base, unit_cost_cents) >= REVIEW_CENTS
