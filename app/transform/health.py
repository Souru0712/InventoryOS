"""Pure inventory health scoring: one number, and every reason behind it.

A score on its own is a guess dressed up as a fact. Each dimension here is scored by a rule
that fits in a sentence, carries the findings that moved it, and says so plainly when it was
not scored at all: a store that has dated nothing does not get a perfect expiry score for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.transform.inventory import (
    StockView,
    days_of_cover,
    stale_after,
    suggest_order_base,
    valuation_cents,
)
from app.transform.reports import money

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")

WEIGHTS: dict[str, Decimal] = {
    "stock": Decimal("0.30"),
    "expiry": Decimal("0.25"),
    "overstock": Decimal("0.20"),
    "reorder": Decimal("0.15"),
    "data": Decimal("0.10"),
}

LABELS = {
    "stock": "Stock availability",
    "expiry": "Expiration risk",
    "overstock": "Overstock",
    "reorder": "Reorder health",
    "data": "Data completeness",
}

# (lowest score in the band, key, label), best first.
BANDS: tuple[tuple[int, str, str], ...] = (
    (90, "excellent", "Excellent"),
    (75, "healthy", "Healthy"),
    (60, "needs_attention", "Needs attention"),
    (40, "at_risk", "At risk"),
    (0, "critical", "Critical"),
)

# Expiry scores zero once this share of stock value is at risk. Linear would call 4% of the
# shelf going in the bin "96, excellent", which no small business would agree with.
EXPIRY_ZERO_AT = Decimal("0.10")
OVERSTOCK_DAYS = 60
STALE_COUNT_DAYS = 14

RULES = {
    "stock": "The share of your stock, weighted by how much of it you sell, that is at or below "
    "its reorder point.",
    "expiry": f"Stock value that may expire unsold or is already past its date. "
    f"{int(EXPIRY_ZERO_AT * 100)}% of your stock value at risk scores zero.",
    "overstock": f"The share of stock value holding more than {OVERSTOCK_DAYS} days of sales, "
    "or not selling at all.",
    "reorder": "The share of stock above its reorder point, weighted by sales, that will still "
    "run out before a delivery could arrive.",
    "data": f"The average of: stock with a cost, stock with a reorder point, perishable stock "
    f"with dates, and weight and volume counted in the last {STALE_COUNT_DAYS} days.",
}

TONE_RANK = {"bad": 0, "warn": 1, "good": 2, "info": 3}


def _n(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _round(value: Decimal) -> int:
    return int(Decimal(value).quantize(ONE, rounding=ROUND_HALF_UP))


def _score_from_share(share: Decimal, zero_at: Decimal = ONE) -> int:
    """100 when nothing is wrong, 0 once `zero_at` of it is, linear between."""
    fraction = max(ZERO, ONE - Decimal(share) / zero_at)
    return max(0, min(100, _round(fraction * HUNDRED)))


def band_for(score: int | None) -> tuple[str, str]:
    if score is None:
        return "unscored", "Not scored yet"
    for floor, key, label in BANDS:
        if score >= floor:
            return key, label
    return BANDS[-1][1], BANDS[-1][2]


# --- inputs -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemHealth:
    """One stock item, with the sales signal needed to judge it."""

    item: StockView
    velocity_per_day: Decimal = ZERO
    # Whether a zero velocity really means "not selling". Without a POS link or a recipe it
    # means "unknown", and unknown stock is never called overstocked or about to run out.
    has_sales_signal: bool = False

    @property
    def qty(self) -> Decimal:
        return max(Decimal(self.item.qty_base), ZERO)

    @property
    def value_cents(self) -> int:
        return valuation_cents(self.qty, self.item.cost_per_base_cents)

    @property
    def out_of_stock(self) -> bool:
        return self.qty <= ZERO

    @property
    def at_or_below_reorder(self) -> bool:
        reorder = Decimal(self.item.reorder_point_base)
        return self.out_of_stock or (reorder > ZERO and self.qty <= reorder)

    @property
    def cover(self) -> Decimal | None:
        if not self.has_sales_signal:
            return None
        return days_of_cover(self.qty, Decimal(self.velocity_per_day))

    @property
    def approaching_stockout(self) -> bool:
        """Above its reorder point, but will still run out before a delivery could land."""
        cover = self.cover
        return (
            not self.at_or_below_reorder
            and cover is not None
            and cover <= Decimal(self.item.lead_time_days)
        )

    @property
    def not_selling(self) -> bool:
        return self.has_sales_signal and self.qty > ZERO and Decimal(self.velocity_per_day) <= ZERO

    @property
    def overstocked(self) -> bool:
        if not self.has_sales_signal or self.qty <= ZERO:
            return False
        if Decimal(self.velocity_per_day) <= ZERO:
            return True
        cover = self.cover
        return cover is not None and cover > OVERSTOCK_DAYS

    @property
    def value_moved_per_day(self) -> Decimal:
        return Decimal(self.velocity_per_day) * Decimal(self.item.cost_per_base_cents or 0)

    @property
    def order_base(self) -> Decimal:
        return suggest_order_base(self.item, Decimal(self.velocity_per_day))


@dataclass(frozen=True)
class ExpiryFigures:
    at_risk_cents: int = 0
    at_risk_products: int = 0
    expired_cents: int = 0
    expired_products: int = 0
    dated_items: int = 0
    item_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class HealthInput:
    items: list[ItemHealth]
    expiry: ExpiryFigures = ExpiryFigures()
    coverage_percent: int | None = None
    needs_dates: int = 0
    promotable_urgent: int = 0
    promotable: int = 0
    currency: str = "USD"


# --- output -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One line of explanation, and where to go to act on it."""

    tone: str  # bad | warn | good | info
    text: str
    link: str | None = None
    item_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ComponentScore:
    key: str
    score: int | None
    findings: list[Finding]
    figures: dict[str, int] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return LABELS[self.key]

    @property
    def weight(self) -> Decimal:
        return WEIGHTS[self.key]

    @property
    def rule(self) -> str:
        return RULES[self.key]

    @property
    def measured(self) -> bool:
        return self.score is not None

    @property
    def tone(self) -> str:
        if self.score is None:
            return "info"
        return "good" if self.score >= 75 else "warn" if self.score >= 60 else "bad"


