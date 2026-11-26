# ADR-0079: Order-line ingestion

- **Status:** Proposed, 2026-11-20 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T3.05: POS-FR-05, POS-FR-07, SQ-10..13, SQ-18, PD-16;
  AC-POS-02 on the sandbox in T3.11, AC-POS-03 with T3.06's webhooks)
- **Tasks:** T3.05 (-a cursor and backfill, -b consumption hand-off); T3.06 to T3.08 build on it

## Context

The legacy sync re-read 60 days of completed orders every hour and summed them into one daily
sale per location and day (audit SQ1, SQ2, DB10). That lost the order, its lines, its modifiers
and its moment, and every hour repeated work that had not changed.

## Decision

1. **Orders are staged per connection** (`external_orders`, unique per connection and external
   id; `external_order_lines`, unique per order and line uid) as the POS last told them. A newer
   version replaces the staged order; an older or repeated one changes nothing.
   `app/order_ingestion.py` is their only writer. It is provider-neutral and reads through the
   adapter contract, whose orders now carry `updated_at`.
2. **A cursor per connection** (`order_cursors`) reads orders changed since the last read, less a
   ten-minute overlap so late updates are seen again (SQ-11). Square's search now filters on
   `updated_at` in every state (open, completed, cancelled), ten locations per request.
3. **A completed order at a tracked location is one sale** (`record_pos_order()` in
   `app/sales.py`), with a line per order line keyed by its uid. A resend or a new version is a
   revision of that sale, never a second one. Its consumption follows as the existing
   `consume_sale` job, at the order's completion moment (PD-16). Open and cancelled orders, and
   orders at untracked locations, are staged only (SQ-13). A custom amount is no sale line
   (SQ-18). A modifier with no mapping raises `unmapped_product` on the POS modifier, and mapping
   it clears that.
4. **Order lines take over from the daily totals at a business date.** The first read of a
   connection sets `lines_from` seven days back and voids the legacy daily totals from that date
   on (`retire_daily_totals()`), so those orders count once. Days before it keep their daily
   totals. The legacy hourly sync no longer reads orders. Its daily-total writers stay unused
   until T3.12 removes them.
5. **Everything runs as jobs.** `ingest_orders` (deduplicated per business) is queued every five
   minutes for each connected business (`orders_interval_minutes`) and by "Sync now". A revoked
   authorization marks the connection and is not retried. A passing failure fails the job, which
   tries again. Each read is a `SyncRun` of kind `orders`.

## Alternatives considered

- **Writing sales straight from the POS payload with no staging:** versions, reconciliation
  counts (T3.08) and later re-normalization all need the order as received.
- **Backfilling 60 days:** the daily totals already cover them, and voiding two months of totals
  would rewrite more consumption than the switch needs.
- **Keeping both paths for a while:** they would count the same orders twice.

## Consequences

- A location tracked later picks up orders from the cursor on. Its earlier days are not
  backfilled.
- An order cancelled after completion stays a sale until T3.07 voids it.
- Near-real-time ingestion needs T3.06's webhooks. Until then a sale arrives within the five-minute
  read.
