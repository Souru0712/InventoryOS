# ADR-0053: Sales model and sources

- **Status:** Proposed, 2026-11-14 (in the T2.04 pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T2.04: POS-FR-17, -18, DR-29..31, DR-34; DM-7's `sales_daily`
  freeze, split to T2.04 by the owner on 2026-11-12)
- **Tasks:** T2.04; T2.05 (consumption reads lines), T2.12 and T2.13 (manual entry and import
  write through it), T3.05 (Square order lines replace the daily feed), T3.10 (mode switching)

## Context

Sales lived in `sales_daily`: one row per Square sale item, location and day, written by the
Square sync. Velocity, cover, reorder, reports and the completeness checks read it. A business
with no POS had no way in, and two sources could both be counted for the same day. DR-30 needs
one model for every source, and DR-31 needs one source of record per location and business date.
DM-7 freezes `sales_daily` at T2.04.

## Decision

1. **Six tables** (migration `9c4e1b7a2d05`):
   - **`sales_sources`:** location; kind (`pos_adapter`, `manual`, `file_import`, `api`,
     `inventoryos_pos`); provider; business days from and to; and whether it is the source of
     record.
   - **`sales_transactions`:**
     - **What it is:** a sale, or a whole day entered at once, from one source.
     - **Fields:** source reference, revision, `occurred_at` or a business date, status
       (completed, corrected, voided), and gross, discount, net and tax. A Standalone business
       fills only some.
     - **Unique:** (source, reference, revision).
   - **`sales_transaction_lines`:**
     - **What was sold:** an owned variation, or a POS item not mapped yet, kept by reference so
       it can be mapped and reprocessed.
     - **Fields:** quantity, money, a consumption status, and the recipe version used (filled
       in by T2.05).
     - **`sales_line_modifiers`:** the modifiers chosen on each line, with how many.
   - **`sales_returns` and `sales_return_lines`:**
     - one return per (source, reference);
     - each line points at the sold line where known;
     - a return never restocks by itself (DR-26).
2. **One writer:** `app/sales.py` records sales, and a test fails on any other writer.
   - **Business date:** a transaction's business date is its location's for `occurred_at`
     (ADR-0027), or the day given for a daily entry.
   - **Origin:** its origin is its source's kind, set at recording and never changed (DR-34).
   - **Resends:** a resend that changes nothing adds nothing. A changed one is the next revision,
     and the one before is marked corrected.
3. **One source of record per location and business date** (DR-31):
   - Adding a source of record whose days overlap another is refused. A source is stopped
     before another takes over.
   - **Other sources:** a source that is not of record, such as a POS left connected while sales
     are entered by hand, can still send sales. They are kept with their origin, their lines are
     skipped, they are never counted, and `sales_source_overlap` is raised for that location and
     day.
4. **One reader:** velocity, cover, Square-tracked depletion, reports, the dashboard's
   completeness banner and the products page's sold-without-a-recipe list all read
   `daily_sales()`. It sums the latest completed revision of each counted transaction per
   location, day and thing sold. Nothing reads a source's own records (DR-30).
5. **The freeze** (DM-7):
   - **Writers moved:** nothing writes `sales_daily` (a static test). The legacy Square sync
     and the demo seed now record each day's sums as one daily transaction per location (with
     reference `daily:<date>`), from a POS source created on first use.
   - **Older days arriving later:** if a later feed brings days earlier than the source's start,
     and no other source of record covers them, the source is extended back.
   - **Until T3.05:** this bridge keeps every Square figure flowing until order-line ingestion
     replaces it, and T3.12 removes the legacy sync.
6. **History** (backfill): each location's `sales_daily` days became daily transactions from a
   POS source of record that starts on its first sales day.
   - **Lines:** one per sold item with its units and money, linked to the owned variation where
     T2.01 made one.
   - **Nothing replayed:** nothing is replayed into the ledger. "Not replayed" in the data
     migration roadmap means no consumption is posted for the past.
   - **Parity checked:** on the demo database the 126 item-days read through `daily_sales()`
     equal `sales_daily` exactly, and velocity is unchanged.
   - `sales_daily` is contracted at T2.05.

## Alternatives considered

- **Stop writing `sales_daily` without a new writer:** Square sales would stop reaching
  velocity and reorder until T3.05, a regression of some fifteen tasks.
- **Read `sales_daily` for days before a cutover and the new model after:** two read paths, and
  DR-30 wants one.
- **Refuse a sale from a source not of record:** the sale would be lost. Keeping it flagged
  shows what arrived without counting it twice.

## Consequences

- **T2.05** consumes `pending` lines of counted transactions, records the recipe version, and
  raises `unmapped_product` for lines with no variation.
- **T2.12 and T2.13** add manual and file sources through `add_source()` and `record_sale()`.
- **T3.10** switches modes with `stop_source()` and `add_source()`.
- **Built in T2.04** with migration `9c4e1b7a2d05`.
