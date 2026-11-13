# Data migration roadmap

Phase 3, 2026-11-05. Planning only; **no migration is executed in this phase.** The existing
inventory application is the foundation (C-25), so its schema evolves into the target in
[TARGET_DOMAIN_AND_DATA_ARCHITECTURE](TARGET_DOMAIN_AND_DATA_ARCHITECTURE.md). Assessment and
risks: [DATA_MIGRATION_ASSESSMENT](../audit/DATA_MIGRATION_ASSESSMENT.md).

## 1. Starting point [verified in Phase 2]

| Fact | Consequence |
|---|---|
| No production deployment, no customers | No customer history to preserve; migrations are schema evolution |
| Local dev DB: 2 demo tenants, 18 items, 0 POS connections, at head `948e22969d09` | Every step is tested on this demo data and on fixture data |
| 13 Alembic revisions, single head, migration tests (model diff, downgrade, populated upgrade) | Keep the chain (C-39); extend the tests |
| 34 tables (11 snack box), naive timestamps, no CHECKs, no delete rules | Hygiene precedes new tables |

## 2. Rules

1. **Expand → migrate → contract.** Add structures, backfill, move readers and writers, then drop
   old structures in a later step. The application keeps working after every step.
2. **Forward-only in production.** Until G-R1, every migration also has a tested downgrade; after
   launch, rollback means restoring from backup or shipping a forward fix.
3. **Backup before every production migration** once any production data exists (REL-05).
4. **Destructive steps need owner approval** (REL-10): DM-1 is approved by C-37; DM-16 needs approval
   at the time.
5. **Invariants checked in the migration test** for every step (§5).
6. **Never invent history.** Legacy records that cannot reproduce today's balance stay read-only
   and are never replayed into the ledger.

## 3. Sequence

| Step | What | Task | Expand / migrate / contract | Must precede |
|---|---|---|---|---|
| DM-1 | Remove the 11 snack-box tables (code moved to its own repository) | T0.05 | Contract (destructive, approved C-37) | T1.02 |
| DM-2 | Timestamps → `timestamptz`; CHECK constraints; delete rules | T1.01 | Migrate | T1.02, and so every new table |
| DM-3 | Locations: address, time zone, cutoff, open days; provider ids nullable | T1.04 | Expand | T1.13, T2.04 |
| DM-4 | Memberships backfilled from `users.tenant_id` (each user → Owner) | T1.05 | Expand + backfill | T1.06 |
| DM-5 | Item split: `inventory_items` + `item_locations` + `item_packages` from `stock_items` | T1.13, T1.15 | Expand + backfill (compatibility layer kept) | T1.16 |
| DM-6 | Ledger tables; one `opening_balance` movement per item × location = `qty_base`; `stock_balances` populated | T1.16, T1.17 | Expand + backfill | T1.18 |
| DM-7 | Legacy freeze: `inventory_counts`, `inventory_events` read-only at T1.18; `sales_daily` at T2.04 (split by the owner, 2026-11-12) | T1.18, T2.04 | Migrate (writers removed) | T2.05 |
| DM-8 | `alerts` → `exceptions` (five kinds mapped) | T1.19 | Expand + backfill | T1.25 |
| DM-9 | Products and recipes: `sold_items` → products/variations; `recipe_components` → recipe version 1 | T2.01, T2.02 | Expand + backfill | T2.05 |
| DM-10 | Batch allocation: allocation columns; existing batches get allocations from the opening movement (remaining = FEFO-derived at migration) | T2.06 | Expand + backfill | T2.07 |
| DM-11 | Suppliers from free-text `supplier` values on items and documents | T2.10 | Expand + backfill | T2.11 |
| DM-12 | Receivings created for already-applied documents (history only, no new movements) | T2.11 | Expand + backfill | — |
| DM-13 | External products from Square-origin `sold_items` (provider ids) with mappings | T3.03 | Expand + backfill | T3.04 |
| DM-14 | Provider connections: unique active (provider, merchant, location) | T3.02 | Migrate (constraint) | T3.05 |
| DM-15 | Plans and entitlements from the `Plan` enum and `CAPABILITIES` | T4.01 | Expand + backfill | T4.02 |
| DM-16 | Contract: drop `users.tenant_id`, the `stock_items` compatibility layer and `qty_base`, unused legacy writers | T4.16 | Contract (needs approval) | T4.17, G-R1 |

