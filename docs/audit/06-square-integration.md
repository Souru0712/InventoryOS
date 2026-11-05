# 06 — Square integration assessment

Everything below is verified against mocked HTTP. **Nothing has run against a Square sandbox
seller** (`NEEDS-YOU.md` §1); OAuth, webhook delivery, rate limits and real payload shapes remain
unproven.

## Data flow [CODE]

| Direction | What | How | Cadence |
|---|---|---|---|
| In | Locations, catalog (items, variations, categories, measurement units) | `POST /v2/catalog/search`, paginated | Hourly + on `catalog.version.updated` (60 s debounce) |
| In | Counts for inventory-tracked variations | `POST /v2/inventory/counts/batch-retrieve` (250/batch) | Hourly + `inventory.count.updated` webhook |
| In | Sales | `POST /v2/orders/search`, `COMPLETED` only, last 60 days by `closed_at`, aggregated to `sales_daily` | Hourly |
| Out | Delivery received | `ADJUSTMENT NONE → IN_STOCK`, idempotency key `receipt-{document_id}` (scan: random uuid) | On apply |
| Out | Physical count of a Square-owned SKU | `PHYSICAL_COUNT`, random uuid key | On count |
| Out | Derived weight/volume | Never pushed | — |

Ownership split, as implemented: Square owns counts of its inventory-tracked SKUs
(`track_mode=provider`); StockPulse owns weight/volume items (`derived`), un-tracked items
(`manual`), batches, expiry, alerts. This matches the brief.

## The eight known risks

| # | Risk | Verdict | Evidence |
|---|---|---|---|
| 1 | Invoice receiving overwrites Square inventory | **Fixed** — receiving sends an `ADJUSTMENT` delta, never a count. **But** if the push fails, the delivery is silently lost at the next sync (C4) | `app/orchestration/receiving.py:387-402`, `app/orchestration/sync.py:258`; `tests/test_square_writeback.py::test_a_delivery_cannot_erase_a_sale_that_happened_just_before_it` [RUN]; probe R3 [RUN] |
| 2 | Counts and deliveries are different event types | **Partly.** Distinct at Square (`PHYSICAL_COUNT` vs `ADJUSTMENT`). Locally both are `inventory_counts` rows told apart by a free-text `source`, and a delivery re-anchors derived stock as though it were an observation (root of C2) | `app/orchestration/receiving.py:331`, `app/web/dashboard.py:413` |
| 3 | Duplicate Square events deduct twice | **No, today.** `inventory.count.updated` carries absolute quantities, so re-applying is a no-op; older counts are dropped by `calculated_at` vs `last_synced_at`. Sales are re-aggregated and upserted, so re-reads are idempotent. No event-id record is kept for inventory webhooks — this becomes a real risk the moment sales are processed as deltas (R2) | `app/web/webhooks.py:49-81`, probe R5 [RUN], `tests/test_webhooks.py` |
| 4 | Refunds restore ingredients | **No — refunds are ignored entirely.** Only `line_items` of `COMPLETED` orders are read; return line items and refund webhooks never reach product code (refund webhooks go to the snack-box handler only). Inventory is therefore not wrongly restored, but net sales and velocity are overstated and no refund record exists | `app/transform/square_normalize.py:138`, `app/web/webhooks.py:129-143` |
| 5 | Remakes and waste consume inventory independently of the sale | **Not modelled.** No waste events exist. A remake rung through Square consumes a recipe as a sale; one not rung is invisible | — |
| 6 | Historical movements can be silently edited/deleted | **Yes.** Balance overwritten without a movement on sync, recompute, invoice receipt and manual count; batches hard-deleted (probe R6 [RUN]); alias silently repointed (`receiving.py:422-424`) | [04](04-database-and-ledger.md#inventory-ledger-assessment) |
| 7 | Tenant isolation enforced | **Yes in the app layer** for all 53 product routes; webhook merchant routing ambiguous (S7) | [05](05-security.md) |
| 8 | PostgreSQL behaviour actually tested | **Yes, in this audit** — see [07](07-tests.md) | [RUN] |

## Other integration findings

| ID | Finding | Evidence |
|---|---|---|
| SQ1 | Sales arrive only via the hourly sync; there is no `order.*` / `payment.*` webhook for the product. Derived stock is up to an hour stale. Landing copy "Recipes consume stock as the orders land" overstates this | `app/web/templates/marketing/landing.html:213` |
| SQ2 | Each hourly sync re-reads **60 days of orders** for every tenant, sequentially across tenants (`sync_all_tenants`). A café at 300 orders/day ≈ 90 paginated calls per hour per tenant. Fine for 21 tenants; replace with an incremental cursor (`updated_at` since last run) before ~50 | `app/orchestration/sync.py:51,171`, `app/orchestration/scheduler.py:37` |
| SQ3 | Square write-back happens inside the HTTP request, before the DB commit, with `time.sleep` back-off; failures swallowed (C4, C8) | `app/providers/square/client.py:52-71` |
| SQ4 | Count push uses a random idempotency key, so a client retry cannot be de-duplicated by Square. Harmless for an absolute count, but inconsistent with receipts | `app/orchestration/sync.py:250` |
| SQ5 | No disconnect / revoke flow, no UI for an expired or revoked token beyond "last failure" | — |
| SQ6 | Over-scoped OAuth (S6); merchant attachable to multiple tenants (S7) | — |
| SQ7 | Square-inactive locations still count toward the plan's location allowance (ordered by id) | `app/orchestration/subscriptions.py:43` |
| SQ8 | Variation units: Square `METRIC_MILLIGRAM` maps to `g` — a milligram-priced item would be off by 1000× | `app/transform/square_normalize.py:17` |
| SQ9 | Webhook handler processes synchronously; a slow catalog re-read risks Square's 10 s delivery timeout and retries | `app/web/webhooks.py:145-160` |

## Adapter architecture

`provider` columns and a normalize contract (`app/transform/square_normalize.py` produces plain
rows that `app/load/upsert.py` consumes) are a sufficient seam. A formal `POSAdapter` interface
should wait until a second POS is funded, per the brief and `README.md` ("Deliberately not built").

## Recommended changes (in order)

1. Outbox + retry for every Square write; local commit first (R3).
2. Order-level ingestion with `closed_at`, location and return linkage (R2), incremental cursor
   (SQ2), event-id dedupe for any delta processing.
3. Refunds: record returns as their own rows; **never** auto-restore ingredients; let the
   operator classify a returned item as restock or waste.
4. Separate the snack-box OAuth app or drop its scopes from the product (S6).
5. Unique active connection per merchant across tenants (S7).
6. Run the sandbox script in `NEEDS-YOU.md` §1 end-to-end.
