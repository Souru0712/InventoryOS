# ADR-0038: Item and category screens

- **Status:** Accepted, 2026-11-12 (the owner merged the T1.14 pull request, #33: "Yes, merge it")
- **Decision ID:** none (implements T1.14; INV-FR-01, -02, -03, -06, -10, INV-FR-14, DR-24, AC-INV-01)
- **Tasks:** T1.14; T1.15 (packages and conversions join the item page), T2.10 (suppliers)

## Context

No item could be edited after creation (audit P3): the only screen was `/app/items/new`, which
made one stock row at one location. Since T1.13, items are defined once per business with
per-location settings, and every reader shows those rows.

## Decision

1. **Screens at `/app/catalog`** ("Items" in the menu), kept apart from `/app/items/{stock item
   id}/...` so the two kinds of id never share a URL. `/app/items/new` now redirects there.
   - **List** (`inventory.view`): search by name; filter by category, supplier, status
     (active, inactive, all), low stock, expiring within 7 days of each location's business date,
     and location.
   - **Item page** (`inventory.view` to look, `item.edit` to change): name, kind, category, unit,
     use-by dates, shelf life, notes; levels per location (reorder point, par, minimum, storage
     area, stocked or not); "also stock it at" another location; its barcodes; deactivate or bring
     back.
   - **Categories** (`item.edit`): add, rename, merge, archive, restore.
2. **Items are created at one or more locations at once**, each with its opening quantity, as
   stock rows that the T1.13 hook links to one item, because quantities still live on stock rows
   until the ledger (T1.16). Definitions and levels are written to `inventory_items` and
   `item_locations` directly. Names are unique per business in any case; slugs get `-2`, `-3`
   when taken.
3. **The unit someone counts in decides how the item is measured**: one picker grouped by counted,
   weighed and measured by volume. Once stock has moved (a quantity, a non-zero count, a stock
   event or a dated lot) the dimension is locked (INV-FR-14); a new item's zero opening count is
   not movement.
4. **Categories are rows** (`inventory_categories`), and items point at one (`category_id`); the
   free-text column from T1.13-a is migrated into them. A category is chosen by typing its name:
   the same name in any case reuses it (and brings back an archived one), a new name creates it.
   Merging moves every item and archives the source. Nothing is deleted (DR-24).
5. **Deactivating an item** keeps all its history and takes it out of every pick list:
   `active_stock()` now requires the item and its location settings to be active (AC-INV-01).
6. **Every change is audited** (category "Items and categories"): items added, changed,
   deactivated, brought back, stocked somewhere; levels changed (recorded against the location);
   categories added, renamed, merged, archived, restored.

## Alternatives considered

- Keep the category as free text: renaming and merging would rewrite every item, and an archived
  category has nowhere to live.
- Separate dimension and unit fields: two fields that must agree; the unit already says it.
- Allow deleting items never used: DR-24 allows deletion only through account deletion, and
  deactivating covers the need.
- Put the screens under `/app/items/{id}`: the same path segment would hold a stock item id for
  counts and thresholds and an item id for the item page.

## Consequences

- The supplier is still free text on each location's stock row until suppliers arrive (T2.10);
  packages and conversions join the item page with T1.15.
- When a POS renames a product it syncs, the item's shared name follows the POS.
- Built in T1.14 with migration `9c903ba85a8f`.
