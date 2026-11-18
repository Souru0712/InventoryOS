"""The sales model's rules (T2.04; POS-FR-17, -18, DR-29..31, DR-34). Pure.

Every sale, from any source, is a sales transaction with lines, an origin and a business date
(DR-30). Each location has one sales source of record per business date (DR-31): sales from
another source for that date are kept but never counted, and raise `sales_source_overlap`, so
manual and POS sales are never both consumed. A transaction's origin is its source's kind and
never changes (DR-34). A changed resend is the next revision; the one before it is corrected.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

SALES_ORIGINS = ("pos_adapter", "manual", "file_import", "api", "inventoryos_pos")
ORIGIN_LABELS = {
    "pos_adapter": "POS",
    "manual": "Entered by hand",
    "file_import": "Imported",
    "api": "API",
    "inventoryos_pos": "InventoryOS POS",
}
TRANSACTION_STATUSES = ("completed", "corrected", "voided")
CONSUMPTION_STATUSES = ("pending", "posted", "gap", "skipped")
RETURN_STATUSES = ("recorded", "classified")
RETURN_CLASSES = ("restock", "waste")
PLACES = Decimal("0.0001")


class SalesRuleError(ValueError):
    """A sale or a source the rules refuse; nothing is written."""


@dataclass(frozen=True)
class Span:
    """The business days a source is the source of record for: from `first`, to `last` (both
    included), or open-ended."""

    first: date
    last: date | None = None

    def covers(self, day: date) -> bool:
        return self.first <= day and (self.last is None or day <= self.last)

    def overlaps(self, other: Span) -> bool:
        ends_before = self.last is not None and self.last < other.first
        starts_after = other.last is not None and other.last < self.first
        return not (ends_before or starts_after)


def check_span(first: date, last: date | None) -> Span:
    if last is not None and last < first:
        raise SalesRuleError("A source cannot stop before it starts.")
    return Span(first, last)


def check_new_source(new: Span, existing: Iterable[Span]) -> Span:
    """A location keeps one source of record per business date (DR-31): a new one may not
    overlap any it already has."""
    for span in existing:
        if new.overlaps(span):
            until = f" to {span.last:%d %b %Y}" if span.last else " on"
            raise SalesRuleError(
                f"This location already takes its sales from another source from "
                f"{span.first:%d %b %Y}{until}. Stop that one first."
            )
    return new


def of_record(sources: Iterable[tuple[int, Span]], day: date) -> int | None:
    """The source of record for a business date, if any."""
    found = [source_id for source_id, span in sources if span.covers(day)]
    return found[0] if found else None


def check_quantity(raw: Decimal | str | int) -> Decimal:
    """A line's quantity: a number, kept to four places. A negative one belongs on a return."""
    try:
        amount = Decimal(str(raw))
    except InvalidOperation as exc:
        raise SalesRuleError(f"{raw} is not a quantity.") from exc
    if not amount.is_finite() or amount < 0:
        raise SalesRuleError("A sold quantity cannot be negative; record a return instead.")
    return amount.quantize(PLACES)


@dataclass(frozen=True)
class SaleLine:
    """One line as recorded: what was sold (an owned variation, or a POS item not yet mapped),
    how many, with which modifiers, and its money where the source gives it."""

    line_key: str
    quantity: Decimal
    product_variation_id: int | None = None
    external_ref: str | None = None
    description: str = ""
    modifiers: tuple[tuple[int, int], ...] = ()  # (modifier id, how many)
    gross_cents: int | None = None
    net_cents: int | None = None


def check_lines(lines: Sequence[SaleLine]) -> list[SaleLine]:
    """Each line key once, and each line says what was sold."""
    seen: set[str] = set()
    for line in lines:
        if not line.line_key:
            raise SalesRuleError("Every line needs a key.")
        if line.line_key in seen:
            raise SalesRuleError(f"Line {line.line_key} appears twice.")
        seen.add(line.line_key)
        if line.product_variation_id is None and not line.external_ref:
            raise SalesRuleError(f"Line {line.line_key} does not say what was sold.")
    return list(lines)


def same_sale(a: Sequence[SaleLine], b: Sequence[SaleLine]) -> bool:
    """Whether a resend changes nothing, so it is not a new revision."""

    def key(lines: Sequence[SaleLine]) -> list[tuple[object, ...]]:
        return sorted(
            (
                ln.line_key,
                ln.quantity.quantize(PLACES),
                ln.product_variation_id,
                ln.external_ref,
                tuple(sorted(ln.modifiers)),
                ln.gross_cents,
                ln.net_cents,
            )
            for ln in lines
        )

    return key(a) == key(b)