@dataclass(frozen=True)
class HealthReport:
    score: int | None
    components: list[ComponentScore]
    currency: str = "USD"

    @property
    def band(self) -> str:
        return band_for(self.score)[0]

    @property
    def band_label(self) -> str:
        return band_for(self.score)[1]

    def component(self, key: str) -> ComponentScore:
        return next(c for c in self.components if c.key == key)

    @property
    def headline(self) -> list[Finding]:
        """The one line from each dimension that matters most, worst first."""
        lines = []
        for component in self.components:
            if component.findings:
                lines.append((component, min(component.findings, key=lambda f: TONE_RANK[f.tone])))
        lines.sort(key=lambda pair: (TONE_RANK[pair[1].tone], -pair[0].weight))
        return [finding for _, finding in lines]

    def to_snapshot(self) -> dict:
        """What is kept each night, enough to explain the next day's change."""
        return {
            "score": self.score,
            "currency": self.currency,
            "components": {
                c.key: {"score": c.score, "figures": c.figures} for c in self.components
            },
        }


# --- the five dimensions ------------------------------------------------------------------------


def importance(items: list[ItemHealth]) -> dict[int, Decimal]:
    """How much each item matters: the value of it that sells in a day, at cost.

    A missing Coke should move the score more than an obscure candy flavour. Items with nothing
    to judge by count as an average item, because unknown is not the same as unimportant.
    """
    moved = {health.item.id: health.value_moved_per_day for health in items}
    positive = [value for value in moved.values() if value > ZERO]
    average = sum(positive, ZERO) / len(positive) if positive else ONE
    return {item_id: value if value > ZERO else average for item_id, value in moved.items()}


def _ids(items: list[ItemHealth]) -> tuple[int, ...]:
    return tuple(health.item.id for health in items)


def _not_scored(key: str, text: str, link: str | None = None) -> ComponentScore:
    return ComponentScore(key, None, [Finding("info", f"Not scored yet: {text}", link)])


