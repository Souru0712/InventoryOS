# ADR-0066: Reorder suggestions on ledger usage

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.15: ROR-FR-01..05, -07, -08, STD-FR-09, AC-ROR-01..05;
  DM-9 contract)
- **Tasks:** T2.15; T2.16 (reports read the same usage)

## Context

Reorder quantities, health, expiry projections and markdown suggestions all read a velocity that
came from POS sales multiplied through the flat `recipe_components`, plus direct sales of
POS-tracked SKUs. That input ignores production, usage records and hand-entered sales, and
`recipe_components` was due to be dropped once velocity read ledger usage (owner, 2026-11-18).
The spec's reorder formula also differs from the existing "top up to par".

## Decision

1. **Velocity is ledger usage.** Use per stock item and business day is what `sale_consumption`,
   `production_input` and `manual_consumption` movements took, a reversal netting against the
   type it undid (`item_moves()`). The sales signal ("zero means not used") is items a recipe in
   effect uses, or that were used in the window. Health, expiry, promotions and alerts read it.
   A POS-tracked SKU is used through its product's recipe like anything else.
2. **Count-based fallback (STD-FR-09).** A location's sales are complete when every open day of
   the window has a POS source of record or recorded sales. Where they are not, or nothing
   recorded use, use between the first and last count of the window replaces recorded use: first
   count + what else came and went (receipts, production, waste, adjustments) − last count
   (AC-ANL-08). Counts are count-session lines (which keep counts that matched) and every
   non-batch count posting. The suggestion says which it used.
3. **The spec formula (ROR-FR-02):** max(0, use per day × (lead time + cover days) ×
   (1 + safety %) + reorder point − on hand − on order), rounded up to the purchase unit: the
   preferred (or only) active supplier item's pack, else the stock row's receiving pack. On order
   is zero until purchase orders (R1.1). Lead time is the supplier's, else the item's. Par
   levels no longer set the quantity.
4. **Settings without schema** (the task allows no DB change): window 7/14/28/56/90 days
   (default 28), cover days (default 7) and safety % (default 10) are chosen on the page and
   carried into the CSV.
5. **Count first (ROR-FR-05, AC-ROR-03)** for stock below zero, never counted, or last counted
   more than 14 days ago (the health module's stale threshold).
6. **Order-by (ROR-FR-04):** the last day to order so a delivery lands before stock reaches the
   reorder point, moved earlier onto the supplier's delivery days; groups sort by it.
7. **CSV on every plan (E-05)**, grouped by supplier, behind `data.export`; no reorder route
   accepts anything but GET (ROR-FR-08, AC-ROR-05). The `reorder_csv` capability stays in the
   plan catalogue untouched; nothing checks it.
8. **`recipe_components` dropped** (migration `2675519dc2bc`, DM-9 contract); the downgrade
   rebuilds it from the version-1 recipes. The demo seeds its recipes as first versions in
   effect from the start (`save_recipe(from_the_start=True)`, refused once a recipe exists) and
   consumes its sales history on the ledger.

## Alternatives considered

- **Tenant settings for cover and safety:** a migration the task rules out; the page settings
  can move into business settings later.
- **Keep par as the target:** contradicts ROR-FR-02 and the AC-ROR-01 example.
- **Keep POS-SKU direct sales as a velocity source:** a second idea of usage beside the ledger,
  which consumption never posts.

## Consequences

- A business whose POS items have no recipes shows no use until they are mapped (T3.03/T3.04)
  or counted twice.
- The completeness notice still treats a POS-counted item without a recipe as covered, although
  nothing consumes it; to revisit with mapping.
- Count-based use reconstructs a quick count's quantity from the ledger, which is exact for counts
  posted at the time they were taken (every count screen does).
