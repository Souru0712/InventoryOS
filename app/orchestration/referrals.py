"""Referral codes, the store-credit ledger, and what a cancellation or refund does to a reward.

Policy, as the checkout page states it: a referrer earns credit when the friend's first box is
picked up. If that box is refunded later the credit is reversed, even if it was already spent;
the balance then goes below zero and is only ever offset against future credit, never billed.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    BoxOrder,
    BoxOrderStatus,
    BoxSettings,
    CreditKind,
    CreditTransaction,
    Customer,
    Referral,
    ReferralStatus,
)
from app.transform.credits import BOUGHT, check_referral_transition, redeemable, referral_problem

log = logging.getLogger(__name__)
# No 0/O or 1/I: codes get read out loud at the counter.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ReferralProblem(ValueError):
    """A referral code that cannot be used. The message is written for the customer."""


def new_referral_code(db: Session, tenant_id: int) -> str:
    while True:
        code = "SNACK-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        taken = db.scalars(
            select(Customer.id).where(
                Customer.tenant_id == tenant_id, Customer.referral_code == code
            )
        ).first()
        if taken is None:
            return code


def post_credit(
    db: Session,
    tenant_id: int,
    customer_id: int,
    amount_cents: int,
    kind: CreditKind,
    reference: str,
    *,
    box_order_id: int | None = None,
    referral_id: int | None = None,
    note: str | None = None,
) -> CreditTransaction | None:
    """Add one ledger row. None when this reference already posted: the same event never pays twice."""
    existing = db.scalars(
        select(CreditTransaction).where(
            CreditTransaction.tenant_id == tenant_id, CreditTransaction.reference == reference
        )
    ).first()
    if existing is not None:
        log.info("credit %s was already posted; not posting it again", reference)
        return None
    row = CreditTransaction(
        tenant_id=tenant_id,
        customer_id=customer_id,
        amount_cents=amount_cents,
        kind=kind,
        reference=reference,
        box_order_id=box_order_id,
        referral_id=referral_id,
        note=(note or None) and note[:300],
    )
    db.add(row)
    db.flush()
    return row


def balance_cents(db: Session, customer_id: int) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(CreditTransaction.amount_cents), 0)).where(
            CreditTransaction.customer_id == customer_id
        )
    )
    return int(total or 0)


def apply_code(
    db: Session, order: BoxOrder, customer: Customer, code: str, settings: BoxSettings
) -> Referral:
    """Attach a referral to a first order and take the discount off it. Raises ReferralProblem."""
    code = code.strip().upper()
    referrer = db.scalars(
        select(Customer).where(
            Customer.tenant_id == order.tenant_id, Customer.referral_code == code
        )
    ).first()
    existing = db.scalars(
        select(Referral).where(
            Referral.tenant_id == order.tenant_id, Referral.referred_customer_id == customer.id
        )
    ).first()
    # An earlier attempt whose order was never paid gave nothing away, so the customer may try again.
    retry = (
        existing is not None
        and existing.status is ReferralStatus.CANCELLED
        and (
            existing.box_order_id is None
            or db.get(BoxOrder, existing.box_order_id).status is BoxOrderStatus.CANCELLED
        )
    )
    bought = db.scalars(
        select(BoxOrder.id).where(
            BoxOrder.customer_id == customer.id,
            BoxOrder.id != order.id,
            BoxOrder.status.in_([BoxOrderStatus(s) for s in BOUGHT]),
        )
    ).first()
    problem = referral_problem(
        code_found=referrer is not None,
        self_referral=referrer is not None and referrer.id == customer.id,
        referrer_active=bool(referrer and referrer.is_active),
        already_referred=existing is not None and not retry,
        bought_before=bought is not None,
    )
    if problem:
        raise ReferralProblem(problem)

    referral = (
        existing if retry else Referral(tenant_id=order.tenant_id, referred_customer_id=customer.id)
    )
    referral.referrer_customer_id = referrer.id
    referral.code = code
    referral.status = ReferralStatus.PENDING
    referral.box_order_id = order.id
    referral.reward_cents = settings.referral_reward_cents
    referral.cancelled_at = None
    if not retry:
        db.add(referral)
    order.discount_cents = min(settings.referral_discount_cents, order.price_cents)
    db.flush()
    return referral


def referral_for_order(db: Session, order: BoxOrder) -> Referral | None:
    return db.scalars(
        select(Referral).where(
            Referral.tenant_id == order.tenant_id, Referral.box_order_id == order.id
        )
    ).first()


def qualify_for_order(db: Session, order: BoxOrder, now: datetime | None = None) -> Referral | None:
    """The friend's first box was picked up: the referral qualifies and the referrer is paid."""
    now = now or datetime.now(UTC)
    referral = referral_for_order(db, order)
    if referral is None or referral.status is not ReferralStatus.PENDING:
        return referral
    check_referral_transition("pending", "qualified")
    referral.status = ReferralStatus.QUALIFIED
    referral.qualified_at = now

    referrer = db.get(Customer, referral.referrer_customer_id)
    if referrer is None or not referrer.is_active or referral.reward_cents <= 0:
        db.flush()
        return referral
    check_referral_transition("qualified", "rewarded")
    post_credit(
        db,
        order.tenant_id,
        referrer.id,
        referral.reward_cents,
        CreditKind.REFERRAL_REWARD,
        f"referral_reward:{referral.id}",
        box_order_id=order.id,
        referral_id=referral.id,
        note="friend's first box picked up",
    )
    referral.status = ReferralStatus.REWARDED
    referral.rewarded_at = now
    db.flush()
    return referral


