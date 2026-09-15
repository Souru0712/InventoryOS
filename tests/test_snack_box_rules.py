"""Snack box rules that need no database: allocation, credit, taste, and the box builder."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.transform.allocation import Allocation, TransitionError, check_transition, position, short_lines
from app.transform.boxes import Candidate, Rules, build_box, score, suggest_replacements, unsafe
from app.transform.credits import ReferralTransitionError, check_referral_transition, redeemable, referral_problem
from app.transform.taste import Received, Review, Stated, Taste, build_taste, crowd_quality

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
RULES = Rules(min_items=8, max_items=12, target_cogs_cents=Decimal(800), cogs_ceiling_cents=Decimal(1200),
              min_days_after_pickup=16)


def alloc(line_id, minutes, applied=True, packed=False, qty=1):
    return Allocation(line_id, line_id, T0 + timedelta(minutes=minutes), Decimal(qty), applied, packed)


def snack(i, **changes):
    values = dict(
        stock_item_id=i, name=f"s{i}", category=f"cat{i % 4}", origin=("US", "JP", "KR", "MX")[i % 4],
        flavors=frozenset({("sweet", "salty", "spicy", "weird")[i % 4]}), allergens=frozenset(),
        unit_cost_cents=Decimal(100), available_base=Decimal(5), days_left=120,
    )
    values.update(changes)
    return Candidate(**values)


def shelf(n=12):
    return [snack(i) for i in range(n)]


# --- allocation ----------------------------------------------------------------------------------

def test_walk_ins_may_buy_the_count_less_box_units_it_was_never_told_about():
    pos = position(Decimal(6), [alloc(1, 0), alloc(2, 1), alloc(3, 2, applied=False)])
    assert (pos.store_available_base, pos.allocated_base, pos.unapplied_base) == (5, 3, 1)


def test_the_shelf_holds_applied_unpacked_units_but_not_packed_ones():
    pos = position(Decimal(6), [alloc(1, 0), alloc(2, 1, applied=False, packed=True)])
    assert pos.on_shelf_base == 6


def test_the_till_overselling_shorts_the_newest_paid_box_first():
    # 10 on hand, four boxes took one each (Square shows 6), then the till sold 7: Square shows -1.
    pos = position(Decimal(-1), [alloc(1, 0), alloc(2, 5), alloc(3, 10), alloc(4, 15)])
    assert short_lines(pos) == [4]


def test_a_packed_box_never_goes_short():
    assert short_lines(position(Decimal(-2), [alloc(1, 0, packed=True), alloc(2, 5)])) == [2]


def test_nothing_goes_short_while_the_till_is_in_stock():
    assert short_lines(position(Decimal(0), [alloc(1, 0)])) == []


def test_money_that_has_moved_can_only_leave_by_refund():
    check_transition("pending_payment", "paid")
    check_transition("picked_up", "refunded")
    with pytest.raises(TransitionError):
        check_transition("paid", "cancelled")
    with pytest.raises(TransitionError):
        check_transition("refunded", "paid")


# --- credit -------------------------------------------------------------------------------------

def test_redemption_is_capped_and_never_spends_a_negative_balance():
    assert (redeemable(1000, 2499), redeemable(5000, 1999), redeemable(-500, 2499), redeemable(0, 2499)) == (
        1000, 1999, 0, 0)


def test_referral_problems():
    ok = dict(code_found=True, self_referral=False, referrer_active=True, already_referred=False, bought_before=False)
    assert referral_problem(**ok) == ""
    assert "does not exist" in referral_problem(**{**ok, "code_found": False})
    assert "your own" in referral_problem(**{**ok, "self_referral": True})
    assert "no longer active" in referral_problem(**{**ok, "referrer_active": False})
    assert "already been used" in referral_problem(**{**ok, "already_referred": True})
    assert "first box only" in referral_problem(**{**ok, "bought_before": True})


def test_referral_transitions():
    check_referral_transition("pending", "qualified")
    check_referral_transition("rewarded", "reversed")
    with pytest.raises(ReferralTransitionError):
        check_referral_transition("rewarded", "cancelled")
    with pytest.raises(ReferralTransitionError):
        check_referral_transition("cancelled", "pending")


# --- taste --------------------------------------------------------------------------------------

def test_one_bad_rating_is_a_hint_and_a_stated_preference_is_a_verdict():
    reviews = [Review(1, "disliked", False, T0)]
    assert build_taste(Stated(), reviews, [], {1: "Chips"}, {}).category["chips"] == -0.25
    stated = build_taste(Stated(avoided_categories=frozenset({"Chips"})), reviews, [], {1: "Chips"}, {})
    assert (stated.category["chips"], stated.category_source["chips"]) == (-1.0, "they said so")


def test_undoing_never_again_sticks_even_though_the_survey_said_it():
    reviews = [Review(1, "never_again", False, T0)]
    assert 1 in build_taste(Stated(never_again=frozenset({1})), reviews, [], {}, {}).never_again
    assert 1 not in build_taste(Stated(), reviews, [], {}, {}).never_again


def test_send_again_is_repeat_intent_and_a_rating_is_not():
    taste = build_taste(Stated(), [Review(1, "loved", False, T0), Review(2, "liked", True, T0)], [], {}, {})
    assert (1 in taste.repeat, 2 in taste.repeat, 1 in taste.loved) == (False, True, True)


def test_the_latest_rating_wins():
    reviews = [Review(1, "loved", False, T0 + timedelta(days=9)), Review(1, "disliked", False, T0)]
    assert build_taste(Stated(), reviews, [], {}, {}).affinity == {1: 40}


def test_boxes_since_counts_boxes_not_days():
    received = [Received(10, 1, T0), Received(11, 2, T0 + timedelta(days=30)), Received(12, 3, T0 + timedelta(days=60))]
    taste = build_taste(Stated(), [], received, {3: "Candy"}, {3: "JP"})
    assert (taste.boxes_since, taste.boxes_received) == ({1: 2, 2: 1, 3: 0}, 3)
    assert (taste.last_categories, taste.last_origins) == (frozenset({"candy"}), frozenset({"JP"}))


def test_crowd_quality_is_shrunk_until_there_are_enough_reviews():
    quality = crowd_quality([(1, "loved")] + [(2, "loved")] * 9 + [(3, "never_again")])
    assert (quality[1], quality[2], quality[3]) == (0.25, 0.75, -0.25)


# --- the builder ---------------------------------------------------------------------------------

def test_safety_rules_no_score_can_override():
    taste = Taste(allergens=frozenset({"peanut"}), never_again=frozenset({2}), spice="none")
    assert unsafe(snack(0, allergens=None), taste, RULES) == "allergens not recorded"
    assert unsafe(snack(1, allergens=frozenset({"peanut"})), taste, RULES) == "contains peanut"
    assert unsafe(snack(2), taste, RULES) == "on their never-again list"
    assert unsafe(snack(3, days_left=None), taste, RULES) == "no expiry date on record"
    assert unsafe(snack(4, days_left=10), taste, RULES) == "only 10 days left"
    assert unsafe(snack(5, available_base=Decimal(0)), taste, RULES) == "not enough left on the shelf"
    assert unsafe(snack(6, flavors=frozenset({"spicy"})), taste, RULES) == "they asked for nothing spicy"
    assert unsafe(snack(7), taste, RULES) == ""


def test_a_box_stays_inside_its_snack_budget():
    picks, _ = build_box(shelf(), Taste(), "world", Rules(8, 12, Decimal(800), Decimal(850), 16))
    assert len(picks) == 8 and sum(p.candidate.unit_cost_cents for p in picks) <= 850


def test_a_budget_too_small_for_the_minimum_says_so():
    picks, reason = build_box(shelf(), Taste(), "world", Rules(8, 12, Decimal(800), Decimal(286), 16))
    assert picks == [] and "$2.86 snack budget" in reason


def test_shelf_pressure_cannot_put_a_disliked_item_in_a_known_customers_box():
    ageing = snack(20, days_left=17, at_risk_base=Decimal(30))
    taste = Taste(affinity={20: -40}, boxes_since={20: 5}, boxes_received=6)
    picks, _ = build_box(shelf() + [ageing], taste, "world", RULES)
    assert 20 not in {p.candidate.stock_item_id for p in picks}


def test_asked_for_again_beats_the_cooldown_and_nothing_else_does():
    taste = Taste(repeat=frozenset({1}), boxes_since={1: 0, 2: 0}, boxes_received=1)
    ids = {p.candidate.stock_item_id for p in build_box(shelf(), taste, "world", RULES)[0]}
    assert 1 in ids and 2 not in ids


def test_every_pick_says_why_and_splits_customer_from_shelf():
    picks, _ = build_box(shelf(), Taste(), "chaos", RULES)
    assert picks and all(p.why for p in picks)
    assert all(abs(p.score - p.fit - p.shelf) < 1e-9 for p in picks)


def test_replacements_prefer_a_required_flavour_the_box_would_lose_and_respect_the_budget():
    current = [score(snack(i), Taste()) for i in (0, 1, 2, 3, 4, 5, 7, 8)]  # item 2 is the only spicy one
    pricey = snack(14, unit_cost_cents=Decimal(600))
    offers = suggest_replacements(shelf(12) + [pricey], Taste(), "chaos", RULES, current, 2)
    assert offers and "spicy" in offers[0].candidate.flavors
    assert 14 not in {p.candidate.stock_item_id for p in offers}
