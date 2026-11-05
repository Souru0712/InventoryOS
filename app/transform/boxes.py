"""Pure box builder: safety first, then what this shopper would want, then what the shelf needs to move.

Ported from the Snack Box CLI. Shelf pressure is capped below the bonus for something new, so a
box can never be filled with stock the customer has no reason to want just because it is ageing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.transform.taste import Taste

ZERO = Decimal("0")
FLAVORS = ("sweet", "salty", "spicy", "sour", "weird")


@dataclass(frozen=True)
class BoxType:
    categories: frozenset[str] = frozenset()  # allowed, lower-case; empty means any
    exclude_flavors: frozenset[str] = frozenset()
    require_flavors: tuple[str, ...] = ()
    max_per_category: int = 2
    max_per_origin: int = 12


BOX_TYPES: dict[str, BoxType] = {
    "sweet": BoxType(
        frozenset({"candy", "chocolate", "cookie", "gummy"}),
        frozenset({"spicy", "salty"}),
        (),
        3,
        12,
    ),
    "chaos": BoxType(frozenset(), frozenset(), ("sweet", "salty", "spicy", "weird"), 2, 12),
    "world": BoxType(frozenset(), frozenset(), (), 2, 2),
}


@dataclass(frozen=True)
class Weights:
    repeat_request: int = 60
    never_received: int = 25
    category: int = 15
    quality: int = 10
    spice: int = 10
    expiry_urgency: int = 12
    surplus: int = 8
    min_fit: int = 0
    novelty_boxes: int = 2
    loved_repeat_boxes: int = 1
    green_days: int = 90


WEIGHTS = Weights()
# (favourite slots, never-had slots) by how adventurous the customer asked their box to be.
MIX = {"familiar": (4, 4), "balanced": (2, 6), "surprise": (0, 12)}


@dataclass(frozen=True)
class Candidate:
    stock_item_id: int
    name: str
    category: str
    origin: str
    flavors: frozenset[str]
    allergens: frozenset[str] | None  # None: nobody has read the label, so it never goes in a box
    unit_cost_cents: Decimal
    available_base: Decimal
    days_left: int | None
    at_risk_base: Decimal = ZERO
    quality: float = 0.0


@dataclass(frozen=True)
class Rules:
    min_items: int
    max_items: int
    target_cogs_cents: Decimal  # stop adding once a box has min_items and has spent this much
    cogs_ceiling_cents: Decimal  # never spend more than this: it is what keeps the margin
    min_days_after_pickup: int


@dataclass(frozen=True)
class Pick:
    candidate: Candidate
    fit: float
    shelf: float
    driver: str  # customer | shelf | filler | packer
    why: str

    @property
    def score(self) -> float:
        return self.fit + self.shelf


def payment_fee_cents(price_cents: int, fee_bps: int, fee_fixed_cents: int) -> Decimal:
    return Decimal(price_cents) * Decimal(fee_bps) / Decimal(10000) + Decimal(fee_fixed_cents)


def cogs_ceiling_cents(
    price_cents: int,
    packaging_cents: int,
    fee_bps: int,
    fee_fixed_cents: int,
    min_contribution_cents: int,
) -> Decimal:
    """Most the snacks in a box may cost and still leave the minimum contribution."""
    return (
        Decimal(price_cents)
        - payment_fee_cents(price_cents, fee_bps, fee_fixed_cents)
        - packaging_cents
        - min_contribution_cents
    )


def contribution_cents(
    price_cents: int, cogs_cents: Decimal, packaging_cents: int, fee_bps: int, fee_fixed_cents: int
) -> Decimal:
    return (
        Decimal(price_cents)
        - Decimal(cogs_cents)
        - payment_fee_cents(price_cents, fee_bps, fee_fixed_cents)
        - packaging_cents
    )


def unsafe(candidate: Candidate, taste: Taste, rules: Rules) -> str:
    """Why this item can never go in this customer's box; empty when it can. No score overrides it."""
    if candidate.available_base < 1:
        return "not enough left on the shelf"
    if candidate.allergens is None:
        return "allergens not recorded"
    hit = candidate.allergens & taste.allergens
    if hit:
        return f"contains {', '.join(sorted(hit))}"
    if candidate.stock_item_id in taste.never_again:
        return "on their never-again list"
    if candidate.days_left is None:
        return "no expiry date on record"
    if candidate.days_left < rules.min_days_after_pickup:
        return f"only {candidate.days_left} days left"
    if "spicy" in candidate.flavors and taste.spice == "none":
        return "they asked for nothing spicy"
    return ""


