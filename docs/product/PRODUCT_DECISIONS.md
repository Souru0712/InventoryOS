# Product Decisions

Authoritative source for **what is decided, assumed and open**. When another document conflicts
with this one, this one wins until it is updated. Nothing marked OPEN is implemented until the owner
answers.

Update rule: when the owner decides, move the row to §1 with the date and the owner's words, and
update affected documents in the same change.

## 1. Decisions made (confirmed by the owner)

Source: Phase 1 brief, revised 2026-11-04, unless noted.

| ID | Decision | Rationale (owner's stated reasons) | Alternatives not taken |
|---|---|---|---|
| C-01 | Three separate entities: InventoryOS (the product), the POS-Inventory-Automation codebase (formerly StockPulse; now InventoryOS's chosen foundation, C-25), JuJu Juice (separate business, reference environment). No repository merge | Avoid conflating a product idea with code that was built for other purposes | Declare the existing app to be InventoryOS; merge JuJu and the app |
| C-02 | InventoryOS is an inventory and operations management platform for independent food-service — not a stock-counting app | Value comes from connecting inventory to the activities that move it | Narrow counting/stock app |
| C-03 | Initial market: independent juice bars, smoothie shops, cafés, coffee shops, bubble tea/specialty beverage, small food-service; multi-location and sophisticated operators as expansion. *Supersedes the 2026-09-07 six-segment list for the existing app* | Ingredient-based, perishable, recipe-driven businesses feel the problem most | Retail and specialty food generally; enterprise chains |
| C-04 | Not a POS at launch; integrates with external POS. Square is a potential first integration, **not the permanent architectural foundation** | Keep scope on inventory; avoid coupling the domain to one provider | Build around Square's data model; build a POS first |
| C-05 | **Pricing hypotheses by POS situation** (not final published prices): Complete $99/location/month · Standalone $99/month (one location) · InventoryOS POS $149–199/month starting (future) · Multi-location ~$149–199+ · Advanced ~$199–299+. **Supersedes the single $49/month plan of the first Phase 1 brief** | Price follows the customer's situation and complexity; a single low price undervalued the product | One $49 plan (first brief); Starter $29 / Pro $59 (existing app) |
| C-06 | Pricing dimensions are combinable: operating mode × business scale × capability level, implemented with configurable plans, entitlements and billing rules; prices never hard-coded in domain logic | Avoid separate codebases per plan; allow repricing without code | Fixed tiers in code; separate products per mode |
| C-07 | Three customer scenarios: A has a POS and keeps it → Complete; B no POS, doesn't want one → Standalone; C no POS, wants one → future InventoryOS POS. Scenario C is not "connect Square" | Different needs and different sources of sales data | Treat all customers as POS users |
| C-08 | Standalone and Connected are **operating modes of one engine**, not separate products or codebases; only the source of transaction data differs | One implementation of inventory logic | Separate Standalone app |
| C-09 | Standalone must be complete and useful without a POS — not a stripped-down trial; manual, imported and POS-generated records are distinguished; no pretending standalone customers have automated sales data | Honest product for no-POS businesses | Standalone as a lite tier |
| C-10 | Native InventoryOS POS is a future direction requiring validation of demand, market, scope, regulation and costs; never a launch dependency; payment processing, hardware, card readers, fees, taxes and third-party services are not included in its subscription price | Building a POS is far larger than the inventory core | Build a POS for launch |
| C-11 | Multi-location and Advanced are expansion dimensions; essential inventory functionality is never restricted to make higher tiers attractive | Retention through value | Gate core features |
| C-12 | Initial commercial launch prioritizes a valuable **$99 single-location** product; both Standalone and Connected are in the product definition; sequence justified by Phase 2 evidence and validation | Revenue from the core first | Launch Connected only |
| C-13 | JuJu keeps its brand, customer experience, business logic and requirements; JuJu gets the capabilities it needs as a business regardless of InventoryOS progress; no shared repo/DB/frontend/deployment assumed | JuJu is a business, not a test fixture | Make JuJu tenant #1 now |
| C-14 | Inventory is a ledger with history; balances derive from it; corrections by reversal/adjustment | Trust and auditability | Single mutable quantity |
| C-15 | Recipe changes never alter historical records | Historical integrity | Edit recipes in place |
| C-16 | Waste is a distinct operational event, never double-counted with its ledger movement | Loss visibility without inflated figures | Waste as a generic adjustment |
| C-17 | Receiving distinguishes ordered / invoiced / arrived / accepted; invoice upload never silently changes balances; OCR/AI extraction is an enhancement, not a prerequisite | Reliable receiving without depending on AI | AI-first receiving |
| C-18 | Never purchase automatically; recommendations explain their inputs | Owner control | Auto-ordering |
| C-19 | Permissions enforced on the backend; least privilege; location-specific access | Security | UI-only hiding |
| C-20 | Phase 1 = product blueprint only; no code, schema or application change; no Phase 2 audit | Requirements before implementation | — |
| C-21 | Phase 2 inspects whether the existing app supports all three operating modes without unnecessary Square dependencies; Phase 3 prioritizes Complete and Standalone, evaluates native POS separately, then multi-location and Advanced | Sequenced validation | — |
| C-22 | Email is the notification channel at launch; no mobile app or push *(owner, 2026-09-14, existing app; carried over)* | Low operating cost | Native app |
| C-23 | Explainable, rule-based intelligence; ML not on day one *(owner, 2026-09-10)* | Trust | Opaque AI recommendations |
| C-24 | **InventoryOS is the new name for StockPulse** and replaces it as the single cash-engine product; the StockPulse brand is retired *(owner, 2026-11-04: "InventoryOS is the new name for StockPulse"; was PD-01)* | Keeps the one-cash-engine rule intact | Run InventoryOS as a second product |
| C-25 | **Build InventoryOS on the POS-Inventory-Automation codebase**; the owner will rename the repository. Building on it does not mean its current features meet this blueprint — Phase 2 audits the gaps *(owner, 2026-11-04: "Build on the POS-Inventory-Automation Codebase"; was PD-02)* | Reuses the multi-tenant shell, Square OAuth/catalog sync, invoice extraction and Stripe already built | Grow from JuJu's codebase; new build |
| C-26 | **Commercial target: 21 paying customers** as the validation target; **$1,000 MRR remains the owner's gate** before any new revenue product, including a native POS *(owner, 2026-11-04: "Sounds like a good start"; was PD-31)* | Proves retention across enough businesses; keeps scope discipline | $1,029 MRR (≈ 11 customers at $99) as the goal |
| C-27 | **Build order: shared engine → Standalone sales capture → Square adapter** *(owner, 2026-11-04, "as suggested"; was PD-42)* | Connected needs everything Standalone needs plus an adapter; proves no Square dependency; Square code has never run live | Connected first or in parallel |
| C-28 | `docs/product/` in this repository is the permanent home of the product documentation *(follows from C-25; was PD-04)* | Docs live with the code they govern | Separate docs repository |

## 2. Recommendations adopted in this package

| ID | Recommendation | Where | Alternative |
|---|---|---|---|
| R-01 | InventoryTransaction header + append-only ledger entries with idempotency, cost snapshots, actor, location | DOMAIN_MODEL DR-01..07 | Mutable balance with audit log |
| R-02 | Counts are observations that generate variance entries | DR-12 | Counts overwrite balances |
| R-03 | Effective-dated recipe versions referenced by consumption | DR-16 | Copy recipe into every sale |
| R-04 | One SalesTransaction model for every source, with origin label | DR-30, DR-34 | Separate POS and manual pipelines |
| R-05 | One sales source of record per location per business date | DR-31 | Allow mixing with dedupe heuristics |
| R-06 | Order-line POS ingestion with incremental cursor; webhooks + reconciliation | INTEGRATION SQ-10..12 | Daily aggregates (existing app) |
| R-07 | Prepared and finished goods are ordinary items; production mode per product prevents double counting | DR-18 | Separate finished-goods ledger |
| R-08 | Modifier effects add / remove / substitute / scale / none | RCP-FR-03 | Per-modifier recipe copies |
| R-09 | Exceptions as first-class records with dedup and auto-resolve | REC-FR-11..15 | Notifications only |
| R-10 | Reserved stock not implemented | DOMAIN_MODEL §5 | Reservations for production |
| R-11 | Negative balance only from system-observed events, raising "count required" | DR-08 | Block sales at zero |
| R-12 | CSV import and quick-add in R1 (onboarding depends on it) | INV-FR-08 | Typing items one by one |
| R-13 | Count-based actual usage as Standalone's primary usage measure | ANL-FR-14, STD-FR-08 | Require sales data |
| R-14 | Eight preset roles from permission keys; custom roles Advanced | USER_ROLES_AND_PERMISSIONS | Three fixed roles |

## 3. Open decisions

Priority: **P0** blocks Phase 2/3 planning · **P1** blocks R1 build of the named area · **P2** before
launch.

| ID | P | Decision | Context / conflict | Recommendation |
|---|---|---|---|---|
| PD-01 | P0 | **Is InventoryOS the new identity of the existing "StockPulse" product, or a second product?** | Owner's rule (2026-09-06): one cash-engine product until it earns $1,000. StockPulse has a brand, landing and pricing page | **Decided 2026-11-04 → C-24** |
| PD-02 | P0 | **Technical foundation** | Existing app: multi-tenant shell, Square OAuth/catalog, invoice extraction, Stripe, Python; lacks a ledger and makes Square authoritative for some counts (`track_mode=provider`). JuJu: strongest inventory domain, TypeScript/Supabase, single-tenant. New build | **Decided 2026-11-04 → C-25** |
| PD-03 | P1 | **JuJu ↔ InventoryOS relationship** (options A–D, [JUJU §6](JUJU_REFERENCE_AND_PARITY.md#6-possible-future-relationships-no-commitment)) | The foundation is Python (C-25), so a shared library (C) and growing from JuJu's code (D) are ruled out without rewriting JuJu; A (parity by specification) or B (JuJu as a tenant via the sales API) remain | A until InventoryOS R1 ships; revisit B after R1. JuJu proceeds on its own code |
| PD-04 | P2 | Permanent home of these docs | Temporarily in the existing app's repo | **Decided → C-28** |
| PD-05 | P1 | Trial length, card requirement, payment grace period | Existing app: 30-day trial | 30-day trial, no card; 7-day grace |
| PD-06 | P2 | One-off setup fee | Existing app sells $149 setup; not carried over | Optional founder-assisted setup only if approved |
| PD-07 | P1 | Retention after cancellation; ingestion during read-only | Audit D10: 90 days then deletion | Read-only 90 days with ingestion continuing; deletion after notice; export throughout |
| PD-08 | P1 | Cost basis | Both existing apps use latest cost | Snapshot = current cost at posting; valuation at latest cost; revisit FIFO actual cost in R2 |
| PD-09 | P1 | Write inventory back to Square? | Owner's original pain: online orders accepted for sold-out items | InventoryOS authoritative; optional availability write-back via outbox in R1.1 |
| PD-10 | P1 | Refunds and stock | Both apps: refunds never restock | R1 record only; R1.1 operator classifies restock or waste |
| PD-11 | P1 | Roles at launch | Brief lists eight roles; Advanced lists "more granular permissions" | R1: Owner, Administrator, Manager, Employee, Read-only; R1.1: Inventory manager, Purchasing staff, Production staff presets (core); custom roles Advanced |
| PD-12 | P1 | Count variance approval | — | R1 post-then-review; pre-approval option R1.1 |
| PD-13 | P0 | **Release plan** ([spec §Roadmap boundaries](PRODUCT_SPECIFICATION.md#roadmap-boundaries)) | Both modes in R1; POs staged | Approve: Standalone + Connected in R1; production in R1; POs, write-back R1.1; native POS separate track |
| PD-14 | P2 | Attribution on shared devices | JuJu credits the signed-in account | R1 "logged by" picker; R1.1 PIN switch |
| PD-15 | P1 | Production with insufficient recorded inputs | Physical production happened | Allow; record negative balance + exception |
| PD-16 | P1 | Consumption trigger per sales source | Square: completed. JuJu: Ready. Standalone: end of business day | Per-source setting |
| PD-17 | P2 | Default reprocessing window | — | Since last count of affected items, max 30 days, with preview |
| PD-18 | P0 | Tenant isolation mechanism | Existing app: per-route checks; JuJu: RLS single-tenant | Decide in Phase 2; must satisfy SAAS-T-04 and location scope (DR-35) |
| PD-19 | P1 | Can Employees post a receiving? | Small shops: whoever receives posts | Yes; Employee-entered cost changes flagged |
| PD-20 | P2 | MFA required for Owners/Administrators? | — | Optional at launch with strong prompt |
| PD-21 | P0 | Authentication approach | Python foundation chosen (C-25); the existing auth lacks reset, verification and throttling (audit S1/S2) | Decide in Phase 2: close the gaps in framework-native auth, or adopt a managed provider; Supabase Auth no longer fits |
| PD-22 | P2 | Sales tax on subscriptions | US SaaS taxability varies | Enable Stripe Tax; confirm with an accountant |
| PD-23 | P2 | Subscription refund policy | — | No partial-month refunds; goodwill at founder discretion |
| PD-24 | P2 | Email provider | Audit D9: Resend | Resend |
| PD-25 | P1 | Assisted invoice extraction in R1, and its cap | Owner (2026-09-06): invoice photos are "the record and the ingestion path"; brief: enhancement | R1 includes photo as record and reviewed extraction; manual entry always available; cap per tenant per day |
| PD-26 | P2 | Existing-app features not in the brief (health score, markdowns, date reading, barcode, weekly report) | Avoid silent creep and silent loss | Barcode lookup and weekly email R1; markdowns and date reading R2; health score only if the owner asks |
| PD-27 | P2 | Alert channels | Existing app: email; JuJu: screens | In-app + email digest at R1 |
| PD-28 | P1 | Private snack-box module in the existing app | Not part of InventoryOS; widens Square scopes | Out of scope; separate it if that codebase is chosen |
| PD-29 | P2 | Auto-dispose expired prepared batches default | JuJu: always on | Off by default; opt-in |
| PD-30 | P2 | Quantity precision | — | 4 dp base units; 2 dp display |
| PD-31 | P0 | **Commercial target under the new pricing** | 21 customers / $1,029 MRR was set with $49; at $99, 21 customers ≈ $2,079 MRR and $1,029 ≈ 11 customers | **Decided 2026-11-04 → C-26** |
| PD-32 | P2 | Standalone beyond one location | Standalone is "$99/month for one location"; Complete is per location | Price additional Standalone locations like Complete (per location) until Multi-location pricing exists |
| PD-33 | P2 | Multi-location price vs Complete per-location | Two Complete locations = $198, inside the ~$149–199+ range | Decide in Phase 3: per-location with a centralized-management add-on, or packages including N locations (E-06 supports both) |
| PD-34 | P2 | Advanced contents and boundaries | Advanced lists granular permissions and advanced audit history; core must keep standard roles and audit trail | Advanced = custom roles, extended history/exports, forecasting, automation, API; launch only when ≥ 3 paying customers ask for the same capability |
| PD-35 | P1 (Phase 3) | **Native POS validation criteria and approach** | Large build; regulatory, hardware, support | Validate first: Scenario C interest captured at signup; ≥ 10 qualified prospects willing to pre-commit at $149+; build vs white-label vs partner costed; not before the $1,000 revenue gate |
| PD-36 | P1 | What a Scenario C prospect gets at launch | No native POS | Standalone, or a supported POS + Complete; interest recorded |
| PD-37 | P2 | Can Employees enter daily sales? | Often done at close by staff | Manager+ by default; Owner toggle allows Employees |
| PD-38 | P1 | Standalone sales granularity | Daily totals vs per transaction | Daily totals per variation (modifiers optional) in R1 |
| PD-39 | P2 | Billing on mode switch | Standalone ↔ Complete | Self-serve plan change at cutover, prorated |
| PD-40 | P2 | Hour-level shelf life | Bubble-tea toppings expire in hours; date-only expiry can't express it | Expiry date-time for prepared items in R1.1 |
| PD-41 | P2 | Can Production staff edit production recipes? | Prep leads often own batch recipes | No by default; Owner may grant |
| PD-42 | P0 (Phase 3) | **Standalone vs Connected implementation sequence** | Both in R1; existing Square code unproven live | **Decided 2026-11-04 → C-27** |

## 4. Assumptions (to confirm)

| ID | Assumption | Impact if wrong |
|---|---|---|
| A-01 | A meaningful share of target customers use Square; another share runs without a POS | Mode priorities, adapter roadmap |
| A-02 | One currency per organization; USD at launch; US customary + metric units; English only | Money and units |
| A-03 | Sizing: 21 orgs at launch, headroom 500; ≤ 1,000 items, ≤ 1,500 order lines/day, ≤ 15 users per org | Performance targets |
| A-04 | Perishables received several times per week; shelf lives in days (hours for some prepared items) | Expiry design |
| A-05 | Invoices are mostly paper/PDF; extraction accuracy on real invoices unknown | RCV effort, PD-25 |
| A-06 | POS price is authoritative; InventoryOS product prices are informational | Margins |
| A-07 | A POS void after completion means not made (restore); a refund means made (no restore) | WF-17 |
| A-08 | A business-day cutoff handles late closings | Daily reports, Standalone entry |
| A-09 | Employees have smartphones or a shared tablet | Mobile-first UX |
| A-10 | Square production OAuth for other sellers works without App Marketplace listing — verify before R1 | Launch dependency |
| A-11 | JuJu continues on its own codebase through its launch | JuJu schedule |
| A-12 | InventoryOS needs no end-customer PII from POS orders | Privacy scope |
| A-13 | Standalone customers can produce a daily item-level sales figure (from a terminal report, register or tally) | Standalone theoretical consumption |
| A-14 | Segment profiles in PRODUCT_VISION §5.2 reflect real businesses; no interviews done yet | Positioning, packaging |

## 5. Contradictions requiring resolution

| # | Contradiction | Where | Status |
|---|---|---|---|
| 1 | First Phase 1 brief: one $49 plan; revised brief: $99 Complete/Standalone + expansions | Briefs | Resolved: revised brief wins (C-05) |
| 2 | Target "21 customers = $1,029 MRR" only holds at $49 | Briefs | Resolved: C-26 |
| 3 | Existing app codes Starter $29 / Pro $59 and 30-day Pro trial | `app/entitlements.py` | Implementation gap for Phase 2; PD-05 for trial |
| 4 | Complete per location vs Multi-location ~$149–199+ overlap | Pricing | PD-33 |
| 5 | Advanced includes "more granular permissions" and "advanced audit history" vs never gating essentials | Pricing | PD-34; standard roles and core audit stay in base offerings |
| 6 | First brief called JuJu "first-class operational environment"; revised brief calls it a reference environment, "not the definition of every customer's needs" | Briefs | Resolved: both hold — JuJu keeps its own capabilities and is a reference, not a template (C-13) |
| 7 | "Not a POS" vs future native POS offering | Briefs | Resolved: not a POS at launch; native POS future (C-04, C-10) |
| 8 | Owner's "one cash engine until $1,000" vs a native POS product line | Memory vs pricing | Resolved: InventoryOS is the one cash engine (C-24); native POS waits for the $1,000 gate (C-26) and validation (PD-35) |
| 9 | Owner's "invoice photo is non-negotiable" vs "OCR is an enhancement" | 2026-09-06 vs brief | PD-25 (both satisfied: photo as record, extraction optional) |
| 10 | Existing app makes Square authoritative for tracked SKUs vs "Square is not the foundation / not the ledger" | Existing code vs brief | PD-09, Phase 2 |
| 11 | Segment list of 2026-09-07 (retail, specialty food) vs revised segment list | Memory vs brief | Resolved by C-03 |

## 6. Facts that corrected the first brief

| Brief statement | Finding | Source |
|---|---|---|
| Existing app "may use FastAPI, Jinja, and HTMX" | FastAPI + Jinja + vanilla JS; no HTMX | `POS-Inventory-Automation/docs/audit/03-architecture.md` |
| Existing app has inventory, low-stock, expiry, Square | Also invoice extraction, barcode, date reading, markdowns, health score, Stripe, private snack-box module; **no ledger, recipe UI or waste module** | Audit 02 |
| JuJu has recipes, batches, expiry | Also an append-only ledger, receiving, counts, waste, margins, purchase suggestions, forecasting | [JUJU_REFERENCE_AND_PARITY](JUJU_REFERENCE_AND_PARITY.md) |

## 7. Mapping to the 2026-11-04 technical audit decisions (existing app)

| Audit | InventoryOS status |
|---|---|
| D1 pricing | Superseded by C-05; trial/setup → PD-05, PD-06 |
| D2 segment | Resolved by C-03 |
| D3 ledger | Product requirement (R-01); applying it to that codebase depends on PD-02 |
| D4 order-level ingestion | Product requirement (R-06) |
| D5 roles | PD-11 |
| D6 snack-box | PD-28 |
| D7 multi-location | Complete per location; Multi-location expansion (C-11, PD-33) |
| D8 host | Phase 2 |
| D9 email | PD-24 |
| D10 retention | PD-07 |
| D11 refunds | PD-10 |
| D12 AI spend | RCV-FR-14, PD-25 |
| D13 accounts | Phase 2 / operations |
