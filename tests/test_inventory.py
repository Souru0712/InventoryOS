from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.transform.inventory import (
    Component,
    SaleRow,
    StockView,
    consumption_by_day,
    daily_velocity,
    days_of_cover,
    stale_after,
    suggest_order_base,
    valuation_cents,
)
from app.units import Dimension, to_base

TODAY = date(2026, 9, 6)


def view(**overrides) -> StockView:
    defaults = dict(
        id=1,
        slug="milk",
        name="Whole milk",
        dimension=Dimension.VOLUME,
        stock_unit="l",
        track_mode="derived",
        qty_base=to_base(30, "l"),
        reorder_point_base=to_base(20, "l"),
        cost_per_base_cents=Decimal("0.095"),
        lead_time_days=3,
    )
    return StockView(**{**defaults, **overrides})


def test_sales_explode_into_base_units_per_stock_item():
    sales = [SaleRow(sold_item_id=10, dt=TODAY, units=Decimal("100"))]
    components = [
        Component(10, 1, to_base(220, "ml")),  # milk
        Component(10, 2, to_base(18, "g")),  # beans
    ]
    daily = consumption_by_day(sales, components)
    assert daily[1][TODAY] == Decimal("22000.0000")  # 22 litres
    assert daily[2][TODAY] == Decimal("1800.0000")  # 1.8 kg


def test_velocity_and_cover_use_the_trailing_window():
    daily = {TODAY - timedelta(days=offset): Decimal("1400") for offset in range(7)}
    velocity = daily_velocity(daily, 7, TODAY)
    assert velocity == Decimal("1400.0000")
    assert days_of_cover(to_base(28, "l"), velocity) == Decimal("20.0")


def test_cover_is_unknown_when_nothing_moves():
    assert days_of_cover(Decimal("100"), Decimal("0")) is None


def test_suggested_order_tops_up_to_par_and_respects_dimension():
    countable = view(
        id=2,
        dimension=Dimension.COUNT,
        stock_unit="each",
        qty_base=Decimal("9"),
        par_level_base=Decimal("36"),
    )
    assert suggest_order_base(countable, Decimal("4")) == Decimal("27")

    weighed = view(
        id=3,
        dimension=Dimension.WEIGHT,
        stock_unit="kg",
        qty_base=to_base("3.1", "kg"),
        par_level_base=to_base(8, "kg"),
    )
    assert suggest_order_base(weighed, Decimal("300")) == Decimal("4900.00")


def test_suggested_order_falls_back_to_lead_time_cover():
    item = view(par_level_base=None, qty_base=to_base(10, "l"), lead_time_days=3)
    # 3 L/day over 3 + 14 days = 51 L target, 10 L on hand.
    assert suggest_order_base(item, to_base(3, "l"), cover_days=14) == Decimal("41000.00")


def test_no_order_needed_when_above_par():
    item = view(qty_base=to_base(90, "l"), par_level_base=to_base(80, "l"))
    assert suggest_order_base(item, Decimal("1000")) == Decimal("0")


def test_valuation_handles_sub_cent_unit_costs():
    # Milk at 95 cents per litre is 0.095 cents per millilitre.
    assert valuation_cents(to_base(34, "l"), Decimal("0.095")) == 3230


def test_stale_count_detection():
    now = datetime(2026, 9, 6, tzinfo=UTC)
    assert stale_after(None, 14, now) is True
    assert stale_after(datetime(2026, 8, 1, tzinfo=UTC), 14, now) is True
    assert stale_after(datetime(2026, 9, 1, tzinfo=UTC), 14, now) is False
    # Naive timestamps from the database are treated as UTC, not crashed on.
    assert stale_after(datetime(2026, 9, 1), 14, now) is False
