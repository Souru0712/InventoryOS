# Operating-mode implementation plan

Phase 3, 2026-11-05. Planning only. How the three operating modes are delivered on **one engine**
(DR-29..31). Current readiness: [operating-mode audit](../audit/OPERATING_MODE_READINESS.md) —
Complete low, Standalone not usable (probe PA1), native POS not started. Task IDs refer to
[DEVELOPMENT_TASK_BREAKDOWN](DEVELOPMENT_TASK_BREAKDOWN.md). Prices are hypotheses held in billing
configuration (DR-36); nothing here depends on them.

## 1. One engine, three sources

```
                         SHARED (built once, Stages 1–2)
   identity & access · locations · items/units/conversions · LEDGER · counts · adjustments
   products & versioned recipes · CONSUMPTION ENGINE · batches & expiry · production · waste
   suppliers & receiving · reorder · exceptions · reports · onboarding core · billing/entitlements
                 ▲                         ▲                          ▲
     Standalone source           Connected source               Native POS source
     (Stage 2, T2.12–T2.14)      (Stage 3, T3.01–T3.12)         (Stage 8 evaluation only)
     daily sales entry           adapter interface + Square     checkout writes
     sales file import           order-line ingestion           SalesTransactions
     direct usage records        mapping, refunds, recon        (origin inventoryos_pos)
```

What differs between modes is **only** how SalesTransactions arrive and the screens that manage
that arrival. Mode is a property of a location's sales source of record per business date
(DR-31), not of the organization's plan or the codebase (C-06, DR-32).

## 2. Shared functionality (both launch modes)

| Capability | Tasks | Why both modes need it |
|---|---|---|
| Tenancy, auth, roles | T1.02–T1.12 | Every customer |
| Locations with time zone and business-day cutoff | T1.04 | Business dates for sales, reports, expiry |
| Items, units, conversions | T1.13–T1.15 | Recipes and receiving in any unit |
| Ledger, adjustments, counts, verification | T1.16–T1.23 | The product's core promise |
| Products, versioned recipes, costing | T2.01–T2.03 | Consumption and margins |
| Sales model + consumption engine | T2.04–T2.05 | Standalone entries and POS lines use the same engine |
| Batches, expiry, production, waste | T2.06–T2.09 | Juice-bar daily operations |
| Suppliers, receiving, reorder | T2.10, T2.11, T2.15 | Purchasing loop |
| Reports, weekly summary, settings, employee home | T2.16–T2.19, T2.21 | Owner value and staff speed |
| Onboarding core (step 0 question, checklist, imports, opening count) | T2.20 | Both paths share 8 of 10 steps |
| Plans and entitlements | T4.01–T4.03 | Complete and Standalone differ only in sales source (E-05) |

## 3. Standalone (no POS, doesn't want one)

**Goal.** A complete, useful product without any POS credential or connectivity (STD-FR-01).
Delivered **before** Connected (C-27) because it has no external dependency and proves the
engine end to end.

| Requirement | Task | Notes |
|---|---|---|
| Daily sales entry per variation, modifiers optional (PD-38) | T2.12 | Manager+ by default; Owner may allow Employees (PD-37) |
| Correction of a saved day by reversal + re-post | T2.12 | WF-26; AC-SAL-03 |
| `sales_missing` for open days without sales | T2.12 | AC-SAL-05 |
| Sales file import with column mapping and unmatched-name review | T2.13 | AC-SAL-04; also serves "POS we don't support yet" |
| Direct usage records (staff meals, samples, catering by weight) | T2.14 | `manual_consumption` movements |
| Origin labelling on every sales figure | T2.04, T2.16 | AC-SAL-08 |
| Count-based actual usage as the primary usage measure | T2.17 | ANL-FR-14; works with zero sales data |
| Reorder falls back to count-based usage and says so | T2.15 | STD-FR-09 |
| Standalone onboarding path | T2.20 | AC-SAAS-10 |
| No-POS end-to-end suite in CI | T2.22 | AC-SAL-01, AC-SAL-07 |

**Honest limitations shown in the product:** sales figures are only as good as what is entered or
imported; daily (not per-order) granularity; theoretical-vs-actual only over days with sales (R1.1,
STD-FR-10). The dashboard shows data coverage (CMD-FR-13).

**Definition of done (M4):** a business with no POS configured completes onboarding and runs a full
operating week — receiving, production, waste, daily sales, counts, reports — with every R1
workflow test passing and no POS credentials present (AC-SAL-01, AC-SAAS-10).

## 4. Complete (keeps its POS) — Square first

**Goal.** The same product with sales arriving automatically from the customer's POS.

