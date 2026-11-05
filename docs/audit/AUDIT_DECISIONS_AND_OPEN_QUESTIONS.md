# Audit decisions and open questions

Phase 2, 2026-11-04. Read-only. **All ten decisions in §3 were approved by the owner on 2026-11-04
("Approve all ten") and recorded as C-33..C-42** in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md#1-decisions-made-confirmed-by-the-owner).
The owner actions (§4) and questions (§5) remain open.

## 1. Confirmed findings (evidence-backed)

| # | Finding | Evidence | Label |
|---|---|---|---|
| 1 | The existing app's test suite passes: 542 passed + 6 Postgres-only skipped on SQLite this session; 548/548 on PostgreSQL 16 earlier today | §1 of [technical audit](INVENTORY_PROJECT_TECHNICAL_AUDIT.md#1-verification-done-this-session) | RUN / RUN-earlier |
| 2 | There is no inventory ledger; balances are overwritten by seven code paths | C6; [DB audit §4](INVENTORY_PROJECT_DATABASE_AUDIT.md#4-inventory-update-logic) | CODE |
| 3 | Derived consumption loses same-day sales and crosses locations; invoices can be applied twice; failed Square writes are erased | probes R1–R4 | RUN |
| 4 | A business without Square cannot create a location or a stock item through the app | probe PA1 | RUN |
| 5 | Alerts are evaluated only during a Square sync | `app/orchestration/scheduler.py:37-45`, `app/orchestration/sync.py:182` | CODE |
| 6 | Plans are Starter/Pro in code; Starter gates core features; checkout is one fixed unit | probe PA2; `app/web/billing.py:56` | RUN / CODE |
| 7 | One person cannot belong to two businesses; there are no roles | probe PA3 | RUN |
| 8 | One Square merchant can attach to two businesses | probe PA4 | RUN |
| 9 | No recipe, production, waste, supplier, adjustment, count-session, staff, export or audit features exist | route list; [traceability](INVENTORYOS_REQUIREMENTS_TRACEABILITY.md) | CODE |
| 10 | The code had no git remote and has no CI (remote added 2026-11-05: private `Souru0712/InventoryOS`) | `git remote` empty on 2026-11-04; `origin` set and in sync on 2026-11-05 | RUN |
| 11 | No customer data exists; the local database is demo seed data at migration head | read-only row counts | RUN |
| 12 | JuJu has a working signed-movement ledger, production transactions and waste, single-tenant, TypeScript/Supabase; its CI passed on the audited code (documented) | [JuJu audit](JUJU_TECHNICAL_AUDIT.md); `git diff --stat 34ea7ec..HEAD` docs-only | CODE / documented |

## 2. Uncertainties

| # | Unknown | Why | How to resolve |
|---|---|---|---|
| U1 | Behaviour against a real Square sandbox seller or merchant | Never run | NEEDS-YOU §1 sandbox script, then a live pilot (NFR §8 gate 4) |
| U2 | Stripe behaviour in test mode | Never run; recorded payloads only | Test-mode lifecycle run (gate 5) |
| U3 | Invoice/date extraction accuracy and cost on real documents | Never run on real documents | Trial with the extraction-quality report on the owner's real invoices (convenience store, snack-box project); produce-supplier invoices still needed later |
| U4 | Performance under load (PERF-01..07) | Not load-tested; C8 suggests stalls | Load test after C8 fix |
| U5 | Accessibility (WCAG 2.1 AA) | Templates not audited in this phase | Audit when UI work starts |
| U6 | Dependency vulnerabilities | `pip-audit` not installed; installing was out of bounds | Add to CI |
| U7 | JuJu test results on this machine | Node 24.15.0 < 24.22.2 required | Upgrade Node; or rely on CI |
| U8 | Whether health-score metrics match the blueprint's metric definitions | Not compared line by line | Review during the reporting milestone |
| U9 | PostgreSQL re-run this session | Needs Docker; earlier run today passed | Run in CI once CI exists |

## 3. Decisions requiring owner approval

| ID | Decision | Options | Recommendation (approved → C-ID) | Why | Blocks |
|---|---|---|---|---|---|
| AD-01 (PD-18) | **How the code enforces tenant isolation** | A scoped data-access layer · B Postgres RLS · C schema per tenant | **A now, B later as defense in depth** → C-33 | Testable on SQLite and Postgres; fits the layering; C contradicts C-30 | Engine milestone (P0) |
| AD-02 (PD-09) | **Who owns stock numbers** | Ledger owns all items; Square counts never overwrite stock (with R1.1 write-back they post `pos_correction` movements) · keep Square authoritative for Square-tracked SKUs | **Ledger owns everything**; disable Square write-back until the outbox exists (R1.1) → C-34 | Blueprint's ledger principle; C4 shows write-back is unsafe today | Engine |
| AD-03 (baseline D3) | **Replace the inventory engine instead of patching it** | Replace with InventoryTransaction + movements · patch C2/C3 in the daily-aggregate engine | **Replace** → C-35 | Patching keeps a design that cannot meet DR-01..07 or order-line consumption | Engine |
| AD-04 | **InventoryOS owns the product catalog**; POS catalogs are mapped to it | Owned catalog + mapping · keep Square variations as products | **Owned catalog** → C-36 | Required for Standalone, for any second POS, and for a future native POS | Engine |
| AD-05 (baseline D6) | **Snack-box module** | Remove · extract to its own app and Square OAuth app · keep | **Remove or extract before the Square pilot** → C-37 | Contradicts C-30; widens OAuth scopes for every merchant | Square pilot |
| AD-06 | **Item model** | Org-level item + per-location balances · keep per-location `stock_items` rows | **Org-level item** (DOMAIN_MODEL InventoryItem), keeping `qty_base` as a projection column during transition → C-38 | Multi-location without duplicated item definitions; matches blueprint | Engine (technical; owner may delegate) |
| AD-07 (D-M1) | **Migration history** | Keep the 13-revision chain · squash to a new baseline | **Keep** → C-39 | Tested; no data forces a reset | — |
| AD-08 | **No pilots on the old engine** | Wait for the engine milestone · onboard now | **Wait** → C-40 | Avoids migrating wrong history; C2 would make a pilot's numbers wrong | Pilot timing |
| AD-09 | **Retire Starter/Pro** in favour of a data-driven catalogue (Complete, Standalone; add-ons later) | — | **Approve** (follows C-29 and PRICING_AND_PACKAGING) → C-41 | Current plans cannot express the approved model | Pre-customer |
| AD-10 (PD-03) | **JuJu relationship** | A parity by spec · B JuJu as tenant via sales API · C/D ruled out | **A until R1; revisit B after R1.1** → C-42 | No evidence to change the blueprint's recommendation | — |

## 4. Owner actions (not decisions)

| # | Action | Why |
|---|---|---|
| OA-1 | ~~Push this repository to a private GitHub repository~~ **Done 2026-11-05** (`Souru0712/InventoryOS`) | TR-01 resolved |
| OA-2 | Run the Square sandbox seller script in `NEEDS-YOU.md` §1 when the adapter rebuild starts | U1 |
| OA-3 | Upgrade Node on this machine to 24.22.2 or newer 24.x | U7; JuJu's own checklist R29 |
| OA-4 | Provide the real invoices when the extraction trial is scheduled | U3, TR-28 |

## 5. Questions for the owner

All answered by the owner on 2026-11-04.

1. **Snack-box business** — kept as a separate side project: curated and mystery boxes of Japanese
   snacks for the owner's convenience store, to sell faster, plan quantity and expiry, and help
   customers who cannot read the packaging. Resolves C-37 as **move to its own project, not
   delete**.
2. **Real invoices** — yes, several, from the convenience store and the snack-box project (not JuJu
   or clients). Usable for the extraction trial (U3, TR-28); produce-supplier invoices, the target
   segment's main kind, remain untested.
3. **Pilots** — none. The owner's convenience store uses Toast; ignore it for now, Square remains
   the first POS integration. Recorded as risk TR-31 and as a data point on assumption A-01.
4. **Hosting** — no budget ceiling; choose for architectural compatibility, plan fit and workflow
   automation. Host chosen in Phase 3 (PRODUCT_DECISIONS §7, D8).
5. ~~Should AD-06 (item model) be yours to approve?~~ Answered: approved with the rest (C-38).
