# ADR-0037: Reading stock items' definitions and settings from the split tables

- **Status:** Accepted, 2026-11-12 (the owner merged the T1.13-b pull request, #32: "merged manually")
- **Decision ID:** none (completes T1.13 under ID-10 / ADR-0010 and ADR-0036; C-38)
- **Tasks:** T1.13-b; T1.14 (item screens write the split tables); DM-16 (drops the legacy columns)

## Context

After T1.13-a every stock item is linked to its inventory item and item location, but every
screen, job and template still read the definition and settings straight from `stock_items`.
The item screens (T1.14) will edit `inventory_items` and `item_locations` directly, so readers
must show those rows. They read the fields in some 70 places across Python and templates, and a
missed one would show a stale value silently.

## Decision

1. **`StockItem`'s definition and settings fields read from the split rows.** `name`,
   `category`, `dimension`, `stock_unit` (the item's display unit) and `tracks_expiry` come from
   the inventory item; `reorder_point_base`, `par_level_base` and `is_active` come from the item
   location (`SplitField` in `app/models.py`). The table columns keep their names; the mapped
   attributes become `_name` and so on, the legacy copy DM-16 drops.
2. **Writes go to both**: setting one of those fields on a stock item updates its split row (and
   the derived `base_unit` and `tracks_batches`) and the legacy column. The flush hook now only
   links new stock items; the change mirror of T1.13-a is gone.
3. **Queries move explicitly**: filtering on active and sorting by name go through the split
   tables (`active_stock()`, `by_item_name()` in `app/catalog.py`). A test fails if product code
   queries a legacy column.
4. Relationships to both split rows load with the stock item (`lazy="joined"`), so reading a
   field costs no extra query.

## Alternatives considered

- Rewrite every read site to `item.inventory_item.name` and the like: the same result spread
  over some 70 lines of Python and templates; any line missed reads a stale value without
  failing. The descriptor covers every read, including ones added later.
- Keep reading `stock_items` until T1.14: the item screens would then have to write the legacy
  columns, and move again.

## Consequences

- Quantities (`qty_base`) and everything not listed above stay on `stock_items` until the ledger
  (T1.16, T1.17) and later tasks move them.
- A stock item not yet flushed reads its own legacy values; after the first flush it reads its
  split rows.
- T1.13 is complete with this change.
