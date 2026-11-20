"""Receiving rules (T2.11-a; RCV-FR-05..07, -09, -12, AC-RCV-06, ADR-0062). Pure.

A receiving line says what was ordered (R1.1), invoiced, arrived and accepted. What was accepted
enters stock; the gaps between them are discrepancies, each with its reason (RCV-FR-07).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

STATUSES = ("draft", "posted")
LINE_STATUSES = ("open", "posted", "corrected")
ZERO = Decimal(0)


class ReceivingRuleError(ValueError):
    """A receiving change the rules refuse; nothing is written."""


@dataclass(frozen=True)
class Discrepancies:
    """What a line's quantities say went wrong (AC-RCV-06)."""

    short: Decimal = ZERO  # invoiced but did not arrive
    over: Decimal = ZERO  # arrived beyond what was invoiced
    rejected: Decimal = ZERO  # arrived but not accepted (damaged, wrong item)

    @property
    def any(self) -> bool:
        return bool(self.short or self.over or self.rejected)

    def describe(self, unit: str) -> list[str]:
        found = []
        if self.short:
            found.append(f"short {self.short.normalize()} {unit}".strip())
        if self.over:
            found.append(f"over {self.over.normalize()} {unit}".strip())
        if self.rejected:
            found.append(f"rejected {self.rejected.normalize()} {unit}".strip())
        return found


def check_quantities(
    invoiced: Decimal | None, arrived: Decimal, accepted: Decimal, reject_reason: str
) -> Discrepancies:
    """Arrived and accepted default to invoiced (RCV-FR-05); accepting less than arrived needs
    a reason."""
    if arrived < ZERO or accepted < ZERO or (invoiced is not None and invoiced < ZERO):
        raise ReceivingRuleError("Quantities cannot be below zero.")
    if accepted > arrived:
        raise ReceivingRuleError("More cannot be accepted than arrived.")
    rejected = arrived - accepted
    if rejected and not reject_reason.strip():
        raise ReceivingRuleError("Say why part of the delivery was not accepted.")
    short = over = ZERO
    if invoiced is not None:
        short = max(invoiced - arrived, ZERO)
        over = max(arrived - invoiced, ZERO)
    return Discrepancies(short, over, rejected)
