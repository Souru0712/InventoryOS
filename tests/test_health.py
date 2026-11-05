"""One health number, and every reason behind it."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.transform.health import (
    WEIGHTS,
    ComponentScore,
    ExpiryFigures,
    HealthInput,
    ItemHealth,
    band_for,
    build_actions,
    explain_change,
    importance,
    overall,
    score_data,
    score_expiry,
    score_inventory,
    score_overstock,
    score_reorder,
    score_stock,
    versus_average,
)
from app.transform.inventory import StockView
from app.units import Dimension

NOW = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)


def view(id=1, **overrides) -> StockView:
    defaults = dict(
        id=id,
        slug=f"item-{id}",
        name=f"Item {id}",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode="provider",
        qty_base=Decimal("50"),
        reorder_point_base=Decimal("10"),
        cost_per_base_cents=Decimal("100"),
        lead_time_days=3,
        last_counted_at=NOW,
    )
    return StockView(**{**defaults, **overrides})


def health(id=1, velocity="1", signal=True, **overrides) -> ItemHealth:
    return ItemHealth(view(id, **overrides), Decimal(velocity), signal)


def comp(key, score, figures=None) -> ComponentScore:
    return ComponentScore(key, score, [], figures or {})


# --- the total ----------------------------------------------------------------------------------


def test_the_score_is_the_weighted_average_of_the_five_dimensions():
    components = [
        comp("stock", 92),
        comp("expiry", 60),
        comp("overstock", 80),
        comp("reorder", 70),
        comp("data", 85),
    ]
    expected = 92 * 0.30 + 60 * 0.25 + 80 * 0.20 + 70 * 0.15 + 85 * 0.10
    assert overall(components) == round(expected)


def test_the_weights_add_up():
    assert sum(WEIGHTS.values()) == Decimal("1")


def test_a_dimension_that_cannot_be_measured_drops_out_instead_of_scoring_100():
    # Nothing dated: expiry is unknown, not perfect. The other four are reweighted.
    components = [
        comp("stock", 80),
        comp("expiry", None),
        comp("overstock", 80),
        comp("reorder", 80),
        comp("data", 80),
    ]
    assert overall(components) == 80


def test_nothing_measurable_gives_no_score_rather_than_a_number():
    assert overall([comp("stock", None), comp("data", None)]) is None


@pytest.mark.parametrize(
    "score, band",
    [
        (100, "excellent"),
        (90, "excellent"),
        (89, "healthy"),
        (75, "healthy"),
        (74, "needs_attention"),
        (60, "needs_attention"),
        (59, "at_risk"),
        (40, "at_risk"),
        (39, "critical"),
        (0, "critical"),
        (None, "unscored"),
    ],
)
def test_status_bands(score, band):
    assert band_for(score)[0] == band


# --- stock availability -------------------------------------------------------------------------


def test_eight_of_a_hundred_below_threshold_scores_92():
    # Equal sales, so importance is equal: the example straight from the spec.
    items = [health(i, qty_base=Decimal("5") if i <= 8 else Decimal("50")) for i in range(1, 101)]
    assert score_stock(items, importance(items)).score == 92


def test_a_missing_best_seller_costs_more_than_a_missing_slow_seller():
    coke = health(1, velocity="40", qty_base=Decimal("0"))
    candy = health(2, velocity="0.2")
    rest = [health(i, velocity="2") for i in range(3, 11)]
    coke_out = score_stock([coke, candy, *rest], importance([coke, candy, *rest])).score

    coke_in = health(1, velocity="40")
    candy_out = health(2, velocity="0.2", qty_base=Decimal("0"))
    candy_gone = score_stock(
        [coke_in, candy_out, *rest], importance([coke_in, candy_out, *rest])
    ).score
    assert coke_out < candy_gone


def test_unknown_sales_count_as_an_average_item_not_as_nothing():
    weights = importance([health(1, velocity="4"), health(2, velocity="0", signal=False)])
    assert weights[2] == weights[1]


def test_out_of_stock_is_worse_news_than_low_stock():
    out = score_stock([health(1, qty_base=Decimal("0"))], importance([health(1)]))
    assert out.findings[0].tone == "bad"
    assert out.findings[0].link == "/app/reorder"


# --- expiration risk ----------------------------------------------------------------------------


def test_four_percent_of_stock_at_risk_is_not_excellent():
    # $400 of $10,000. Linear would call this 96; scored against 10% it is 60.
    component = score_expiry(
        ExpiryFigures(at_risk_cents=40000, at_risk_products=3, dated_items=5), 1_000_000, "USD"
    )
    assert component.score == 60
    assert "$400.00" in component.findings[0].text


def test_expired_stock_counts_as_risk_and_is_called_out_first():
    component = score_expiry(
        ExpiryFigures(
            expired_cents=10000,
            expired_products=1,
            at_risk_cents=10000,
            at_risk_products=1,
            dated_items=2,
        ),
        1_000_000,
        "USD",
    )
    assert component.score == 80
    assert component.findings[0].tone == "bad"
    assert "past its date" in component.findings[0].text


def test_nothing_dated_is_not_scored_and_says_so():
    component = score_expiry(ExpiryFigures(dated_items=0), 1_000_000, "USD")
    assert component.score is None
    assert "Not scored yet" in component.findings[0].text


def test_money_is_shown_in_the_stores_currency():
    component = score_expiry(
        ExpiryFigures(at_risk_cents=5000, at_risk_products=1, dated_items=1), 100000, "GBP"
    )
    assert "£50.00" in component.findings[0].text


# --- overstock ----------------------------------------------------------------------------------


def test_a_hundred_days_of_supply_is_overstock():
    slow = health(1, velocity="1", qty_base=Decimal("100"))
    assert slow.overstocked
    assert score_overstock([slow, health(2, velocity="5")], "USD").score < 100


def test_stock_with_no_sales_is_dead_stock_when_sales_would_show():
    dead = health(1, velocity="0", signal=True, qty_base=Decimal("20"))
    assert dead.overstocked and dead.not_selling


def test_stock_with_no_way_to_see_sales_is_never_called_overstocked():
    unknown = health(1, velocity="0", signal=False, qty_base=Decimal("500"))
    assert not unknown.overstocked
    assert score_overstock([unknown], "USD").score is None


# --- reorder health -----------------------------------------------------------------------------


def test_five_days_of_cover_against_a_seven_day_lead_time_is_a_stockout_risk():
    item = health(
        1, velocity="2", qty_base=Decimal("10"), reorder_point_base=Decimal("0"), lead_time_days=7
    )
    assert item.approaching_stockout
    component = score_reorder([item, health(2)], importance([item, health(2)]))
    assert component.score < 100
    assert component.findings[0].link == "/app/reorder"


def test_stock_already_at_its_reorder_point_is_not_counted_twice():
    # It already costs stock availability; reorder health is about the ones still above it.
    low = health(
        1, velocity="5", qty_base=Decimal("5"), reorder_point_base=Decimal("10"), lead_time_days=7
    )
    assert not low.approaching_stockout


# --- data completeness --------------------------------------------------------------------------


def test_data_completeness_averages_its_checks():
    items = [health(1), health(2, cost_per_base_cents=Decimal("0"))]
    # cost: 50%, reorder point: 100%, dates: 85%  ->  78
    assert score_data(items, 85, 2, NOW).score == 78


def test_every_data_gap_says_what_to_fix():
    items = [health(1, cost_per_base_cents=Decimal("0"), reorder_point_base=Decimal("0"))]
    texts = " ".join(f.text for f in score_data(items, 50, 3, NOW).findings)
    assert "3 products still need dates" in texts
    assert "no cost" in texts and "no reorder point" in texts


def test_weight_and_volume_not_counted_recently_is_a_data_gap():
    stale = health(1, track_mode="derived", last_counted_at=datetime(2026, 8, 1, tzinfo=UTC))
    assert "not counted" in " ".join(f.text for f in score_data([stale], None, 0, NOW).findings)


# --- the report ---------------------------------------------------------------------------------


def test_the_headline_puts_the_worst_news_first():
    items = [health(1, qty_base=Decimal("0")), *[health(i) for i in range(2, 6)]]
    report = score_inventory(HealthInput(items, ExpiryFigures(dated_items=1)), NOW)
    assert report.headline[0].tone == "bad"
    tones = [f.tone for f in report.headline]
    assert tones == sorted(tones, key=["bad", "warn", "good", "info"].index)


def test_every_problem_in_the_headline_links_somewhere_to_fix_it():
    items = [health(1, qty_base=Decimal("0")), health(2, velocity="1", qty_base=Decimal("200"))]
    expiry = ExpiryFigures(at_risk_cents=5000, at_risk_products=1, dated_items=1)
    report = score_inventory(HealthInput(items, expiry, coverage_percent=40, needs_dates=2), NOW)
    for finding in report.headline:
        if finding.tone in ("bad", "warn"):
            assert finding.link, finding.text


# --- the action center --------------------------------------------------------------------------


def test_actions_split_urgent_from_recommended():
    items = [
        health(1, qty_base=Decimal("0")),  # out of stock: urgent
        health(
            2,
            velocity="2",
            qty_base=Decimal("10"),
            reorder_point_base=Decimal("0"),
            lead_time_days=7,
        ),  # soon
        health(3, velocity="1", qty_base=Decimal("200")),  # slow mover
        health(4),
        health(5),  # fine
    ]
    expiry = ExpiryFigures(expired_products=1, dated_items=1, item_ids=frozenset())
    actions = build_actions(
        HealthInput(items, expiry, needs_dates=3, promotable_urgent=1, promotable=3)
    )

    urgent = " ".join(a.text for a in actions.urgent)
    recommended = " ".join(a.text for a in actions.recommended)
    assert "past its date" in urgent and "out of stock" in urgent and "within 3 days" in urgent
    assert "before it runs out" in recommended
    assert "2 markdown suggestions" in recommended
    assert "slow-moving" in recommended and "Add dates to 3 products" in recommended
    assert actions.healthy_items == 2


def test_a_quiet_day_has_nothing_to_do():
    actions = build_actions(HealthInput([health(1), health(2)]))
    assert actions.urgent == [] and actions.recommended == []
    assert actions.healthy_items == 2


# --- over time ----------------------------------------------------------------------------------


def snap(score, **components):
    return {
        "score": score,
        "currency": "USD",
        "components": {
            key: {"score": value[0], "figures": value[1]} for key, value in components.items()
        },
    }


def test_a_change_is_explained_dimension_by_dimension():
    before = snap(
        78,
        stock=(90, {"out_of_stock": 1, "below_reorder": 3}),
        expiry=(40, {"at_risk_cents": 31200, "at_risk_products": 6}),
        data=(80, {}),
    )
    after = snap(
        83,
        stock=(95, {"out_of_stock": 0, "below_reorder": 2}),
        expiry=(70, {"at_risk_cents": 9000, "at_risk_products": 3}),
        data=(80, {}),
    )
    change = explain_change(before, after)

    assert change.points == 5
    text = " ".join(line for _, line in change.lines)
    assert "$222.00 less stock at risk of expiring, 3 products cleared" in text
    assert "2 products fewer at or below the reorder point" in text
    assert abs(change.lines[0][0]) >= abs(change.lines[-1][0])  # biggest mover first


def test_a_dimension_scored_for_the_first_time_is_named_not_blamed():
    before = snap(80, stock=(80, {}), expiry=(None, {}))
    after = snap(70, stock=(80, {}), expiry=(50, {"at_risk_cents": 100}))
    text = " ".join(line for _, line in explain_change(before, after).lines)
    assert "Expiration risk is now scored" in text


def test_no_change_can_be_explained_without_two_scores():
    assert explain_change(snap(None), snap(80)) is None


def test_the_snapshot_round_trips_into_an_explanation():
    items = [health(i) for i in range(1, 4)]
    report = score_inventory(HealthInput(items), NOW)
    assert explain_change(report.to_snapshot(), report.to_snapshot()).points == 0


def test_the_business_is_compared_with_its_own_past():
    benchmark = versus_average(83, [72, 76, 76])
    assert benchmark.average == 75
    assert benchmark.percent == 11


def test_too_little_history_makes_no_comparison():
    assert versus_average(83, [72, 76]) is None
    assert versus_average(None, [72, 76, 80]) is None
