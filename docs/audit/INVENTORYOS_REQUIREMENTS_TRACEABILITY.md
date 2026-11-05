# InventoryOS requirements traceability

Phase 2, 2026-11-04. Read-only. Traces every functional requirement in
[PRODUCT_SPECIFICATION](../product/PRODUCT_SPECIFICATION.md) (231 `*-FR-*` IDs; 198 of them are R1, including requirements split R1/R1.1) and
the SaaS and non-functional requirement groups to the existing app's code.

**How to read it.** Rows group requirements that share one implementation answer. **Every FR ID
appears in exactly one row** (checked by script, §4). Release tags come from the spec. "Existing
implementation" uses the statuses from the brief. Evidence paths are relative to the repository
root; labels as in the [technical audit](INVENTORY_PROJECT_TECHNICAL_AUDIT.md). Nothing is marked
implemented without code-level evidence.

Severity: **Critical** blocks R1 *and* threatens data integrity or a whole operating mode ·
**High** R1 requirement missing · **Medium** R1 requirement partial · **Low** R1.1/R2 ·
**Informational** Expansion, Advanced or Future, or already met.

## 1. Functional requirements

| # | Requirement | Phase 1 reference | Existing implementation | Evidence | Gap | Severity | Recommendation |
|---|---|---|---|---|---|---|---|
| 1 | Dashboard stock and expiry lists with next action | CMD-FR-02, CMD-FR-03, CMD-FR-12 | Implemented and verified (Connected only) | `app/web/dashboard.py:119,196`, `app/transform/alerts.py`, `tests/test_alerts.py` | Alerts evaluated only during Square sync (G2) | Medium | Evaluate alerts on a schedule for every tenant and after each posting |
| 2 | Exceptions, unresolved variances, data-completeness banner | CMD-FR-01, CMD-FR-07, CMD-FR-13 | Partially implemented | Health "today's actions" `app/orchestration/health.py` | No Exception model (G15) | High | Build Exception model (REC-FR-11..16) and feed the dashboard from it |
| 3 | Inventory value and money-metric date ranges | CMD-FR-04, CMD-FR-10 | Partially implemented | Latest-cost valuation `app/transform/inventory.py:132` | No snapshot cost basis; no range filter | Medium | Valuation from ledger cost snapshots (ANL-FR-01) |
| 4 | Activity panels: waste, pending receiving, feed, production, receiving | CMD-FR-05, CMD-FR-06, CMD-FR-09, CMD-FR-15, CMD-FR-16 | Partially implemented | Pending documents listed at `/app/receiving`; no feed, waste or production | Most sources do not exist | High | Build on the ledger and new modules |
| 5 | Sales-source status | CMD-FR-08 | Partially implemented | Last sync / failure `app/web/dashboard.py:174` | Connected only; no Standalone status | Medium | Per-location sales-source status (DR-31) |
| 6 | Onboarding checklist | CMD-FR-11 | Missing | Dashboard shows "Connect Square" only | No onboarding (G13) | High | SAAS §5 onboarding |
| 7 | Employee view hides costs | CMD-FR-14 | Missing | No roles | No roles (G4) | High | Roles + view rules (Module 13) |
| 8 | Location summaries | CMD-FR-17 | Missing | — | Expansion | Informational | Later |
| 9 | Item create/edit/deactivate, types, categories, search | INV-FR-01, INV-FR-02, INV-FR-03, INV-FR-10 | Partially implemented | Create only `app/web/dashboard.py:297`; free-text category | No edit/deactivate (P3), no item types, no category management | High | Item module with types (raw, prepared, finished, packaging, supply) |
| 10 | Units, packages, cross-dimension conversions, unit safety | INV-FR-04, INV-FR-05, INV-FR-11, INV-FR-12, INV-FR-13, INV-FR-14 | Partially implemented | `app/units.py:36-52`, `receive_unit`/`pack_size_base` `app/models.py:228-229`, `tests/test_units.py` | No item-specific cross-dimension conversions (juice yields), no tbsp/tsp, no base-unit change guard (G7) | High | Add ItemUnitConversion; extend unit table; keep `units.py` |
| 11 | Levels, current cost with history, valuation | INV-FR-06, INV-FR-07, INV-FR-24 | Partially implemented | `reorder_point_base`, `par_level_base`, `cost_per_base_cents` `app/models.py:215-217`; `/app/items/{id}/thresholds` | Cost overwritten, no history (DB7 context) | Medium | ItemCostHistory; cost snapshots on movements |
| 12 | Bulk import and starter templates | INV-FR-08, INV-FR-08a | Partially implemented | Square catalog import only `app/orchestration/sync.py:93-130` | No CSV import | Medium | CSV import with preview (also unblocks Standalone onboarding) |
| 13 | Barcode lookup | INV-FR-09 | Implemented and verified | `app/transform/barcodes.py`, `app/web/scanning.py:88`, `tests/test_barcodes.py` | — | Informational | Keep |
| 14 | Append-only ledger, balances as projection, idempotency, concurrency, history | INV-FR-15, INV-FR-16, INV-FR-17, INV-FR-18, INV-FR-20, INV-FR-21, INV-FR-22 | Missing | Seven balance writers ([DB audit §4](INVENTORY_PROJECT_DATABASE_AUDIT.md#4-inventory-update-logic)); probes R3–R6 | No ledger (C6), no locking (DB4) | Critical | Ledger first (C-27 step 1); route all writers through one posting service |
| 15 | Manual adjustments; negative-balance rule | INV-FR-19, INV-FR-23 | Missing | Only counts lower stock | — | High | On the ledger |
| 16 | Transfers | INV-FR-25 | Missing | — | Expansion | Informational | Ledger movement types reserved in R1 |
| 17 | Batch creation, fields, tracking flag, default shelf life | BAT-FR-01, BAT-FR-02, BAT-FR-03, BAT-FR-04 | Partially implemented | `StockBatch` `app/models.py:479`; created by receiving/scan/label `app/orchestration/receiving.py:356`, `app/web/dashboard.py:223`; `tracks_expiry` flag | No lot code, no production batches, no default shelf life per item | Medium | Extend `stock_batches` |
| 18 | Allocation, status, never-disappearing batches, batch history and adjustment | BAT-FR-05, BAT-FR-06, BAT-FR-07, BAT-FR-08, BAT-FR-12, BAT-FR-13 | Partially implemented | `allocate_fefo` `app/transform/expiry.py`; hard delete `app/web/dashboard.py:281` (probe R6) | Remaining derived, not allocated; hard delete (C9) | High | Movement-level batch allocation with `inferred`/`explicit` mode |
| 19 | Expiry thresholds, queue, auto-dispose policy, use-by labels | BAT-FR-09, BAT-FR-10, BAT-FR-11, BAT-FR-14 | Partially implemented | `/app/expiring` `app/web/dashboard.py:196-270`, `tests/test_expiry.py` | No per-item threshold override, no auto-dispose (no waste) | Medium | Reuse queue; dispose via waste movements |
| 20 | Printable labels, markdowns, expiry from photo | BAT-FR-15, BAT-FR-16, BAT-FR-17 | Partially implemented (ahead of plan) | Markdowns `app/transform/promotions.py`; date reading `app/extract/expiry.py` (never run on real photos) | R2/Future | Informational | Keep, disabled until R2 if needed |
| 21 | Suppliers, supplier items, purchase history, price change detection | PRC-FR-01, PRC-FR-02, PRC-FR-03, PRC-FR-08 | Partially implemented | Free-text `supplier` `app/models.py:224,380`; invoice lines keep prices | No supplier entity (DB7) | High | Supplier + SupplierItem; derive price history from receipts |
| 22 | Purchase orders, PO receiving, supplier API | PRC-FR-04, PRC-FR-05, PRC-FR-06, PRC-FR-07, PRC-FR-09 | Missing | README "deliberately not built" | R1.1 / Future | Low | Later |
| 23 | Invoice upload, extraction, matching, cap, scan-to-add | RCV-FR-02, RCV-FR-03, RCV-FR-04, RCV-FR-14, RCV-FR-16 | Implemented and verified (extraction mocked) | `app/orchestration/receiving.py:101-280`, `app/extract/invoice.py`, `app/transform/receiving.py`, `tests/test_receiving_flow.py`, `tests/test_invoice_ai.py` | No per-tenant extraction cap (S9); never run on a real invoice | Medium | Keep; add cap and verified-email gate |
| 24 | Manual receiving, quantity/price verification, discrepancies, drafts | RCV-FR-01, RCV-FR-05, RCV-FR-06, RCV-FR-07, RCV-FR-08 | Partially implemented | Review screen `app/web/receiving.py:123`; scan receive `app/orchestration/scanning.py:219` | No price variance, discrepancy types or draft completion | Medium | Extend receiving review |
| 25 | Posting exactly once, cost update, corrections, no stock change before posting | RCV-FR-09, RCV-FR-10, RCV-FR-11, RCV-FR-12, RCV-FR-13 | Partially implemented | Apply posts a count anchor `app/orchestration/receiving.py:329-331`; racy status check `:288`; upload alone changes nothing ✓ | Double apply (C5, probe R4); Square failure erases delivery (C4, probe R3); no reversal | Critical | Receipt movements on the ledger; conditional status transition; outbox |
| 26 | Credit memo / supplier return | RCV-FR-15 | Missing | — | R1.1 | Low | Later |
| 27 | Waste module | WST-FR-01, WST-FR-02, WST-FR-03, WST-FR-04, WST-FR-05, WST-FR-06, WST-FR-07, WST-FR-08, WST-FR-09, WST-FR-10, WST-FR-11, WST-FR-12 | Missing | Only predictive `AlertKind.WASTE_RISK` `app/models.py:62` | No waste events (C7) | Critical | WasteEvent + `waste` movements (DR-33) |
| 28 | Product catalog, recipes, prepared-item lines, yield, instructions, packaging cost split | RCP-FR-01, RCP-FR-02, RCP-FR-04, RCP-FR-05, RCP-FR-06, RCP-FR-14, RCP-FR-17 | Partially implemented | `SoldItem` = Square variation `app/models.py:171`; `RecipeComponent` `:240`; CLI-only writer `app/cli.py:351` | No recipe UI (P1), products owned by Square, no yields/prepared items (G5) | Critical | InventoryOS-owned catalog + Recipe/RecipeVersion/RecipeLine |
| 29 | Modifiers with inventory effect; start from another size | RCP-FR-03, RCP-FR-09 | Missing | — | — | High | Modifier model (scale/add in R1) |
| 30 | Versioning and history | RCP-FR-07, RCP-FR-08, RCP-FR-16, RCP-FR-18 | Missing | No version or effective date (DB9) | — | High | RecipeVersion effective-dated; consumption uses version at `occurred_at` |
| 31 | Recipe cost, margin, completeness, price-change impact | RCP-FR-10, RCP-FR-11, RCP-FR-12, RCP-FR-13 | Missing | — | — | High | Pure costing functions in `transform/` |
| 32 | Bulk recipe import | RCP-FR-15 | Missing | — | R1.1 | Low | Later |
| 33 | Production runs, yields, costs, voids, modes, batch codes, finished goods | PRD-FR-01, PRD-FR-02, PRD-FR-03, PRD-FR-04, PRD-FR-05, PRD-FR-06, PRD-FR-07, PRD-FR-08, PRD-FR-11, PRD-FR-12, PRD-FR-13, PRD-FR-14 | Missing | — | No production (G6) | Critical | ProductionOrder on the ledger; essential for juice bars and bubble tea |
| 34 | What-to-make, variance report, hour-level shelf life | PRD-FR-09, PRD-FR-10, PRD-FR-15 | Missing | — | R1.1/R2 | Low | Later |
| 35 | One sales model for every source; sales source of record; mode switch | POS-FR-17, POS-FR-18, POS-FR-19 | Missing | Only `sales_daily` aggregates `app/models.py:270` (DB10) | — | Critical | SalesSource + SalesTransaction/Line/Return (shared engine) |
| 36 | Square connection lifecycle, status, location mapping, integration health | POS-FR-01, POS-FR-02, POS-FR-03, POS-FR-27 | Partially implemented | `app/providers/square/oauth.py`, `app/web/dashboard.py:491-530` | No disconnect/revoke (SQ5), over-scoped (S6), locations created not mapped | High | Keep OAuth/encryption; add lifecycle and mapping |
| 37 | Catalog sync with modifiers, mapping UI, reprocess | POS-FR-04, POS-FR-06, POS-FR-14 | Partially implemented | Variation sync `app/orchestration/sync.py:93-130` | No modifiers, no mapping UI, no reprocess | High | ExternalProduct/Modifier + mapping |
| 38 | Order-line ingestion, per-line consumption, dedupe, daily reconciliation | POS-FR-05, POS-FR-07, POS-FR-11, POS-FR-12 | Missing | Hourly 60-day re-read into daily aggregates `app/orchestration/sync.py:133-206` | C2, C3, SQ1, SQ2 | Critical | Rebuild ingestion on SalesTransaction with cursor + webhooks |
| 39 | Refunds, voids, discounts | POS-FR-08, POS-FR-09, POS-FR-26 | Partially implemented | Refunds ignored `app/transform/square_normalize.py:138`; COMPLETED-only | Refunds not recorded; completed-then-voided unhandled | Medium | SalesReturn; reversal on void |
| 40 | Sync history, errors, retry | POS-FR-10 | Partially implemented | `sync_runs`; swallowed write failures `app/orchestration/receiving.py:387-402` | C4 | High | Outbox + exceptions |
| 41 | Adapter boundary | POS-FR-15 | Partially implemented | normalize → load seam; Square defaults across models/orchestration | Square leaks outside the adapter | Medium | Adapter interface + static check (MNT-08) |
| 42 | Write-back, generic sales API, native POS | POS-FR-13, POS-FR-16, POS-FR-25 | Partially implemented (write-back exists, defective) | `app/orchestration/sync.py:208-320` | R1.1/Future | Low | Disable write-back until outbox (R1.1, C-34) |
| 43 | Count sessions, expected snapshot, variance, review, history | REC-FR-01, REC-FR-02, REC-FR-03, REC-FR-05, REC-FR-06, REC-FR-07, REC-FR-08, REC-FR-09 | Partially implemented | Single-item counts `app/web/dashboard.py:395`, `app/web/scanning.py:221` | No sessions, no expected snapshot, no variance movement | High | Count sessions generating `count_variance` movements |
| 44 | Exception model and lifecycle | REC-FR-11, REC-FR-12, REC-FR-13, REC-FR-14, REC-FR-15, REC-FR-16 | Partially implemented | `alerts` with 5 kinds `app/models.py:58-63,289` | No exception types beyond alerts (G15) | High | Generalize `alerts` into Exception |
| 45 | Blind counts, batch-level counts | REC-FR-04, REC-FR-10 | Missing | — | R1.1 | Low | Later |
| 46 | Reorder suggestions with formula, supplier grouping, count-first, CSV, never auto-buy | ROR-FR-01, ROR-FR-02, ROR-FR-03, ROR-FR-04, ROR-FR-05, ROR-FR-07, ROR-FR-08 | Partially implemented | `reorder_lines` `app/transform/health.py:665`, `/app/reorder(.csv)` `app/web/health.py:50,75`; formula explained in `reorder.html:7` | Velocity from Square sales only; CSV gated to Pro (PA2); supplier is free text | Medium | Keep the rule engine; feed it ledger usage; ungate CSV |
| 47 | Prepared-item demand, demand forecast | ROR-FR-06, ROR-FR-09 | Missing | — | R1.1/R2 | Low | Later |
| 48 | Cost basis rule, valuation as of date, realized cost of sales | ANL-FR-01, ANL-FR-02, ANL-FR-12 | Partially implemented | Latest-cost valuation `app/transform/inventory.py:132` | No snapshots, no as-of | High | Cost snapshot on every movement |
| 49 | Consumption, waste, expiry-loss, margin, variance, receiving reports; count-based usage | ANL-FR-03, ANL-FR-04, ANL-FR-05, ANL-FR-06, ANL-FR-07, ANL-FR-08, ANL-FR-14 | Partially implemented | Expiry "money at risk" in health score `app/transform/health.py` | Most need ledger, waste, recipes | High | Reports read ledger only |
| 50 | Weekly email summary; CSV of every report | ANL-FR-09, ANL-FR-13 | Partially implemented | Weekly report `app/orchestration/reporting.py`, `app/transform/reports.py` (rendered, never delivered); reorder CSV only | Real email; CSV everywhere | Medium | Keep report pipeline; add exports |
| 51 | Theoretical vs actual, turnover, supplier trends, location comparison, forecasting | ANL-FR-10, ANL-FR-11, ANL-FR-15, ANL-FR-16, ANL-FR-17 | Missing | — | R1.1/R2/Expansion/Advanced | Low | Later |
| 52 | Invitations, roles, backend permissions, activity by person, revocation, last Owner, approvals, location scope | STF-FR-01, STF-FR-02, STF-FR-03, STF-FR-05, STF-FR-06, STF-FR-07, STF-FR-08, STF-FR-10 | Missing | One user per tenant `app/models.py:125,128`; probe PA3 | No memberships/roles (G4); sessions not revocable (S5) | High | Membership + permission keys + server-side sessions (C-32) |
| 53 | Attribution on every record | STF-FR-04 | Partially implemented | `inventory_events.user_id` on scans only | Most writers record no actor | High | Actor on every ledger transaction |
| 54 | Shared-device PIN switch | STF-FR-09 | Missing | — | R1.1 | Low | Later |
| 55 | Business, location, units, reasons, alert and recipe settings; operating mode per location | ADM-FR-01, ADM-FR-02, ADM-FR-03, ADM-FR-04, ADM-FR-05, ADM-FR-06, ADM-FR-14 | Partially implemented | Email preferences only `app/web/settings.py:55`; tenant timezone/report schedule have no UI | Locations are Square-shaped (G11) | High | Settings module; de-Square locations |
| 56 | POS connections and user management screens | ADM-FR-07, ADM-FR-08 | Partially implemented | Connect only | See rows 36, 52 | High | With rows 36 and 52 |
| 57 | Subscription and entitlements view | ADM-FR-09, ADM-FR-15 | Partially implemented | Stripe Portal `app/web/billing.py:78`; trial banner `app/entitlements.py:167-210` | Starter/Pro, not Complete/Standalone | Medium | Data-driven plan catalogue |
| 58 | Data export, business audit history, account deletion | ADM-FR-10, ADM-FR-11, ADM-FR-12 | Missing | Reorder CSV only | — | High | Export zip, AuditEvent, deletion workflow |
| 59 | Founder console | ADM-FR-16 | Partially implemented | `app/cli.py` | No UI; acceptable at launch scale | Medium | Keep CLI; add audited support access later |
| 60 | Multi-location administration | ADM-FR-13 | Missing | — | Expansion | Informational | Later |
| 61 | Standalone operations | STD-FR-01, STD-FR-02, STD-FR-03, STD-FR-04, STD-FR-05, STD-FR-06, STD-FR-07, STD-FR-08, STD-FR-09, STD-FR-10, STD-FR-11 | Missing | No location without Square (probe PA1); sales only from Square | G1, G2, G14 | Critical | C-27 step 2, right after the engine |

## 2. SaaS and non-functional requirement groups

| # | Requirement group | Phase 1 reference | Existing implementation | Evidence | Gap | Severity | Recommendation |
|---|---|---|---|---|---|---|---|
| 62 | Tenancy and isolation | SAAS-T-01..11, DR-09, DR-35 | Partially implemented | [SaaS §1](SAAS_READINESS_AUDIT.md#1-multi-tenancy-code-run) | No central mechanism (S8), merchant routing (S7), snack-box tenant code vs C-30 | Critical | C-33: scoped access, then RLS |
| 63 | Authentication | SAAS-A-01..10, C-31, C-32 | Partially implemented | [SaaS §2](SAAS_READINESS_AUDIT.md#2-authentication-c-31-c-32-code-run) | S1, S2, S4, S5, no MFA | High | Finish in-app auth (C-32) |
| 64 | Billing and plan catalogue | SAAS-B-01..21, E-01..10 | Partially implemented | [SaaS §4–5](SAAS_READINESS_AUDIT.md#4-billing-code-run) | Starter/Pro; quantity 1; core gated | High | Data-driven catalogue; per-location quantity |
| 65 | Onboarding | SAAS §5 | Missing | `app/web/auth.py:29-82` | G13 | High | Build with Standalone |
| 66 | Export, retention, deletion | SAAS-D-01..06 | Missing | — | — | High | With ADM-FR-10/12 |
| 67 | Data integrity rules | DR-01..36 | Mostly missing | [DB audit §3–4](INVENTORY_PROJECT_DATABASE_AUDIT.md#3-domain-model-compatibility-with-the-blueprint) | Ledger, idempotency, locking, constraints | Critical | Ledger + constraints in the first engine milestone |
| 68 | Security controls | SEC-01..21 | Partially implemented | [05-security](05-security.md); webhook HMAC, token encryption, Sentry scrubbing done | S1–S12, no headers (S10), no CSRF (S4) | High | Security batch before first customer |
| 69 | Reliability and data safety | REL-01..11 | Partially implemented | Advisory-lock scheduler `app/db.py:169`; no backups (O2); swallowed failures (C4) | Backups, outbox, transactional postings | High | With ledger and deploy |
| 70 | Performance | PERF-01..07 | Unknown due to insufficient evidence | Not load-tested; blocking I/O in async routes (C8) | — | Medium | Fix C8; load test before launch |
| 71 | Scalability | SCL-01..07 | Partially implemented | Stateless web tier ✓ (signed cookies); 60-day re-read per tenant (SQ2) | Per-tenant fair jobs, ledger indexing | Medium | Incremental sync cursor; job table |
| 72 | Observability | OBS-01..07 | Partially implemented | `/healthz` `/readyz` `app/web/ops.py:18-24`; Sentry `app/monitoring.py`; plain logs (O4) | No request ids, no integrity metrics | Medium | JSON logs; integrity metrics |
| 73 | Maintainability and CI | MNT-01..08 | Partially implemented | Modular monolith ✓, migration tests ✓; no CI, no lint/type config, T1 | — | High | CI first (O1) |
| 74 | Accessibility and usability | UX-01..09 | Unknown due to insufficient evidence | Templates not audited for WCAG in this phase | — | Medium | Audit when UI work starts |

## 3. Gap register (G1–G15)

New in Phase 2; baseline IDs keep their meaning ([01-executive-audit](01-executive-audit.md#findings-register)).

| ID | Gap | Evidence | Severity |
|---|---|---|---|
| G1 | Locations, sellable products, recipes and sales can be created only by the Square sync or the CLI seed; a tenant without Square cannot create a single stock item | `app/load/upsert.py:43,74,233`, `app/cli.py:277,338,351,369`, `app/web/dashboard.py:319-321`; probe PA1 [RUN] | Critical |
| G2 | Low-stock and expiry alerts are evaluated only inside the Square sync | `app/orchestration/scheduler.py:37-45`, `app/orchestration/sync.py:182` [CODE] | High |
| G3 | Plan catalogue in code (Starter/Pro), core features gated, no per-location quantity, marketing prices hard-coded | `app/entitlements.py:27-51`, `app/web/billing.py:56`, `app/web/marketing.py:16,30,43`; probe PA2 [RUN] | High |
| G4 | No memberships or roles; one person cannot belong to two businesses | `app/models.py:125,128`; probe PA3 [RUN] | High |
| G5 | Recipe model is a flat sold-item → stock-item quantity; no versions, modifiers, yields, prepared items; CLI-only | `app/models.py:240`, `app/cli.py:351` | Critical |
| G6 | No production module | — | Critical |
| G7 | Units: same-dimension only, no item-specific conversions, no tbsp/tsp | `app/units.py:36-52` | High |
| G8 | No supplier entity (= DB7) | `app/models.py:224,380` | High |
| G9 | No business audit history (AuditEvent) | — | High |
| G10 | Data export limited to the reorder CSV | `app/web/health.py:75` | High |
| G11 | Location model is Square-shaped: `provider_location_id` required; no address, cutoff or operating mode | `app/models.py:157-169` | High |
| G12 | `TrackMode.PROVIDER` is the default; Square counts overwrite balances | `app/models.py:212`, `app/load/upsert.py:197` | High |
| G13 | No onboarding; the only path is "Connect Square" | `app/web/templates/app/dashboard.html:19-37` | High |
| G14 | No Standalone sales capture and no SalesTransaction model | `app/models.py:270` | Critical |
| G15 | No Exception model beyond five alert kinds | `app/models.py:58-63` | High |

## 4. Coverage check

A script extracted every `*-FR-*` ID from the spec's requirement tables and from §1 above:
231 IDs in the spec, 231 in this matrix, each exactly once, none extra. Re-run it after editing
either document (it is part of the audit's consistency check recorded in
[README](README.md#5-verification)).

## 5. Scorecard

48 of the 61 rows in §1 contain at least one R1 requirement (counted by the same script):

| Status | Rows |
|---|---|
| Implemented and verified | 3 — barcode lookup (row 13) without caveat; dashboard lists (row 1) Connected only; invoice receiving (row 23) with extraction mocked |
| Partially implemented | 31 |
| Missing | 14 |

Interpretation: the existing app covers **receiving, batches/expiry, barcodes, units within a
dimension, alert rules, reorder rules, Stripe lifecycle and the Square transport layer**. It does
not cover the blueprint's core: **ledger, recipes, production, waste, sales model, Standalone,
roles**. That shape is why the [reuse matrix](REUSE_ADAPT_REBUILD_MATRIX.md) recommends keeping the
shell and periphery and rebuilding the inventory core inside it.
