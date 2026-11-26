"""The weekly summary (T2.18; ANL-FR-09, AC-ANL-07). Pure shaping of figures read elsewhere."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.transform.inventory import ZERO, StockView
from app.units import format_qty

LOW_STOCK_SHOWN = 10
EXCEPTIONS_SHOWN = 5


@dataclass(frozen=True)
class LowStock:
    """An item at or below its reorder point, or out of stock."""

    name: str
    location: str
    qty_base: Decimal
    reorder_point_base: Decimal
    unit: str


@dataclass(frozen=True)
class OpenException:
    title: str
    severity: str
    money_cents: int | None


@dataclass(frozen=True)
class SalesData:
    """A location's sales last week: where they came from, and on its Standalone days how many
    expected days are covered (STD-FR-07, WF-24, PD-54)."""

    location: str
    sales_from: str  # "" when nothing was sold
    covered: int
    expected: int  # 0 when no day was expected (not Standalone, or closed)
    percent: Decimal | None = None


@dataclass(frozen=True)
class WeeklySummary:
    """What an owner who never opens the app needs from last week (ANL-FR-09)."""

    period_start: date
    period_end: date
    stock_value_cents: int
    items_without_cost: int
    waste_cents: int  # last week's waste at each movement's snapshot
    waste_uncosted: bool
    at_risk_cents: int  # stock that will not be used before its date, and stock past it
    low_stock: tuple[LowStock, ...]
    low_stock_count: int
    exceptions: tuple[OpenException, ...]
    exceptions_open: int
    sales_data: tuple[SalesData, ...] = ()


def week_bounds(today: date, weeks_back: int = 1) -> tuple[date, date]:
    """Monday-to-Sunday window ending before today."""
    last_sunday = today - timedelta(days=today.weekday() + 1)
    end = last_sunday - timedelta(days=7 * (weeks_back - 1))
    return end - timedelta(days=6), end


def low_stock(stock: Iterable[StockView]) -> list[LowStock]:
    """Out of stock, or at or below a reorder point, the furthest below first."""
    found = [
        item
        for item in stock
        if Decimal(item.qty_base) <= ZERO
        or (
            Decimal(item.reorder_point_base) > ZERO
            and Decimal(item.qty_base) <= Decimal(item.reorder_point_base)
        )
    ]
    found.sort(key=lambda i: (Decimal(i.qty_base) - Decimal(i.reorder_point_base), i.name.lower()))
    return [
        LowStock(
            i.name,
            i.location_name,
            Decimal(i.qty_base),
            Decimal(i.reorder_point_base),
            i.stock_unit,
        )
        for i in found
    ]


def money(cents: int, currency: str = "USD") -> str:
    symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(currency, "")
    return f"{symbol}{cents / 100:,.2f}"


def qty(qty_base: Decimal, unit_code: str) -> str:
    return format_qty(qty_base, unit_code)
