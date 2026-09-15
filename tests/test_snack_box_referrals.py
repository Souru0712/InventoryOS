"""Referral codes and the credit ledger: earned once, only for a first box that is picked up, reversed when
that box is refunded, and never spendable below zero."""
import pytest
from sqlalchemy import select

from app.models import BoxOrderStatus, CreditKind, CreditTransaction, Referral, ReferralStatus
from app.orchestration import boxes, customers, referrals
from tests.snack_box_support import NOW, install_fake_square, open_store, pack_all, paid_box, shopper


@pytest.fixture
def store(db, tenant, location, monkeypatch):
    # 90 cents a snack, so a box still fits the budget a referral discount leaves.
    open_store(db, tenant, location, install_fake_square(monkeypatch), n_items=16, cost_cents=90)
    return tenant


def deliver(db, order):
    assert pack_all(db, order) == []
    assert boxes.pick_up(db, order, NOW) == []


def ledger_for(db, customer):
    return [(t.kind, t.amount_cents) for t in db.scalars(
        select(CreditTransaction).where(CreditTransaction.customer_id == customer.id).order_by(CreditTransaction.id))]


def test_a_friend_gets_the_discount_and_the_referrer_is_paid_at_pickup(db, store):
    sarah, john = shopper(db, store, "sarah@shop.test"), shopper(db, store, "john@shop.test")
    order = paid_box(db, store, john, code=sarah.referral_code)
    assert order.discount_cents == 500 and order.status is BoxOrderStatus.PAID
    referral = db.scalars(select(Referral)).one()
    assert referral.status is ReferralStatus.PENDING
    assert pack_all(db, order) == [] and referrals.balance_cents(db, sarah.id) == 0  # packed is not enough
    boxes.pick_up(db, order, NOW)
    assert referral.status is ReferralStatus.REWARDED and referrals.balance_cents(db, sarah.id) == 500


def test_an_unknown_code_is_refused(db, store):
    with pytest.raises(referrals.ReferralProblem, match="does not exist"):
        boxes.place_order(db, store.id, shopper(db, store), "chaos", "SNACK-NOPE12", now=NOW)


def test_your_own_code_is_refused(db, store):
    sarah = shopper(db, store, "sarah@shop.test")
    with pytest.raises(referrals.ReferralProblem, match="your own"):
        boxes.place_order(db, store.id, sarah, "chaos", sarah.referral_code, now=NOW)


def test_a_code_is_for_a_first_box_only(db, store):
    sarah, john = shopper(db, store, "sarah@shop.test"), shopper(db, store, "john@shop.test")
    deliver(db, paid_box(db, store, john))
    with pytest.raises(referrals.ReferralProblem, match="first box only"):
        boxes.place_order(db, store.id, john, "chaos", sarah.referral_code, now=NOW)


def test_one_referral_per_customer(db, store):
    sarah, mike, john = (shopper(db, store, f"{n}@shop.test") for n in ("sarah", "mike", "john"))
    paid_box(db, store, john, code=sarah.referral_code)
    with pytest.raises(referrals.ReferralProblem, match="already been used"):
        boxes.place_order(db, store.id, john, "chaos", mike.referral_code, now=NOW)


def test_a_first_order_that_was_never_paid_lets_the_code_be_used_again(db, store):
    sarah, john = shopper(db, store, "sarah@shop.test"), shopper(db, store, "john@shop.test")
    unpaid = boxes.place_order(db, store.id, john, "chaos", sarah.referral_code, False, NOW)
    boxes.cancel(db, unpaid, NOW)
    assert db.scalars(select(Referral)).one().status is ReferralStatus.CANCELLED
    again = paid_box(db, store, john, code=sarah.referral_code)
    assert again.discount_cents == 500 and db.scalars(select(Referral)).one().status is ReferralStatus.PENDING


def test_a_refund_before_the_reward_cancels_it(db, store):
    sarah, john = shopper(db, store, "sarah@shop.test"), shopper(db, store, "john@shop.test")
    order = paid_box(db, store, john, code=sarah.referral_code)
    boxes.refund(db, order, restock=False, now=NOW)
    assert db.scalars(select(Referral)).one().status is ReferralStatus.CANCELLED
    assert ledger_for(db, sarah) == []