| Requirement | Task | Reused code |
|---|---|---|
| Adapter interface, normalized types, static boundary check | T3.01 | normalize/load seam (`app/transform/square_normalize.py`, `app/load/upsert.py`) |
| Connection lifecycle: OAuth, product-only scopes, disconnect, revoke detection, one active connection per merchant + location | T3.02 | `app/providers/square/oauth.py`, token encryption |
| Location mapping; catalog → external products and modifiers | T3.03 | `app/orchestration/sync.py` catalog pull |
| Product mapping UI, unmapped exceptions, reprocess | T3.04 | — |
| Order-line ingestion with cursor + backfill → consumption engine | T3.05 | `SquareClient.orders` |
| Webhooks with event-id dedupe via jobs | T3.06 | `app/providers/square/webhooks.py` HMAC |
| Refunds, voids, discounts | T3.07 | — |
| Reconciliation and failure recovery | T3.08 | `sync_runs` |
| Integration health and dashboard status | T3.09 | — |
| Mode switch with cutover | T3.10 | — |
| Sandbox end-to-end run | T3.11 | `scripts/` sandbox seeding script |
| Remove the legacy hourly 60-day sync and `sales_daily` dependency | T3.12 | — |

**Not in R1 for Connected:** Square write-back (C-34; R1.1 via outbox, T4B.02); a second POS
(T7.05, by demand). Toast is noted as adapter demand only (owner, 2026-11-04).

**Definition of done (M5):** sandbox merchant connected; a completed order of mapped items posts
consumption within 5 minutes; duplicate webhooks and reconciliation never double-post; refunds
recorded without restock; revoked token surfaces an exception (AC-POS-01..12, AC-SAAS-09).

## 5. Mode switching

Standalone → Connected (and back) is a location setting with a **cutover business date**
(WF-25, T3.10): history is kept; POS sales before the cutover are not ingested as consumption;
manual entries after it are refused or flagged `sales_source_overlap` (DR-31, AC-SAL-06). Billing
follows the switch (PD-39, T4.02, AC-SAAS-11). No data migration, no re-setup (STD-FR-11).

## 6. Future InventoryOS POS — prerequisites, scope and decision point

**Not in the launch roadmap** (C-29). Stage 8 is an evaluation milestone only.

| Topic | Assessment |
|---|---|
| Architectural prerequisites | All delivered by R1 if the plan is followed: owned catalog with variations, modifiers and per-location prices (T2.01, C-36); SalesTransaction with origin and money fields (T2.04); source of record per date (DR-31); consumption engine (T2.05); entitlements with operating mode as a dimension (T4.01) |
| New domains it would require | Checkout/cart and order lifecycle; payments (authorization, capture, refunds, tips); tax calculation per jurisdiction; receipts (print/email/SMS); cash handling and drawer reconciliation; end-of-day close; discounts and comps at the till; offline operation and later sync; device and printer management; staff clock-in possibly |
| External dependencies | A payment processor with card-present hardware and hosted card entry (e.g. Square, Stripe Terminal, Adyen — examples, not evaluated); receipt printers, cash drawers, card readers; tax rate data |
| Security and compliance | Keep card data out of InventoryOS entirely (processor-hosted fields, terminal SDKs) to minimize PCI scope; sales tax and receipt rules; consumer refund rules; uptime during service becomes business-critical |
| Operations | Real-time support during trading hours; hardware logistics and replacement; outage playbooks; much higher support load than inventory software |
| Relative complexity [INFERENCE] | Several times the Connected adapter (Stage 3): new UI surface for speed at the till, offline sync, payments state machine, hardware. Not estimable responsibly before Stage 8 discovery |
| Costs not in the subscription | Processing fees, hardware, card readers, third-party services (passed through; brief §4.3) |
| When it makes commercial sense | Only when **all** hold: the owner's $1,000 MRR gate (C-26); PD-35 evidence (signup interest + ≥ 10 qualified prospects willing to pre-commit at the hypothesis price); a build-vs-partner-vs-integrate study (T8.02) showing a margin after processing and support costs |
| Default recommendation | **Integrate or partner before building**: Scenario C prospects get Standalone or a supported POS + Complete (PD-36); measure interest at signup (SAAS-B-20) |

## 7. Dependencies between modes

```
Stage 1 core ──► T2.04 sales model ──► T2.05 consumption engine ──┬─► Standalone capture (T2.12–T2.14) ─► M4
                                                                   └─► Square adapter (T3.01–T3.12) ───► M5
T2.01 owned catalog ─► T3.04 mapping            T4.01 entitlements ─► T4.02 billing per mode/location
T3.10 mode switch needs both M4 and M5          Native POS (Stage 8) needs M6 + revenue gate + PD-35
```
