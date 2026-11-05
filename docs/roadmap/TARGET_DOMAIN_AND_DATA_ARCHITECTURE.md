# Target domain and data architecture

Phase 3, 2026-11-05. Planning only. The **entity definitions and integrity rules DR-01..36 live in
[DOMAIN_MODEL](../product/DOMAIN_MODEL.md)** and are not repeated here. This document turns them into
a physical target for this codebase: tables, ownership, keys, constraints, indexes, transaction
boundaries and how today's schema gets there. Current schema facts:
[database audit](../audit/INVENTORY_PROJECT_DATABASE_AUDIT.md).

## 1. Principles

1. **Stock is explained, never assigned.** Balance = Σ movements; `stock_balances` is a projection
   updated in the same transaction (DR-01..03). No other code path writes quantities.
2. **Business record + ledger entries.** The record says what happened and why; its movements say
   what it did to stock. Reports read quantities and costs from movements only (DR-33) — so waste
   is never double-counted.
3. **Everything tenant-owned carries `organization_id`**; everything location-scoped also carries
   `location_id` (DR-09, DR-35).
4. **History is immutable.** Recipe versions, cost snapshots, posted movements and audit events are
   never edited (DR-02, DR-16, DR-19, DR-23); master data is deactivated, not deleted (DR-24).
5. **Time is UTC-aware**; business dates are computed in the location's time zone with its cutoff
   hour (DR-20).

## 2. Target tables by module

Names are *(proposed)* except where an existing table is kept. "Existing →" marks a table that is
reshaped from a current one ([migration roadmap](DATA_MIGRATION_ROADMAP.md)).

| Module | Table | Key columns and constraints | Existing |
|---|---|---|---|
| Organization | `organizations` | id; name; profile; deletion_requested_at; subscription fields | Existing → `tenants` (rename optional, ID-09) |
| | `locations` | org; name; address; time_zone (IANA); business_day_cutoff; currency; open_days; is_active | Existing → `locations` (provider ids move to mappings) |
| | `settings` | org (+ location nullable); key; value JSON; versioned via audit | new |
| | `audit_events` | org; actor (user/system/support); action; subject type/id; before/after summary; at; ip — append-only | new |
| Identity | `users` | id; email unique; password_hash; verified_at; mfa fields | Existing → `users` (drop `tenant_id`) |
| | `memberships` | user; org; role; location_scope (all \| list); status — unique (user, org) | new |
| | `invitations`, `sessions`, `auth_tokens` | token hashes, expiry, single use; sessions revocable | new |
| Catalog | `inventory_items` | org; name; type (raw, prepared, finished, packaging, supply); category; base_unit; dimension; tracks_batches; tracks_expiry; default_shelf_life; is_active — unique (org, slug) | Existing → split from `stock_items` (C-38) |
| | `item_locations` | org; location; item; reorder_point; par; min; storage area — unique (location, item) | Existing → from `stock_items` per-location fields |
| | `item_unit_conversions` | org; item; from_unit; to_unit; factor; note — cross-dimension only (DR-11) | new |
| | `item_packages` | org; item; name; base_qty (e.g. case = 6000 ml) | Existing → `receive_unit`, `pack_size_base` |
| | `item_cost_history` | org; item; unit_cost_base; source; effective_at — append-only | new |
| | `categories` | org; name; kind (item/product) | new (free text today) |
| Ledger | `inventory_transactions` | org; location; type; source_type; source_id; idempotency_key **unique (org, key)**; actor; occurred_at; posted_at; reverses_id | new |
| | `inventory_movements` | transaction; org; location; item; batch nullable; allocation_mode (explicit/inferred); qty_base ≠ 0 with sign CHECK by type; unit_cost_snapshot; movement_type | new |
| | `stock_balances` | org; location; item; qty_base; updated_at — PK (location, item) | new (replaces `stock_items.qty_base`) |
| Batches | `batches` | org; location; item; source ref; lot_code; received_at/produced_at; expires_at (date or datetime); status | Existing → `stock_batches` |
| Procurement | `suppliers`, `supplier_items` | contacts, lead time, delivery days; supplier code, pack, last price | new (free text today) |
| | `receivings`, `receiving_lines` | 4-quantity model (ordered nullable, invoiced, arrived, accepted); discrepancy; status draft→posted, posted once (DR-25) | Existing → `documents`/`document_lines` keep the invoice; receiving becomes the posting record |
| | `documents`, `document_lines`, `stock_aliases`, `extraction_corrections` | kept | Existing |
| Products | `products`, `product_variations`, `modifiers`, `modifier_effects` | owned catalog (C-36); prices per variation | new (Square `sold_items` become external products) |
| Recipes | `recipes`, `recipe_versions`, `recipe_lines` | version effective_from; immutable once referenced; prepared-item lines; packaging flag; yield | Existing → `recipe_components` migrated to version 1 |
| Production | `production_orders` | prepared item; multiplier; expected/actual yield; status; void window | new |
| Waste | `waste_events`, `waste_lines`, `waste_reasons` | reason required; actor; batch; related order; void window | new |
| Counts | `stock_counts`, `stock_count_lines` | expected snapshot at line timestamp; counted; variance; reason; review | Existing → `inventory_counts` kept as legacy history |
| Exceptions | `exceptions` | org; location; type; severity; subject; money impact; status; dedupe key **unique (org, type, subject) where open**; resolved_by | Existing → generalizes `alerts` |
| Sales | `sales_sources` | location; kind; effective_from business date (one of record per date, DR-31) | new |
| | `sales_transactions`, `sales_transaction_lines`, `sales_returns` | origin (DR-34); occurred_at; business date; line identity key; revision; consumption status | new (replaces `sales_daily`) |
| | `import_files`, `import_mappings` | column mappings for file imports | new |
| Integrations | `provider_connections` | **unique active (provider, merchant, external location)** across orgs (DR-22); encrypted tokens; scopes; status incl. revoked | Existing (constraint changed) |
| | `external_products`, `external_modifiers`, `product_mappings`, `external_orders`, `external_order_lines` | unique (connection, external id) (DR-21) | new |
| | `webhook_events` | unique (provider, event_id); processed_at | Existing (generalized) |
| | `sync_runs`, `sync_cursors` | per connection | Existing + new |
| | `outbound_commands` | R1.1 outbox | new (R1.1) |
| Billing | `plans`, `entitlements`, `subscriptions` | plan code; billing basis; entitlement set (modes, max locations, flags, limits) | new (enum + dict today) |
| Jobs | `jobs` | type; org; payload; idempotency key; attempts; next_run_at; status; last_error | new |
| Notifications | `email_log`, `email_suppressions` | kept | Existing |
| Removed | 11 snack-box tables | moved to the snack-box project (C-37) | T0.05 |

