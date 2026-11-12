# ADR-0042: Every stock writer posts through the ledger

- **Status:** Accepted, 2026-11-12 (owner: "Yes, merge it", to merging PR #38 with R6 and
  `sales_daily` handled as proposed)
- **Decision ID:** none (implements T1.18; DR-01, DR-07, DR-12, DR-25, AC-RCV-05; C-34, C-35; DM-7)
- **Tasks:** T1.18; T2.04 (freezes `sales_daily`); T2.06 (batch hard delete, probe R6); T4.16 (drops `qty_base`)

## Context

After T1.17 the ledger opened equal to `stock_items.qty_base`, but seven writers still changed
that column directly ([database audit §4](../audit/INVENTORY_PROJECT_DATABASE_AUDIT.md#4-inventory-update-logic)):
invoice and scan receipts, dashboard and scan counts, the derived recompute and Square's counts.
Every screen, report, alert and export reads `qty_base`; the column is dropped only at DM-16
(T4.16). ADR-0041 requires the drift since the opening balances to be posted before readers
switch.

## Decision

1. **`qty_base` is the balance's projection.** `post_transaction()` sets the stock row's
   `qty_base` to the new balance in the same transaction, under the balance's row lock. It is
   the column's only writer: a static test fails on any other assignment, `StockItem(qty_base=…)`,
   `.values(qty_base=…)`, `setattr` or SQL that sets it. Readers are unchanged.
2. **Receipts.** Applying an invoice first claims the document with a conditional update
   (`… WHERE status <> 'applied'`, DR-07), then posts one `receipt` transaction for all its
   applied lines (key `receipt:document:<id>`, unit cost from the line). A second request finds
   nothing to claim and is told "already applied" (AC-RCV-05). A scanned receipt posts its own
   `receipt` (key `receipt:scan:<uuid>`). A receipt no longer sets `last_counted_at`: it was the
   derived engine's re-anchor, and a delivery is not a count of the shelf.
3. **Counts are observations** (DR-12). `post_count()` locks the balance, computes counted −
   balance and posts one `count_variance`, or nothing when they match. The dashboard says what
   was posted; the scan page shows the variance.
4. **Gone:** `refresh_derived_quantities`, `derived_quantity`, `/app/recalculate` and the button,
   the sync's derived step, and the scan page's re-read from Square. `apply_provider_counts`
   records only when Square last reported a count (C-34); the webhook answers `seen`, not
   `updated`. Sales consume nothing until T2.05 (C-40).
5. **New stock rows open through the ledger.** Creating an item posts each location's starting
   quantity as `opening` (key `opening:<item>:<location>`, `post_opening()`); a zero posts
   nothing. The demo seed does the same.
6. **Catch-up migration** `ba3da8bb8b6f`: for each stock row whose balance differs from its
   quantity, one `adjustment` of the difference; for one with no balance, its `opening` and
   balance row. System actor, current cost, key `migration:catch-up:<item>:<location>`,
   re-runnable; the downgrade removes them and recomputes the balances.
7. **DM-7, in part.** Nothing writes `inventory_counts` or `inventory_events` any more (a test
   fails on constructing either); both stay readable as history. `sales_daily` keeps its writer
   (the Square sync) because velocity, cover, reorder and reports read it and nothing replaces it
   before T2.04, whose entry already makes it legacy there. **Decided by the owner
   (2026-11-12):** DM-7 is split, so counts and events freeze at T1.18 and `sales_daily` at
   T2.04.
8. **Probe R6 stays.** Batch removal deletes a date annotation, not stock, so no posting is
   involved; making batches never disappear needs batch columns (T2.06, which names C9 and R6).
   **Decided by the owner (2026-11-12):** R6 moves to T2.06.

## Alternatives considered

- **Readers switch to `stock_balances`** (a SplitField-style property or joins): every screen and
  SQL filter on `qty_base` changes, for a column DM-16 drops anyway. The projection keeps them
  as they are and is held equal by the same lock that serializes postings.
- **Count variance computed in the route:** a receipt landing between reading the balance and
  posting would be lost; the variance is worked out under the lock instead.
- **Lazy catch-up at first posting:** history appears whenever someone next touches the item;
  a migration posts it once, at a known time, with a test.
- **Square counts as `pos_correction`:** C-34 says Square never changes stock in R1.

## Consequences

- A test that builds stock with a quantity by hand calls `tests/ledger_helpers.opened()` so the
  ledger agrees with it.
- `TrackMode.DERIVED` remains as a label (stale-count alerts) until T2.05 replaces consumption.
- Built in T1.18 with migration `ba3da8bb8b6f`.
