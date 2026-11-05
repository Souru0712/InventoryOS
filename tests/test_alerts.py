from datetime import UTC, datetime
from decimal import Decimal

from app.transform.alerts import evaluate_item, evaluate_stock
from app.transform.inventory import StockView
from app.units import Dimension, to_base

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def view(**overrides) -> StockView:
    defaults = dict(
        id=1,
        slug="item",
        name="Whole milk",
        dimension=Dimension.VOLUME,
        stock_unit="l",
        track_mode="derived",
        qty_base=to_base(30, "l"),
        reorder_point_base=to_base(20, "l"),
        cost_per_base_cents=Decimal("0.095"),
        lead_time_days=3,
        last_counted_at=NOW,
    )
    return StockView(**{**defaults, **overrides})


def test_out_of_stock_wins_over_everything():
    alert = evaluate_item(view(qty_base=Decimal("0")), Decimal("1000"), NOW)
    assert alert.kind == "out_of_stock"
    assert alert.severity == 0
    assert "out of stock" in alert.message


def test_reorder_point_breach_is_reported_in_the_stock_unit():
    alert = evaluate_item(view(qty_base=to_base(18, "l")), Decimal("0"), NOW)
    assert alert.kind == "low_stock"
    assert "18 L" in alert.message and "20 L" in alert.message


def test_velocity_catches_stock_that_looks_healthy():
    # 60 L on hand, well above the 20 L reorder point, but 25 L a day and a 3 day lead time.
    alert = evaluate_item(view(qty_base=to_base(60, "l")), to_base(25, "l"), NOW)
    assert alert.kind == "low_stock"
    assert "days of cover" in alert.message


def test_weight_and_count_items_use_the_same_rules():
    ham = view(
        id=2,
        name="Sliced ham",
        dimension=Dimension.WEIGHT,
        stock_unit="kg",
        qty_base=to_base("2.1", "kg"),
        reorder_point_base=to_base("2.5", "kg"),
    )
    cups = view(
        id=3,
        name="Takeaway cups",
        dimension=Dimension.COUNT,
        stock_unit="each",
        qty_base=Decimal("250"),
        reorder_point_base=Decimal("300"),
    )
    assert evaluate_item(ham, Decimal("0"), NOW).kind == "low_stock"
    assert "2.1 kg" in evaluate_item(ham, Decimal("0"), NOW).message
    message = evaluate_item(cups, Decimal("0"), NOW).message
    # "each" labels a column; inside a sentence the bare count is the English.
    assert "is at 250, at or below its reorder point of 300" in message
    assert " each" not in message


def test_stale_count_only_applies_to_derived_stock():
    old = datetime(2026, 8, 1, tzinfo=UTC)
    healthy = dict(qty_base=to_base(90, "l"), reorder_point_base=to_base(5, "l"))
    derived = evaluate_item(view(last_counted_at=old, **healthy), Decimal("0"), NOW)
    provider = evaluate_item(
        view(track_mode="provider", last_counted_at=old, **healthy), Decimal("0"), NOW
    )
    assert derived.kind == "stale_count"
    assert provider is None


def test_healthy_stock_produces_no_alert():
    assert evaluate_item(view(qty_base=to_base(90, "l")), to_base(1, "l"), NOW) is None


def test_stock_is_ranked_most_urgent_first():
    items = [
        view(id=1, name="fine", qty_base=to_base(90, "l"), reorder_point_base=to_base(5, "l")),
        view(id=2, name="low", qty_base=to_base(10, "l")),
        view(id=3, name="empty", qty_base=Decimal("0")),
    ]
    alerts = evaluate_stock(items, {2: Decimal("0"), 3: Decimal("0")}, NOW)
    assert [a.stock_item_id for a in alerts] == [3, 2]
