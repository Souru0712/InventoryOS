# ADR-0036: Splitting stock items into inventory items and item locations

- **Status:** Proposed, 2026-11-12 (in the T1.13-a pull request; accepted when the owner merges it)
- **Decision ID:** none (carries out ID-10 / ADR-0010 under C-38; DM-5)
- **Tasks:** T1.13-a (schema, backfill, mirror); T1.13-b (readers move); T1.15, T1.16 build on it

## Context

ADR-0010 decided the tables: `inventory_items` once per business, `item_locations` per location,
`stock_items` kept as a compatibility layer until DM-16. Today every screen, the Square sync, the
demo seed and many tests read and write `stock_items` directly, and moving all of them at once
would be one change too large to review. The backfill also needs a rule for the same slug at
several locations.

## Decision

1. **Backfill (DM-5)**: every stock item becomes exactly one item location. Stock items sharing a
   slug in a business become one inventory item when they share a dimension. A slug measured
   differently elsewhere (weight here, count there) cannot share a base unit, so it becomes its
   own item with the dimension appended (`flour-count`). The first row (lowest id) gives the
   item its name, category and display unit.
2. **Item fields**: `item_type` starts as `resale` for anything with a shelf price and `raw`
   otherwise, until the item screens (T1.14) let people say. `base_unit` follows the dimension
   (`each`, `g`, `ml`); `display_unit` is the old `stock_unit`; `tracks_batches` follows
   `tracks_expiry` until T2.06 defines batch tracking on its own; shelf life and storage area
   start empty. `dimension` is stored as its plain value with a named CHECK, avoiding a second
   Postgres enum.
3. **The mirror** (`app/catalog.py`): a `before_flush` listener on every session. A new stock
   item is linked to its item (found by slug and dimension, or created) and gets a new item
   location. A change to a mirrored field is carried across: name, category, dimension, unit and
   expiry tracking to the item; reorder point, par and active to the location's settings. A
   quantity change writes nothing to the split tables. Nothing else writes them until T1.13-b.
4. **Scope**: inventory items are business-wide definitions (no quantities), so a location-limited
   member sees them all; item locations follow the location scope. Reads go through
   `inventory_items()` and `item_locations()`.

## Alternatives considered

- Move every reader and writer in one change: hundreds of reads across screens, jobs and tests in
  one review; the task itself splits it into -a and -b.
- Explicit calls at the three places that create stock items: the many tests and fixtures that
  create stock items directly would leave rows unlinked, and T1.13-b would then break on them.
- A database view or triggers: two dialects to keep in step, and the logic (slug and dimension
  rules) belongs in Python with the rest of the domain.
- Merge regardless of dimension: one item could not have one base unit.

## Consequences

- Until T1.13-b, `stock_items` stays the row every screen uses; the split tables are a faithful
  mirror. When one location renames a shared item, the shared name follows the last change.
- Name uniqueness per business (case-insensitive, DOMAIN_MODEL) is not enforced yet; the item
  screens add it (T1.14).
- Built in T1.13-a with migration `e3f7a1c9d5b2`.
