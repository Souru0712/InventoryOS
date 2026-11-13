# ADR-0050: Owned product catalog

- **Status:** Proposed, 2026-11-13 (in the T2.01 pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T2.01 under C-36: RCP-FR-01, RCP-FR-03, DM-9)
- **Tasks:** T2.01; T2.02 (recipes per variation and per modifier), T2.04 and T2.05 (sales and
  consumption), T3.03 and T3.04 (POS products mapped onto these)

## Context

What a business sells was a list of Square variations (`sold_items`). A Standalone business
could not define its own menu, and nothing could map a POS onto a menu the business owns. C-36
decided that InventoryOS owns products, variations and modifiers, and that POS catalogs are
mapped to them, never the reverse.

## Decision

1. **Three tables** (migration `5e0b7c41d2a9`):
   - **`products`:** name unique per business, optional category, active flag.
   - **`product_variations`:** a size per product, unique within it, with an optional
     informational price in cents. The POS stays the price authority (RCP-FR-01).
   - **`modifiers`:** name unique per business, optional group, an effect, and a scale factor.
     - **Effects:** in R1 they are `none`, `scale` and `add` (RCP-FR-03); `remove` and
       `substitute` arrive in R1.1.
     - **Scale factor:** a CHECK keeps one, above zero, exactly when the effect is `scale`.
   - **No `modifier_effects` table:** an add modifier's lines are its recipe, which T2.02
     builds (DOMAIN_MODEL: Recipe owned by a variation, a modifier or a prepared item). The
     target architecture listed such a table; the effect lives on the modifier instead.
2. **One module writes the catalog:** `app/products.py` creates, edits and deactivates products,
   variations and modifiers.
   - Nothing is deleted (DR-24), and a product always keeps one size on sale.
   - Every change is audited under the new "Products and modifiers" category.
3. **Screens at `/app/products`:**
   - **Who:** `recipe.view` reads the catalog; `product.edit` changes it, under the
     subscription's write rule.
   - **Prices are money:** like every amount they sit behind `inventory.view_costs`, so an
     Employee never sees one. The task's "prices hidden from roles without it" is read as the
     cost permission: a Read-only accountant keeps them, which the margins (`reports.financial`)
     need.
   - **Editing without prices:** a role that cannot see prices sends none, and the price stays
     as it was.
4. **Backfill (DM-9, products half):** every `sold_items` row becomes a variation.
   - **Names:** Square names a variation "Item - Variation", so the name splits at its last
     " - ". A name without one becomes a product sold as "Regular".
   - **Prices:** a price above zero is kept.
   - **Source link:** `source_sold_item_id` (unique) records where each variation came from,
     for T2.02's recipe version 1 and T3.03's mappings (DM-13).
   - **Repeats:** running it again adds nothing, and a product name already taken is not
     reused.
   - **Self-contained:** the migration keeps its own copy of the split rule. The demo seed
     applies the same rule through `products_from_sold_items()`.
5. **The Square sync still writes `sold_items` only:** a new POS item becomes a product through
   mapping (T3.03, T3.04), not automatically. This keeps the catalog the business's own.

## Alternatives considered

- **Keep `sold_items` as the product list:** rejected by C-36.
- **One product per sold item, without splitting names:** a 12 oz and a 16 oz would be two
  products, which breaks "Start from 12 oz" (AC-RCP-06) and per-size recipes.
- **Create products automatically on every Square sync:** it would make Square the author of the
  catalog again. Mapping is T3.04's job.

## Consequences

- **T2.02:** recipes hang off `product_variations` and `modifiers`; `recipe_components` becomes
  version 1 through `source_sold_item_id`.
- **T2.05:** consumption needs a variation per sale line. Until T3.04 maps POS products, sales
  still reach stock only as they do today.
- Built in T2.01 with migration `5e0b7c41d2a9`.
