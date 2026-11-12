# ADR-0041: Opening balances

- **Status:** Accepted, 2026-11-12 (owner: "Yes, merge it"; PR #37)
- **Decision ID:** none (implements T1.17; DR-03; DM-6 backfill under C-35 and ADR-0040)
- **Tasks:** T1.17; T1.18 (routes the writers through the ledger and catches it up)

## Context

The ledger (ADR-0040) is empty. The product's quantities live in `stock_items.qty_base`, the
number every screen shows. The ledger must begin equal to it, without loss.

## Decision

1. **A data migration** (`a6eec9216078`) posts, for every stock row (one item at one location)
   with a non-zero quantity, one `opening` transaction holding one `opening_balance` movement:
   - its quantity is exactly that row's quantity, so a negative one stays negative;
   - its unit cost is the row's current cost;
   - it is posted by the system, at the time of the migration;
   - its key is `migration:opening:<item>:<location>`.
   Every pair gets its `stock_balances` row, zero quantities included, so a zero needs no
   movement (DR-04).
2. **Re-runnable**: a key or balance row already there is skipped, so running the backfill again
   posts nothing.
3. **SQL, not the posting service**: a migration must not depend on application code that keeps
   changing. It follows the service's rules: signs, four places, one transaction per event.
4. **Downgrade** removes the opening postings and the balances; the stock rows are untouched.
5. A legacy cost below zero opens at zero: the ledger keeps costs at zero or above (DR-19), and
   one slip should not stop the migration.

## Alternatives considered

- One transaction per business holding every opening movement: one key could not say which
  item and location it opened, and a re-run could not skip just the missing ones.
- Movements for zero quantities: a zero movement is refused by design (DR-04).
- Use the last count time as the opening time: counts and sales after it are already in the
  quantity, so the opening is "as of now".

## Consequences

- Until T1.18 the screens still show `stock_items.qty_base` and the old writers still change it,
  so the ledger is a snapshot as of this migration. T1.18 must post the difference for any pair
  that moved in between before readers switch, and new stock rows must post their opening
  quantity through the ledger.
- Built in T1.17 with migration `a6eec9216078`; the catch-up and the writers' move followed in
  T1.18 (ADR-0042, migration `ba3da8bb8b6f`).