def score_stock(items: list[ItemHealth], weights: dict[int, Decimal]) -> ComponentScore:
    if not items:
        return _not_scored("stock", "there is no stock to judge")
    total = sum((weights[h.item.id] for h in items), ZERO)
    low = [h for h in items if h.at_or_below_reorder]
    out = [h for h in low if h.out_of_stock]
    below = [h for h in low if not h.out_of_stock]
    score = _score_from_share(sum((weights[h.item.id] for h in low), ZERO) / total)

    findings = []
    if out:
        findings.append(
            Finding("bad", f"{_n(len(out), 'product')} out of stock", "/app/reorder", _ids(out))
        )
    if below:
        findings.append(
            Finding(
                "warn",
                f"{_n(len(below), 'product')} at or below the reorder point",
                "/app/reorder",
                _ids(below),
            )
        )
    if not low:
        findings.append(Finding("good", "Everything is above its reorder point"))
    return ComponentScore(
        "stock",
        score,
        findings,
        {"tracked": len(items), "out_of_stock": len(out), "below_reorder": len(below)},
    )


def score_expiry(expiry: ExpiryFigures, total_value_cents: int, currency: str) -> ComponentScore:
    if expiry.dated_items == 0:
        return _not_scored(
            "expiry", "nothing is dated, so there is nothing to measure", "/app/expiring"
        )

    risk = expiry.at_risk_cents + expiry.expired_cents
    if total_value_cents > 0:
        share = Decimal(risk) / Decimal(total_value_cents)
    else:
        share = ONE if risk else ZERO
    score = _score_from_share(share, EXPIRY_ZERO_AT)

    findings = []
    if expiry.expired_cents:
        findings.append(
            Finding(
                "bad",
                f"{money(expiry.expired_cents, currency)} past its date and still on the shelf, "
                f"across {_n(expiry.expired_products, 'product')}",
                "/app/expiring",
            )
        )
    if expiry.at_risk_cents:
        findings.append(
            Finding(
                "bad" if score < 60 else "warn",
                f"{money(expiry.at_risk_cents, currency)} may expire unsold, "
                f"across {_n(expiry.at_risk_products, 'product')}",
                "/app/promotions",
            )
        )
    if not risk:
        findings.append(Finding("good", "Nothing dated is at risk of expiring unsold"))
    return ComponentScore(
        "expiry",
        score,
        findings,
        {
            "at_risk_cents": expiry.at_risk_cents,
            "at_risk_products": expiry.at_risk_products,
            "expired_cents": expiry.expired_cents,
            "expired_products": expiry.expired_products,
        },
    )


def score_overstock(items: list[ItemHealth], currency: str) -> ComponentScore:
    judged = [h for h in items if h.has_sales_signal and h.qty > ZERO]
    if not judged:
        return _not_scored("overstock", "no sales yet to show how fast stock moves")

    over = [h for h in judged if h.overstocked]
    dead = [h for h in over if h.not_selling]
    slow = [h for h in over if not h.not_selling]
    total = sum(h.value_cents for h in judged)
    over_value = sum(h.value_cents for h in over)
    share = (
        Decimal(over_value) / Decimal(total)
        if total > 0
        else Decimal(len(over)) / Decimal(len(judged))
    )
    score = _score_from_share(share)

    findings = []
    if slow:
        findings.append(
            Finding(
                "warn",
                f"{_n(len(slow), 'slow-moving product')}, {money(sum(h.value_cents for h in slow), currency)} "
                f"tied up in over {OVERSTOCK_DAYS} days of supply",
                "/app/health#overstock",
                _ids(slow),
            )
        )
    if dead:
        findings.append(
            Finding(
                "warn",
                f"{_n(len(dead), 'product')} with no sales in four weeks, "
                f"{money(sum(h.value_cents for h in dead), currency)} on the shelf",
                "/app/health#overstock",
                _ids(dead),
            )
        )
    if not over:
        findings.append(Finding("good", "Nothing is sitting on the shelf too long"))
    return ComponentScore(
        "overstock",
        score,
        findings,
        {
            "overstock_items": len(over),
            "dead_items": len(dead),
            "overstock_cents": over_value,
        },
    )


