"""Pure store-credit and referral rules. Money is integer cents; a balance is only ever a ledger sum."""

from __future__ import annotations

from collections.abc import Iterable

# A referral qualifies when the referred customer's first box is picked up, and is rewarded when
# the credit is posted. A referrer whose account is closed stays qualified and is never paid.
REFERRAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"qualified", "cancelled"}),
    "qualified": frozenset({"rewarded", "cancelled"}),
    "rewarded": frozenset({"reversed"}),
    "cancelled": frozenset(),
    "reversed": frozenset(),
}
# Order states that mean a customer has bought before. A cancelled, never-paid order does not.
BOUGHT = frozenset({"paid", "packed", "picked_up", "refunded"})


class ReferralTransitionError(ValueError):
    """A referral was asked to move somewhere its state does not allow."""


def check_referral_transition(current: str, target: str) -> None:
    if target not in REFERRAL_TRANSITIONS.get(current, frozenset()):
        raise ReferralTransitionError(f"a referral that is {current} cannot become {target}")


def balance(amounts: Iterable[int]) -> int:
    return sum(amounts)


def redeemable(balance_cents: int, due_cents: int) -> int:
    """Credit that may go on an order: never more than is owed, never out of a zero or negative balance."""
    return max(0, min(balance_cents, due_cents))


def referral_problem(
    *,
    code_found: bool,
    self_referral: bool,
    referrer_active: bool,
    already_referred: bool,
    bought_before: bool,
) -> str:
    """Why a referral code cannot be used on this order; empty when it can."""
    if not code_found:
        return "That referral code does not exist."
    if self_referral:
        return "You cannot use your own referral code."
    if not referrer_active:
        return "That referral code is no longer active."
    if already_referred:
        return "A referral code has already been used on this account."
    if bought_before:
        return "Referral codes are for a first box only."
    return ""
