# Inventory project — database audit

Phase 2, 2026-11-04. Read-only. Subject: `app/models.py` (885 lines, 34 tables), `app/db.py`,
`migrations/versions/` (13 revisions). Requirements baseline:
[DOMAIN_MODEL](../product/DOMAIN_MODEL.md) and its integrity rules DR-01..36. Labels as in the
[technical audit](INVENTORY_PROJECT_TECHNICAL_AUDIT.md).

## 1. Schema [CODE]

| Group | Tables | Model lines |
|---|---|---|
| Tenancy / auth / billing | `tenants` (plan, subscription status, Stripe ids, report schedule, timezone), `users`, `provider_connections` | `app/models.py:92,123,142` |
| Catalog | `locations`, `sold_items`, `stock_items`, `recipe_components`, `product_identifiers`, `stock_aliases` | `:157,171,195,240,433,588` |
| Quantities | `inventory_counts`, `inventory_events`, `stock_batches`, `sales_daily` | `:255,455,479,270` |
| Receiving | `documents`, `document_lines`, `extraction_corrections` | `:369,405,565` |
| Intelligence | `alerts`, `promotions`, `health_snapshots` | `:289,503,546` |
| Operations | `sync_runs`, `email_log`, `email_suppressions`, `webhook_events` | `:308,341,323,874` |
| Snack box (private, not InventoryOS) | `box_settings`, `snack_profiles`, `customers`, `customer_login_tokens`, `customer_product_preferences`, `preference_changes`, `box_orders`, `box_lines`, `box_reviews`, `referrals`, `credit_transactions` | `:643–870` |

### Relationships (product tables)

```
tenants 1─* users                     (users.tenant_id NOT a membership: one user, one tenant)
tenants 1─* provider_connections      (unique tenant+provider+merchant; NOT unique per merchant)
tenants 1─* locations 1─* stock_items 1─* stock_batches
                       └─* inventory_counts, inventory_events, alerts, promotions
tenants 1─* sold_items 1─* recipe_components *─1 stock_items     (no location on the recipe)
sold_items × locations × dt → sales_daily                         (daily aggregate)
tenants 1─* documents 1─* document_lines ─→ stock_items
```

### Constraints, indexes, types

| Aspect | Finding | Evidence |
|---|---|---|
| Natural keys | Good where present: `(tenant_id, provider, provider_item_id)`, `(tenant_id, location_id, slug)`, `(tenant_id, sold_item_id, location_id, dt)` | `app/models.py:175,200,275` |
| CHECK constraints | **None** (DB1) — negative quantities, zero receipts, unknown `source` strings accepted | `grep CheckConstraint` → 0 |
| Delete rules | **No `ON DELETE`** anywhere (DB2); no account-deletion path exists to exercise them | `grep ondelete` → 0 |
| Cross-tenant FK safety | FKs reference ids only; nothing stops a row of tenant A pointing at tenant B's stock item except route checks | schema [INFERENCE from FK definitions] |
| Timestamps | `timestamp without time zone` everywhere (DB3); UTC enforced per connection | `app/db.py:48`, probe PA5 [RUN] |
| Quantities | Exact `NUMERIC(18,4)` (`Qty`) | `app/db.py:61` |
| Money | Integer cents for prices; `Qty` for per-base-unit costs | `app/models.py:184,217` |
| Enums | Python enums stored as DB enums (`Plan`, `SubscriptionStatus`, `TrackMode`, `AlertKind`…) — adding plan codes needs a migration | `app/models.py:26-90` |
| Indexes | Adequate for today's volumes; hot paths for a ledger and order-level sales do not exist yet (DB11) | `app/models.py` |
| Soft deletion | `is_active` flags on locations, sold items, stock items; **batches hard-deleted** (C9, probe R6) | `app/web/dashboard.py:281` |

## 2. Migrations [CODE, RUN-earlier]

