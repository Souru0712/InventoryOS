# ADR-0055: Sale consumption posting

- **Status:** Accepted, 2026-11-18 (owner: "Yes, merge it"; PR #57, T2.05-b)
- **Decision ID:** none (implements T2.05-b: WF-12 step 7, WF-28's reprocessing, POS-FR-14; the
  DM-7 contract, approved by the owner on 2026-11-18)
- **Tasks:** T2.05; T2.06 (inferred batch allocation for sales, moved there by the owner),
  T3.04 (mapping, then reprocessing through it), T2.15 (velocity moves to ledger usage, then
  `recipe_components` is dropped)

## Context

T2.05-a resolves what a sold line used (ADR-0054); nothing posted it. WF-12 asks for one
idempotent `sale` transaction per sales transaction revision, keyed `sale:<transaction
id>:r<revision>`, that never blocks or drops a sale, leaves a failed line `pending` for a
background retry, and records the recipe version used on each line. A changed resend is the
next revision (ADR-0053), so what the earlier revision took must come back. The owner approved
three points on 2026-11-18 ("Yes, as proposed"): drop `sales_daily` now, keep
`recipe_components` until T2.15 moves velocity to ledger usage, and post without batches until
T2.06 adds inferred allocation for sales.

## Decision

1. **A job per sale** (`consume_sale`, migration `1da987c1c195`): `record_sale()` queues it,
   deduplicated per transaction. `consume_sale()` in `app/consumption.py` is safe to repeat:
   - only the latest revision of a sale, completed and counted for its day (DR-31), holds
     stock; every other revision's standing posting is reversed, at its own moment, and its
     pending lines become `skipped`;
   - the latest's pending lines are resolved at the sale's moment (`sale_moment()`), and what
     they used is summed per item and posted as one `sale` transaction of `sale_consumption`
     movements at the sale's location, at that moment, by the system;
   - each line records the size's recipe version and becomes `posted`, or `gap` when anything
     was left out; a line with nothing pending means the work was done, so a repeat posts
     nothing.
   - A failure rolls back with the job and leaves the lines `pending`; the queue retries it and
     dead-letters into `job_failed` after the last attempt.
2. **Gaps are `unmapped_product` exceptions** (REC-FR-12; PRODUCT_SPECIFICATION: a missing
   recipe at sale time raises `unmapped_product`), each on the thing to fix, adding to the open
   one so it counts the sales:
   - a line with no product: the POS item at that location (`pos_item`);
   - a size with no recipe in effect: the variation (opens its product);
   - an add-in with no recipe: the modifier;
   - a prepared item made to order with no recipe: the item;
   - an item the location does not stock: the item at that location (opens the item). Its use
     is left out rather than posted where the ledger has no balance for it.
3. **Costs:** each movement's cost is the cost in effect at the sale's moment (`cost_on()`); an
   item used without one is posted at zero and raises `missing_cost` on that item at that
   location.
4. **Reprocessing** (`reprocess_sale()`, WF-28): what the sale still holds is reversed and its
   lines are posted afresh at the sale's own moment, with key `sale:<id>:r<rev>:p<n>` for the
   n-th posting, so the ledger's idempotency never returns the reversed one.
5. **History is never consumed** (DATA_MIGRATION_ROADMAP rule 6): lines recorded before this
   revision were never consumed, so the migration marks the pending ones `skipped`;
   `record_sale(consume=False)` keeps a sale for reference only (the demo's past days, from
   before its opening balances).
6. **`sales_daily` is dropped** (DM-7 contract). The downgrade rebuilds it from the daily POS
   transactions and returns the skipped lines of counted sales to `pending`.
7. **After posting**, stock evaluation is queued (`evaluate_exceptions`), for low stock and
   below zero.

## Alternatives considered

- **Post inside `record_sale()`:** a recipe or ledger failure would refuse the sale; WF-12 says
  a sale is never blocked.
- **One posting per sold line:** many transactions per order for no gain; the line keeps its
  status and recipe version, and the transaction names the sale.
- **Auto-stock a recipe item at the selling location:** a background job would change what a
  location carries without anyone deciding it.
- **A new exception type per gap:** REC-FR-12 fixes the R1 types; the spec already raises a
  missing recipe as `unmapped_product`.

## Consequences

- Every sales source consumes stock by recording through `record_sale()`.
- **Open (for T3.05, recorded here):** a sale that arrives after a later count of its items
  was already seen by that count, yet still posts at its own moment. Late order sales against
  counts are decided with Square order lines (T3.05).
- `sales_daily` is gone; `recipe_components` stays for velocity until T2.15.
- **Built in T2.05-b** with migration `1da987c1c195`.