def score_reorder(items: list[ItemHealth], weights: dict[int, Decimal]) -> ComponentScore:
    selling = [h for h in items if h.has_sales_signal]
    if not selling:
        return _not_scored("reorder", "no sales yet to project a stockout from")
    judged = [h for h in selling if not h.at_or_below_reorder]
    if not judged:
        # Every selling item is already at its reorder point; stock availability carries that.
        return _not_scored(
            "reorder", "everything that sells is already at its reorder point", "/app/reorder"
        )

    total = sum((weights[h.item.id] for h in judged), ZERO)
    approaching = [h for h in judged if h.approaching_stockout]
    score = _score_from_share(sum((weights[h.item.id] for h in approaching), ZERO) / total)

    findings = []
    if approaching:
        findings.append(
            Finding(
                "warn",
                f"{_n(len(approaching), 'product')} will run out before a delivery could arrive",
                "/app/reorder",
                _ids(approaching),
            )
        )
    else:
        findings.append(
            Finding("good", "Nothing above its reorder point will run out before a delivery")
        )
    return ComponentScore("reorder", score, findings, {"approaching": len(approaching)})


def score_data(
    items: list[ItemHealth], coverage_percent: int | None, needs_dates: int, now: datetime
) -> ComponentScore:
    if not items:
        return _not_scored("data", "there is no stock to judge")

    count = len(items)
    no_cost = [h for h in items if Decimal(h.item.cost_per_base_cents or 0) <= ZERO]
    no_reorder = [h for h in items if Decimal(h.item.reorder_point_base) <= ZERO]
    derived = [h for h in items if h.item.track_mode == "derived"]
    stale = [h for h in derived if stale_after(h.item.last_counted_at, STALE_COUNT_DAYS, now)]

    checks = [
        Decimal(count - len(no_cost)) / count * HUNDRED,
        Decimal(count - len(no_reorder)) / count * HUNDRED,
    ]
    if coverage_percent is not None:
        checks.append(Decimal(coverage_percent))
    if derived:
        checks.append(Decimal(len(derived) - len(stale)) / len(derived) * HUNDRED)
    score = _round(sum(checks, ZERO) / len(checks))

    findings = [
        Finding(
            "good" if score >= 90 else "warn",
            f"{score}% of stock information complete",
            None if score >= 90 else "/app/health#data",
        )
    ]
    if needs_dates:
        findings.append(
            Finding(
                "warn",
                f"{_n(needs_dates, 'product')} still {'needs' if needs_dates == 1 else 'need'} dates",
                "/app/expiring",
            )
        )
    if no_cost:
        findings.append(
            Finding(
                "warn",
                f"{_n(len(no_cost), 'product')} with no cost, so its value is not counted",
                "/app",
                _ids(no_cost),
            )
        )
    if no_reorder:
        findings.append(
            Finding(
                "warn",
                f"{_n(len(no_reorder), 'product')} with no reorder point",
                "/app",
                _ids(no_reorder),
            )
        )
    if stale:
        findings.append(
            Finding(
                "warn",
                f"{_n(len(stale), 'weight or volume item')} not counted in {STALE_COUNT_DAYS} days",
                "/app",
                _ids(stale),
            )
        )

    figures = {
        "needs_dates": needs_dates,
        "no_cost": len(no_cost),
        "no_reorder_point": len(no_reorder),
        "stale": len(stale),
    }
    if coverage_percent is not None:
        figures["coverage_percent"] = coverage_percent
    return ComponentScore("data", score, findings, figures)


def overall(components: list[ComponentScore]) -> int | None:
    """The weighted average of the dimensions that could be measured.

    An unmeasured dimension drops out and the rest are reweighted, rather than being counted
    as a perfect 100 or a failing 0 - both would be a claim about something nobody knows.
    """
    measured = [c for c in components if c.measured]
    if not measured:
        return None
    weight = sum((c.weight for c in measured), ZERO)
    return _round(sum((Decimal(c.score) * c.weight for c in measured), ZERO) / weight)


