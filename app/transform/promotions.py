"""Pure promotion arithmetic: what to mark down, by how much, and why.

Every number here can be shown to the owner with the sentence that produced it. Nothing is
learned or fitted: the tiers and discount bands are plain rules, so a recommendation can be
argued with rather than believed. Prices are cents per base unit throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from app.units import format_amount

ZERO = Decimal("0")
CENT = Decimal("1")

# Days left -> how urgent, most pressing first.
URGENCY_TIERS: tuple[tuple[int, str], ...] = (
    (3, "critical"),
    (7, "high"),
    (14, "medium"),
    (30, "low"),
)

# How deep a cut each tier allows: (floor, ceiling) as a fraction off the normal price.
DISCOUNT_BANDS: dict[str, tuple[Decimal, Decimal]] = {
    "critical": (Decimal("0.30"), Decimal("0.50")),
    "high": (Decimal("0.20"), Decimal("0.30")),
    "medium": (Decimal("0.10"), Decimal("0.20")),
    "low": (Decimal("0.05"), Decimal("0.10")),
}

# Shelf prices end in round money; a markdown to $1.47 looks like a mistake.
PRICE_STEP_CENTS = Decimal("5")

DEFAULT_MIN_MARGIN = Decimal("0.20")


@dataclass(frozen=True)
class PromotionInput:
    """One dated lot that might need marking down."""

    stock_item_id: int
    name: str
    stock_unit: str
    remaining_base: Decimal
    projected_leftover_base: Decimal | None
    days_left: int
    velocity_per_day: Decimal | None
    cost_per_base_cents: Decimal
    retail_per_base_cents: Decimal | None
    batch_id: int | None = None
    min_margin: Decimal = DEFAULT_MIN_MARGIN


@dataclass(frozen=True)
class Suggestion:
    """A markdown recommendation, with the reasoning that produced it."""

    item: PromotionInput
    urgency: str
    units_at_risk_base: Decimal
    cost_at_risk_cents: int
    retail_at_risk_cents: int
    current_velocity: Decimal | None
    required_velocity: Decimal
    discount_percent: int
    normal_price_cents: int
    suggested_price_cents: int
    floor_price_cents: int
    held_at_floor: bool
    expected_revenue_cents: int
    reasons: list[str] = field(default_factory=list)

    @property
    def acceleration(self) -> Decimal | None:
        """How many times faster it has to sell. None when nothing is selling to compare."""
        if not self.current_velocity or self.current_velocity <= ZERO:
            return None
        return (self.required_velocity / self.current_velocity).quantize(Decimal("0.1"))


def _days(count: int) -> str:
    return f"{count} day" if count == 1 else f"{count} days"


def urgency_for(days_left: int) -> str:
    """Which tier a lot falls into. Past its date is as urgent as it gets."""
    if days_left < 0:
        return "expired"
    for limit, name in URGENCY_TIERS:
        if days_left <= limit:
            return name
    return "none"


def required_velocity(remaining_base: Decimal, days_left: int) -> Decimal:
    """How fast it must move to clear by the date. The day it expires does not count."""
    days = max(Decimal(days_left), Decimal("1"))
    return (Decimal(remaining_base) / days).quantize(Decimal("0.01"))


def _round_price(cents: Decimal) -> Decimal:
    """Down to the nearest 5 cents: never round a markdown up past what was promised."""
    stepped = (cents / PRICE_STEP_CENTS).quantize(CENT, rounding=ROUND_DOWN) * PRICE_STEP_CENTS
    return max(stepped, ZERO)


def discount_for(urgency: str, acceleration: Decimal | None) -> Decimal:
    """Start at the shallow end of the tier, deepen with how much acceleration is needed.

    A lot that needs to sell twice as fast gets the top of its band; one that needs a nudge
    gets the bottom. Linear between, because a rule the owner can follow beats a fitted curve.
    """
    band = DISCOUNT_BANDS.get(urgency)
    if band is None:
        return ZERO
    low, high = band
    if acceleration is None:
        # Nothing is selling at all, so the tier's deepest cut is the honest starting point.
        return high
    over = max(acceleration - Decimal("1"), ZERO)
    return (low + (high - low) * min(over / Decimal("2"), Decimal("1"))).quantize(Decimal("0.01"))


def suggest(item: PromotionInput) -> Suggestion | None:
    """Recommend a markdown, or nothing at all when the stock will sell in time.

    Returns None when there is nothing to say: the shelf clears itself, the lot is not close
    enough to its date, or there is no selling price to discount.
    """
    leftover = item.projected_leftover_base
    at_risk = leftover if leftover is not None else item.remaining_base
    if at_risk is None or at_risk <= ZERO:
        return None

    urgency = urgency_for(item.days_left)
    if urgency in ("none", "expired"):
        # Too far out to act on, or already too late to sell.
        return None
    if item.retail_per_base_cents is None or item.retail_per_base_cents <= ZERO:
        return None

    normal = Decimal(item.retail_per_base_cents)
    cost = Decimal(item.cost_per_base_cents or 0)
    needed = required_velocity(item.remaining_base, item.days_left)
    current = item.velocity_per_day
    acceleration = (
        (needed / current).quantize(Decimal("0.1")) if current and current > ZERO else None
    )

    discount = discount_for(urgency, acceleration)
    floor = _round_price(cost * (Decimal("1") + item.min_margin)) if cost > ZERO else ZERO
    wanted = _round_price(normal * (Decimal("1") - discount))
    price = max(wanted, floor)
    held = price > wanted

    reasons = [
        f"{format_amount(item.remaining_base, item.stock_unit)} on the shelf, "
        f"{'best before today' if item.days_left == 0 else _days(item.days_left) + ' until the date'}",
    ]
    if current and current > ZERO:
        reasons.append(
            f"selling {format_amount(current, item.stock_unit)} a day; "
            f"{format_amount(needed, item.stock_unit)} a day clears it in time"
        )
    else:
        reasons.append("no sales in the last four weeks, so none of it is on course to sell")
    reasons.append(
        f"{_days(item.days_left)} left puts it in the {urgency} tier, "
        f"{int(DISCOUNT_BANDS[urgency][0] * 100)}-{int(DISCOUNT_BANDS[urgency][1] * 100)}% off"
    )
    if held:
        reasons.append(
            f"held at the {int(item.min_margin * 100)}% margin floor over cost rather than "
            f"the {int(discount * 100)}% the tier allows"
        )

    actual_discount = int(((normal - price) / normal * 100).quantize(CENT)) if normal > ZERO else 0
    return Suggestion(
        item=item,
        urgency=urgency,
        units_at_risk_base=Decimal(at_risk),
        cost_at_risk_cents=int((Decimal(at_risk) * cost).quantize(CENT)),
        retail_at_risk_cents=int((Decimal(at_risk) * normal).quantize(CENT)),
        current_velocity=current,
        required_velocity=needed,
        discount_percent=actual_discount,
        normal_price_cents=int(normal.quantize(CENT)),
        suggested_price_cents=int(price.quantize(CENT)),
        floor_price_cents=int(floor.quantize(CENT)),
        held_at_floor=held,
        expected_revenue_cents=int((Decimal(at_risk) * price).quantize(CENT)),
        reasons=reasons,
    )


def suggest_all(items: list[PromotionInput]) -> list[Suggestion]:
    """Only the lots worth acting on, most money at stake first."""
    found = [suggestion for item in items if (suggestion := suggest(item))]
    return sorted(found, key=lambda s: (-s.cost_at_risk_cents, s.item.days_left))


def effectiveness(
    velocity_before: Decimal | None, velocity_during: Decimal | None
) -> Decimal | None:
    """How many times faster it sold once marked down. None when there is nothing to compare."""
    if not velocity_before or velocity_before <= ZERO or velocity_during is None:
        return None
    return (Decimal(velocity_during) / Decimal(velocity_before)).quantize(Decimal("0.1"))
