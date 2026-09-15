"""Pure stock and expiry alerting. Works identically for count, weight and volume."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.transform.expiry import BatchPosition, ItemExpiry
from app.transform.inventory import (
    ZERO,
    StockView,
    days_of_cover,
    stale_after,
    suggest_order_base,
)
from app.units import Dimension, format_amount, format_approx

STALE_COUNT_DAYS = 14
EXPIRY_HORIZON_DAYS = 30
# With no sales signal nothing can be projected, so only a lot this close to its date is raised.
EXPIRY_SOON_DAYS = 7


@dataclass(frozen=True)
class AlertProposal:
    stock_item_id: int
    kind: str
    message: str
    qty_base: Decimal
    threshold_base: Decimal
    days_of_cover: Decimal | None
    suggested_order_base: Decimal
    severity: int  # 0 highest
    value_cents: int | None = None


def evaluate_item(
    item: StockView,
    velocity_per_day: Decimal,
    now: datetime,
    stale_count_days: int = STALE_COUNT_DAYS,
) -> AlertProposal | None:
    """Return the single most severe alert for one stock item, if any."""
    qty = Decimal(item.qty_base)
    reorder = Decimal(item.reorder_point_base)
    cover = days_of_cover(qty, velocity_per_day)
    suggestion = suggest_order_base(item, velocity_per_day)
    qty_label = format_amount(qty, item.stock_unit)

    if qty <= ZERO:
        return AlertProposal(
            item.id,
            "out_of_stock",
            f"{item.name} is out of stock",
            qty,
            reorder,
            cover,
            suggestion,
            0,
        )

    if reorder > ZERO and qty <= reorder:
        return AlertProposal(
            item.id,
            "low_stock",
            f"{item.name} is at {qty_label}, at or below its reorder point of "
            f"{format_amount(reorder, item.stock_unit)}",
            qty,
            reorder,
            cover,
            suggestion,
            1,
        )

    # Velocity can breach lead time even when the absolute quantity looks healthy.
    if cover is not None and cover <= Decimal(item.lead_time_days):
        return AlertProposal(
            item.id,
            "low_stock",
            f"{item.name} has {qty_label} left, about {cover} days of cover "
            f"against a {item.lead_time_days} day lead time",
            qty,
            reorder,
            cover,
            suggestion,
            2,
        )

    if item.track_mode == "derived" and stale_after(item.last_counted_at, stale_count_days, now):
        return AlertProposal(
            item.id,
            "stale_count",
            f"{item.name} has not been physically counted in over {stale_count_days} days, "
            "so its quantity is an estimate",
            qty,
            reorder,
            cover,
            ZERO,
            3,
        )

    return None


def evaluate_stock(
    items: list[StockView],
    velocity: dict[int, Decimal],
    now: datetime,
    stale_count_days: int = STALE_COUNT_DAYS,
) -> list[AlertProposal]:
    """Evaluate every item, most severe first."""
    proposals = [
        proposal
        for item in items
        if (proposal := evaluate_item(item, velocity.get(item.id, ZERO), now, stale_count_days))
    ]
    return sorted(proposals, key=lambda p: (p.severity, p.days_of_cover or ZERO))


def amount_of(quantity: Decimal, item: StockView) -> str:
    """'5 Oat milk 1L carton' or '5.2 L of Cold brew'. Expiry quantities are estimates, so rounded."""
    if item.dimension is Dimension.COUNT and item.stock_unit == "each":
        return f"{quantity.quantize(Decimal('1'))} {item.name}"
    return f"{format_approx(quantity, item.stock_unit)} of {item.name}"


def counts_as_waste_risk(position: BatchPosition, horizon_days: int) -> bool:
    """One rule for the alert, the email and the page, so they never disagree about the money."""
    if position.expired or position.remaining_base <= ZERO or position.days_left > horizon_days:
        return False
    if position.projected_leftover_base is None:
        return position.days_left <= EXPIRY_SOON_DAYS
    return position.projected_leftover_base > ZERO


def evaluate_expiry(
    expiry: ItemExpiry, horizon_days: int = EXPIRY_HORIZON_DAYS
) -> list[AlertProposal]:
    """Up to two alerts per item: stock already past its date, and stock that will not sell in time.

    Kept separate from evaluate_item because they are independent of the stock level: an item
    can be low on the shelf and still hold a lot that will expire before it sells.
    """
    item = expiry.item
    proposals: list[AlertProposal] = []

    expired = [p for p in expiry.positions if p.expired and p.remaining_base > ZERO]
    if expired:
        quantity = sum((p.remaining_base for p in expired), ZERO)
        oldest = min(p.batch.expires_on for p in expired)
        proposals.append(
            AlertProposal(
                item.id,
                "expired",
                f"{amount_of(quantity, item)} passed its date on {oldest:%d %b} and is still "
                "counted as stock. Take it off sale, then record a count.",
                quantity,
                ZERO,
                None,
                ZERO,
                1,
                sum(p.at_risk_cents for p in expired),
            )
        )

    at_risk = [p for p in expiry.positions if counts_as_waste_risk(p, horizon_days)]
    if at_risk:
        quantity = sum((p.at_risk_base for p in at_risk), ZERO)
        soonest = min(p.batch.expires_on for p in at_risk)
        amount = amount_of(quantity, item)
        if any(p.projected_leftover_base is None for p in at_risk):
            message = f"{amount} reaches its date on {soonest:%d %b}. Sell it first."
        else:
            message = (
                f"About {amount} will not sell before {soonest:%d %b} at the current rate. "
                "Move it to the front, or mark it down."
            )
        proposals.append(
            AlertProposal(
                item.id,
                "waste_risk",
                message,
                quantity,
                ZERO,
                None,
                ZERO,
                2,
                sum(p.at_risk_cents for p in at_risk),
            )
        )
    return proposals


def evaluate_expiries(
    expiries: list[ItemExpiry], horizon_days: int = EXPIRY_HORIZON_DAYS
) -> list[AlertProposal]:
    """Expiry alerts for every item with dated stock, largest loss first."""
    proposals = [p for expiry in expiries for p in evaluate_expiry(expiry, horizon_days)]
    return sorted(proposals, key=lambda p: (p.severity, -(p.value_cents or 0)))