def score_inventory(health: HealthInput, now: datetime) -> HealthReport:
    weights = importance(health.items)
    total_value = sum(h.value_cents for h in health.items)
    components = [
        score_stock(health.items, weights),
        score_expiry(health.expiry, total_value, health.currency),
        score_overstock(health.items, health.currency),
        score_reorder(health.items, weights),
        score_data(health.items, health.coverage_percent, health.needs_dates, now),
    ]
    return HealthReport(overall(components), components, health.currency)


# --- the action center --------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    tier: str  # urgent | recommended
    text: str
    link: str
    count: int


@dataclass(frozen=True)
class ActionCenter:
    urgent: list[Action]
    recommended: list[Action]
    healthy_items: int


def build_actions(health: HealthInput) -> ActionCenter:
    """Today's work, gathered from every part of the product so nobody has to go looking."""
    items = health.items
    out = [h for h in items if h.out_of_stock]
    low = [h for h in items if h.at_or_below_reorder and not h.out_of_stock]
    approaching = [h for h in items if h.approaching_stockout]
    slow = [h for h in items if h.overstocked]

    urgent = []
    if health.expiry.expired_products:
        urgent.append(
            Action(
                "urgent",
                f"Take {_n(health.expiry.expired_products, 'product')} past its date off sale",
                "/app/expiring",
                health.expiry.expired_products,
            )
        )
    if out:
        urgent.append(
            Action(
                "urgent",
                f"Reorder {_n(len(out), 'product')} that "
                f"{'is' if len(out) == 1 else 'are'} out of stock",
                "/app/reorder",
                len(out),
            )
        )
    if health.promotable_urgent:
        urgent.append(
            Action(
                "urgent",
                f"Mark down {_n(health.promotable_urgent, 'product')} expiring within 3 days",
                "/app/promotions",
                health.promotable_urgent,
            )
        )

    recommended = []
    to_reorder = len(low) + len(approaching)
    if to_reorder:
        recommended.append(
            Action(
                "recommended",
                f"Reorder {_n(to_reorder, 'product')} before "
                f"{'it runs' if to_reorder == 1 else 'they run'} out",
                "/app/reorder",
                to_reorder,
            )
        )
    other_promotions = max(health.promotable - health.promotable_urgent, 0)
    if other_promotions:
        recommended.append(
            Action(
                "recommended",
                f"Review {_n(other_promotions, 'markdown suggestion')}",
                "/app/promotions",
                other_promotions,
            )
        )
    if slow:
        recommended.append(
            Action(
                "recommended",
                f"Review {_n(len(slow), 'slow-moving product')}",
                "/app/health#overstock",
                len(slow),
            )
        )
    if health.needs_dates:
        recommended.append(
            Action(
                "recommended",
                f"Add dates to {_n(health.needs_dates, 'product')}",
                "/app/scan",
                health.needs_dates,
            )
        )

    troubled = {h.item.id for h in (*out, *low, *approaching, *slow)} | set(health.expiry.item_ids)
    healthy = sum(1 for h in items if h.item.id not in troubled)
    return ActionCenter(urgent, recommended, healthy)


# --- over time ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    points: int
    # (points this dimension contributed, the sentence), largest first
    lines: list[tuple[int, str]]


def contributions(snapshot: dict) -> dict[str, Decimal]:
    """How many of the overall points each measured dimension supplied."""
    measured = {
        key: value
        for key, value in snapshot.get("components", {}).items()
        if value.get("score") is not None and key in WEIGHTS
    }
    weight = sum((WEIGHTS[key] for key in measured), ZERO)
    if weight <= ZERO:
        return {}
    return {key: Decimal(value["score"]) * WEIGHTS[key] / weight for key, value in measured.items()}


