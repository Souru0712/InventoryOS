# ADR-0039: Units, packages and conversions

- **Status:** Proposed, 2026-11-12 (in the T1.15 pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T1.15; INV-FR-04, -05, -11..14; DR-10, DR-11; AC-INV-02, AC-INV-03; DM-5)
- **Tasks:** T1.15; T2.02 (recipes), T2.11 (receiving) and every later entry screen use the resolver

## Context

Quantities were entered only in an item's own display unit, a single pack size per stock row
stood in for packages, and nothing could convert between weight and volume. Juice bars buy
produce by weight and use juice by volume (G7); a wrong conversion corrupts every figure.

## Decision

1. **One resolver** (`app/transform/conversions.py`, pure) turns a quantity in any built-in unit
   or item package into the item's base unit (g, ml, each), exact to four places (DR-10):
   - within a dimension, by the constants in `app/units.py` (now with tbsp and tsp);
   - across dimensions, only through that item's own conversion, never an assumed density;
   - otherwise it refuses, naming the missing conversion (DR-11).
2. **Packages** (`item_packages`): a business-named way an item comes, "case" = 6 × gal, where
   the unit is a built-in unit or another of the item's packages (nesting up to five deep; loops
   and names that clash with units are refused). One package is marked as how deliveries arrive.
3. **Conversions** (`item_unit_conversions`): "1 lb = 340 ml" for one item, at most one per pair
   of measures, usable in both directions.
4. **The delivery package keeps the stock rows' pack size**: receiving and scanning still read
   `stock_items.pack_size_base`, so every change to packages or conversions rewrites it for all
   the item's locations. That way there is no second source of truth while those screens wait for
   T2.11. The add-item form's pack size becomes the item's "pack" package.
5. **Backfill (DM-5, second half)**: each item's distinct pack sizes become packages named
   "pack", "pack 2" and so on. The size is written in the item's display unit when that is exact,
   else in its base unit. The first one is marked as how deliveries arrive.
6. **Entry**: a count can now be given in any unit or package of the item (`unit` on the count
   form); an unconvertible one answers 400 with the reason. The editor sits on the item page, and
   shows what one of each package is in the base unit.
7. Changing how an item is measured once stock has moved stays refused (INV-FR-14, ADR-0038).

## Alternatives considered

- A density table per ingredient: assumed numbers are exactly what INV-FR-05 forbids.
- Packages as plain multipliers of the base unit: "a case of 24 bottles" could not be written as
  the business says it, and a bottle size change would not carry into the case.
- Move receiving and scanning to packages now: they are rebuilt in T2.11; until then the delivery
  package sets the pack size they already read.
- Hypothesis for property tests: a new dependency; seeded random cases cover the round trips.

## Consequences

- Packages and conversions are removed outright for now: nothing records history against them
  yet. When receivings reference packages (T2.11) removal becomes deactivation (DR-24).
- Built in T1.15 with migration `6d82b619c66f`.