## 4. Entity mapping

| Current | Target | Transformation |
|---|---|---|
| `tenants` | Organization (physical name kept, ID-09) | + profile fields |
| `users` (+ `tenant_id`) | `users` + `memberships` | One Owner membership per user; email stays globally unique |
| `locations` | `locations` | + address, time zone (existing), cutoff (default 04:00 proposed), open days; provider ids → mappings |
| `stock_items` | `inventory_items` + `item_locations` + `stock_balances` | Merge rows with the same slug across locations into one item; per-location fields to `item_locations`; `qty_base` → opening movement |
| `receive_unit`, `pack_size_base` | `item_packages` | One package per item where set |
| `cost_per_base_cents` | current cost + `item_cost_history` (one entry) | Snapshot on the opening movement |
| `recipe_components` | `recipes` / `recipe_versions` (v1) / `recipe_lines` | Effective from migration time; no earlier versions invented |
| `sold_items` | `products` + `product_variations` (+ `external_products` when provider ≠ demo) | Name, price, category kept |
| `stock_batches` | `batches` + allocations | Remaining preserved as of migration |
| `alerts` | `exceptions` | Kinds mapped; resolved ones closed |
| `documents`, `document_lines`, `stock_aliases`, `extraction_corrections` | kept; `receivings` added | History only |
| `inventory_counts`, `inventory_events`, `sales_daily` | legacy, read-only | Not replayed |
| `provider_connections`, `webhook_events`, `sync_runs` | kept, generalized | Constraint added; connections re-authorized after scope change |
| `tenants.plan`, Stripe fields | `subscriptions`, `plans`, `entitlements` | Starter/Pro retired (C-41) |

## 5. Validation per step (migration-test invariants)

| Step | Invariant |
|---|---|
| DM-2 | Every timestamp column is aware; values unchanged when read as UTC |
| DM-4 | Every former user has exactly one active membership in its former tenant; login still works |
| DM-5 | Every `stock_items` row maps to exactly one (item, item_location); no item without a location row |
| DM-6 | For every (location, item): Σ movements = previous `qty_base`; `stock_balances` equals Σ movements |
| DM-9 | Every sold item is the source of exactly one product variation, its name split at the last " - " (T2.01); every recipe component appears once in version 1 of its recipe (T2.02) |
| DM-10 | For batch-tracked items: Σ batch remaining = balance (DR-13) |
| DM-13 | Every Square-origin sold item has an external product and a mapping |
| DM-15 | Every tenant has a subscription with an entitlement set; no core capability gated |
| All | Single Alembic head; model/migration diff empty; upgrade over populated data passes on Postgres |

## 6. Identifier strategy

Integer primary keys stay (no external exposure without scope checks, C-33). Idempotency keys:
`<source_type>:<source_id>[:r<revision>]`, unique per organization; migration-created transactions
use `migration:<step>:<entity>:<id>`. External ids live only in integration tables, unique per
connection (DR-21).

## 7. Tenant and location assignment

Every existing row already has `tenant_id`. Rows without `location_id` that are location-scoped
(counts, events) get it from their stock item's location when moved into new structures. New
tables are created scoped from the start (T1.02 precedes them).

## 8. POS connection migration

No connection exists in any environment. If one is created before T3.02 (e.g. sandbox testing), it
is **re-authorized** after the scope change (S6); tokens are never copied between environments.

## 9. Rollback strategy

| Period | Rollback |
|---|---|
| Before G-R1 (no customer data) | `alembic downgrade` (tested for every step) or restore the staging database snapshot; fix forward is preferred |
| After G-R1 | Expand/contract keeps the previous release compatible with the new schema until the contract step; restore from PITR for data damage; never edit ledger rows to "undo" — post reversals |

## 10. Cutover criteria

| Cutover | Criteria |
|---|---|
| Staging to new schema (each stage) | All migration-test invariants pass in CI on Postgres; staging deploy runs the migration under the advisory lock; smoke test green |
| First pilot (M4) | DM-1..DM-12 applied; demo data verified; pilot starts with an opening count (C-40, ID-13) |
| Production launch (G-R1) | DM-1..DM-16 applied; restore drill passed (T4.09); backup taken immediately before |
| Any future customer data migration | Physical count at cutover posted as a count session; legacy data kept read-only |
