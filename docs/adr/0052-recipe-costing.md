# ADR-0052: Recipe costing, margins and completeness

- **Status:** Proposed, 2026-11-13 (in the T2.03 pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T2.03: RCP-FR-10..13, -17, AC-RCP-03, -07, -08)
- **Tasks:** T2.03; T2.09 (production) gives prepared items a cost of their own

## Context

Recipes are versioned (ADR-0051), and each item has a current cost per location (ADR-0048).
Owners ask "what does a drink cost me, and what happens to my margin when milk goes up?" Before
T2.03 nothing combined the two, and nothing listed what was sold with no recipe at all.

## Decision

1. **Pure costing** (`app/transform/costing.py`):
   - A line costs its base-unit quantity × the ingredient's cost basis.
   - **Totals:** ingredients and packaging are totalled apart (RCP-FR-17), and the sum is
     divided by what one making yields, giving a cost per serving or per unit made
     (RCP-FR-10).
   - **Gaps:** an ingredient with no cost (none, or zero) is listed and left out, never priced
     at zero. A total with a gap says "at least".
   - **Margin:** price − cost per serving, labelled "estimated at today's costs" (RCP-FR-11).
     It is claimed only when every line has a cost and the size has a price.
2. **At one location:** costs are shown at the location asked for, else the first active one,
   because each item's cost is per location (DR-19). A business with several locations switches
   between them.
3. **Prepared items:** a prepared item with no cost of its own is costed from its production
   recipe: cost per unit made = the batch's cost ÷ its yield. If any input has no cost, the
   prepared item has none either; it is never cheaper than it is (as AC-PRD-03 will require).
   Once T2.09 records production, a prepared item carries its own cost and that comes first.
4. **Where costs show** (`inventory.view_costs` only; an Employee's card still carries no cost,
   AC-RCP-09):
   - the recipe card's cost panel: line costs, the totals and the gaps;
   - each size on the product page: cost and margin.
5. **What a cost change touches** (RCP-FR-13, AC-RCP-07):
   - **Affected recipes:** `/app/catalog/{item}/recipes` takes the item's latest cost change
     from its history (ADR-0048). It lists every recipe whose version in effect uses the item,
     directly or through prepared items.
   - **Each row shows** the recipe's cost before and after, its margin before and after, and
     the change. Figures are rounded before differencing, so the change matches what is shown.
   - **A first cost** has nothing to compare, so only today's figures appear.
   - **After a delivery:** an applied delivery's page lists the cost changes it made, each
     linking to that view with the number of recipes affected.
6. **Sold without a recipe** (RCP-FR-12, AC-RCP-08):
   - **The list:** `/app/products` shows the sizes sold in the last 30 days (each location's
     business days) that have no recipe in effect, most sold first. Each links to its recipe
     editor, or to the card for a role that cannot edit.
   - **POS-counted items** need no recipe and are not listed.
   - **Exception deferred:** an `unmapped_product` exception per sale comes with the
     consumption engine (T2.05). This list is the recipe-authoring view.

## Alternatives considered

- **Average an item's cost across locations:** a two-site owner would see a cost that neither
  site pays. A location switch shows each one's own figures.
- **Store recipe costs:** they would go stale with every receipt. Costing is computed when
  asked, from the version in effect and today's costs (PD-08).
- **Count a missing cost as zero:** the margin would look better than it is.

## Consequences

- **Dashboard banner:** the completeness banner (ADR-0049) still counts sales against
  `recipe_components`, because that is what today's velocity uses. Until T2.05, a recipe written
  in the new editor clears this list but not that banner.
- **T2.09** will record production with the same per-unit rule.
- **Built in T2.03** with no schema change.
