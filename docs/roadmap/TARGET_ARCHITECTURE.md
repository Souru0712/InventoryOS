# Target architecture

Phase 3, 2026-11-05. Planning only. Inputs: the Phase 1 blueprint ([`docs/product/`](../product/README.md)),
the Phase 2 audit ([`docs/audit/`](../audit/README.md)), and owner decisions C-01..C-42 in
[PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md). Choices marked **ID-nn** are explained, with
alternatives, in [IMPLEMENTATION_DECISIONS](IMPLEMENTATION_DECISIONS.md). Paths marked *(proposed)*
are not yet in the code.

## 1. Constraints that shape the architecture

| Constraint | Source |
|---|---|
| Build on the existing Python codebase; keep its shell, replace its inventory core | C-25, C-35; [reuse matrix](../audit/REUSE_ADAPT_REBUILD_MATRIX.md) |
| One configurable product and schema for every segment; no per-customer code | C-30, SAAS-T-11 |
| Every stock change is a ledger movement; the ledger owns all stock, including Square-tracked items | DR-01..07, C-34 |
| Every sale from every source becomes a SalesTransaction; one consumption engine | DR-29..31, POS-FR-17 |
| No POS is required for any core workflow | DR-29, STD-FR-01 |
| Tenant isolation through one mandatory scoped data-access layer | C-33 |
| InventoryOS owns the product catalog; POS catalogs are mapped to it | C-36 |
| Plans and entitlements are configuration; prices never in domain code | DR-32, DR-36, C-41 |
| Scale: 21 organizations at launch, design headroom 500 | A-03, SCL-01 |
| Solo founder operating it | baseline audit; TR-16 |

## 2. Shape: a modular monolith (ID-01)

```
                 ┌──────────────────────────── one container image ────────────────────────────┐
 Browser ──────► │ WEB process (FastAPI, Jinja, ≥ 2 instances)                                  │
 (owner, staff)  │   routes → permission check → scoped data access → services → domain rules   │
                 │                                                                              │
 Square ───────► │   /webhooks/{provider}: verify signature, store event, enqueue job, ack ≤ 2 s │
 Stripe ───────► │   /billing/webhook: verify, apply lifecycle                                   │
                 ├──────────────────────────────────────────────────────────────────────────────┤
                 │ WORKER process (scheduler with advisory leader lock + jobs table drain)      │
                 │   scheduled: sync cursors, reconciliation, alert/exception evaluation,       │
                 │   nightly ledger verification, digests, retention, trial closing             │
                 │   queued: webhook processing, invoice extraction, exports, reprocessing      │
                 └──────────────┬──────────────────────────┬──────────────────────┬────────────┘
                                ▼                          ▼                      ▼
                     PostgreSQL 16 (managed,       Object storage (invoice    Email provider,
                     PITR backups)                 photos, exports)           Sentry, uptime
```

No microservices, message broker or SPA (ID-06, ID-07). The existing process model (web +
leader-locked scheduler, `app/orchestration/scheduler.py:176`, `app/db.py:169`) is kept and gains a
Postgres **jobs table** so slow or retryable work leaves the request path (fixes C8, SQ9; REL-07,
SCL-03). A broker is reconsidered only if job latency or volume exceeds what one worker drains
(threshold in ID-06).

## 3. Modules and layering

The existing layer-first layout is kept (ID-01): `transform/` pure rules, `load/` adapter output
writers, `orchestration/` services, `web/` routes. Two layers are added: **scoped data access**
and the **posting service**.

```
web/            routes, forms, templates            (no business rules, no raw queries)
   │ permission dependency (require_permission)      ← identity & access
   ▼
orchestration/  services: one per workflow            (transactions, calls posting service)
   │
   ├── access/              scoped data access: every query carries OrgContext(org, locations)
   ├── ledger posting service (proposed: orchestration/ledger.py) — the ONLY writer of movements
   ▼
transform/      pure domain rules: units, conversions, recipe resolution, consumption, costing,
                FEFO allocation, reorder math, report math (no ORM, no I/O)
providers/      adapters: square/ (HTTP, OAuth, webhook verification) behind adapter interface
load/           adapter-normalized rows → integration tables (ExternalProduct, ExternalOrder…)
```