13 revisions, single head `948e22969d09`, chain `66bc17b36008` (initial) → … → `bddc0b07d9b2`
(subscription state) → `948e22969d09` (Stripe event ordering). `tests/test_migrations.py` diffs
the migrated schema against the models, checks one head, downgrade to base, and upgrade over
populated data; passed on PostgreSQL 16 [RUN-earlier] and SQLite [RUN]. The local dev database is
at head. **Observation:** migration discipline is a genuine asset for the large schema changes
the blueprint needs.

## 3. Domain model compatibility with the blueprint

| Blueprint need | Existing support | Verdict | Evidence |
|---|---|---|---|
| Organizations / tenants | `tenants`, `tenant_id` on every business row | **Reusable** (rename to Organization is optional) | `app/models.py:1` docstring, `:92` |
| Multiple locations | `locations` per tenant; stock items per location | **Partial**: Square-shaped (`provider`, `provider_location_id` required), no address, business-day cutoff or operating mode; recipes and sales consumption location-blind (C3) | `:157-169`, probe R2 |
| Location-specific inventory | `stock_items` keyed by `(tenant, location, slug)` | **Reusable pattern** — item identity and per-location balance are fused into one row; blueprint separates InventoryItem (org-level) from per-location balances | `:197` |
| Users and roles | `users.tenant_id`, globally unique email, no role | **Missing**: needs Membership (user × org × role × location scope) | `:125,128`, probe PA3 |
| Inventory ledger entries | none (C6) | **Missing** — the central gap | §4 |
| Recipe versioning | `recipe_components` without version/effective date (DB9) | **Missing** | `:240` |
| Batch traceability | `stock_batches` with receipt/expiry/source; remaining derived by FEFO, not by allocation records | **Partial** | `:479`, `app/transform/expiry.py` |
| Receiving | `documents` + `document_lines` + aliases; stock effect is an `inventory_counts` anchor, not a receipt movement | **Partial** | `app/orchestration/receiving.py:329-331` |
| Waste events | none (C7) | **Missing** | — |
| POS connections | `provider_connections` (encrypted tokens, expiry, status) | **Reusable with fixes** (merchant uniqueness S7, disconnect) | `:142`, probe PA4 |
| Sales transaction ingestion | `sales_daily` aggregates; no order, line, refund or time of day (DB10) | **Missing** at the required grain | `:270` |
| Subscription plans | `tenants.plan` enum `starter/pro` + Stripe ids | **Partial**: plan list is code, not data | `:30-35`, `app/entitlements.py:27-51` |
| Feature entitlements | `Capabilities` dataclass per plan in code | **Partial**: pattern reusable, catalogue must become configuration | `app/entitlements.py:40-51`, probe PA2 |
| Audit history | `inventory_events` (scans, snack boxes), `extraction_corrections` | **Missing** for settings, roles, connections (DR-23) | `:455,565` |
| Suppliers | free text (DB7) | **Missing** | `:224,380` |
| Units and conversions | `stock_unit`, `dimension`, `receive_unit`, `pack_size_base` | **Partial**: no item-specific cross-dimension conversions | `:210-229`, `app/units.py` |
| Idempotent integration events | `webhook_events` (snack-box/Stripe paths); no event-id record for Square inventory webhooks | **Partial** | `:874`, probe R5 |

## 4. Inventory update logic

**There is no inventory ledger.** `stock_items.qty_base` is a mutable balance with seven writers
[CODE, verified in baseline and unchanged]:

| Writer | Location | History left behind | Actor | Idempotency |
|---|---|---|---|---|
| Square webhook / hourly sync (`apply_provider_counts`) | `app/load/upsert.py:169-197` | none | — | absolute value |
| Derived recompute (`refresh_derived_quantities`) | `app/orchestration/views.py:257` | none | — | recompute |
| Invoice receipt (`apply_document`) | `app/orchestration/receiving.py:329` | `inventory_counts` anchor (`source='receipt'`) | none | status check, racy (C5) |
| Scan receipt | `app/orchestration/scanning.py:234` | count + event | yes | none |
| Manual count | `app/web/dashboard.py:410` | count | none | none |
| Scan count | `app/web/scanning.py:239` | count + event | yes | none |
| Snack boxes | `app/orchestration/boxes.py:266,283` | event | — | flag |

