# ADR-0051: Versioned recipes

- **Status:** Accepted, 2026-11-13 (owner: "Yes, merge it"; PR #51, T2.02-a). T2.02-b adds the
  screens under the same decision.
- **Decision ID:** none (implements T2.02 under C-36: RCP-FR-02, -04..09, -14, -17, DR-11, DR-16,
  DR-17, AC-RCP-01, -02, -05, -06, -09, DM-9)
- **Tasks:** T2.02-a (model and versioning), T2.02-b (editor and staff card); T2.03 (costing),
  T2.05 (consumption), T2.07 (waste expansion), T2.09 (production) read the versions

## Context

Recipes existed only as `recipe_components`: a Square sale item linked to stock rows at one
location, with no history. Editing one would silently change what every past sale had used.
C-36 gives the business its own products (ADR-0050). Recipes now need to:
- belong to the business's own sizes and modifiers;
- keep their history (DR-16);
- convert units (DR-11);
- let prepared items use one another without circles (DR-17).

## Decision

1. **Three tables** (migration `7a3c9d2e4f18`):
   - **`recipes`:** one per owner, held by a named CHECK. The owner is a product variation, an
     "add" modifier, or a prepared or finished item (its production recipe, used by T2.09).
   - **`recipe_versions`:**
     - each has a number and `effective_from`;
     - each has a yield: servings for a product, or an amount in any of a prepared item's units,
       kept in its base unit too (RCP-FR-05);
     - each has instructions and who saved it.
   - **`recipe_lines`:**
     - each line has an item, its amount as entered, the same amount in base units (DR-11), and
       a packaging flag (RCP-FR-14, -17);
     - an item appears once per version.
2. **Immutable versions** (DR-16):
   - Versions and lines are append-only (`APPEND_ONLY` in the scoped layer). `app/recipes.py`
     is their only writer, and a test fails on any other.
   - Saving adds the next version, effective now or at a chosen later moment (RCP-FR-07). A
     moment in the past is refused: the past keeps its recipe. A save that changes nothing adds
     nothing.
   - Every save is audited (`recipe.saved`, under "Recipes").
3. **The version in effect** at an instant is the latest one that had started by then; a tie
   goes to the later save (`version_at()`).
   - A sale, waste or production run uses the version in effect at its own time, however late
     it arrives (AC-RCP-02).
   - The consumption engine (T2.05) records which version it used.
4. **Units and circles:**
   - **Units:** each line goes through the item's resolver (`entered_base`, ADR-0039). A line
     whose unit cannot convert is refused with the missing conversion named, and nothing is
     written (AC-RCP-01).
   - **Circles:** a prepared item's recipe that would lead back to itself through other
     prepared items is refused, and the circle is named (AC-RCP-05). The check also counts
     versions scheduled for later.
5. **Start from another size** (RCP-FR-09, AC-RCP-06): `start_from()` returns the other size's
   current lines scaled by the sizes' amounts, so "12 oz" to "16 oz" gives 16/12. Names without
   comparable amounts, such as "Small" and "Large", are not scaled. T2.02-b's editor uses it.
   - **Packaging is not scaled:** a bigger drink still takes one cup and one lid.
   - **Instructions come along** with the lines.
7. **Screens** (T2.02-b):
   - **The staff card** at `/app/recipes/{kind}/{owner_id}` (`recipe.view`) shows the version
     in use, its yield, lines and instructions, any version scheduled for later, and every
     version with who saved it and what changed. It carries no cost at all (AC-RCP-09).
   - **The editor** (`recipe.edit`, plus `production_recipe.edit` for a prepared item) offers:
     - an ingredient picker, an amount, and a unit field suggesting every unit and package;
     - a packaging tick, yield and instructions;
     - an optional later start, in the business's own time zone;
     - "start from" buttons for the product's other sizes.
   - **Ways in:** each size on the product page, each "adds ingredients" modifier, and each
     prepared item's page link to their recipe.
6. **Backfill (DM-9, recipes half):** each variation made from a sold item gets version 1 of
   that item's `recipe_components`.
   - **Lines:** one line per item, in its base unit; packaging is flagged by item type.
   - **Effective date:** effective from 2000-01-01, so every past sale reads it.
   - The demo seed does the same.
   - **Kept for now:** `recipe_components` stays, and still drives today's velocity figures,
     until the consumption engine (T2.05) contracts it.

## Alternatives considered

- **Edit a recipe in place and keep an audit trail:** past consumption would change with every
  edit, which DR-16 forbids.
- **Drafts before a version takes effect:** the domain model mentions draft, effective and
  superseded. A version scheduled for later covers the need without a second editing state.
  Drafts can come with the editor if owners ask.
- **Recipes per location:** recipes describe the product, not the shelf. A line names the item
  (`inventory_items`), and consumption finds the stock at the sale's location.

## Consequences

- **Until T2.05:** recipes edited in the new model do not yet move stock or velocity;
  `recipe_components` still does.
- **T2.03:** costs a version from its lines and the cost basis (ADR-0048).
- **T2.09:** production uses the prepared item's version and its yield.
- **Built in T2.02-a** with migration `7a3c9d2e4f18`.