| Module (DOMAIN_MODEL §2) | Responsibility | Owns tables (target) | Reuses |
|---|---|---|---|
| Identity & access | Users, sessions, tokens, MFA, memberships, roles, permissions, invitations | users, sessions, auth_tokens, memberships, invitations | `app/security.py`, `app/web/auth.py` (C-32) |
| Organization | Organization, locations, settings, audit events | organizations (= tenants), locations, settings, audit_events | `tenants`, `locations` |
| Catalog | Inventory items, categories, units, conversions, packages, costs | inventory_items, item_locations, item_unit_conversions, item_cost_history, categories | `app/units.py`, `stock_items` (migrated) |
| Ledger | Transactions, movements, balance projection, verification | inventory_transactions, inventory_movements, stock_balances | — (new; JuJu design reference) |
| Batches & expiry | Batches, allocations, expiry queue, auto-dispose | batches (= stock_batches), movement batch allocation | `app/transform/expiry.py` |
| Procurement & receiving | Suppliers, supplier items, receivings, invoices, extraction, matching | suppliers, supplier_items, receivings, documents, document_lines, stock_aliases | `app/orchestration/receiving.py`, `app/extract/` |
| Products & recipes | Products, variations, modifiers, recipes, versions, lines, costing | products, product_variations, modifiers, recipes, recipe_versions, recipe_lines | — (new; JuJu reference) |
| Production | Production orders, yields, output batches | production_orders | — (new) |
| Waste | Waste events, lines, reasons | waste_events, waste_lines, waste_reasons | — (new) |
| Counts & exceptions | Count sessions, lines, exceptions | stock_counts, stock_count_lines, exceptions | `alerts` generalized |
| Sales | Sales sources, transactions, lines, returns, consumption engine, imports | sales_sources, sales_transactions, sales_transaction_lines, sales_returns, import_files | — (new) |
| Integrations | Connections, external catalog/orders, mappings, events, sync runs, outbox (R1.1) | provider_connections, external_products, external_modifiers, product_mappings, webhook_events, sync_runs, outbound_commands | `app/providers/square/`, `sync_runs`, `webhook_events` |
| Reorder & analytics | Suggestions, reports, exports, weekly summary | (read models only) | `app/transform/health.py` rules, `app/transform/reports.py` |
| Billing & entitlements | Plans, subscriptions, entitlement sets | plans, entitlements, subscriptions (fields on organizations today) | `app/entitlements.py` `allows()`, Stripe lifecycle |
| Notifications | Email templates, preferences, suppressions, digests | email_log, email_suppressions | `app/notify/email.py` |
| Jobs | Queue, retries, dead letters | jobs (proposed) | scheduler + advisory lock |

**Rule:** a module calls another module only through its service functions, never by writing its
tables. The ledger posting service is the only writer of `inventory_movements` and
`stock_balances` (DR-01).

## 4. Core data flows

### 4.1 Any stock-changing business event

```
form / import / adapter event
  → service validates (permission, entitlement, units, business rules in transform/)
  → service writes the business record (waste event, receiving, production order, count…)
  → posting service: ONE InventoryTransaction(idempotency key) + N movements
        (row lock on affected stock_balances rows; sign checks; cost snapshot; actor)
  → same DB transaction updates stock_balances projection
  → commit
  → enqueue: exception evaluation, low-stock evaluation (jobs table)
```

Re-posting the same idempotency key returns the original transaction (DR-05). Corrections are
reversal transactions (DR-06). Nightly verification recomputes Σ movements and raises
`balance_drift` (DR-03).