## 3. Ownership and relationships

```
organizations 1─* locations 1─* item_locations *─1 inventory_items *─1 organizations
organizations 1─* memberships *─1 users                (location_scope ⊆ org's locations)
inventory_transactions 1─* inventory_movements ─→ (location, item, batch?)
stock_balances = projection of movements per (location, item)
business records ─→ inventory_transactions (source_type, source_id)
   receivings · production_orders · waste_events · stock_counts · sales_transactions · adjustments
products 1─* product_variations 1─* recipes 1─* recipe_versions 1─* recipe_lines ─→ inventory_items
sales_transaction_lines ─→ product_variation (+ modifiers) ─→ recipe_version used (recorded)
provider_connections 1─* external_products ─→ product_mappings ─→ product_variations
```

Cross-tenant references are prevented in the write path by the scoped repositories (every
referenced id is loaded through the same `OrgContext`) and, where practical, by composite foreign
keys `(organization_id, id)` on new tables (SAAS-T-05).

## 4. Ledger strategy

| Aspect | Decision | Requirement |
|---|---|---|
| Granularity | One transaction per business event; one movement per item × location × batch | DR-01 |
| Movement types | `opening_balance`, `receipt`, `sale_consumption`, `manual_consumption`, `production_input`, `production_output`, `waste`, `count_variance`, `adjustment`, `transfer_out`/`transfer_in` (Expansion), `pos_correction` (R1.1 write-back only), `reversal` | DOMAIN_MODEL §4.2 |
| Sign rules | CHECK constraints per type at the database | DR-04 |
| Idempotency | `unique (organization_id, idempotency_key)`; key `<source_type>:<source_id>[:r<revision>]` | DR-05 |
| Concurrency | `SELECT … FOR UPDATE` on the `stock_balances` rows touched, in a stable order (item id) to avoid deadlocks; conditional status transitions on source records | DR-07, AC-INV-07, AC-RCV-05 |
| Corrections | Reversal transaction referencing the original; the source record is marked corrected | DR-06, WF-26 |
| Negative balances | Allowed only from system-observed events (POS consumption, production) and raise `negative_balance`; manual waste/adjustment below zero refused for Employees | DR-08, AC-INV-09, AC-INV-10 |
| Cost | Movement stores unit-cost snapshot = current cost at posting (PD-08); valuation at current cost; FIFO actual cost revisited in R2 | DR-19, ANL-FR-01 |
| Batches | Movement-level batch with allocation_mode; batch remaining = Σ its movements; Σ batch remaining = balance | DR-13..15 |
| Verification | Nightly job recomputes Σ movements per (location, item) vs projection → `balance_drift` | DR-03, AC-INV-11 |
| Immutability | No UPDATE/DELETE path in application code; a static test greps for writes to movement tables outside the posting service; DB role without UPDATE/DELETE on movements once roles are split (SEC-17) | DR-02, AC-INV-05 |