def test_a_refund_after_the_reward_reverses_it(db, store):
    sarah, john = shopper(db, store, "sarah@shop.test"), shopper(db, store, "john@shop.test")
    order = paid_box(db, store, john, code=sarah.referral_code)
    deliver(db, order)
    boxes.refund(db, order, restock=False, now=NOW)
    assert db.scalars(select(Referral)).one().status is ReferralStatus.REVERSED
    assert ledger_for(db, sarah) == [(CreditKind.REFERRAL_REWARD, 500), (CreditKind.REFERRAL_REVERSAL, -500)]


def test_a_spent_reward_that_is_reversed_only_ever_comes_off_future_credit(db, store):
    sarah, john = shopper(db, store, "sarah@shop.test"), shopper(db, store, "john@shop.test")
    friend_order = paid_box(db, store, john, code=sarah.referral_code)
    deliver(db, friend_order)
    own = paid_box(db, store, sarah, use_credit=True)
    assert own.credit_cents == 500 and referrals.balance_cents(db, sarah.id) == 0

    boxes.refund(db, friend_order, restock=False, now=NOW)
    assert referrals.balance_cents(db, sarah.id) == -500
    later = boxes.place_order(db, store.id, sarah, "chaos", use_credit=True, now=NOW)
    assert later.credit_cents == 0 and later.amount_due_cents == later.price_cents
    summary = referrals.summary(db, sarah)
    assert (summary["available_cents"], summary["balance_cents"]) == (0, -500)


def test_credit_covers_at_most_the_price_and_comes_back_on_a_refund(db, store):
    sarah = shopper(db, store, "sarah@shop.test")
    referrals.adjust(db, sarah, 3000, "goodwill after a damaged box")
    order = boxes.place_order(db, store.id, sarah, "chaos", use_credit=True, now=NOW)
    assert (order.credit_cents, order.status, order.payment_source) == (2499, BoxOrderStatus.PAID, "credit")
    assert referrals.balance_cents(db, sarah.id) == 501
    boxes.refund(db, order, restock=False, now=NOW)
    assert referrals.balance_cents(db, sarah.id) == 3000


def test_cancelling_an_unpaid_order_returns_its_credit(db, store):
    sarah = shopper(db, store, "sarah@shop.test")
    referrals.adjust(db, sarah, 500, "birthday")
    order = boxes.place_order(db, store.id, sarah, "chaos", use_credit=True, now=NOW)
    assert order.credit_cents == 500 and referrals.balance_cents(db, sarah.id) == 0
    boxes.cancel(db, order, NOW)
    assert referrals.balance_cents(db, sarah.id) == 500


def test_a_reward_posts_once_however_often_pickup_is_recorded(db, store):
    sarah, john = shopper(db, store, "sarah@shop.test"), shopper(db, store, "john@shop.test")
    order = paid_box(db, store, john, code=sarah.referral_code)
    deliver(db, order)
    referral = db.scalars(select(Referral)).one()
    referrals.qualify_for_order(db, order, NOW)
    assert boxes.pick_up(db, order, NOW) == ["Only a packed box can be picked up."]
    assert referrals.post_credit(db, store.id, sarah.id, 500, CreditKind.REFERRAL_REWARD,
                                 f"referral_reward:{referral.id}") is None
    assert ledger_for(db, sarah) == [(CreditKind.REFERRAL_REWARD, 500)]


def test_several_friends_several_rewards(db, store):
    sarah = shopper(db, store, "sarah@shop.test")
    for name in ("john", "lisa"):
        deliver(db, paid_box(db, store, shopper(db, store, f"{name}@shop.test"), code=sarah.referral_code))
    summary = referrals.summary(db, sarah)
    assert (summary["successful"], summary["earned_cents"], summary["balance_cents"]) == (2, 1000, 1000)


def test_a_closed_referrer_account_qualifies_but_is_not_paid(db, store):
    sarah, john = shopper(db, store, "sarah@shop.test"), shopper(db, store, "john@shop.test")
    order = paid_box(db, store, john, code=sarah.referral_code)
    customers.deactivate(db, sarah, NOW)
    deliver(db, order)
    assert db.scalars(select(Referral)).one().status is ReferralStatus.QUALIFIED
    assert ledger_for(db, sarah) == []


def test_a_manual_adjustment_needs_a_reason(db, store):
    with pytest.raises(ValueError, match="why"):
        referrals.adjust(db, shopper(db, store), 500, "  ")
