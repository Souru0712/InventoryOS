"""What to mark down, by how much, and why it can be argued with."""
from decimal import Decimal

import pytest

from app.transform.promotions import (
    PromotionInput,
    discount_for,
    effectiveness,
    required_velocity,
    suggest,
    suggest_all,
    urgency_for,
)


def lot(**overrides) -> PromotionInput:
    """Gatorade 28oz: costs 80c, sells for $1.99."""
    defaults = dict(
        stock_item_id=1,
        name="Gatorade Lemon-Lime 28oz",
        stock_unit="each",
        remaining_base=Decimal("24"),
        projected_leftover_base=Decimal("13"),
        days_left=9,
        velocity_per_day=Decimal("1.2"),
        cost_per_base_cents=Decimal("80"),
        retail_per_base_cents=Decimal("199"),
    )
    return PromotionInput(**{**defaults, **overrides})


# --- when to say nothing at all ---------------------------------------------------------------

def test_stock_that_clears_itself_gets_no_recommendation():
    # 24 units, 4 a day, 10 days: 40 projected sales. Nothing to do.
    assert suggest(lot(projected_leftover_base=Decimal("0"), velocity_per_day=Decimal("4"),
                       days_left=10)) is None


def test_a_date_too_far_out_is_left_alone():
    assert suggest(lot(days_left=60)) is None


def test_stock_already_past_its_date_is_not_a_promotion():
    # It comes off the shelf; discounting it would be the wrong advice.
    assert suggest(lot(days_left=-1)) is None


def test_an_item_with_no_selling_price_cannot_be_discounted():
    assert suggest(lot(retail_per_base_cents=None)) is None


# --- the tiers --------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "days, tier",
    [(0, "critical"), (3, "critical"), (4, "high"), (7, "high"),
     (8, "medium"), (14, "medium"), (15, "low"), (30, "low"), (31, "none"), (-1, "expired")],
)
def test_urgency_follows_the_days_left(days, tier):
    assert urgency_for(days) == tier


def test_a_deeper_cut_the_closer_the_date():
    shallow = suggest(lot(days_left=20, projected_leftover_base=Decimal("13")))
    deep = suggest(lot(days_left=2, projected_leftover_base=Decimal("13")))
    assert deep.discount_percent > shallow.discount_percent


# --- how fast it has to move ------------------------------------------------------------------

def test_required_velocity_is_what_clears_the_shelf_in_time():
    assert required_velocity(Decimal("24"), 10) == Decimal("2.40")


def test_the_last_day_still_asks_for_the_whole_lot():
    # Never divide by zero days; on the final day it all has to go.
    assert required_velocity(Decimal("24"), 0) == Decimal("24")


def test_acceleration_says_how_much_faster_it_must_sell():
    suggestion = suggest(lot(remaining_base=Decimal("24"), days_left=10,
                             velocity_per_day=Decimal("1.2")))
    assert suggestion.required_velocity == Decimal("2.40")
    assert suggestion.acceleration == Decimal("2.0")


def test_nothing_selling_leaves_acceleration_unknown_and_takes_the_top_of_the_band():
    # 9 days out is the medium tier, 10-20%. Rounding the price down can carry it a little
    # past the ceiling, which is the right direction to err.
    suggestion = suggest(lot(velocity_per_day=Decimal("0"), days_left=9))
    assert suggestion.acceleration is None
    assert suggestion.discount_percent >= 20


def test_a_critical_lot_with_no_sales_cuts_hardest():
    suggestion = suggest(lot(velocity_per_day=Decimal("0"), days_left=2))
    assert suggestion.urgency == "critical"
    assert suggestion.discount_percent >= 50


def test_a_lot_needing_a_nudge_gets_the_shallow_end_of_its_band():
    barely = discount_for("high", Decimal("1.1"))
    doubled = discount_for("high", Decimal("2.0"))
    assert barely < doubled
    assert Decimal("0.20") <= barely <= Decimal("0.30")
    assert Decimal("0.20") <= doubled <= Decimal("0.30")


# --- money ------------------------------------------------------------------------------------

def test_the_numbers_at_stake_are_the_leftover_not_the_whole_lot():
    suggestion = suggest(lot(projected_leftover_base=Decimal("13")))
    assert suggestion.units_at_risk_base == Decimal("13")
    assert suggestion.cost_at_risk_cents == 1040  # 13 x 80c
    assert suggestion.retail_at_risk_cents == 2587  # 13 x $1.99


def test_the_suggested_price_is_the_discount_applied_and_rounded_down():
    suggestion = suggest(lot(days_left=9, velocity_per_day=Decimal("1.2"),
                             remaining_base=Decimal("24")))
    # High tier on $1.99; the price lands on a 5 cent step, never rounded up.
    assert suggestion.suggested_price_cents % 5 == 0
    assert suggestion.suggested_price_cents < suggestion.normal_price_cents


def test_expected_revenue_is_what_the_leftover_would_bring_in():
    suggestion = suggest(lot(projected_leftover_base=Decimal("13")))
    assert suggestion.expected_revenue_cents == 13 * suggestion.suggested_price_cents


# --- the margin floor -------------------------------------------------------------------------

def test_a_markdown_never_goes_below_the_margin_floor():
    # Costs 80c, so a 50% cut off $1.20 would sell at a loss.
    suggestion = suggest(lot(retail_per_base_cents=Decimal("120"), days_left=1,
                             velocity_per_day=Decimal("0")))
    assert suggestion.suggested_price_cents >= 80
    assert suggestion.held_at_floor is True
    assert any("margin floor" in reason for reason in suggestion.reasons)


def test_the_floor_can_be_relaxed_for_a_loss_leader():
    cheap = suggest(lot(retail_per_base_cents=Decimal("120"), days_left=1,
                        velocity_per_day=Decimal("0"), min_margin=Decimal("0")))
    assert cheap.suggested_price_cents < 80 + 1  # down to cost, which is the point


def test_a_healthy_margin_leaves_the_discount_alone():
    suggestion = suggest(lot(days_left=9))
    assert suggestion.held_at_floor is False


# --- the explanation --------------------------------------------------------------------------

def test_every_recommendation_explains_itself():
    suggestion = suggest(lot())
    assert len(suggestion.reasons) >= 3
    joined = " ".join(suggestion.reasons)
    assert "on the shelf" in joined
    assert "a day" in joined
    assert "tier" in joined


def test_the_explanation_names_the_real_tier():
    assert "high tier" in " ".join(suggest(lot(days_left=5)).reasons)


# --- ordering ---------------------------------------------------------------------------------

def test_the_biggest_loss_is_recommended_first():
    small = lot(stock_item_id=1, name="Small", projected_leftover_base=Decimal("2"))
    large = lot(stock_item_id=2, name="Large", projected_leftover_base=Decimal("40"),
                remaining_base=Decimal("60"))
    assert [s.item.name for s in suggest_all([small, large])] == ["Large", "Small"]


def test_nothing_worth_doing_gives_an_empty_list():
    assert suggest_all([lot(days_left=90), lot(projected_leftover_base=Decimal("0"))]) == []


# --- afterwards -------------------------------------------------------------------------------

def test_effectiveness_compares_before_with_during():
    assert effectiveness(Decimal("1.2"), Decimal("4.8")) == Decimal("4.0")


def test_effectiveness_needs_something_to_compare():
    assert effectiveness(Decimal("0"), Decimal("4.8")) is None
    assert effectiveness(None, Decimal("4.8")) is None
    assert effectiveness(Decimal("1.2"), None) is None