## 5. Transaction boundaries

| Operation | One database transaction contains | Outside the transaction (jobs) |
|---|---|---|
| Post a business event | Source record + inventory transaction + movements + balance projection + audit event | Exception evaluation, low-stock evaluation, notifications |
| Sales line ingestion | External line upsert + SalesTransaction/line + consumption movements | Unmapped/gap exceptions, reconciliation counters |
| Recipe save | New RecipeVersion + lines + audit event | Margin recalculation display caches |
| Membership change | Membership + session revocations + audit event | Notification email |
| Stripe webhook | Subscription state + entitlement set + audit event | Emails |
| Square write (R1.1) | Outbox row with the local change | Send to Square with retry |

Side effects to external systems never happen inside the request transaction (REL-04).

## 6. Indexing

| Table | Index | Serves |
|---|---|---|
| inventory_movements | (organization_id, location_id, item_id, occurred_at) | item history, as-of balances, verification |
| inventory_movements | (organization_id, batch_id) where batch_id is not null | batch history |
| inventory_transactions | unique (organization_id, idempotency_key); (organization_id, source_type, source_id) | idempotency, record → movements |
| sales_transactions | (organization_id, location_id, business_date); unique (connection_id, external_line_uid) on lines | reports, dedupe |
| exceptions | (organization_id, status, severity) and partial unique open dedupe key | dashboard |
| webhook_events | unique (provider, event_id) | dedupe |
| jobs | (status, next_run_at) | worker drain |

Volume note: ~7,500 movements/day for a busy Connected location (NFR §3a sizing). Partitioning or
archival of movements is **not** needed for R1; revisit at 100+ organizations with monthly range
partitioning on `occurred_at`, keeping every movement retrievable (SCL-04).

## 7. Audit and history

AuditEvent for settings, roles, memberships, connections, mappings, recipes, approvals, support
access and billing (DR-23); retained ≥ 1 year (SEC-14). Ledger and recipe versions are their own
history. Legacy tables (`inventory_counts`, `inventory_events`, `sales_daily`) are kept read-only
as pre-ledger history and never replayed ([migration roadmap](DATA_MIGRATION_ROADMAP.md)).

## 8. Retention, backup and recovery

| Item | Policy | Source |
|---|---|---|
| Active tenant data | Kept while subscribed | — |
| After cancellation | Read-only 90 days with ingestion continuing, then deletion after notice; export throughout | PD-07 recommendation (needs approval, ID-14) |
| Deletion request | 7-day cancellable window, then hard delete of tenant rows and files; backups age out | SAAS-D-04 |
| Audit events | ≥ 1 year | SEC-14 |
| Backups | Managed daily + PITR; RPO ≤ 15 min; RTO ≤ 4 h; quarterly restore drill and one before launch | REL-05 |
| Files | Object storage with versioning; tenant-scoped keys; authorized serving | REL-06, DR-28 |

## 9. Migration considerations

There is **no customer data** (audit). Migrations are schema evolution with demo data, following
expand → migrate → contract, forward-only in production, tested on populated data
(`tests/test_migrations.py` pattern), keeping the existing 13-revision chain (C-39). Sequence and
per-table mapping: [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md).
