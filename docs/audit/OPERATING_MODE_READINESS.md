# Operating-mode readiness

Phase 2, 2026-11-04. Read-only. Baseline: [PRICING_AND_PACKAGING](../product/PRICING_AND_PACKAGING.md),
spec §Operating modes, Modules 9 and 15, decisions C-27 (engine → Standalone → Square) and C-29
(release plan). Prices below are the owner's hypotheses; nothing here treats them as software
logic. Labels as in the [technical audit](INVENTORY_PROJECT_TECHNICAL_AUDIT.md).

## Summary

| Offering / dimension | Readiness of the existing app | Main blockers |
|---|---|---|
| **Complete** (Connected, hypothesis $99/location/month) | **Low.** A Square-only prototype of the Connected path exists; stock numbers are wrong for the target customer | No ledger (C6); daily-aggregate sales (C2, C3); no recipe/mapping UI; write-back failures lost (C4); never run live |
| **Standalone** (no POS, hypothesis $99/month) | **Not usable.** A tenant without Square cannot create a location, so cannot create any stock item | G1 (probe PA1), G2, no sales capture (G14), no recipes UI (G5) |
| **InventoryOS POS** (future, hypothesis $149–199+/month) | Not started; correctly so | Needs the SalesTransaction model first; decisions listed in §3 |
| **Multi-location** | **Data model partly ready**, behaviour not | Location-blind consumption (C3), no transfers, no location-scoped roles |
| **Advanced** | Not started; two precursors exist (health score, reorder suggestions) | Needs ledger history and order-level sales |

## 1. Complete — customer keeps its POS

| Capability | Existing | Evidence | Needed for commercial reliability |
|---|---|---|---|
| External POS connection | Square OAuth, encrypted tokens, refresh | `app/providers/square/oauth.py`, `app/orchestration/sync.py:72-90` | Disconnect/revoke, scope split (S6), merchant uniqueness (S7), sandbox → live run |
| Sales ingestion | Hourly, 60-day re-read, daily aggregates | `app/orchestration/sync.py:133-206` | Order-line ingestion with `occurred_at` + location, incremental cursor, `order.*` webhooks (POS-FR-05) |
| Product mapping | Automatic variation → sold item; Square-tracked SKUs auto-linked | `app/load/upsert.py:61-134` | ExternalProduct/Modifier + mapping UI + unmapped-product exception (POS-FR-04, -06, REC-FR-12) |
| Recipe mapping | Flat recipe per sold item, CLI-only | `app/models.py:240`, `app/cli.py:351` | Recipe UI, versions, modifiers, prepared items (Module 7) |
| Theoretical consumption | Derived engine from daily totals | `app/transform/inventory.py:60-163` | Rebuild on the ledger per order line (WF-12) — current engine loses same-day sales (C2) and crosses locations (C3) |
| Reconciliation | Hourly full re-read; single-item counts | `tests/test_square_catalog.py::test_the_hourly_sync_recovers_a_sale_whose_webhook_never_arrived` | Count sessions with expected snapshot and variance (Module 10); daily POS reconciliation (POS-FR-12) |
| Refunds and voids | Refunds ignored (safe for stock, wrong for sales); voids before completion fine | `app/transform/square_normalize.py:138` | Record returns; completed-then-voided reversal (POS-FR-08, -09) |
| Integration failure recovery | Sync failures logged to `sync_runs`; write failures swallowed | `app/orchestration/receiving.py:387-402` | Outbox + retry + exceptions (POS-FR-10) |
| Multiple POS providers | `provider` columns, normalize/load seam | `app/models.py:148,163,179` | Adapter interface (POS-FR-15) — build the seam, not a second adapter |

**Verdict:** the Square HTTP/OAuth/webhook layer is a sound adapter starting point; everything
between the adapter and the stock figure must be rebuilt around SalesTransaction and the ledger.
Per C-27, that rebuild is the shared engine and is done once for both modes.

## 2. Standalone — no POS and none wanted

### What depends on Square today [CODE, RUN]

| Dependency | Effect on a Standalone tenant | Evidence |
|---|---|---|
| Locations are created only by Square sync (or CLI seed) | **No location → cannot create any stock item → nothing else works** | `app/load/upsert.py:43`, `app/web/dashboard.py:319-321`; probe PA1 [RUN] |
| Sellable products only from the Square catalog | Cannot define products or recipes | `app/load/upsert.py:74`, `app/cli.py:338` |
| Sales only from Square | Derived (weight/volume) stock never falls except by counts | `app/load/upsert.py:233` |
| Alert evaluation runs only inside the Square sync | **No low-stock or expiry alerts**, ever | `app/orchestration/scheduler.py:37-45`, `app/orchestration/sync.py:182` |
| Onboarding is "Connect Square" | Dead end | `app/web/templates/app/dashboard.html:19-37` |
| `TrackMode.PROVIDER` default | Harmless without Square (items created manually are `DERIVED`/`MANUAL`, `app/web/dashboard.py:363`) | `app/models.py:212` |

