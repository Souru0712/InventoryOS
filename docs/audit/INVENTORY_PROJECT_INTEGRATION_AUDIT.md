# Inventory project — integration audit (Square)

Phase 2, 2026-11-04. Read-only. Requirements baseline:
[INTEGRATION_REQUIREMENTS](../product/INTEGRATION_REQUIREMENTS.md) and spec Module 9 (POS-FR-01..27).
Detail on data flows and the eight known risks: baseline [06-square-integration](06-square-integration.md),
re-verified where marked.

> **Status of evidence.** Every Square behaviour below is verified only against mocked HTTP and
> recorded payloads. **Nothing has run against a Square sandbox seller or a live merchant.**
> `scripts/send_test_webhook.py` (untracked) and the sandbox-seeding script exist; their use is not
> recorded as a completed run.

## 1. Inventory of the integration [CODE]

| Concern | Implementation | Evidence |
|---|---|---|
| Authentication | OAuth 2 authorization-code flow; `state` checked; token exchange and refresh | `app/providers/square/oauth.py:50-95`, `app/web/dashboard.py:491-530` |
| Scopes | `MERCHANT_PROFILE_READ, ITEMS_READ, INVENTORY_READ, INVENTORY_WRITE, ORDERS_READ` **plus `ORDERS_WRITE, PAYMENTS_READ, PAYMENTS_WRITE` for the private snack-box module** — every merchant is asked for all eight (S6) | `app/providers/square/oauth.py:12-22` |
| Credential storage | Access and refresh tokens Fernet-encrypted (`access_token_enc`, `refresh_token_enc`) with `ENCRYPTION_KEY` from the environment | `app/providers/square/oauth.py:29-47`, `app/models.py:142-155` |
| Token refresh | Refreshed when within 7 days of expiry (`REFRESH_MARGIN`) at the start of a sync; failure raises `NotConnected` | `app/orchestration/sync.py:52,72-90` |
| Disconnect / revoke | **Missing** — no route, no revoke call (SQ5) | route list |
| Environments | `SQUARE_ENV` = `sandbox` (default) or production base URLs | `app/config.py:29,98-100` |
| API endpoints used | `merchants`, `locations`, `catalog/search`, `inventory/counts/batch-retrieve`, `orders/search`, `inventory/changes/batch-create`, `online-checkout/payment-links` (snack box) | `app/providers/square/client.py:92-170` |
| Webhook verification | HMAC-SHA256 over notification URL + raw body, constant-time compare; endpoint refuses events until a signature key is configured | `app/providers/square/webhooks.py`, `app/web/webhooks.py:110-121`; `tests/test_webhooks.py` [RUN] |
| Webhook events handled | `inventory.count.updated` (absolute counts, stale ones dropped); `catalog.version.updated` (re-read catalog, 60 s debounce); `payment.*`/`refund.*` **only for snack-box tenants** | `app/web/webhooks.py:129-175` |
| Merchant → tenant routing | First `ProviderConnection` matching the merchant id; the same merchant can be attached to two tenants (S7) | `app/web/webhooks.py:38-46`; probe PA4 [RUN] |
| Data synchronization | Hourly `run_sync` per connected tenant: locations, catalog, counts, **last 60 days of COMPLETED orders** aggregated to `sales_daily`, then derived recompute and alert evaluation | `app/orchestration/sync.py:133-206`, `app/orchestration/scheduler.py:37-52` |
| Product mapping | Square variation → `sold_items` automatically; Square-tracked SKUs auto-linked to stock items (`provider_variation_id`); everything else needs recipes, which only the CLI writes | `app/load/upsert.py:61-134`, `app/cli.py:351` |
| Order handling | Daily aggregates per variation × location; no order, line, time of day or modifier kept (DB10) | `app/transform/square_normalize.py`, `app/load/upsert.py:204-240` |
| Refunds | **Ignored**: return line items and refund webhooks never reach product code; net sales overstated; no stock restore (safe) | `app/transform/square_normalize.py:138` |
| Voids / cancellations | Only `COMPLETED` orders are read, so voids before completion consume nothing; a completed-then-voided order is not handled | same |
| Discounts | Captured in `gross_cents`/`net_cents`; never affect consumption | `app/models.py:280-286` |
| Duplicate-event prevention | Inventory webhooks: no event-id record; safe only because counts are absolute (probe R5). Sales: idempotent re-aggregation | probe R5 [RUN] |
| Idempotency on writes | Receipt adjustments keyed `receipt-{document_id}`; count pushes and scan receipts use random UUIDs (SQ4) | `app/orchestration/sync.py:250-300` |
| Error handling | Sync failures recorded on `sync_runs` and logged; **write-back failures swallowed and later overwritten** (C4) | `app/orchestration/sync.py:117-123,188-195`, `app/orchestration/receiving.py:387-402`; probe R3 [RUN] |
| Retry | HTTP client retries 429/5xx with `time.sleep` back-off inside the request (blocks the event loop in `async` routes, C8); no queued retry for failed writes | `app/providers/square/client.py:15,52-71` |
| Rate limiting | Reactive only (429 retry). Each hourly sync re-reads 60 days of orders for every tenant sequentially (SQ2) | `app/orchestration/sync.py:51,171` |
| Logging | Python logging (plain text, no request id, O4); `sync_runs` rows; Sentry when configured | `app/main.py:37`, `app/monitoring.py` |
| Persistence | `provider_connections`, `sync_runs`, `locations`, `sold_items`, `stock_items.provider_variation_id`, `sales_daily`, `inventory_counts` | `app/models.py` |
| Unit mapping | `METRIC_MILLIGRAM` mapped to `g` — 1000× error for milligram items (SQ8) | `app/transform/square_normalize.py:17` |

## 2. Against the blueprint (Module 9)

