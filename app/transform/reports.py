"""Pure weekly summary shaping. Feeds both the dashboard and the email report."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.transform.alerts import AlertProposal
from app.transform.inventory import ZERO, SaleRow, StockView, valuation_cents
from app.units import Dimension, format_qty


@dataclass
class TopSeller:
    name: str
    units: Decimal
    net_cents: int


@dataclass
class DimensionRoll:
    dimension: str
    item_count: int
    value_cents: int
    at_risk: int


@dataclass
class WeeklyReport:
    period_start: date
    period_end: date
    net_cents: int
    prior_net_cents: int
    orders_units: Decimal
    top_sellers: list[TopSeller] = field(default_factory=list)
    slow_movers: list[TopSeller] = field(default_factory=list)
    alerts: list[AlertProposal] = field(default_factory=list)
    inventory_value_cents: int = 0
    by_dimension: list[DimensionRoll] = field(default_factory=list)

    @property
    def net_change_pct(self) -> Decimal | None:
        if self.prior_net_cents <= 0:
            return None
        delta = Decimal(self.net_cents - self.prior_net_cents) / Decimal(self.prior_net_cents)
        return (delta * 100).quantize(Decimal("0.1"))


def week_bounds(today: date, weeks_back: int = 1) -> tuple[date, date]:
    """Monday-to-Sunday window ending before today."""
    last_sunday = today - timedelta(days=today.weekday() + 1)
    end = last_sunday - timedelta(days=7 * (weeks_back - 1))
    return end - timedelta(days=6), end


def build_weekly_report(
    sales: list[SaleRow],
    item_names: dict[int, str],
    stock: list[StockView],
    alerts: list[AlertProposal],
    period_start: date,
    period_end: date,
    top_n: int = 5,
) -> WeeklyReport:
    """Roll sales and stock into the numbers an owner actually reads."""
    prior_start = period_start - timedelta(days=7)
    prior_end = period_end - timedelta(days=7)

    current = [row for row in sales if period_start <= row.dt <= period_end]
    prior = [row for row in sales if prior_start <= row.dt <= prior_end]

    units_by_item: dict[int, Decimal] = defaultdict(Decimal)
    net_by_item: dict[int, int] = defaultdict(int)
    for row in current:
        units_by_item[row.sold_item_id] += row.units
        net_by_item[row.sold_item_id] += row.net_cents

    ranked = sorted(
        (
            TopSeller(item_names.get(item_id, f"item {item_id}"), units, net_by_item[item_id])
            for item_id, units in units_by_item.items()
        ),
        key=lambda t: t.net_cents,
        reverse=True,
    )

    at_risk_ids = {a.stock_item_id for a in alerts if a.kind != "stale_count"}
    rolls: dict[str, DimensionRoll] = {}
    total_value = 0
    for item in stock:
        value = valuation_cents(item.qty_base, item.cost_per_base_cents)
        total_value += value
        key = item.dimension.value if isinstance(item.dimension, Dimension) else str(item.dimension)
        roll = rolls.setdefault(key, DimensionRoll(key, 0, 0, 0))
        roll.item_count += 1
        roll.value_cents += value
        if item.id in at_risk_ids:
            roll.at_risk += 1

    return WeeklyReport(
        period_start=period_start,
        period_end=period_end,
        net_cents=sum(row.net_cents for row in current),
        prior_net_cents=sum(row.net_cents for row in prior),
        orders_units=sum((row.units for row in current), ZERO),
        top_sellers=ranked[:top_n],
        slow_movers=[t for t in reversed(ranked[-top_n:]) if t.units > ZERO][:top_n],
        alerts=alerts,
        inventory_value_cents=total_value,
        by_dimension=sorted(rolls.values(), key=lambda r: r.dimension),
    )


def money(cents: int, currency: str = "USD") -> str:
    symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(currency, "")
    return f"{symbol}{cents / 100:,.2f}"


def qty(qty_base: Decimal, unit_code: str) -> str:
    return format_qty(qty_base, unit_code)
