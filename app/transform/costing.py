"""Recipe costing (T2.03; RCP-FR-10, -11, -13, -17, AC-RCP-03, -07). Pure.

A recipe's cost is the sum of its lines, each the base-unit quantity times the ingredient's cost
basis (ADR-0048), split into ingredients and packaging, divided by what one making yields. A line
whose ingredient has no cost is listed, never priced at zero, so a total with gaps says so. The
margin is the variation's price less that cost, an estimate at today's costs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.transform.recipes import Line

ZERO = Decimal("0")
PLACES = Decimal("0.0001")
ESTIMATE = "estimated at today's costs"


@dataclass(frozen=True)
class CostedLine:
    line: Line
    unit_cost: Decimal | None  # cents per base unit, None when the ingredient has no cost
    cost_cents: Decimal | None


@dataclass(frozen=True)
class RecipeCost:
    lines: list[CostedLine]
    ingredient_cents: Decimal
    packaging_cents: Decimal
    yield_base: Decimal

    @property
    def total_cents(self) -> Decimal:
        return self.ingredient_cents + self.packaging_cents

    @property
    def per_yield_cents(self) -> Decimal:
        """What one serving (or one base unit of a prepared item) costs."""
        return (self.total_cents / self.yield_base).quantize(PLACES)

    @property
    def missing(self) -> list[int]:
        """The ingredients with no cost: the total leaves them out."""
        return [c.line.inventory_item_id for c in self.lines if c.unit_cost is None]

    @property
    def complete(self) -> bool:
        return not self.missing


@dataclass(frozen=True)
class Margin:
    price_cents: int | None
    cost_cents: Decimal
    complete: bool  # every line has a cost
    label: str = ESTIMATE

    @property
    def known(self) -> bool:
        return self.price_cents is not None and self.complete

    @property
    def amount_cents(self) -> Decimal | None:
        if self.price_cents is None:
            return None
        return Decimal(self.price_cents) - self.cost_cents

    @property
    def percent(self) -> Decimal | None:
        if not self.known or not self.price_cents:
            return None
        amount = self.amount_cents or ZERO
        return (amount * 100 / Decimal(self.price_cents)).quantize(Decimal("0.1"))


def cost_recipe(
    lines: Sequence[Line], unit_costs: Mapping[int, Decimal | None], yield_base: Decimal
) -> RecipeCost:
    """The cost of one making and per yield unit (RCP-FR-10, -17)."""
    costed: list[CostedLine] = []
    ingredients = packaging = ZERO
    for line in lines:
        unit = unit_costs.get(line.inventory_item_id)
        if unit is not None and unit <= 0:
            unit = None  # a cost of zero is no cost, as valuation treats it (ADR-0048)
        cost = (line.qty_base * unit).quantize(PLACES) if unit is not None else None
        costed.append(CostedLine(line, unit, cost))
        if cost is not None:
            if line.is_packaging:
                packaging += cost
            else:
                ingredients += cost
    made = yield_base if yield_base > 0 else Decimal(1)
    return RecipeCost(costed, ingredients, packaging, made)


def margin_of(price_cents: int | None, cost: RecipeCost) -> Margin:
    """Price less the cost of one serving, an estimate at today's costs (RCP-FR-11)."""
    return Margin(price_cents, cost.per_yield_cents, cost.complete)


def per_base_cost(cost: RecipeCost) -> Decimal | None:
    """What one base unit of a prepared item costs to make, or None when a line has no cost:
    an unknown input makes the output unknown, not cheaper (AC-PRD-03)."""
    return cost.per_yield_cents if cost.complete and cost.total_cents > 0 else None
