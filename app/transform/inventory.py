"""Pure inventory math. No I/O, no ORM: quantities in base units only.

Covers the three ways a small business holds stock:
  count   - discrete units the POS already tracks (bags of beans, pastries)
  weight  - bulk held on scales, sold or consumed by mass (deli, flour)
  volume  - liquids depleted by recipe (milk, syrup, draught)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from app.units import Dimension, format_qty, from_base

ZERO = Decimal("0")
DAY = Decimal("1")


@dataclass(frozen=True)
class StockView:
    """Flat read-model of a stock item, decoupled from the ORM."""

    id: int
    slug: str
    name: str
    dimension: Dimension
    stock_unit: str
    track_mode: str
    qty_base: Decimal
    reorder_point_base: Decimal
    cost_per_base_cents: Decimal
    # What one base unit sells for. None for things never sold as they are, like recipe stock.
    retail_per_base_cents: Decimal | None = None
    lead_time_days: int = 2
    par_level_base: Decimal | None = None
    category: str | None = None
    supplier: str | None = None
    location_id: int = 0
    location_name: str = ""
    last_counted_at: datetime | None = None
    # None until decided; False means dismissed as never expiring.
    tracks_expiry: bool | None = None

    @property
    def qty_display(self) -> Decimal:
        return from_base(self.qty_base, self.stock_unit)

    @property
    def qty_label(self) -> str:
        return format_qty(self.qty_base, self.stock_unit)

    @property
    def reorder_label(self) -> str:
        return format_qty(self.reorder_point_base, self.stock_unit)


@dataclass(frozen=True)
class SaleRow:
    """One sold item on one day, in sold units."""

    sold_item_id: int
    dt: date
    units: Decimal
    net_cents: int = 0


@dataclass(frozen=True)
class Component:
    """Recipe link: one sold unit consumes qty_base of a stock item."""

    sold_item_id: int
    stock_item_id: int
    qty_base_per_sale: Decimal


def consumption_by_day(
    sales: list[SaleRow], components: list[Component]
) -> dict[int, dict[date, Decimal]]:
    """Explode sales through recipes into base-unit consumption per stock item per day."""
    by_sold: dict[int, list[Component]] = defaultdict(list)
    for component in components:
        by_sold[component.sold_item_id].append(component)

    result: dict[int, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for sale in sales:
        for component in by_sold.get(sale.sold_item_id, ()):
            result[component.stock_item_id][sale.dt] += sale.units * component.qty_base_per_sale
    return {item_id: dict(days) for item_id, days in result.items()}


def consumed_since(
    daily: dict[date, Decimal], since: datetime | None, until: date | None = None
) -> Decimal:
    """Total base units consumed after a physical count date."""
    if not daily:
        return ZERO
    start = since.date() if since else min(daily)
    end = until or max(daily)
    # The count day itself is already reflected in the counted quantity.
    return sum((qty for day, qty in daily.items() if start < day <= end), ZERO)


def derived_quantity(
    anchor_qty_base: Decimal,
    anchor_at: datetime | None,
    daily: dict[date, Decimal],
    until: date | None = None,
) -> Decimal:
    """Quantity for weight/volume stock: last count minus consumption since."""
    remaining = Decimal(anchor_qty_base) - consumed_since(daily, anchor_at, until)
    return remaining if remaining > ZERO else ZERO


def daily_velocity(daily: dict[date, Decimal], window_days: int, today: date) -> Decimal:
    """Mean base units consumed per day over the trailing window."""
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    start = today - timedelta(days=window_days - 1)
    total = sum((qty for day, qty in daily.items() if start <= day <= today), ZERO)
    return (total / Decimal(window_days)).quantize(Decimal("0.0001"))


def days_of_cover(qty_base: Decimal, velocity_per_day: Decimal) -> Decimal | None:
    """How many days the current quantity lasts; None when nothing is moving."""
    if velocity_per_day <= ZERO:
        return None
    return (Decimal(qty_base) / velocity_per_day).quantize(Decimal("0.1"))


def valuation_cents(qty_base: Decimal, cost_per_base_cents: Decimal) -> int:
    """Value of held stock, rounded to whole cents."""
    return int((Decimal(qty_base) * Decimal(cost_per_base_cents)).quantize(Decimal("1")))


def suggest_order_base(item: StockView, velocity_per_day: Decimal, cover_days: int = 14) -> Decimal:
    """Base units to order: up to par level, else enough to cover lead time plus window."""
    if item.par_level_base is not None:
        target = Decimal(item.par_level_base)
    elif velocity_per_day > ZERO:
        target = velocity_per_day * Decimal(item.lead_time_days + cover_days)
    else:
        target = Decimal(item.reorder_point_base)

    shortfall = target - Decimal(item.qty_base)
    if shortfall <= ZERO:
        return ZERO
    if item.dimension is Dimension.COUNT:
        return shortfall.quantize(Decimal("1"), rounding=ROUND_CEILING)
    return shortfall.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def stale_after(last_counted_at: datetime | None, max_age_days: int, now: datetime) -> bool:
    """True when a derived-quantity item has not been physically counted recently."""
    if last_counted_at is None:
        return True
    reference = last_counted_at
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return (now - reference) > timedelta(days=max_age_days)
