# ADR-0057: Batch history, batch counts and shortfall

- **Status:** Accepted, 2026-11-19 (owner: "Yes, merge when green"; PR #59, T2.06-b)
- **Decision ID:** none (implements T2.06-b: BAT-FR-05's per-item setting, BAT-FR-12, BAT-FR-13,
  DR-13, AC-BAT-04, AC-BAT-08)
- **Tasks:** T2.06; T2.07 (waste of a chosen batch), T2.08 (expiry queue actions)

## Context

ADR-0056 made a batch hold the sum of the movements that name it. Still missing: a view of those
movements (AC-BAT-08), a way to count one batch (BAT-FR-13), the setting that chooses FEFO or
FIFO for an item, and the exception DR-13 asks for when stock of a batch-tracked item is in no
batch. DOMAIN_MODEL §6 names `batch_shortfall` for consumption that found no batch; DR-13 and
AC-BAT-04 ask for it whenever Σ batch remaining differs from the balance.

## Decision

1. **Batch page** (`/app/expiring/batches/{id}`, `batch_view()`): what the batch holds, its
   status at its location's today (active, depleted, expired), and every movement allocated to
   it, newest first, with what it held after each and how its batch was chosen
   (`batch_history()` in `app/ledger/history.py`). Each lot on the expiry page links to it.
2. **Counting one batch** (`count_batch()`, `post_batch_count()`): the difference from what the
   batch holds, worked out under the item's balance lock, posts as one explicit
   `count_variance` on that batch, so the item's stock moves with it. A matching count posts
   nothing.
3. **`batch_shortfall`** (raised by the stock evaluation): for an item that tracks batches, any
   stock at a location that no batch holds opens one on the item at that location. That covers
   undated stock (a delivery with no date, a count that found more) and stock taken beyond every
   batch, with a title for each case. It resolves by itself once the stock is dated or counted
   back into line (AC-BAT-04).
4. **Batch order on the item form:** "Batch used first", soonest expiry or first received;
   audited with the item's other changes.

## Alternatives considered

- **Shortfall only when consumption outran the batches** (DOMAIN_MODEL §6's wording): undated
  stock of a batch-tracked item would break DR-13's conservation with nothing to show for it.
- **Count a batch by adjusting the item:** the item's count would not say which lot was short,
  and inferred allocation could take from a different batch.

## Consequences

- AC-BAT-04 holds: Σ batch remaining equals the balance for batch-tracked items, or a
  `batch_shortfall` is open.
- T2.07 records waste of a chosen batch as an explicit movement on it; the batch page shows it.
- **Built in T2.06-b**; no migration.