### What would work without Square, once a location exists [CODE]

Invoice/scan receiving, batches and expiry queue, barcode lookup, single-item counts ("count
kept locally", `app/web/dashboard.py:428`), weekly report and health snapshots (run for every
tenant, `app/orchestration/scheduler.py:54-98`), billing.

### Against Module 15 and the Standalone workflows

| Requirement | Status |
|---|---|
| STD-FR-01 no POS dependency for core | **Not met** (above) |
| STD-FR-02 daily sales entry | Missing |
| STD-FR-03 sales file import | Missing |
| STD-FR-04 direct usage record | Missing |
| STD-FR-05 correction by reversal + re-post | Missing (no ledger) |
| STD-FR-06 `sales_missing` exception | Missing |
| STD-FR-07 origin labelling | Missing |
| STD-FR-08 count-based actual usage (ANL-FR-14) | Missing (no count sessions or ledger) |
| STD-FR-09 reorder fallback to count-based usage | Missing |
| STD-FR-11 adopt a POS later via cutover | Missing (no mode concept) |
| Manual adjustments, production, waste, recipes | Missing (G5, G6, C7) |
| Standalone onboarding and dashboard | Missing |

**Verdict:** Standalone is the larger build but has **no external dependency**, which is why C-27
puts it first: it exercises the shared engine end to end before the Square adapter is rebuilt.

## 3. Future InventoryOS POS — prerequisites and decisions to make now

Not part of R1 (C-29) and not recommended for it: no evidence in this audit justifies changing the
strategy. Prerequisites and their state:

| Prerequisite | State | Notes |
|---|---|---|
| SalesTransaction/Line/Return with `source` | Missing | Native POS writes the same records (POS-FR-25); build once for Standalone/Connected |
| Product catalog with variations, modifiers, prices per location | Missing (Square-owned today) | Needed by Standalone daily entry anyway |
| Consumption engine per sales line, idempotent | Missing | Same engine (WF-12) |
| Payment processing | Only the snack-box Square payment links | Would be processor-led; never stored card data (SEC-20) |
| Taxes, receipts, tips, cash, end-of-day close, offline mode, hardware | None | Large, regulated, support-heavy (spec §Native POS vs external POS) |
| Order lifecycle | JuJu has one (separate code base) | Concept reference only |

**Architectural decisions to make now so a native POS needs no rework [RECOMMENDED]:**

1. All sales from every source enter as **SalesTransaction** rows with a `source` (manual, import,
   square, api, future `inventoryos_pos`); consumption never reads provider payloads.
2. **InventoryOS owns the product catalog** (products, variations, modifiers); POS catalogs are
   *mapped* to it, not the other way round. Today the reverse is true (`sold_items` = Square
   variations).
3. Location carries its **sales source of record per business date** (DR-31), so a location can
   later switch to the native POS with a cutover.
4. Money fields per sales line (gross, discounts, net, tax) exist from the start even though
   Standalone fills only some.
5. Nothing about payments is modelled until validation (PD-35) — no speculative tables.

## 4. Multi-location and Advanced

| Capability | Existing | Foundational or incremental |
|---|---|---|
| Multiple organizations | `tenant_id` everywhere; one user per org | **Foundational**: memberships, central scoping |
| Multiple locations | `locations`, per-location stock rows, plan location limit | **Foundational**: location as a dimension of ledger, sales and roles (SCL-07) |
| Location-level inventory | Per-location `stock_items` | Incremental once the ledger carries location |
| Location-correct consumption | Broken (C3) | Foundational — fixed by order-line ingestion with location |
| Transfers | None | Incremental on the ledger (`transfer_out`/`transfer_in`) |
| Consolidated reporting | None | Incremental on the ledger |
| Location-scoped roles | None | Foundational in the model (STF-FR-10, R1 model), incremental UI |
| Advanced analytics, forecasting | Health score (`app/transform/health.py`), reorder suggestions (`app/web/health.py`) | Incremental; need 60+ days of ledger history |
| Automation, API access | None; no JSON API | Incremental after the sales API (POS-FR-16, R1.1) |
| Expanded integrations | Square only | Incremental after the adapter interface |

**Rule of thumb:** everything that needs *location* or *organization* in its key is foundational
and belongs in the R1 data model even when its UI is later; analytics, automation and integrations
are incremental.

## 5. Subscription and entitlements

Detail in [SAAS_READINESS_AUDIT §4–5](SAAS_READINESS_AUDIT.md#4-billing-code-run). In short: lifecycle logic
is reusable; the plan catalogue (`Plan` enum, `CAPABILITIES` dict, `PLAN_IDS`), per-location
quantity billing and operating-mode entitlements must become configuration. No pricing value may
move into domain code; the hard-coded marketing prices (`app/web/marketing.py:16,30,43`) should
read from the same configuration.
