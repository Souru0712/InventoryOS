"""The consumption resolver (T2.05-a; WF-12, POS-FR-07, PRD-FR-11, DR-18, AC-RCP-04, AC-PRD-04).

Pure. One sold line, with the recipe versions in effect when it was sold, becomes what it used:
an amount of each item, in base units. Every source's sales go through this one rule (WF-12).

- The size's recipe gives the lines for one serving; its yield divides them.
- A "scale" modifier multiplies those lines (light ice x 1.15). Packaging is not scaled: a
  bigger pour still takes one cup. An "add" modifier adds its own recipe, once per time it was
  chosen, unscaled. A "none" modifier changes nothing.
- A prepared item in a recipe is used as the product's production mode says (DR-18): made ahead,
  the prepared item itself; made to order, what its own recipe is made of, in proportion. Never
  both.
- What cannot be resolved is a gap, never a reason to drop the sale: a line with no product, a
  size with no recipe, an add-in with no recipe, a prepared item with no recipe to make it from.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

PLACES = Decimal("0.0001")
DEPTH = 6  # prepared items inside prepared items; DR-17 already forbids circles

# Gaps a sold line can have; T2.05-b raises them as exceptions on the line.
UNMAPPED = "unmapped_product"  # no owned product variation
NO_RECIPE = "no_recipe"  # the size had no recipe in effect when it was sold
ADD_WITHOUT_RECIPE = "add_without_recipe"  # an add-in modifier with no recipe
NOT_MADE = "no_production_recipe"  # made to order, but a prepared item has no recipe


@dataclass(frozen=True)
class RecipeIn:
    """A recipe version as the resolver needs it: what one making uses and what it yields."""

    version_id: int
    lines: tuple[tuple[int, Decimal, bool], ...]  # (item id, base quantity, is packaging)
    yield_base: Decimal = Decimal(1)


@dataclass(frozen=True)
class ModifierIn:
    modifier_id: int
    effect: str  # "none", "scale" or "add"
    scale_factor: Decimal | None = None
    recipe: RecipeIn | None = None  # an add-in's own recipe
    times: int = 1


@dataclass(frozen=True)
class LineIn:
    """A sold line: how many, of what (None: not mapped yet), how it is made, with what."""

    quantity: Decimal
    recipe: RecipeIn | None
    mapped: bool = True
    made_to_order: bool = True
    modifiers: tuple[ModifierIn, ...] = ()


@dataclass(frozen=True)
class Resolved:
    uses: dict[int, Decimal] = field(default_factory=dict)  # item id -> base quantity used
    versions: tuple[int, ...] = ()  # the recipe versions it read, the size's first
    gaps: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.gaps


# How the resolver finds a prepared item's own recipe, in effect when the sale happened.
PreparedRecipes = Mapping[int, RecipeIn] | Callable[[int], RecipeIn | None]


def _prepared(lookup: PreparedRecipes, item_id: int) -> RecipeIn | None:
    if callable(lookup):
        return lookup(item_id)
    return lookup.get(item_id)


def resolve(
    line: LineIn,
    prepared: PreparedRecipes | None = None,
    prepared_ids: frozenset[int] = frozenset(),
) -> Resolved:
    """What a sold line used. `prepared_ids` are the items made in house (prepared or finished)
    and `prepared` finds their recipes, for a product made to order."""
    lookup: PreparedRecipes = prepared if prepared is not None else {}
    if not line.mapped:
        return Resolved(gaps=(UNMAPPED,))
    if line.recipe is None:
        return Resolved(gaps=(NO_RECIPE,))
    uses: dict[int, Decimal] = {}
    versions = [line.recipe.version_id]
    gaps: list[str] = []
    scale = Decimal(1)
    for modifier in line.modifiers:
        if modifier.effect == "scale" and modifier.scale_factor:
            scale *= Decimal(modifier.scale_factor) ** max(modifier.times, 1)

    def use(item_id: int, amount: Decimal, depth: int = 0) -> None:
        if line.made_to_order and item_id in prepared_ids and depth < DEPTH:
            recipe = _prepared(lookup, item_id)
            if recipe is not None and recipe.yield_base > 0:
                if recipe.version_id not in versions:
                    versions.append(recipe.version_id)
                for inner, qty, _packaging in recipe.lines:
                    use(inner, amount * qty / recipe.yield_base, depth + 1)
                return
            if NOT_MADE not in gaps:
                gaps.append(NOT_MADE)  # used as itself rather than not at all
        uses[item_id] = uses.get(item_id, Decimal(0)) + amount

    per_serving = Decimal(line.quantity) / line.recipe.yield_base
    for item_id, qty, packaging in line.recipe.lines:
        use(item_id, qty * per_serving * (Decimal(1) if packaging else scale))
    for modifier in line.modifiers:
        if modifier.effect != "add":
            continue
        if modifier.recipe is None or modifier.recipe.yield_base <= 0:
            if ADD_WITHOUT_RECIPE not in gaps:
                gaps.append(ADD_WITHOUT_RECIPE)
            continue
        if modifier.recipe.version_id not in versions:
            versions.append(modifier.recipe.version_id)
        times = Decimal(max(modifier.times, 1)) * Decimal(line.quantity)
        for item_id, qty, _packaging in modifier.recipe.lines:
            use(item_id, qty * times / modifier.recipe.yield_base)
    return Resolved(
        {item: amount.quantize(PLACES) for item, amount in uses.items() if amount > 0},
        tuple(versions),
        tuple(gaps),
    )
