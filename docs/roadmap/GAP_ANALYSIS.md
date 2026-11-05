# Gap analysis

Phase 3, 2026-11-05. Planning only. Phase 1 requirements against Phase 2 findings, turned into
actions. The requirement-by-requirement evidence (all 231 functional requirements) is in the
[traceability matrix](../audit/INVENTORYOS_REQUIREMENTS_TRACEABILITY.md); this document groups it by
capability and adds impact, dependencies, priority, action and stage. Complexity: S/M/L as in the
[task breakdown](DEVELOPMENT_TASK_BREAKDOWN.md#conventions).

**Classifications (brief §4):** Reusable = already implemented and reusable · Refactor =
implemented but requires refactoring · Partial = partially implemented · Missing · Incompatible =
architecturally incompatible · Replace = requires replacement · Investigate = requires additional
investigation · Future = future enhancement · Out of scope = out of scope for initial launch.

## 1. Capability gap table

| # | Requirement (module) | Class | Current implementation and evidence | Gap | Business impact | Technical impact | Depends on | Cx | Pri | Action | Stage / tasks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Inventory ledger (INV) INV-FR-15..23 | **Replace** (Incompatible) | Mutable `qty_base`, 7 writers ([DB audit §4](../audit/INVENTORY_PROJECT_DATABASE_AUDIT.md#4-inventory-update-logic)); probes R1–R6 | No movements, idempotency, locking | Stock numbers cannot be trusted or explained | Every stock-changing module depends on it | item model, units | L | P0 | Build ledger + posting service; migrate opening balances; reroute writers | 1 · T1.16–T1.18 |
| 2 | Items, types, categories (INV) INV-FR-01..03, 10 | Partial | Create-only route `app/web/dashboard.py:297`; per-location rows | No edit/deactivate, types, categories | Owners cannot maintain their catalog | Item identity split needed (C-38) | locations | L | P0 | Split item model; item UI | 1 · T1.13, T1.14 |
| 3 | Units and conversions (INV) INV-FR-04..14 | Refactor | `app/units.py` exact same-dimension constants (tested) | No item-specific cross-dimension conversions, no tbsp/tsp | Juice yields (lb → ml) impossible | Recipe and receipt entry depend on it | item model | M | P0 | Extend units; add item conversions and packages | 1 · T1.15 |
| 4 | Barcode lookup (INV) INV-FR-09 | Reusable | `app/transform/barcodes.py`, `tests/test_barcodes.py` | — | Fast item lookup on phones | Re-point to new item model | T1.13 | S | P1 | Keep | 1 · within T1.14 |
| 5 | Counts (REC) REC-FR-01..09 | Refactor | Single-item counts overwrite stock (`app/web/dashboard.py:395`) | No sessions, expected snapshot, variance | Loss detection, Standalone usage measure | Counts become observations (DR-12) | ledger | L | P1 | Count sessions → `count_variance` | 1 · T1.23 |
| 6 | Adjustments and history (INV) INV-FR-19, 20, 23 | Missing | Only counts lower stock | Everything | Owners cannot correct stock with a reason | Ledger feature | ledger | M | P1 | Build | 1 · T1.22 |
| 7 | Cost basis and valuation (ANL) ANL-FR-01, 02 | Partial | Latest-cost valuation `app/transform/inventory.py:132` | No snapshots or history | Past reports change when prices change | Snapshot on movements | ledger | M | P1 | Build cost history and snapshots | 1 · T1.24 |
| 8 | Dashboard (CMD) | Refactor | `/app` lists low stock, expiring, sync status (`app/web/dashboard.py:119-195`) | Exceptions, completeness, role views | First screen must be trustworthy | Reads new projections | exceptions, cost | M | P1 | Rebuild on ledger + exceptions | 1 · T1.25 |
| 9 | Exceptions (REC) REC-FR-11..16 | Refactor | 5 alert kinds (`app/models.py:58-63`) | Types, dedupe, resolution | Problems need owners and actions | Generalize alerts | permissions | M | P1 | Exception model | 1 · T1.19 |
| 10 | Alert evaluation for every tenant | **Incompatible** | Evaluated only inside the Square sync (`app/orchestration/scheduler.py:37-45`) — G2 | No alerts without Square | Standalone customers get no alerts | Needs jobs/scheduling independent of POS | exceptions | M | P1 | Jobs table; scheduled evaluation | 1 · T1.20 |
| 11 | Batches (BAT) BAT-FR-01..08, 12, 13 | Refactor | `stock_batches`, FEFO derived; hard delete (C9) | Allocation records, statuses, no delete | Expiry losses untraceable | Allocation on movements | ledger | L | P1 | Batch allocation refactor | 2 · T2.06 |
| 12 | Expiry queue (BAT) BAT-FR-09..11, 14 | Reusable (adapt) | `/app/expiring`, date types, confidence, tests | Disposal → waste; auto-dispose | Daily action list for perishables | Rewire to batches/waste | waste | M | P1 | Keep and adapt | 2 · T2.08 |
| 13 | Markdowns, date reading from photos (BAT-FR-16, 17) | Future | `app/transform/promotions.py`, `app/extract/expiry.py` (mocked) | Not in R1 | — | Keep code dormant | — | — | P2+ | Hide from R1 surface (PD-26) | R2 |
| 14 | Health score | Out of scope | `app/transform/health.py` | Not in the blueprint's R1 | — | Remove from R1 UI; keep rule code reused by reorder | — | S | — | Hide (PD-26) | 1 · T1.25 |
| 15 | Suppliers (PRC) PRC-FR-01..03, 08 | Missing | Free-text supplier (DB7) | Entity, supplier items, price history | Receiving and reorder by supplier | Backfill from text | item model | M | P1 | Build | 2 · T2.10 |
| 16 | Purchase orders (PRC) PRC-FR-04..07 | Future | Not built (by design) | — | Ordering stays outside the app at launch | Nullable PO ref in receiving | receiving | L | P2 | R1.1 | 4B · T4B.01 |
| 17 | Receiving and invoice capture (RCV) | Refactor | Upload, SHA-256 dedupe, Claude extraction, matching, review (tested); racy apply (C5), Square dependency (C4), XSS (S3) | 4-quantity model, discrepancies, single posting | Strongest feature; must be safe | Post via ledger; extraction as a job | suppliers, batches, jobs | L | P1 | Refactor onto ledger | 0 · T0.06; 2 · T2.11 |
| 18 | Extraction accuracy on real documents | Investigate | Mocked only (U3) | Unknown quality and cost | Decides whether extraction is promoted | — | receiving | S | P1 | Trial on owner's invoices | 4 · T4.15 |
| 19 | Waste (WST) WST-FR-01..12 | Missing | Nothing (C7) | Everything | Biggest visible loss for juice bars | Waste movements, no double count (DR-33) | batches, recipes | M | P1 | Build | 2 · T2.07 |
| 20 | Product catalog (RCP) RCP-FR-01, 03 | **Incompatible** | Products = Square variations (`sold_items`) | Owned catalog, modifiers | Standalone and native POS impossible | Catalog ownership reversed (C-36) | permissions | M | P0 | Build owned catalog; map POS later | 2 · T2.01 |
| 21 | Recipes (RCP) RCP-FR-02, 04..09, 14, 17 | **Replace** | Flat `recipe_components`, CLI-only (P1, G5) | Versions, yields, prepared items, UI | Consumption and margins impossible | New model; v1 migration | catalog, units | L | P0 | Versioned recipes | 2 · T2.02 |
| 22 | Costing and margins (RCP) RCP-FR-10..13 | Missing | — | Everything | "What does a drink cost" | Pure costing | recipes, cost | M | P1 | Build | 2 · T2.03 |
| 23 | Production (PRD) | Missing | — (G6) | Everything | Daily prep for juice bars / bubble tea | Production transactions | recipes, batches | L | P1 | Build | 2 · T2.09 |
| 24 | Sales model (POS-FR-17..19) | **Replace** | `sales_daily` aggregates (DB10) | Transactions, lines, origin, source of record | No per-order truth; refunds lost | Shared by all modes | catalog | M | P0 | SalesTransaction model | 2 · T2.04 |
| 25 | Consumption engine (WF-12) | **Replace** | `consumption()` daily, location-blind (C2, C3) | Correct per-line, versioned, idempotent | Theoretical usage is the product's promise | Pure resolver + posting | recipes, sales, ledger | L | P0 | New engine | 2 · T2.05 |
| 26 | Standalone capture (STD) | Missing | No path without Square (G1, G14, probe PA1) | Entry, import, usage | Half the launch offering | Uses shared engine | engine | M | P1 | Build | 2 · T2.12–T2.14 |
| 27 | Reorder suggestions (ROR) | Refactor | `reorder_lines`, CSV (Pro-gated, PA2), Square velocity | Ledger usage, count-first, ungated | Purchasing decisions | Inputs swapped | suppliers, engine | M | P1 | Reuse rules, new inputs | 2 · T2.15 |
| 28 | Reports (ANL) | Partial | Weekly report, health score | Consumption, waste, margin, variance, CSV | Owner weekly review | Ledger read models | most modules | M | P1 | Build | 2 · T2.16, T2.17 |
| 29 | Weekly summary email (ANL-FR-09) | Refactor | Rendered, never delivered | New content; real delivery | Owners who don't log in | Reuse pipeline | reports, email | S | P1 | Adapt | 2 · T2.18 |
| 30 | Settings (ADM) | Missing | Email preferences only | Business settings | No DB edits for owners | Settings table | audit | M | P1 | Build | 2 · T2.19 |
| 31 | Onboarding (SAAS §5) | Missing | "Connect Square" only (G13) | Whole flow | Commercial bottleneck | Imports, checklist | Standalone capture | L | P1 | Build | 2 · T2.20 |
| 32 | Employee floor UX (WF-23) | Missing | Admin screens only | Employee home, 2-tap tasks | Staff adoption | Templates, optional HTMX | waste, production | M | P1 | Build | 2 · T2.21 |
| 33 | Square OAuth, tokens, webhook verification | Reusable (fix scopes) | `app/providers/square/*`, tested | Disconnect, scopes (S6), merchant uniqueness (S7) | Connected trust and app review | Keep code | adapter | M | P1 | Reuse + fix | 3 · T3.02 |
| 34 | Square sync orchestration | **Replace** | Hourly 60-day re-read to daily rows (SQ1, SQ2) | Cursor, line ingestion, webhooks | Stale and wrong consumption | Rebuild around SalesTransaction | engine | L | P1 | Rebuild | 3 · T3.05, T3.06, T3.12 |
| 35 | Adapter boundary (POS-FR-15) | Partial | normalize → load seam; Square defaults elsewhere | Interface, registry, static check | Second POS without rewrite | Contract tests | sales model | M | P1 | Build interface | 3 · T3.01 |
| 36 | Catalog and product mapping (POS-FR-03, 04, 06, 14) | Missing | Variations auto-become products | External catalog, mapping UI, reprocess | Consumption accuracy | New tables | adapter, engine | M | P1 | Build | 3 · T3.03, T3.04 |
| 37 | Refunds, voids, discounts (POS-FR-08, 09, 26) | Partial | Refunds ignored; COMPLETED only | Record returns; void reversal | Sales overstated | SalesReturn | ingestion | M | P1 | Build | 3 · T3.07 |
| 38 | Reconciliation and integration health (POS-FR-02, 10, 12, 27) | Partial | `sync_runs`, last-failure display | Daily reconciliation, health page | Silent data loss avoided | Jobs + exceptions | webhooks | M | P1 | Build | 3 · T3.08, T3.09 |
| 39 | Square write-back (POS-FR-13) | Refactor (unsafe today) | Pushes inside the request; failures swallowed (C4) | Outbox | Owner's original sold-out pain | Disable now | outbox | S/M | P0 now / P2 | Disable (T0.08); outbox in R1.1 | 0 · T0.08; 4B · T4B.02 |
| 40 | Mode switching (WF-25) | Missing | No mode concept | Cutover | Customers adopt or drop a POS | Source of record per date | both modes | M | P1 | Build | 3 · T3.10 |
| 41 | Tenant isolation (SAAS-T) | Refactor | Every route checks by hand (S8); 6 cross-tenant tests | Central mechanism, full suite | Highest-impact SaaS failure | Scoped layer (C-33) | snack-box removal | M | P0 | Build scoped layer + suite | 1 · T1.02, T1.03 |
| 42 | Memberships, roles, permissions (STF) | Missing | One user = one tenant (PA3) | Everything | Staff and multi-business owners | Membership model | scoped layer | M | P0 | Build | 1 · T1.05, T1.06, T1.10 |
| 43 | Authentication (SAAS-A) | Refactor | bcrypt login/logout; no reset/verify/throttle (S1, S2), unrevocable sessions (S5) | C-31 items, sessions, MFA, CSRF | Customers locked out; abuse | In-app (C-32) | memberships | M | P1 | Finish in-app auth | 1 · T1.07–T1.11 |
| 44 | Audit events (ADM-FR-11, DR-23) | Missing | — | Everything | Owners see who changed what | Append-only table | memberships | S | P1 | Build | 1 · T1.12 |
| 45 | Billing lifecycle (SAAS-B) | Reusable | Checkout, Portal, webhooks, grace, read-only (tested on recorded payloads) | Live test-mode run | Revenue | Keep | — | S | P1 | Keep; run test mode | 4 · T4.03, T4.04 |
| 46 | Plan catalogue and entitlements (E-01..10) | **Replace** | Starter/Pro in code; core gated (PA2); quantity 1 | Data-driven catalogue, per-location billing | Approved pricing cannot be sold | Keep `allows()` | permissions | M | P1 | Rebuild catalogue | 4 · T4.01, T4.02 |
| 47 | Export and deletion (SAAS-D) | Missing | Reorder CSV only | Full export; deletion workflow | Trust; privacy | Jobs | reports | M | P1 | Build | 4 · T4.05, T4.06 |
| 48 | Founder console (ADM-FR-16) | Partial | `app/cli.py` | Web console, audited support access | Solo founder support | Admin auth | audit, jobs | M | P1 | Build | 4 · T4.07 |
| 49 | CI, deployment, backups, monitoring | Missing | Docker + compose; never deployed; no CI, backups, monitoring | Everything operational | Cannot run a paid service | Host (ID-02) | — | M | P0/P1 | Build | 0 · T0.02, T0.09; 4 · T4.09, T4.10 |
| 50 | Test suite and hermeticity | Reusable (fix T1) | 548 tests; `.env` breaks 46 | Hermetic config | Every "verified" depends on it | — | — | S | P0 | Fix | 0 · T0.01 |
| 51 | Web security hardening | Partial | Webhook HMAC, token encryption, scrubbing good; XSS (S3), no headers (S10) | Fixes | Verified vulnerability | — | — | S | P0 | Fix | 0 · T0.06 |
| 52 | Snack-box module | Out of scope | 35 routes, 11 tables, tenant-gated | Move out (C-37) | Focus; scopes | Removal migration | owner repo | M | P0 | Extract | 0 · T0.05 |
| 53 | Multi-location features (transfers, consolidated reports) | Future | Location-aware rows only | Features | Expansion revenue | Ledger already location-aware | gate | L | P3 | Stage 6 | 6 · T6.01–T6.05 |
| 54 | Advanced (forecasting, automation, API) | Future | Health/reorder rules only | Features | Expansion revenue | Needs history | gate | L | P3 | Stage 7 | 7 · T7.01–T7.07 |
| 55 | Native POS | Out of scope | Nothing | Everything | Future offering | Prerequisites built in R1 | revenue gate | XL | Deferred | Evaluate only | 8 · T8.01–T8.04 |
| 56 | Accessibility and performance | Investigate | Not audited (U4, U5) | Evidence | Usability; legal | — | UI, prod env | M | P1 | Review and load test | 4 · T4.13, T4.14 |

## 2. Summary by classification

| Classification | Rows |
|---|---|
| Reusable | 4, 12, 33, 45, 50 |
| Refactor | 3, 5, 8, 9, 11, 17, 27, 29, 39, 41, 43 |
| Partial | 2, 7, 28, 35, 37, 38, 48, 51 |
| Missing | 6, 15, 19, 22, 23, 26, 30, 31, 32, 36, 40, 42, 44, 47, 49 |
| Incompatible / Replace | 1, 10, 20, 21, 24, 25, 34, 46 |
| Investigate | 18, 56 |
| Future | 13, 16, 53, 54 |
| Out of scope for initial launch | 14, 52, 55 |

## 3. Missing capabilities (what the launch needs that nothing provides)

Ledger-backed adjustments; waste; production; suppliers; costing and margins; Standalone sales
capture; onboarding; settings; employee floor screens; memberships, roles and permissions; audit
events; mapping and mode switching for Connected; export and deletion; CI, deployment, backups and
monitoring.

## 4. Technical gaps (what exists but cannot carry the blueprint)

| Gap | Why it cannot be patched | Replacement |
|---|---|---|
| Balance overwrite model | No history to explain, verify or reverse | Ledger (C-35) |
| Daily-aggregate sales | Loses order, time, location, modifiers, refunds | SalesTransaction lines |
| Square-owned catalog | Inverts ownership; blocks Standalone and native POS | Owned catalog + mapping (C-36) |
| Unversioned flat recipes | Edits rewrite history | Versioned recipes |
| Square-gated scheduling | No alerts without Square | Jobs + per-tenant evaluation |
| Code-defined plans | Cannot express mode/location/capability dimensions | Plan/entitlement data |

## 5. Do-not-rebuild list (reliable and compatible)

Units within a dimension; barcode lookup; expiry date types and queue; invoice upload, dedupe,
extraction and matching; Square HTTP client, OAuth and webhook verification; Stripe lifecycle and
`allows()`; scheduler with advisory lock; storage; email pipeline; Sentry scrubbing; startup guards;
migration tests; the pure `transform/` rule style.
