"""Waste rules (T2.07; WST-FR-01..12, DR-33, AC-WST-01..08, ADR-0058). Pure.

A waste event is the business record of a loss: what, how much, why, by whom, when. Its ledger
entries are the stock effect, and every quantity and cost a report shows comes from them, never
from the event (DR-33), so a loss is counted once. The logger may void their own waste for 30
minutes; after that only someone allowed to void waste may, and says why (WST-FR-07).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

STATUSES = ("recorded", "voided")
VOID_WINDOW = timedelta(minutes=30)
ZERO = Decimal(0)

# WST-FR-02: the defaults every business starts with; it may rename them and add its own.
DEFAULT_REASONS: Mapping[str, str] = {
    "spoilage": "Spoilage",
    "expiration": "Past its date",
    "preparation": "Preparation waste",
    "production_loss": "Production loss",
    "damaged": "Damaged goods",
    "remake": "Remake",
    "customer_return": "Customer return",
    "refund": "Refund-related",
    "quality": "Quality-control disposal",
    "spilled": "Spilled",
    "overproduction": "Overproduction",
    "other": "Other",
}


class WasteRuleError(ValueError):
    """A waste entry or void the rules refuse; nothing is written."""


def check_quantity(quantity: Decimal) -> Decimal:
    if quantity <= ZERO:
        raise WasteRuleError("Enter a quantity above zero.")
    return quantity


def check_reason_label(label: str) -> str:
    cleaned = " ".join(label.split())
    if not cleaned:
        raise WasteRuleError("A reason needs a name.")
    if len(cleaned) > 80:
        raise WasteRuleError("Keep a reason under 80 characters.")
    return cleaned


def void_refusal(
    *,
    logged_by: int | None,
    recorded_at: datetime,
    person: int | None,
    may_void_any: bool,
    reason: str,
    now: datetime,
) -> str | None:
    """Why a void is refused, or None when it may go ahead (WST-FR-07, AC-WST-05)."""
    own_and_recent = person is not None and person == logged_by and now - recorded_at <= VOID_WINDOW
    if own_and_recent:
        return None
    if not may_void_any:
        return "After 30 minutes, or for someone else's entry, a Manager voids it."
    if not reason.strip():
        return "Say why it is being voided."
    return None


@dataclass(frozen=True)
class Loss:
    """One waste event's net effect, as its ledger entries say (DR-33)."""

    event_id: int
    reason: str
    item: str  # what left stock
    product: str | None  # the product that was wasted, when one was
    person: str
    qty_base: Decimal  # above zero: what was lost
    cost_cents: int | None  # None when an item had no cost
    unit: str = ""  # the item's display unit, for showing qty_base


@dataclass(frozen=True)
class Total:
    qty_base: Decimal = ZERO
    cost_cents: int = 0
    events: int = 0
    uncosted: bool = False


@dataclass(frozen=True)
class WasteReport:
    by_reason: dict[str, Total] = field(default_factory=dict)
    by_item: dict[str, Total] = field(default_factory=dict)
    by_product: dict[str, Total] = field(default_factory=dict)
    by_person: dict[str, Total] = field(default_factory=dict)
    total_cost_cents: int = 0
    uncosted: bool = False

    def top_items(self, n: int = 5) -> list[tuple[str, Total]]:
        """The items that cost the most, worst first (AC-WST-06)."""
        return sorted(self.by_item.items(), key=lambda kv: (-kv[1].cost_cents, kv[0]))[:n]


def _add(totals: dict[str, Total], key: str, loss: Loss, event_seen: set[tuple[str, int]]) -> None:
    was = totals.get(key, Total())
    first = (key, loss.event_id) not in event_seen
    event_seen.add((key, loss.event_id))
    totals[key] = Total(
        was.qty_base + loss.qty_base,
        was.cost_cents + (loss.cost_cents or 0),
        was.events + (1 if first else 0),
        was.uncosted or loss.cost_cents is None,
    )


def report(losses: Iterable[Loss]) -> WasteReport:
    """Totals by reason, item, product and person (WST-FR-09, AC-WST-06). Item totals add the
    same unit; reason, product and person totals add only cost."""
    out = WasteReport()
    seen: dict[str, set[tuple[str, int]]] = defaultdict(set)
    total = 0
    uncosted = False
    for loss in losses:
        _add(out.by_reason, loss.reason, loss, seen["reason"])
        _add(out.by_item, loss.item, loss, seen["item"])
        if loss.product:
            _add(out.by_product, loss.product, loss, seen["product"])
        _add(out.by_person, loss.person, loss, seen["person"])
        total += loss.cost_cents or 0
        uncosted = uncosted or loss.cost_cents is None
    return WasteReport(out.by_reason, out.by_item, out.by_product, out.by_person, total, uncosted)
