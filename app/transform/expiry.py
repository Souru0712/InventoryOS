"""Pure expiry math: dated lots, first-expired-first-out, and what will not sell in time.

A batch records what arrived: how much, when it expires, what it cost. How much of it is
still on the shelf is never stored. It is derived from the item's own quantity, which Square
sync, physical counts and receipts already keep current; a second ledger that every sale had
to decrement would drift from that number within a week. Works identically for count,
weight and volume because everything here is in base units.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.transform.inventory import StockView, valuation_cents

ZERO = Decimal("0")
QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class Batch:
    """One dated lot as received."""

    id: int
    received_qty_base: Decimal
    expires_on: date
    cost_per_base_cents: Decimal | None = None
    received_at: datetime | None = None


@dataclass(frozen=True)
class BatchPosition:
    """Where one lot stands today."""

    batch: Batch
    remaining_base: Decimal
    days_left: int
    # None when the item has no sales signal to project from.
    projected_leftover_base: Decimal | None
    cost_per_base_cents: Decimal
    sell_first: bool

    @property
    def expired(self) -> bool:
        return self.days_left < 0

    @property
    def at_risk_base(self) -> Decimal:
        """Stock expected to expire unsold; everything on the shelf when there is no signal."""
        if self.expired or self.projected_leftover_base is None:
            return self.remaining_base
        return self.projected_leftover_base

    @property
    def at_risk_cents(self) -> int:
        return valuation_cents(self.at_risk_base, self.cost_per_base_cents)

    @property
    def status(self) -> str:
        if self.remaining_base <= ZERO:
            return "sold_through"
        if self.expired:
            return "expired"
        if self.projected_leftover_base is None:
            return "sell_first" if self.sell_first else "no_signal"
        if self.projected_leftover_base > ZERO:
            return "at_risk"
        return "sell_first" if self.sell_first else "on_track"


@dataclass(frozen=True)
class ItemExpiry:
    """Every dated lot of one stock item, positioned for today."""

    item: StockView
    positions: list[BatchPosition]
    undated_base: Decimal


@dataclass(frozen=True)
class ItemCoverage:
    item: StockView
    dated_base: Decimal
    undated_base: Decimal

    @property
    def fully_dated(self) -> bool:
        return self.undated_base <= ZERO

    @property
    def undated_cents(self) -> int:
        return valuation_cents(self.undated_base, self.item.cost_per_base_cents)


@dataclass(frozen=True)
class Coverage:
    """How much of the stock that can expire actually carries a date.

    Without this the expiry figures look precise while quietly ignoring whatever was never
    dated, which is the difference between a report and a guess.
    """

    rows: list[ItemCoverage]
    covered_cents: int
    total_cents: int

    @property
    def eligible_items(self) -> int:
        return len(self.rows)

    @property
    def dated_items(self) -> int:
        return sum(1 for row in self.rows if row.fully_dated)

    @property
    def needs_dates(self) -> list[ItemCoverage]:
        """Worst offenders first: the most stock value sitting without a date."""
        return sorted(
            (row for row in self.rows if not row.fully_dated),
            key=lambda row: -row.undated_cents,
        )

    @property
    def percent(self) -> int:
        if self.total_cents <= 0:
            # Nothing is valued, so fall back to counting items rather than showing 0%.
            return round(100 * self.dated_items / self.eligible_items) if self.rows else 100
        return round(100 * self.covered_cents / self.total_cents)


def coverage(entries: list[tuple[StockView, Decimal]]) -> Coverage:
    """Build the coverage figure from (item, undated quantity) pairs."""
    rows = []
    covered = total = 0
    for item, undated in entries:
        on_hand = max(Decimal(item.qty_base), ZERO)
        undated = min(max(Decimal(undated), ZERO), on_hand)
        dated = on_hand - undated
        rows.append(ItemCoverage(item, dated, undated))
        covered += valuation_cents(dated, item.cost_per_base_cents)
        total += valuation_cents(on_hand, item.cost_per_base_cents)
    return Coverage(rows=rows, covered_cents=covered, total_cents=total)


def fefo_order(batches: list[Batch]) -> list[Batch]:
    """Soonest expiry first; on the same date, the earlier receipt first."""
    return sorted(batches, key=lambda batch: (batch.expires_on, batch.id))


def allocate_fefo(on_hand_base: Decimal, batches: list[Batch]) -> tuple[list[tuple[Batch, Decimal]], Decimal]:
    """Split the on-hand quantity across dated lots.

    Returns (batch, remaining) pairs in FEFO order, plus the undated remainder. Sales take the
    soonest-expiring stock first, so whatever is still on the shelf is the latest-dated: lots
    are filled from the latest expiry backwards, each capped at what it arrived with. Stock
    beyond every dated lot predates tracking, so it is treated as the oldest on the shelf.
    """
    ordered = fefo_order(batches)
    left = max(Decimal(on_hand_base), ZERO)
    remaining: dict[int, Decimal] = {}
    for batch in reversed(ordered):
        take = min(max(Decimal(batch.received_qty_base), ZERO), left)
        remaining[batch.id] = take
        left -= take
    return [(batch, remaining[batch.id]) for batch in ordered], left


def project_batches(
    on_hand_base: Decimal,
    batches: list[Batch],
    velocity_per_day: Decimal | None,
    today: date,
    fallback_cost_per_base_cents: Decimal = ZERO,
) -> tuple[list[BatchPosition], Decimal]:
    """Position every lot and project how much of it will still be here when it expires.

    Demand is served oldest first: undated stock, then lots by expiry. Expired stock cannot be
    sold, so it absorbs no demand, and the selling clock only advances by what actually sells,
    which lets a lot that expires early hand its demand on to the next one. Selling time runs
    to the expiry date with today counted as spent, so a borderline lot is flagged rather than
    missed. velocity_per_day of None means the item has no sales signal: nothing is projected.
    """
    allocation, undated = allocate_fefo(on_hand_base, batches)
    live = [batch.id for batch, qty in allocation if qty > ZERO and batch.expires_on >= today]
    first_live = live[0] if live else None

    velocity = Decimal(velocity_per_day) if velocity_per_day is not None else None
    clock = undated / velocity if velocity and velocity > ZERO else ZERO

    positions: list[BatchPosition] = []
    for batch, remaining in allocation:
        days_left = (batch.expires_on - today).days
        if velocity is None:
            leftover = None
        elif remaining <= ZERO:
            leftover = ZERO
        elif days_left < 0 or velocity <= ZERO:
            leftover = remaining
        else:
            selling_days = max(Decimal(days_left) - clock, ZERO)
            sold = min(remaining, velocity * selling_days)
            leftover = remaining - sold
            clock += sold / velocity

        cost = (
            Decimal(batch.cost_per_base_cents)
            if batch.cost_per_base_cents is not None
            else Decimal(fallback_cost_per_base_cents)
        )
        positions.append(
            BatchPosition(
                batch=batch,
                remaining_base=remaining.quantize(QUANTUM),
                days_left=days_left,
                projected_leftover_base=leftover.quantize(QUANTUM) if leftover is not None else None,
                cost_per_base_cents=cost,
                sell_first=batch.id == first_live,
            )
        )
    return positions, undated.quantize(QUANTUM)