def unwind_for_order(db: Session, order: BoxOrder, now: datetime | None = None) -> Referral | None:
    """The first box was cancelled or refunded: an unpaid referral is cancelled, a paid reward reversed."""
    now = now or datetime.now(UTC)
    referral = referral_for_order(db, order)
    if referral is None:
        return None
    if referral.status in (ReferralStatus.PENDING, ReferralStatus.QUALIFIED):
        check_referral_transition(referral.status.value, "cancelled")
        referral.status = ReferralStatus.CANCELLED
        referral.cancelled_at = now
    elif referral.status is ReferralStatus.REWARDED:
        check_referral_transition("rewarded", "reversed")
        post_credit(
            db,
            order.tenant_id,
            referral.referrer_customer_id,
            -referral.reward_cents,
            CreditKind.REFERRAL_REVERSAL,
            f"referral_reversal:{referral.id}",
            box_order_id=order.id,
            referral_id=referral.id,
            note="friend's first box was refunded",
        )
        referral.status = ReferralStatus.REVERSED
        referral.reversed_at = now
    db.flush()
    return referral


def redeem(db: Session, order: BoxOrder, customer: Customer) -> int:
    """Put available credit on an order, never more than is owed and never out of a negative balance."""
    amount = redeemable(balance_cents(db, customer.id), order.price_cents - order.discount_cents)
    if amount:
        post_credit(
            db,
            order.tenant_id,
            customer.id,
            -amount,
            CreditKind.REDEMPTION,
            f"redeem:{order.id}",
            box_order_id=order.id,
            note=f"used on box order {order.id}",
        )
        order.credit_cents = amount
        db.flush()
    return amount


def return_redemption(db: Session, order: BoxOrder) -> None:
    """Credit spent on an order that was cancelled or refunded goes back to the customer."""
    if order.credit_cents:
        post_credit(
            db,
            order.tenant_id,
            order.customer_id,
            order.credit_cents,
            CreditKind.REDEMPTION_RETURN,
            f"redeem_return:{order.id}",
            box_order_id=order.id,
            note=f"box order {order.id} did not go ahead",
        )


def adjust(db: Session, customer: Customer, amount_cents: int, note: str) -> CreditTransaction:
    """A manual correction by the store. A note is required so the ledger can explain itself."""
    if not note.strip():
        raise ValueError("say why the credit is being adjusted")
    if amount_cents == 0:
        raise ValueError("an adjustment of zero changes nothing")
    return post_credit(
        db,
        customer.tenant_id,
        customer.id,
        amount_cents,
        CreditKind.ADJUSTMENT,
        f"adjustment:{uuid.uuid4().hex}",
        note=note.strip(),
    )


def summary(db: Session, customer: Customer) -> dict:
    """What the customer's referral page shows."""
    referrals = list(
        db.scalars(select(Referral).where(Referral.referrer_customer_id == customer.id))
    )
    rows = list(
        db.scalars(select(CreditTransaction).where(CreditTransaction.customer_id == customer.id))
    )
    balance = sum(r.amount_cents for r in rows)
    return {
        "code": customer.referral_code,
        "referred": sum(1 for r in referrals if r.status is not ReferralStatus.CANCELLED),
        "pending": sum(
            1 for r in referrals if r.status in (ReferralStatus.PENDING, ReferralStatus.QUALIFIED)
        ),
        "successful": sum(1 for r in referrals if r.status is ReferralStatus.REWARDED),
        "earned_cents": sum(r.amount_cents for r in rows if r.kind is CreditKind.REFERRAL_REWARD),
        "reversed_cents": -sum(
            r.amount_cents for r in rows if r.kind is CreditKind.REFERRAL_REVERSAL
        ),
        "balance_cents": balance,
        "available_cents": max(balance, 0),
        "transactions": sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True),
    }


def ledger(db: Session, tenant_id: int) -> list[dict]:
    """Every referral in the store with both people, the order and where it stands."""
    people = {c.id: c for c in db.scalars(select(Customer).where(Customer.tenant_id == tenant_id))}
    rows = []
    for referral in db.scalars(
        select(Referral).where(Referral.tenant_id == tenant_id).order_by(Referral.created_at.desc())
    ):
        order = db.get(BoxOrder, referral.box_order_id) if referral.box_order_id else None
        rows.append(
            {
                "referral": referral,
                "referrer": people.get(referral.referrer_customer_id),
                "referred": people.get(referral.referred_customer_id),
                "order": order,
            }
        )
    return rows


def credit_rows(
    db: Session, tenant_id: int, limit: int = 200
) -> list[tuple[CreditTransaction, Customer]]:
    return list(
        db.execute(
            select(CreditTransaction, Customer)
            .join(Customer, Customer.id == CreditTransaction.customer_id)
            .where(CreditTransaction.tenant_id == tenant_id)
            .order_by(CreditTransaction.created_at.desc(), CreditTransaction.id.desc())
            .limit(limit)
        ).all()
    )