### 4.2 Three sales sources, one engine (operating modes)

```
 Standalone           Connected (Square first)          Future native POS
 daily entry ──┐      webhook / cursor sync ──┐         checkout ──┐
 file import ──┤      adapter normalizes      │         (not built)│
 usage record ─┤                              │                    │
               ▼                              ▼                    ▼
        SalesTransaction + lines  (origin: manual | file_import | pos_adapter | api | inventoryos_pos)
               │   source of record per location per business date (DR-31)
               ▼
        Consumption engine (transform/: resolve RecipeVersion at occurred_at, modifiers,
        made-ahead vs made-to-order, conversions, FEFO inference)
               ▼
        posting service → sale_consumption movements (idempotent per line revision)
```

Direct usage records (STD-FR-04) post `manual_consumption` without a sale. Nothing downstream of
SalesTransaction knows which source produced it (AC-SAL-07). Details:
[OPERATING_MODE_IMPLEMENTATION_PLAN](OPERATING_MODE_IMPLEMENTATION_PLAN.md).

### 4.3 Integration flow

```
Square ──webhook──► verify HMAC on raw body (reuse app/providers/square/webhooks.py)
                    → insert webhook_events(provider, event_id) — duplicate → 200, no-op
                    → enqueue job → ack ≤ 2 s
worker: job → adapter.fetch/normalize → external_orders/lines → SalesTransaction (line uid key)
        → consumption engine → posting service
scheduled: cursor sync (updated_at since last cursor) + daily reconciliation of order counts
failure: job retries with back-off → dead letter → `sync_failure` exception → health page
```

## 5. Integration architecture

| Concern | Design | Evidence / requirement |
|---|---|---|
| Provider abstraction | `PosAdapter` interface: `authorize_url`, `exchange_code`, `refresh`, `revoke`, `list_locations`, `fetch_catalog`, `fetch_orders(since, cursor)`, `fetch_order(id)`, `parse_webhook(raw, headers)`, `verify_webhook`; returns the normalized types of INTEGRATION_REQUIREMENTS §2 | POS-FR-15, AC-POS-12 |
| Registry | Adapters registered by provider code; `square` first; a static test forbids provider imports outside `app/providers/` | AC-SAL-07, MNT-08 |
| First provider | **Square**: OAuth, token encryption, webhook verification, client and normalizer already exist and are tested; owner priority (Toast deferred) | integration audit §1; owner 2026-11-04 |
| Authentication | OAuth 2 with `state`; product-only scopes (read merchant, catalog, orders; inventory write only when R1.1 write-back is on) | SQ-02, S6 |
| Credentials | Fernet-encrypted tokens with a key from the environment, outside the DB; rotation procedure; revoke on disconnect and on deletion | SEC-10, SEC-21 |
| Webhook ingestion | Verify → persist event id → enqueue → ack; processing idempotent | DR-21, PERF-07 |
| Synchronization | Incremental cursor per connection; initial backfill window; scheduled reconciliation; never the 60-day full re-read (SQ2) | POS-FR-05, -12 |
| Retry | Jobs table: exponential back-off, max attempts, dead letter → exception | POS-FR-10 |
| Idempotency | Event id (webhooks), external line uid (sales lines), transaction key `<source>:<id>[:r<rev>]` (ledger) | INV-FR-21, POS-FR-11 |
| Product mapping | ExternalProduct/ExternalModifier → ProductMapping → ProductVariation / Modifier; unmapped → exception; reprocess as reversal + re-post | POS-FR-04, -06, -14 |
| Transaction mapping | ExternalOrder/Line → SalesTransaction/Line; refunds → SalesReturn (no restock, DR-26); voids → reversal | POS-FR-08, -09 |
| Error monitoring | `sync_runs`, exceptions (`sync_failure`, `pos_reconciliation_mismatch`, `unmapped_product`), Sentry | POS-FR-10 |
| Integration health | Per connection: last success, lag, error rate, unprocessed events, token status | POS-FR-27 |
| Write-back | **Off in R1** (C-34); R1.1 via outbox (`outbound_commands`) per INTEGRATION_REQUIREMENTS §3.4 | POS-FR-13 |