def fits_type(candidate: Candidate, box_type: BoxType) -> bool:
    if box_type.categories and candidate.category.lower() not in box_type.categories:
        return False
    return not (candidate.flavors & box_type.exclude_flavors)


def cooled_down(candidate: Candidate, taste: Taste, w: Weights = WEIGHTS) -> bool:
    """Time since they last had it: shorter for loved items, waived when they asked for it again."""
    since = taste.boxes_since.get(candidate.stock_item_id)
    if since is None or candidate.stock_item_id in taste.repeat:
        return True
    return since >= (
        w.loved_repeat_boxes if candidate.stock_item_id in taste.loved else w.novelty_boxes
    )


def score(candidate: Candidate, taste: Taste, w: Weights = WEIGHTS) -> Pick:
    """Split an item's rank into what it does for the customer and what it does for the shelf."""
    item_id = candidate.stock_item_id
    fit, shelf, why = 0.0, 0.0, []
    if item_id in taste.repeat:
        fit += w.repeat_request
        why.append("asked for it again")
    if item_id in taste.affinity:
        points = taste.affinity[item_id]
        fit += points
        why.append(
            "loved it before"
            if item_id in taste.loved
            else "liked it before"
            if points > 0
            else "rated it neutral"
            if points == 0
            else "rated it low before"
        )
    elif taste.boxes_received and not taste.received(item_id):
        fit += w.never_received
        why.append("new to them")
    category = candidate.category.lower()
    lean = taste.category.get(category, 0.0)
    if lean:
        fit += lean * w.category
        why.append(
            f"{'likes' if lean > 0 else 'avoids'} {category} ({taste.category_source.get(category, '')})"
        )
    if "spicy" in candidate.flavors and taste.spice in ("mild", "hot"):
        fit += w.spice if taste.spice == "hot" else -w.spice
        why.append("likes it hot" if taste.spice == "hot" else "prefers mild")
    if candidate.quality:
        fit += candidate.quality * w.quality
        why.append(
            "other customers rate it well"
            if candidate.quality > 0
            else "other customers rate it poorly"
        )
    if candidate.days_left is not None:
        urgency = w.expiry_urgency * max(0, w.green_days - candidate.days_left) / w.green_days
        if urgency:
            shelf += urgency
            why.append(f"{candidate.days_left} days to expiry")
    if candidate.at_risk_base > ZERO:
        shelf += w.surplus
        why.append(f"{int(candidate.at_risk_base)} projected unsold")
    driver = "customer" if fit > 0 and fit >= shelf else "shelf" if shelf > 0 else "filler"
    return Pick(candidate, round(fit, 1), round(shelf, 1), driver, "; ".join(why) or "fits the box")


def _pool(
    candidates: Iterable[Candidate],
    taste: Taste,
    box_type: BoxType,
    rules: Rules,
    skip: set[int],
    w: Weights,
) -> list[Pick]:
    pool = [
        score(c, taste, w)
        for c in candidates
        if c.stock_item_id not in skip
        and not unsafe(c, taste, rules)
        and fits_type(c, box_type)
        and cooled_down(c, taste, w)
    ]
    if taste.known:
        pool = [p for p in pool if p.fit >= w.min_fit]
    return sorted(pool, key=lambda p: (-p.score, p.candidate.stock_item_id))


