# ADR-0054: Consumption engine

- **Status:** Proposed, 2026-11-18 (in the T2.05-a pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T2.05: WF-12, POS-FR-07 shared part, PRD-FR-11, DR-18,
  AC-PRD-04, AC-RCP-04)
- **Tasks:** T2.05 (`-a` resolver and production mode, `-b` posting and reprocessing); T2.07
  (waste of a product expands through it), T2.12 and T2.13 (their sales consume through it),
  T3.04 (reprocessing after mapping)

## Context

A sale has to take out of stock what it used. The old engine (`app/transform/inventory.py`
60-163) read `sales_daily` and `recipe_components`. It lost sales made after a same-day count
and crossed locations (C2, C3). WF-12 asks for one engine for every source: the recipe version
in effect when the sale happened, its modifiers, the product's production mode, and the gaps it
cannot resolve, recorded and never dropped. DR-18 forbids consuming both a prepared item and
what it is made of. T2.04 gave every sale the same lines (ADR-0053) and T2.02 gave recipes
versions (ADR-0051).

## Decision

1. **A pure resolver** in `app/transform/consumption.py`: `resolve(line, prepared,
   prepared_ids)` turns one sold line into an amount of each item in base units, the recipe
   versions it read, and its gaps.
   - The size's recipe gives one making; its yield divides it (`quantity / yield`).
   - A `scale` modifier multiplies the recipe's lines (Light ice × 1.15), compounding when
     chosen more than once. Packaging lines are not scaled: a bigger pour still takes one cup.
   - An `add` modifier adds its own recipe once per time it was chosen, per item sold,
     unscaled (two vanilla pumps on three lattes are six pumps).
   - A `none` modifier changes nothing. `remove` and `substitute` arrive with the catalog's
     R1.1 effects (ADR-0050).
   - Amounts are rounded to 4 places at the end, never per line.
2. **Production mode per product** (PRD-FR-11): `products.production_mode`, `made_to_order`
   (the default) or `made_ahead` (migration `b3f5d8e2a147`, named CHECK). It is set on the
   product page through `update_product()` and audited.
   - **Made ahead:** a prepared or finished item in the recipe is used as itself.
   - **Made to order:** it is replaced by what its own recipe (in effect at the sale) is made
     of, in proportion to its yield, recursively for a prepared item inside a prepared item.
   - Never both (DR-18, AC-PRD-04).
3. **Recipes in effect at the sale's moment** (`app/consumption.py`): `sale_moment()` is the
   sale's `occurred_at`; a day's sales entered as a whole use the end of that business day at
   its location (ADR-0027). Every recipe read (the size's, each add-in's, each prepared item's)
   uses `effective_version()` at that moment (DR-16).
4. **Gaps, never a lost sale:** a line with no owned product (`unmapped_product`), a size with
   no recipe in effect (`no_recipe`), an add-in with no recipe (`add_without_recipe`), a
   prepared item made to order with no recipe (`no_production_recipe`, used as itself rather
   than not at all). The rest of the line is still resolved.
5. **Units:** recipe lines are stored in base units, converted when the recipe is saved
   (`entered_base`, DR-11, ADR-0039). A line that cannot convert is refused at save, so the
   resolver never meets one; `conversion_missing` stays for -b's posting checks.
6. **T2.05-b** posts what a line used as `sale_consumption` through `post_transaction()` with
   key `sale:<id>:r<rev>`, records the recipe version and consumption status on the line,
   reverses and re-posts a revised sale, raises gaps and `missing_cost` as exceptions, and runs
   as a job.

## Alternatives considered

- **Expand prepared items when the recipe is saved:** a later change to the prepared item's
  recipe would not reach products that use it, and the mode could not differ per product.
- **Production mode per prepared item:** the same juice is pressed ahead for one drink and to
  order for another; the spec puts the mode on the product (PRD-FR-11, DOMAIN_MODEL).
- **Scale packaging with the drink:** a light-ice drink would use 1.15 cups.
- **Drop a line it cannot resolve:** the sale would disappear from usage and nobody would know
  why; WF-12 keeps it with a gap.

## Consequences

- Every source's sales consume through one rule: manual entry (T2.12), file import (T2.13),
  Square orders (T3.05), and waste of a product (T2.07).
- **Finding (needs the owner's decision):** WF-12 step 6 allocates batches (FEFO, marked
  inferred). Movement-level batch allocation is built in T2.06, which comes after T2.05.
  Proposed: T2.05-b posts without batches and T2.06 adds inferred allocation for sales.
- **Before T2.05-b:** retiring the old engine drops `sales_daily` and `recipe_components`
  (DM-7, DM-9 contract), which is destructive and needs the owner's approval. Velocity still
  reads `recipe_components` until T2.15 moves it to ledger usage.
- **Built in T2.05-a:** the resolver, production mode, the loader and their tests. Nothing
  posts yet.