## 6. SaaS architecture (summary)

| Concern | Design | Detail |
|---|---|---|
| Tenant isolation | `OrgContext` from the session's active membership; scoped repositories; static test bans unscoped queries; parametrized isolation suite; RLS later | C-33; [testing strategy](TESTING_AND_QUALITY_STRATEGY.md) |
| Authentication | In-app (C-32): bcrypt (existing), server-side sessions, verification/reset tokens, DB-backed throttling, TOTP, CSRF | SAAS-A-01..09 |
| Authorization | Permission keys per operation; role presets; location scope on membership; cost fields hidden from Employee | USER_ROLES_AND_PERMISSIONS |
| Billing boundary | Stripe owns payment methods, invoices, proration; InventoryOS stores subscription state from verified webhooks only | SEC-20 |
| Plans and entitlements | Plan and Entitlement rows; `allows()` single check reads them; operating mode, location count and capability flags are separate dimensions | C-41; [SaaS plan](SAAS_AND_PRICING_IMPLEMENTATION_PLAN.md) |

## 7. Frontend

Server-rendered Jinja with the existing design system (`app/web/static/`, redesign `22ee0b5`).
Floor screens (waste, counts, production, receiving lines, daily sales) are mobile-first and use
small progressive enhancement; **HTMX is allowed for those screens only** if approved (ID-07). No
SPA framework, no CSS framework swap (baseline audit 03).

## 8. API surface

| Surface | When | Auth |
|---|---|---|
| HTML routes | R1 | Session + CSRF |
| Webhooks (`/webhooks/square`, `/billing/webhook`) | R1 | Provider signature |
| Generic sales API `POST /api/v1/sales` | R1.1 (T4B.03) | Per-organization API key, scoped to one location; rate-limited |
| Public read/write API | Advanced (T7.04) | API keys with scopes |

## 9. Deployment architecture (ID-02)

| Environment | Purpose | Contents |
|---|---|---|
| Local | Development | SQLite for fast unit tests; Postgres via `compose.yaml` for integration tests |
| CI (GitHub Actions) | Every push / PR | Lint, type check, unit (SQLite), full suite (Postgres 16 service), migrations, isolation suite, dependency audit |
| Staging | Continuous deploy from `main` | Same image and services as production; Square sandbox; Stripe test mode |
| Production | Customers | Web ≥ 2 instances, worker 1 (+ standby), managed Postgres 16 with PITR, object storage, email provider, Sentry, uptime monitor |

Recommended host: **Render** (web service + background worker + managed Postgres with point-in-time
recovery + preview environments), chosen for fit and automation per the owner's criteria; to be
confirmed against current plans when T0.09 starts. Alternatives in ID-02.

## 10. Future native POS fit

A native POS, if ever approved (Stage 8), is a separate module (`checkout`) that writes
SalesTransactions with origin `inventoryos_pos` and calls a payment processor's hosted/terminal
APIs. Because catalog ownership (C-36), the sales model (DR-30) and source-of-record per date
(DR-31) exist from R1, **no change to the inventory engine is needed**. Payments, tax, receipts,
cash, offline mode and hardware are new, regulated, support-heavy scope (OPERATING_MODE plan §4).

## 11. JuJu interoperability

No shared database, repository or deployment (C-42). Until R1 ships, JuJu informs the design and
is validated through a reference tenant seeded by one-way import ([JUJU_VALIDATION_PLAN](JUJU_VALIDATION_PLAN.md)).
After R1.1 the generic sales API is the only integration point if the owner decides JuJu becomes a
tenant (T5.04).