### Dangerous patterns the brief asked about

| Pattern | Present? | Evidence |
|---|---|---|
| Overwriting stock quantities without ledger records | **Yes** — every writer above | §4 table |
| Inconsistent unit conversions | **Low risk inside a dimension** (exact constants, `app/units.py`); **one defect at the Square boundary**: `METRIC_MILLIGRAM` maps to `g` (SQ8) | `app/transform/square_normalize.py:17` |
| Duplicate inventory deductions | **Not today** (absolute counts, re-aggregated daily sales); **duplicate receipts yes** (C5, probe R4). Becomes a real risk once sales are deltas (R2) without event-id dedupe | probes R4, R5 |
| Missing transaction boundaries | Square writes happen inside the request before commit; failures swallowed, then overwritten by the next sync (C4, probe R3) | `app/orchestration/receiving.py:387-402` |
| Inadequate tenant ownership | Every row has `tenant_id` and all 53 product routes check it by hand; nothing enforces it centrally (S8); one Square merchant can attach to two tenants (S7, probe PA4) | `app/web/webhooks.py:38-46` |
| Uncontrolled deletion of history | Batch hard delete (C9); alias silently repointed; balances overwritten | `app/web/dashboard.py:281`, `app/orchestration/receiving.py:422-424` |
| Incorrect concurrent updates | No `SELECT … FOR UPDATE` or conditional updates anywhere (DB4) | probe R4 |
| Square as authority over stock | `TrackMode.PROVIDER` is the **default** track mode; Square counts overwrite `qty_base` (G12). Conflicts with the blueprint's "the ledger is the source of truth" | `app/models.py:212`, `app/load/upsert.py:197` |

## 5. Derived-quantity engine defects [RUN probes]

| ID | Defect | Probe |
|---|---|---|
| C2 | Sales on the day of a count or delivery, after it, are never deducted (`consumed_since` keeps `start < day`) | R1 |
| C3 | Recipe consumption ignores location | R2 |
| C5 | Concurrent invoice apply raises stock twice | R4 |

These are properties of the daily-aggregate design, not isolated bugs; the blueprint's
order-line ingestion (POS-FR-05) and ledger (INV-FR-15..23) remove the design rather than patch it.

## 6. What a ledger migration touches [INFERENCE from §4]

The seven writers are concentrated in five modules (`load/upsert.py`, `orchestration/views.py`,
`orchestration/receiving.py`, `orchestration/scanning.py`, `web/dashboard.py`, plus
`web/scanning.py`). Routing them through one `post_transaction()` service is a bounded change.
The blueprint's model (InventoryTransaction header + InventoryMovement lines, DR-01..07) and the
baseline's proposed `inventory_movements` table ([04](04-database-and-ledger.md#recommended-ledger-design-r1-needs-approval-d3))
agree on the essentials; the blueprint adds the transaction header, batch allocation mode and
cost snapshot rules. Migration approach: [DATA_MIGRATION_ASSESSMENT](DATA_MIGRATION_ASSESSMENT.md).

## 7. Verdict

| Keep | Change | Add |
|---|---|---|
| Postgres + SQLAlchemy + Alembic; exact `Qty`; `tenant_id` everywhere; natural keys; migration tests; `stock_batches`; `documents`/`document_lines`/`stock_aliases`; `provider_connections`; `webhook_events` pattern; Stripe fields | Timestamps → `timestamptz`; CHECK constraints; delete rules; `users` → memberships; locations de-Squared; plan/entitlements → data; `stock_items` split into item + per-location balance (or kept as balance projection); batch removal → waste movement | Ledger (transaction + movements), sales transactions/lines/returns, sales sources, recipes with versions/modifiers/prepared items, production orders, waste events, suppliers, count sessions, exceptions, audit events, item unit conversions |