| Requirement | Status | Gap |
|---|---|---|
| POS-FR-01 OAuth, encrypted tokens, refresh, disconnect, revoked-token detection | Partial | Disconnect and revocation handling missing; scopes over-broad |
| POS-FR-02 Connection status page | Partial | Dashboard shows last sync/failure; no location mapping view |
| POS-FR-03 Map Square locations to InventoryOS locations | Partial | Locations are *created from* Square, not mapped; plan limit flips `is_active` |
| POS-FR-04 Catalog sync incl. modifiers → ExternalProduct/ExternalModifier | Partial | Variations only; no modifiers; sold items double as products |
| POS-FR-05 Order-line ingestion with occurred_at, location, modifiers; webhooks + reconciliation | **Missing** | Daily aggregates, hourly only, no `order.*` webhook (SQ1) |
| POS-FR-06 Mapping UI | **Missing** | None |
| POS-FR-07 Consumption per order line, idempotent on line identity | **Missing** | Consumption recomputed from daily totals (C2, C3) |
| POS-FR-08 Refunds recorded, no auto-restore | Partial | Not restored (good), but not recorded |
| POS-FR-09 Voids | Partial | Pre-completion voids fine; completed-then-voided unhandled |
| POS-FR-10 Sync history, errors, retry | Partial | `sync_runs` exists; no retry of failed writes; failures not surfaced as exceptions |
| POS-FR-11 Duplicate prevention by event id and order-line identity | **Missing** | Not needed by today's absolute model; required once sales are deltas |
| POS-FR-12 Daily reconciliation of order counts | Partial | Hourly full re-read is a crude reconciliation; no per-day count comparison |
| POS-FR-14 Reprocess after mapping fix | **Missing** | — |
| POS-FR-15 Adapter boundary: business logic consumes normalized events only | Partial | `square_normalize` → `load/upsert` seam exists; orchestration, webhooks, scheduler, models default to `"square"` |
| POS-FR-26 Discounts never change consumption | Implemented | Consumption uses units, not money |
| POS-FR-27 Integration health per connection | Partial | Last sync and failure only |

## 3. Does it assume Square is the only POS? [CODE]

**Yes, in practice; partially, in design.**

| Square assumption | Where |
|---|---|
| `provider` columns default to `"square"` | `app/models.py:148,163,179,313` |
| Locations, sellable items and sales can only be created by the Square sync (or CLI seed) | `app/load/upsert.py:43,74,233`; probe PA1 |
| `active_connection(provider="square")`, `run_sync` builds a `SquareClient` directly | `app/orchestration/sync.py:59,115,147` |
| The scheduler syncs — and therefore evaluates alerts for — only tenants with an active connection | `app/orchestration/scheduler.py:37-45`, `app/orchestration/sync.py:182` |
| Receiving and scanning push to Square for `PROVIDER` items from orchestration code | `app/orchestration/receiving.py:387-402`, `app/orchestration/scanning.py` |
| `TrackMode.PROVIDER` is the default and lets Square overwrite balances | `app/models.py:212`, `app/load/upsert.py:197` |
| Onboarding and dashboard copy: "Connect Square" is the only path | `app/web/templates/app/dashboard.html:19-37,90-92` |

What is provider-neutral already: the `provider` + external-id columns, the normalize → load
contract (plain dict rows), encrypted connection storage, `sync_runs`.

## 4. What must change for provider independence

Ordered by dependency; sizes are rough [INFERENCE].

| # | Change | Blueprint ref | Size |
|---|---|---|---|
| 1 | **SalesSource + SalesTransaction/Line/Return model** shared by all modes; consumption engine reads only this | POS-FR-17, DR-29..31 | L |
| 2 | **Adapter interface** (`connect`, `sync_catalog`, `fetch_orders(since)`, `parse_webhook`, `revoke`) returning normalized types from INTEGRATION_REQUIREMENTS §2; Square becomes the first implementation | POS-FR-15 | M |
| 3 | Order-line ingestion with incremental cursor, `order.*` webhooks, event-id + line-identity dedupe | POS-FR-05, -11 | M |
| 4 | **Outbox** for every Square write; local commit first; retry with surfaced failures | POS-FR-10, REL-04 | M |
| 5 | Ledger owns stock; Square counts become `pos_correction` observations, not overwrites (C-34; off in R1, outbox in R1.1) | DR-01, G12 | M |
| 6 | ExternalProduct/ExternalModifier + mapping UI + reprocess | POS-FR-04, -06, -14 | M |
| 7 | Scope split: product app asks only for read + inventory scopes; snack-box scopes leave with the snack-box module | S6 | S |
| 8 | Unique active connection per merchant; disconnect/revoke; token-health surfacing | S7, POS-FR-01, -27 | S |
| 9 | Move webhook processing and sync off the request path (job/outbox) | SQ9, C8 | S–M |

**Not needed now:** a second POS adapter (the brief forbids building one in this phase), a message
broker, or microservices.

## 5. Reliability verdict

| Question | Answer |
|---|---|
| Will a Connected customer's stock be right? | **No, today** — C2/C3 make derived stock wrong for the target customer; C4 can erase a delivery |
| Can it recover from a missed webhook? | Yes, hourly full re-read (verified with mocks) |
| Can it recover from a failed write to Square? | **No** — swallowed and overwritten (C4) |
| Is it ready for a real merchant? | No: never run against a sandbox seller; scopes would draw review questions; no disconnect |
| Is the code a good base for the Connected adapter? | **Yes for the HTTP client, OAuth, webhook verification, normalization and encrypted storage; no for the sync orchestration and sales model**, which must be rebuilt around order lines and the ledger ([reuse matrix](REUSE_ADAPT_REBUILD_MATRIX.md)) |
