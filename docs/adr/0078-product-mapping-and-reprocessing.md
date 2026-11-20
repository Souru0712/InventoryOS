# ADR-0078: Product mapping and reprocessing

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T3.04: POS-FR-06, POS-FR-14, PD-17, WF-28, REC-FR-12,
  AC-POS-06; DM-13's mapping half)
- **Tasks:** T3.04; T3.05 builds order lines through these mappings

## Context

A POS sale only uses stock once its item means one of the business's product sizes. Until now that
link was `source_sold_item_id`, made once from the legacy sold items, with no way to change it,
no screen, and no way to fix the sales that came in while an item was unmapped.

## Decision

1. **`product_mappings`** maps an external product (a POS variation) to a product size, or an
   external modifier to a modifier. There is one active mapping per external id (partial unique
   indexes). A change supersedes the active row (`superseded_at`) instead of editing it, so who
   mapped what, and when, stays. `app/mappings.py` is the only writer (a test enforces it), and
   every change is audited under the new `mapping` category.
2. **New sales use the mapping.** The legacy daily feed takes a line's size from the active
   mapping first, then from `source_sold_item_id`. Mapping a POS item clears its open
   `unmapped_product` exceptions at every location.
3. **Suggestions by name** (`app/transform/mappings.py`, pure) rank sizes by product name, then by
   size; an exact product name outranks a matching size on another product. A POS product can
   also be made one of the business's own: its size is added to the product of the same name, or
   a new product is created.
4. **Reprocessing is previewed, confirmed, then run as a job** (`reprocess_mapping`). The preview
   counts the counted sales since a start date whose line does not yet name the mapped size, and
   what they would take from stock by the recipe in effect now. The job points those lines at the
   size (`remap_lines` in `app/sales.py`, the owner of sales lines), then reverses and posts each
   sale again at its own moment (`reprocess_sale`, WF-12). A repeat finds nothing left to change,
   so a sale's use posts once. A superseded mapping's job does nothing.
5. **The window (PD-17)** defaults to the business date of the latest count of anything the size
   uses: a count already settled the sales before it, and reprocessing them would take their use
   twice. That count is a ledger `count_variance` (read through `counted_at()` in
   `app/ledger/history.py`) or a posted count-session line. The window reaches back 30 days at
   most, and the person may choose a later start.
6. **DM-13's mapping half**: each external product whose sold item became a product size is
   mapped to that size, made by the product.

## Alternatives considered

- **Editing the mapping row in place:** loses who mapped what before, which WF-28 audits.
- **Reprocessing during the request:** a month of order lines (T3.05) is too much for one request.
- **Starting at the earliest of the items' counts:** that undoes counts of the other items.

## Consequences

- A modifier mapping takes effect on sales recorded after it. Past lines kept no record of the POS
  modifier, so there is nothing to reprocess for one.
- An unsold, unmapped catalog item opens no exception. It is listed on the mapping screen;
  exceptions come from sales.