def build_box(
    candidates: list[Candidate],
    taste: Taste,
    box_type: str,
    rules: Rules,
    keep: tuple[Pick, ...] = (),
    exclude: frozenset[int] = frozenset(),
    w: Weights = WEIGHTS,
) -> tuple[list[Pick], str]:
    """Choose min..max_items items; an empty list comes back with the reason."""
    bt = BOX_TYPES[box_type]
    chosen: list[Pick] = list(keep)
    pool = _pool(
        candidates, taste, bt, rules, {p.candidate.stock_item_id for p in keep} | set(exclude), w
    )

    def spent() -> Decimal:
        return sum((p.candidate.unit_cost_cents for p in chosen), ZERO)

    def fits(p: Pick) -> bool:
        c = p.candidate
        return (
            all(q.candidate.stock_item_id != c.stock_item_id for q in chosen)
            and spent() + c.unit_cost_cents <= rules.cogs_ceiling_cents
            and sum(q.candidate.category.lower() == c.category.lower() for q in chosen)
            < bt.max_per_category
            and sum(q.candidate.origin == c.origin for q in chosen) < bt.max_per_origin
        )

    def take(limit: int, want: Callable[[Pick], bool]) -> None:
        for p in pool:
            if sum(1 for q in chosen if want(q)) >= limit or len(chosen) >= rules.max_items:
                return
            if want(p) and fits(p):
                chosen.append(p)

    for flavor in bt.require_flavors:
        if not any(flavor in p.candidate.flavors for p in chosen):
            pick = next((p for p in pool if flavor in p.candidate.flavors and fits(p)), None)
            if pick is None:
                return [], f"no {flavor} item fits"
            chosen.append(pick)
    favorites, new = MIX.get(taste.novelty, MIX["balanced"])
    take(favorites, lambda p: p.candidate.stock_item_id in taste.loved | taste.repeat)
    take(new, lambda p: not taste.received(p.candidate.stock_item_id))
    for p in pool:
        if len(chosen) >= rules.max_items or (
            len(chosen) >= rules.min_items and spent() >= rules.target_cogs_cents
        ):
            break
        if fits(p):
            chosen.append(p)
    if len(chosen) < rules.min_items:
        return [], (
            f"only {len(chosen)} items fit (need {rules.min_items}) within a "
            f"${rules.cogs_ceiling_cents / 100:.2f} snack budget"
        )
    return chosen, ""


def suggest_replacements(
    candidates: list[Candidate],
    taste: Taste,
    box_type: str,
    rules: Rules,
    current: list[Pick],
    replacing_id: int,
    limit: int = 3,
    w: Weights = WEIGHTS,
) -> list[Pick]:
    """Items that could stand in for one line, given the rest of the box. Safety rules still apply."""
    bt = BOX_TYPES[box_type]
    rest = [p for p in current if p.candidate.stock_item_id != replacing_id]
    rest_ids = {p.candidate.stock_item_id for p in rest}
    spent = sum((p.candidate.unit_cost_cents for p in rest), ZERO)
    missing = {f for f in bt.require_flavors if not any(f in p.candidate.flavors for p in rest)}

    offers = []
    for c in candidates:
        if c.stock_item_id in rest_ids or c.stock_item_id == replacing_id:
            continue
        if unsafe(c, taste, rules) or not fits_type(c, bt):
            continue
        if spent + c.unit_cost_cents > rules.cogs_ceiling_cents:
            continue
        if (
            sum(p.candidate.category.lower() == c.category.lower() for p in rest)
            >= bt.max_per_category
        ):
            continue
        if sum(p.candidate.origin == c.origin for p in rest) >= bt.max_per_origin:
            continue
        pick = score(c, taste, w)
        if taste.known and pick.fit < w.min_fit:
            continue
        offers.append(pick)
    # A flavour the box type requires comes first; something they had recently is offered, but last.
    offers.sort(
        key=lambda p: (
            not (missing & p.candidate.flavors),
            not cooled_down(p.candidate, taste, w),
            -p.score,
            p.candidate.stock_item_id,
        )
    )
    return offers[:limit]