def _describe(key: str, before: dict, after: dict, currency: str) -> str | None:
    """The plain-language reason a dimension moved, from its stored figures."""

    def delta(name: str) -> int:
        return int(after.get(name, 0)) - int(before.get(name, 0))

    if key == "stock":
        change = delta("out_of_stock") + delta("below_reorder")
        if change:
            return f"{_n(abs(change), 'product')} {'fewer' if change < 0 else 'more'} at or below the reorder point"
    elif key == "expiry":
        cents = delta("at_risk_cents") + delta("expired_cents")
        products = delta("at_risk_products") + delta("expired_products")
        if cents:
            text = f"{money(abs(cents), currency)} {'less' if cents < 0 else 'more'} stock at risk of expiring"
            if products < 0:
                text += f", {_n(-products, 'product')} cleared"
            return text
    elif key == "overstock":
        change = delta("overstock_items")
        if change:
            return f"{_n(abs(change), 'slow-moving product')} {'fewer' if change < 0 else 'more'}"
    elif key == "reorder":
        change = delta("approaching")
        if change:
            return f"{_n(abs(change), 'product')} {'fewer' if change < 0 else 'more'} heading for a stockout"
    elif key == "data":
        if (
            "coverage_percent" in before
            and "coverage_percent" in after
            and delta("coverage_percent")
        ):
            return f"expiry dates now cover {after['coverage_percent']}% of stock, from {before['coverage_percent']}%"
        gaps = delta("no_cost") + delta("no_reorder_point") + delta("stale") + delta("needs_dates")
        if gaps:
            return (
                f"{_n(abs(gaps), 'gap')} in stock information {'filled' if gaps < 0 else 'opened'}"
            )
    return None


def explain_change(before: dict, after: dict) -> Change | None:
    """Why the score moved between two snapshots, dimension by dimension."""
    if before.get("score") is None or after.get("score") is None:
        return None
    currency = after.get("currency") or before.get("currency") or "USD"
    was, now = contributions(before), contributions(after)

    lines = []
    for key in WEIGHTS:
        b = before.get("components", {}).get(key, {})
        a = after.get("components", {}).get(key, {})
        points = _round(now.get(key, ZERO) - was.get(key, ZERO))
        if (b.get("score") is None) != (a.get("score") is None):
            state = "now scored" if a.get("score") is not None else "no longer scored"
            lines.append((points, f"{LABELS[key]} is {state}"))
            continue
        reason = _describe(key, b.get("figures", {}), a.get("figures", {}), currency)
        if reason:
            lines.append((points, f"{LABELS[key]}: {reason}"))
    lines.sort(key=lambda line: -abs(line[0]))
    return Change(int(after["score"]) - int(before["score"]), lines)


@dataclass(frozen=True)
class Benchmark:
    average: int
    percent: int  # signed: how much better (positive) or worse than the average
    days: int


def versus_average(
    today: int | None, history: list[int], minimum_days: int = 3
) -> Benchmark | None:
    """Against this business's own recent past, never against other stores."""
    if today is None or len(history) < minimum_days:
        return None
    average = Decimal(sum(history)) / len(history)
    if average <= ZERO:
        return None
    return Benchmark(
        _round(average), _round((Decimal(today) - average) / average * HUNDRED), len(history)
    )


# --- what to order ------------------------------------------------------------------------------

REASONS = {
    "out_of_stock": "Out of stock",
    "below_reorder": "At or below reorder point",
    "runs_out": "Runs out before a delivery",
}
_REASON_RANK = {"out_of_stock": 0, "below_reorder": 1, "runs_out": 2}


@dataclass(frozen=True)
class ReorderLine:
    health: ItemHealth
    reason: str  # out_of_stock | below_reorder | runs_out
    order_base: Decimal

    @property
    def reason_label(self) -> str:
        return REASONS[self.reason]

    @property
    def days_to_stockout(self) -> Decimal | None:
        return self.health.cover


def reorder_lines(items: list[ItemHealth]) -> list[tuple[str, list[ReorderLine]]]:
    """What to order, grouped by who to order it from, most urgent first within each supplier.

    Suppliers are alphabetical; stock with no supplier set comes last, where it is easy to see.
    """
    groups: dict[str, list[ReorderLine]] = {}
    for health in items:
        if health.out_of_stock:
            reason = "out_of_stock"
        elif health.at_or_below_reorder:
            reason = "below_reorder"
        elif health.approaching_stockout:
            reason = "runs_out"
        else:
            continue
        groups.setdefault(health.item.supplier or "", []).append(
            ReorderLine(health, reason, health.order_base)
        )
    for lines in groups.values():
        lines.sort(key=lambda line: (_REASON_RANK[line.reason], line.days_to_stockout or ZERO))
    return sorted(groups.items(), key=lambda group: (group[0] == "", group[0].lower()))
