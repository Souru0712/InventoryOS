# Data migration assessment

Phase 2, 2026-11-04. Read-only; **no migration was executed**. Conceptual strategy for moving the
existing schema to the blueprint's domain model ([DOMAIN_MODEL](../product/DOMAIN_MODEL.md)).

## 1. The data that exists [RUN]

| Where | Contents | Evidence |
|---|---|---|
| Production | **None.** The app has never been deployed and has no customers | baseline [01](01-executive-audit.md#current-application-state) |
| Local dev database `stockpulse.db` (read-only row counts) | 2 tenants, 2 users, 2 locations (`provider='demo'`), 18 sold items, 18 stock items, 30 recipe components, 20 counts, 756 daily sales rows, 1 document, 7 batches, **0 POS connections**; at migration head `948e22969d09` | read-only `sqlite3` query this session |
| `stockpulse.db.bak-20260910-192545` | Older dev backup | file listing; not opened |
| Square | No merchant connected anywhere; the sandbox seller script exists but no connection row is stored locally | `provider_connections` = 0 |
| Stripe | No customers recorded locally (no connection to check remotely) | [UNKNOWN] for the Stripe test account |

**Consequence:** there is no customer history to preserve. The migration problem is **schema
evolution of a codebase**, not a data migration. That removes most risk and permits reshaping
tables more freely than would be safe with live data.

## 2. Structures by fate

| Fate | Tables / structures | Notes |
|---|---|---|
| **Remain as-is** | `documents`, `document_lines`, `extraction_corrections`, `stock_aliases`, `product_identifiers`, `email_log`, `email_suppressions`, `sync_runs`, `health_snapshots`, `promotions` | Add `timestamptz`; add constraints |
| **Remain, extended** | `tenants` (→ Organization fields: profile, deletion request), `locations` (address, business-day cutoff, open days, sales source of record; `provider`/`provider_location_id` nullable), `stock_batches` (lot code, source reference, status), `provider_connections` (unique active per merchant, scopes, revoked state), `webhook_events` (generic provider events), `alerts` (→ Exception, or kept for notifications) | Additive columns first |
| **Reshaped** | `users` (drop `tenant_id`; add Membership table), `stock_items` (split item definition from per-location balance projection, or keep per-location rows and add an org-level item — decision in Phase 3), `sold_items` (→ InventoryOS product/variation + ExternalProduct mapping), `recipe_components` (→ Recipe/RecipeVersion/RecipeLine), `tenants.plan` enum (→ Plan/Entitlement tables) | See §4 |
| **Replaced** | `sales_daily` (→ SalesTransaction/Line/Return; daily aggregates may remain as a reporting projection), `inventory_counts` as stock anchors (→ count sessions producing variance movements), `inventory_events` (→ ledger movements) | |
| **New** | InventoryTransaction, InventoryMovement, ItemUnitConversion, ItemCostHistory, Supplier, SupplierItem, ProductionOrder, WasteEvent/WasteLine/WasteReason, StockCount/StockCountLine, Exception, SalesSource, ExternalProduct/ExternalModifier, ProductMapping, Membership, Invitation, Session, AuthToken, AuditEvent, Plan, Entitlement, OutboundCommand, ImportFile | DOMAIN_MODEL §4 |
| **Removed** | 11 snack-box tables | If C-37 ends in deletion rather than a move to its own app |

## 3. Identifiers

| Topic | Today | Change | Risk |
|---|---|---|---|
| Primary keys | Integer autoincrement per table | Keep integers internally [RECOMMENDED]; never expose sequential ids across tenants without scope checks | Enumeration risk is mitigated by central scoping (C-33), not by id format |
| External ids | `provider` + `provider_*_id` columns | Move to ExternalProduct / connection-scoped tables | — |
| Idempotency keys | Only `receipt-{document_id}` for Square writes | `<source_type>:<source_id>[:r<revision>]` unique per organization (BUSINESS_WORKFLOWS conventions) | — |
| Users | Email globally unique, tied to one tenant | Email unique per user; Membership carries tenant | Existing two dev users become two memberships |

## 4. Transformations (if any data were kept)

Written so the same steps would also apply if pilot data appears before the rebuild finishes.

| Step | Transformation |
|---|---|
| 1 | `users` → one Membership per user with role Owner and all-locations scope |
| 2 | `locations` → set address/cutoff defaults; sales source = `square` if a connection exists, else `manual` |
| 3 | `sold_items` → Product + ProductVariation (name, price) and ExternalProduct (provider ids) with a mapping row |
| 4 | `recipe_components` → one Recipe per sold item with RecipeVersion 1 effective from migration time (earlier history does not exist and is not invented) |
| 5 | `stock_items` → items + per-location balances; **one `opening` movement per item = current `qty_base`**, dated at migration, actor `system`, cost snapshot = `cost_per_base_cents` |
| 6 | `stock_batches` → keep rows; allocations start from the opening movement (remaining = derived FEFO remaining at migration) |
| 7 | `inventory_counts`, `inventory_events`, `sales_daily` → kept read-only as **legacy history** (not replayed into the ledger, because they cannot reproduce today's balance — C6) |
| 8 | `tenants.plan` → Subscription + Plan rows; entitlements derived |

## 5. Preserving history and reconciling balances

- History that cannot be proven correct is **preserved but not replayed**: legacy tables stay
  read-only; the ledger starts from opening balances. Replaying `sales_daily` through a new engine
  would re-create C2/C3 effects or invent figures.
- **Balance reconciliation at cutover:** for every item × location, opening movement =
  `qty_base`; the nightly verification (DR-03) then holds from day one. For real customers the
  recommended practice would be a physical count at cutover, posted as a count session.
- **Square connections:** none exist. Any future connection made before the adapter rebuild should
  be **re-authorized** after it, because the scope set changes (S6).

## 6. Introducing tenant and location ownership

Tenant ownership already exists on every row (`tenant_id`). What changes:

| Item | Approach |
|---|---|
| Central scoping | Introduce the scoped data-access layer (C-33) before reshaping tables, so new tables are born scoped |
| Location on records | Add `location_id` to every location-scoped table that lacks it (counts, events → movements already carry it); backfill from the stock item's location |
| Cross-tenant FK safety | Composite constraints or write-path validation (SAAS-T-05) as tables are reshaped |
| Snack-box tenant gating | Removed with the module (C-30) |

## 7. Avoiding data loss — migration rules

1. Forward-only in production; migrations tested on populated data (the existing
   `tests/test_migrations.py` pattern, extended to new tables).
2. **Expand → migrate → contract**: add new tables/columns, backfill, switch reads, then drop old
   structures in a later release.
3. Naive → aware timestamps: convert assuming UTC (the app already enforces UTC per connection,
   `app/db.py:48`), in one migration with a verification query.
4. Backup before every production migration once data exists (REL-05).
5. Destructive steps (dropping snack-box tables, legacy tables) need owner approval (REL-10).

## 8. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Pilot data created on the old engine before the ledger exists | Medium | Medium | Do not onboard pilots until the engine milestone; if unavoidable, cut over with a physical count |
| Timestamp conversion shifts times | Low | Medium | UTC is already enforced per connection; verify with a before/after query |
| Splitting `stock_items` breaks many screens at once | Medium | Medium | Keep `qty_base` as the balance-projection column during transition |
| Long migration chain becomes hard to reason about | Low | Low | Keep the chain (it is tested); optional baseline reset is decision D-M1 below |

## 9. Decision for the owner

**D-M1 — Keep or reset the migration history before R1.** With no customer data, the 13 existing
migrations could be squashed into a fresh baseline. **Recommendation: keep the chain** — it is
tested end to end and costs nothing to keep; revisit only if migration runtime becomes a problem.
