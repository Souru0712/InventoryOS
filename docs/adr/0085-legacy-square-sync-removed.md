# ADR-0085: Legacy Square sync removed; the hourly catalog read is a job

- **Status:** Proposed
- **Decision ID:** none
- **Tasks:** T3.12

## Context

Stage 3 rebuilt the Square path on the adapter contract:
- the external catalog and locations (ADR-0077);
- orders read from a cursor as jobs (ADR-0079);
- webhooks stored once and turned into jobs (ADR-0080);
- reconciliation (ADR-0082).

The old sync still ran beside it. Every hour, `sync_all_tenants` called `run_sync` for each
business in turn, inside the scheduler. `run_sync` read Square's catalog itself and wrote the
legacy rows next to the external catalog:
- sale items (`sold_items`);
- a stock item for every variation Square tracks, made directly rather than through `app/items.py`;
- Square's SKUs and barcodes on those items, and its prices on them;
- presence flags.

While write-back was on, it also read Square's counts. The webhook noted Square's counts on those
stock items.

The T3.11 sandbox run showed three problems with keeping both paths:
- **Stock items nobody asked for:** on the old seller the legacy rows made 18 stock items, each of
  which opened an `out_of_stock` exception.
- **Units flipping:** the adapter's catalog read did not ask for MEASUREMENT_UNIT objects, so
  each path set Saffron's unit differently on every read (`mg`, then not).
- **Serial scaling:** the per-tenant loop ran one business after another inside the scheduler
  (audit SQ2, SCL-06).

The POS daily-totals writer (`upsert_sales_daily`) and the order aggregator behind it had no
caller left since T3.05.

## Decision

The Square path is the adapter path only.

- **On request:** `run_sync` (after connecting, "Sync now", `cli sync`) is a forced
  `refresh_catalog()`.
- **Hourly:** the scheduler queues one `refresh_catalog` job per connected business
  (`queue_catalog_refresh`). Event-driven reads are debounced as before; a forced read is not.
- **Removed:** the legacy rows, Square's count reads, the count-webhook handling, the daily-totals
  writer and its aggregator, and `app/load/`.
- **Count webhooks:** an `inventory.count.updated` event is stored once and marked ignored, since
  Square's counts never change stock (C-34).
- **Units:** the adapter's catalog read now includes MEASUREMENT_UNIT objects.
- **Unmapped products:** `daily_sales()` keeps a POS line's own id when the line is neither one of
  the business's sizes nor a legacy sale item. "Sold without a recipe" and the completeness note
  name and count each unmapped Square product from the external catalog.

## Alternatives considered

- **Keep `run_sync` hourly with only the catalog part:** still one business after another inside
  the scheduler, and a second code path beside the event-driven job.
- **Keep writing `sold_items` from the external catalog:** C-36 and DM-9/DM-13 moved products and
  POS products to their own tables; a second copy only drifts.
- **Keep noting Square's counts:** nothing reads the note since C-34, and reading counts needs
  scopes InventoryOS no longer asks for (SQ-02).

## Consequences

- No code path writes POS daily totals except the demo seed, makes a sale item except the demo
  seed, makes a POS-counted stock item, or reads Square's counts. A static test enforces it
  (`tests/test_legacy_sync_removed.py`).
- **History stays and is still read:** existing sale items, POS-counted stock items, their
  identifiers and prices, and earlier daily totals.
- **No longer copied from Square:** barcodes and prices onto stock items. Stock items are the
  business's own (C-38). A Square product reaches stock only through a mapping and a recipe.
- **Write-back stays for R1.1:** `push_counts_to_square`, `push_adjustments_to_square`, off until
  then.
- **Catalog freshness:** a missed catalog event is recovered within the hour by the job.
- **Built:** T3.12, on the branch.
