# ADR-0056: Batch allocation

- **Status:** Proposed, 2026-11-18 (in the T2.06-a pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T2.06-a: BAT-FR-01, -05, -06, -08, DR-13..15, AC-BAT-01..04;
  DM-10; WF-12 step 6, moved from T2.05 by the owner on 2026-11-18)
- **Tasks:** T2.06 (`-a` allocation, `-b` batch history, batch counts, `batch_shortfall`, the
  batch-order setting); T2.07 (waste of a chosen batch), T2.08 (expiry queue), T2.09
  (production output batches), T2.11 (receiving)

## Context

A batch recorded what arrived; what it still held was worked out on every read by spreading the
item's quantity over its batches from the latest expiry back (`allocate_fefo`). Nothing tied a
movement to a batch, so a sale, a count or a disposal could not say which lot it took, expiry
losses were not traceable, and removing a date deleted the batch (C9, probe R6). DR-13..15 ask
that a batch's remaining be the sum of the movements that name it, that Σ batch remaining equal
the balance, and that an expired batch stay until something accounts for it. The ledger's
movements already carried `batch_id` and `allocation_mode` (ADR-0040), unused.

## Decision

1. **A movement names its batch; a batch holds the sum of its movements** (DR-15):
   `batch_remaining()` in `app/ledger/posting.py`. Nothing stores a remaining quantity.
2. **Inferred allocation inside the posting service:** a decrease posted without a batch
   (a sale, a count that found less, an adjustment) is split, while the item's balance is
   locked, across the item's batches at that location that still hold stock, in the item's
   order (`inventory_items.batch_order`): soonest expiry first (FEFO, the default) or first
   received first (FIFO) (BAT-FR-05). Each part is marked `inferred`; a movement with a batch
   chosen by a person is `explicit` (BAT-FR-06). What no batch holds leaves as undated stock (a
   shortfall). A reversal names the batches of what it undoes, so it gives the stock back to
   them. The rules are pure, in `app/transform/batches.py`.
3. **`batch_assignment`**, a new transaction kind and movement type: stock moved between a batch
   and undated stock, net zero for the item. It dates stock already on the shelf, takes a wrong
   date off, and gives existing batches their stock at migration. `assign_batch()` posts it.
4. **Writers** (`app/batches.py`): a receipt with a date creates its batch and receives into it
   (receiving and barcode scans, AC-BAT-01); dating stock on the shelf (`date_stock()`) takes
   only what no batch holds; removing a date (`undate_batch()`) moves what the batch holds back
   to undated stock, and the batch stays with its history (DR-24, probe R6 retired).
5. **Readers:** expiry positions, coverage and the items list read each batch's remaining from
   the ledger; undated stock is the recorded quantity less what the batches hold. Batches that
   hold nothing leave the expiry screens.
6. **DM-10** (migration `77e8f4d7dad6`): `batch_order`, `stock_batches.lot_code`, the new kind
   and type in the ledger's CHECKs, and one `batch_assignment` per existing batch giving it what
   it held as the product showed it (the old spread), keyed `migration:DM-10:batch:<id>`. No
   balance changes; the downgrade deletes the assignments.

## Alternatives considered

- **Keep deriving remaining from the balance:** sales and disposals could never name a lot, and
  an expired lot would vanish as soon as stock dropped (DR-14).
- **An allocation table beside the movements:** a second record of the same quantity to keep in
  step; TARGET_DOMAIN_AND_DATA_ARCHITECTURE has one movement per item, location and batch.
- **Allocate in each caller (consumption, counts, adjustments):** every writer would need the
  same rule and the same lock; in the posting service it holds for all of them.
- **Date stock on the shelf with an adjustment:** it would read as a correction in adjustment
  reports and review thresholds; an assignment moves nothing.

## Consequences

- Every decrease of an item that has batches takes from them; sales consumption (T2.05) is now
  allocated, marked inferred.
- A count that finds more than recorded adds undated stock; Σ batch remaining equals the balance
  until something undated arrives or a sale outruns the batches. Flagging that as
  `batch_shortfall` (DR-13) is T2.06-b.
- Item history shows an assignment as two rows that net to zero.
- **Built in T2.06-a** with migration `77e8f4d7dad6`. **T2.06-b:** batch history with running
  remaining (BAT-FR-12, AC-BAT-08), a batch's own count (BAT-FR-13), `batch_shortfall`, and the
  batch order on the item form.
