# Integration Requirements

Authoritative source for **external system integration**: POS (Square first), generic sales
sources, billing, email, storage and assisted extraction. Module summary: M9 in
[PRODUCT_SPECIFICATION](PRODUCT_SPECIFICATION.md#module-9--sales-sources-and-pos-integrations-pos).

## 1. Principles

| ID | Principle |
|---|---|
| INT-P-01 | **Square is an external transaction source, not the inventory ledger** [CONFIRMED]. InventoryOS's ledger is authoritative for ingredient stock |
| INT-P-02 | **Adapters**: provider code translates to/from normalized types; business logic never reads provider payloads [CONFIRMED] |
| INT-P-03 | Keep four things distinct [CONFIRMED]: imported transaction (ExternalOrder) · expected consumption (ledger `sale_consumption`) · actual physical stock (counts) · verified discrepancies (posted, reviewed variances) |
| INT-P-04 | Every inbound event is idempotent (event id + entity identity); every outbound write has an idempotency key and goes through an outbox (local commit first) |
| INT-P-05 | Webhooks are hints; scheduled reconciliation is the guarantee. Missing a webhook never loses data |
| INT-P-06 | Integration failures never block in-store operations; they surface as exceptions |
| INT-P-07 | Least-privilege OAuth scopes; only what the enabled features use |
| INT-P-08 | A sold product may map to a recipe of many ingredients, with variations, modifiers and substitutions — never assume 1 POS item = 1 inventory item [CONFIRMED] |
| INT-P-09 | **Square is a potential first integration, not the permanent architectural foundation** [CONFIRMED]; no Square concept appears outside its adapter (DR-29) |
| INT-P-10 | Every sale from every source — POS adapter, manual entry, file import, API, future native POS — enters as a SalesTransaction carrying its origin (DR-30, DR-34) |

## 2. Adapter contract (normalized types)

A POS adapter implements these capabilities; anything it cannot provide is declared unsupported.

| Capability | Normalized output / input | Required |
|---|---|---|
| `authorize` / `refresh` / `revoke` | Connection credentials, scopes, external merchant id | Yes |
| `list_locations` | `{external_location_id, name, timezone, address, active}` | Yes |
| `sync_catalog(since?)` | `NormalizedProduct {external_item_id, external_variation_id, name, variation_name, category, active, price}`; `NormalizedModifier {external_modifier_id, list_name, name}` | Yes |
| `fetch_orders(since cursor)` | `NormalizedOrder {external_order_id, version, state, occurred_at, location, lines[], refunds[]}`; `NormalizedOrderLine {line_uid, external_variation_id, name, qty, modifiers[{external_modifier_id, name, qty}], note}` | Yes |
| `parse_webhook(raw, headers)` | Verified `NormalizedEvent {event_id, type, entity_ids, occurred_at}` or rejection | Yes (if provider has webhooks) |
| `fetch_refunds(since)` | `NormalizedRefund {external_refund_id, order_id, lines[{line_uid, qty}], amount, status}` | Yes |
| `push_inventory(commands)` | Absolute count or adjustment per external variation with idempotency key | Optional, R1.1 (C-34) |

Non-POS sources produce the same normalized sales model: daily sales entry (STD-FR-02) and sales
file import (STD-FR-03) in **R1**; an authenticated sales API (POS-FR-16) in R1.1 for first-party
ordering systems such as JuJu's and for unsupported POS systems. See §9.

## 3. Square integration (R1)

### 3.1 Authorization and connection

| ID | Requirement |
|---|---|
| SQ-01 | OAuth code flow (not personal access tokens) with `state` CSRF protection; tokens encrypted at rest; refresh before expiry; detect revoked authorization and mark connection `revoked` with an exception |
| SQ-02 | Scopes: read merchant profile, catalog and orders; refunds/payments read only if needed for refund detail; inventory write **only** when write-back is enabled (R1.1, C-34). No payment-write or order-write scopes (existing app over-scoped: audit S6) |
| SQ-03 | One active connection per Square merchant + location across all organizations (DR-22) |
| SQ-04 | Connection page: merchant name, mapped location, status, last successful catalog/order sync, last error, reconnect, disconnect (revokes token, stops ingestion, keeps history) |
| SQ-05 | Location association: choose which Square locations to track, up to the location entitlement (Complete is per location); others listed as not tracked |

### 3.2 Catalog

| ID | Requirement |
|---|---|
| SQ-06 | Import items, variations, modifier lists/modifiers, categories into ExternalProduct/ExternalModifier |
| SQ-07 | Incremental sync on catalog change webhook (debounced) + scheduled full refresh daily |
| SQ-08 | Deleted/archived Square variations marked inactive; mappings kept for history |
| SQ-09 | Square measurement units are informational; InventoryOS units come from recipes/items (existing app mis-maps milligrams: audit SQ8) |

### 3.3 Orders and consumption

| ID | Requirement |
|---|---|
| SQ-10 | Ingest at **order-line level** with `occurred_at` = order completion/closed time, location and line uid; never daily aggregates (existing-app defects C2/C3 came from aggregation) |
| SQ-11 | Incremental cursor (updated-since) per connection; overlap window to catch late updates; no 60-day full re-read per hour (audit SQ2) |
| SQ-12 | Webhook-triggered fetch for near-real-time consumption (target: posted within 5 minutes of completion); scheduled reconciliation every ≤ 60 minutes |
| SQ-13 | Only completed orders consume (PD-16 confirms trigger); open orders stored without consumption; cancelled before completion → no consumption |
| SQ-14 | Order changed after completion (new version) → reverse prior consumption for changed lines and re-post, keyed by version |
| SQ-15 | Refunds/returns: record ExternalRefund linked to order and lines; net out of sales metrics; **no automatic stock restore** (DR-26); classification UI in R1.1 |
| SQ-16 | Unmapped variation or modifier → line stored, `unmapped_product` exception, reprocessable (WF-28) |
| SQ-17 | Daily reconciliation per business day (location time zone): Square completed-order count and line count vs ingested; mismatch → `pos_reconciliation_mismatch` |
| SQ-18 | Non-inventory lines (gift cards, custom amounts) recognized and skipped without exceptions; configurable "ignore" mapping |

### 3.4 Write-back to Square (R1.1, C-34)

The existing app pushes receipts and counts to Square because the owner's original pain was
online orders accepted for sold-out items. It is off in R1 (C-34); when it returns in R1.1:
- Only for items Square tracks inventory on, and only as **availability/counts derived from
  InventoryOS** — InventoryOS remains authoritative.
- Through the outbox (`OutboundCommand`): local commit first, then send with an idempotency key
  derived from the source record; retry with back-off; dead-letter → exception. Never swallow a
  failed write (existing-app defect C4).
- Square-originated count changes on those items are recorded as `pos_correction` movements,
  never silent overwrites.

### 3.5 Webhooks

| ID | Requirement |
|---|---|
| SQ-19 | Verify signature over the exact raw body and notification URL; reject unsigned/invalid |
| SQ-20 | Record every event by provider event id (SyncEvent); duplicate → acknowledge, no work |
| SQ-21 | Acknowledge fast (< 2 s); do fetch/processing in a background job (Square retries slow endpoints; audit SQ9) |
| SQ-22 | Route by merchant + location to exactly one connection |
| SQ-23 | Candidate event types — **verify names against Square's current API reference at implementation**: catalog version updated; order created/updated (or fulfillment updated); refund created/updated; OAuth authorization revoked; inventory count updated (only if write-back) |

### 3.6 Sync history, errors, retry

| ID | Requirement |
|---|---|
| SQ-24 | SyncRun log per job: kind, window, counts fetched/ingested/consumed/skipped, duration, status, error |
| SQ-25 | Transient errors (429, 5xx, timeouts) retried with exponential back-off and jitter, respecting rate-limit headers; permanent errors (401, 403) mark connection error + exception |
| SQ-26 | Any connection without a successful sync for > 2 h during business hours → `sync_failure` (warning); > 24 h → critical |
| SQ-27 | No blocking HTTP calls inside request handlers that serve users (existing-app defect C8) |

## 4. Data mapping

| Square | InventoryOS | Notes |
|---|---|---|
| Merchant | IntegrationConnection.external_merchant_id | — |
| Location | BusinessLocation (via connection) | One in R1 |
| Catalog item + variation | ExternalProduct → ProductMapping → ProductVariation → Recipe | Variation is the mapping grain |
| Modifier | ExternalModifier → Modifier (effect add / remove / substitute / scale / none) | JuJu-style "Light ice" is a scale modifier |
| Order | ExternalOrder | occurred_at = completion time |
| Order line item | ExternalOrderLine | line uid is the idempotency grain |
| Return / refund | ExternalRefund | No auto restock |
| Inventory count (tracked variations) | Only with write-back: compared to InventoryOS → `pos_correction` | — |

## 5. Idempotency keys

| Event | Key (InventoryTransaction unless stated) |
|---|---|
| Webhook received | `sq:event:<event_id>` |
| Sales consumption | `sale:<transaction_id>:r<revision>`; the SalesTransaction itself is unique by `pos:square:<order_id>:v<order_version>` |
| Consumption reversal (order change/void) | `reversal:sale:<transaction_id>:r<old_revision>` |
| Refund recorded | `pos:square:refund:<refund_id>` |
| Outbound count/adjustment | `out:<source_type>:<source_id>:<item_id>` |
| Receiving | `receiving:<id>` |
| Waste | `waste:<id>` |
| Count | `count:<id>` |
| Production | `production_order:<id>` |
| Standalone daily sales / import | SalesTransaction unique by `manual:<location>:<business_date>` or `file_import:<file_hash>:<business_date>`; consumption `sale:<transaction_id>:r<revision>` |
| Direct usage | `usage:<id>` |
| Transfer | `transfer:<id>:out` / `transfer:<id>:in` |

## 6. Failure modes

| Failure | Behaviour | Surfaced as |
|---|---|---|
| Webhook lost | Reconciliation fetch picks up the order | — |
| Webhook duplicated | Ignored by event id | — |
| Same order via webhook and poll | Idempotent by line key | — |
| Token expired | Refresh; if refresh fails → connection error | `sync_failure` |
| Authorization revoked | Connection `revoked`; ingestion stops | `sync_failure` critical + email |
| Rate limited | Back-off; resume from cursor | SyncRun warning |
| Unmapped product | Line stored, no consumption | `unmapped_product` |
| Unconvertible recipe unit | Other ingredients posted, gap recorded | `conversion_missing` |
| Order edited after completion | Reverse + re-post changed lines | Activity feed |
| Outbound write fails (R1.1) | Retry; dead-letter after N | Exception |

## 7. Other external systems

| System | Use | Requirement | Release |
|---|---|---|---|
| **Stripe** | Subscription billing | [SAAS_REQUIREMENTS §Billing](SAAS_REQUIREMENTS.md#4-billing); webhook signature, idempotent, order-safe | R1 |
| Transactional email | Verification, reset, invites, alert digest, weekly summary | SPF/DKIM/DMARC on sending domain; bounce/complaint suppression list; provider via env config (PD-24) | R1 |
| Object storage | Invoice and photo files | S3-compatible; private bucket; tenant-scoped keys; encryption at rest; lifecycle per retention | R1 |
| Assisted extraction (LLM/OCR) | Invoice lines, packaging dates | Enhancement, never prerequisite [CONFIRMED]; per-tenant daily cap; timeout ≤ 60 s off the request path; prompts exclude unrelated tenant data; results always reviewed | R1 (invoices), R2 (dates) |
| Error tracking | Exceptions with PII scrubbing | — | R1 |
| Toast, Clover | Additional POS adapters | Same contract; only when funded by demand [CONFIRMED future] | Future |
| Accounting (QuickBooks, Xero) | Export COGS/waste | — | Future |
| Supplier ordering APIs | PO transmission | Manual purchasing always supported | Future |

## 8. Existing implementations (reference only)

| Aspect | Existing inventory app | JuJu |
|---|---|---|
| Auth | Square OAuth, encrypted tokens | Personal access token (single merchant) |
| Catalog | Synced hourly + webhook | Not synced; JuJu owns its menu |
| Sales | Completed orders, 60-day re-read hourly, aggregated daily (defects C2, C3) | Own orders; POS walk-ins via payment webhook → order → name match; deduct at Ready |
| Refunds | Ignored | Recorded; no restock; full POS refund cancels ticket |
| Write-back | Receipt adjustments + counts; failures swallowed (C4) | None |
| Webhooks | Inventory count, catalog version (+ snack-box payments) | Payment and refund created/updated |
| Verified live | Never (mocked only) | Sandbox verified incl. webhooks and refunds; real POS walk-in untested |

Sources: [audit 06](../audit/06-square-integration.md); JuJu `README.md`,
`src/server/walkins.ts`, `src/server/payments.ts`.

## 9. Standalone data-entry alternatives

For businesses without a POS (Scenario B), sales and usage enter without any external system:

| Method | Grain | Origin label | Release | Notes |
|---|---|---|---|---|
| Daily sales entry | Product variation × business day | `manual` | R1 | Fast grid; corrections by revision |
| Sales file import | Rows from CSV/XLSX (card terminal item report, delivery platform export, spreadsheet) | `file_import` | R1 | Saved column mappings per source; learned name matching; preview; idempotent by file hash |
| Direct usage record | Ingredient quantity | `manual` (usage) | R1 | Staff meals, samples, catering by weight |
| Sales API | Order lines | `api` | R1.1 | First-party ordering sites (JuJu), unsupported POS |
| Counts only | Count interval | — | R1 | Actual usage from counts with no sales at all |

Standalone records are never presented as POS-generated data (DR-34). Automatic per-order
reconciliation is a Connected capability.

## 10. Future native POS boundary

| Topic | Boundary |
|---|---|
| Status | Future product direction; validation required before any build (PD-35); not a launch dependency [CONFIRMED] |
| Relationship to the engine | Native sales are a sales source (`inventoryos_pos`) producing SalesTransactions; consumption, reporting and reconciliation are unchanged (POS-FR-25) |
| What it would add | Checkout, menu/pricing, order lifecycle, tax, receipts, refunds, tips, cash, end-of-day close, payment-processor integration, hardware and card readers, offline operation |
| Not included in the subscription | Payment processing, hardware, card-reader costs, transaction fees, taxes, third-party services [CONFIRMED] |
| Build options to evaluate | Build in-house, white-label a POS, or partner — each with different regulatory, support and cost profiles |
| Guardrail | No R1 design decision may assume transactions come only from external adapters (DR-30) |
