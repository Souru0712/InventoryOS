# JuJu ↔ InventoryOS capability comparison

Phase 2, 2026-11-04. Read-only. Sources: [JUJU_TECHNICAL_AUDIT](JUJU_TECHNICAL_AUDIT.md) (JuJu commit
`ae1e702`), the [inventory project audits](INVENTORY_PROJECT_TECHNICAL_AUDIT.md), and the blueprint
([JUJU_REFERENCE_AND_PARITY](../product/JUJU_REFERENCE_AND_PARITY.md), decisions C-13, C-30, C-42).

JuJu is a separate business with its own codebase (TypeScript, Next.js, Supabase), brand and
customer experience. Nothing here proposes merging repositories, databases or deployments.
**Code reuse between JuJu and InventoryOS is not possible in practice** (different languages and
runtimes); reuse means *design and domain rules*.

## 1. Capability comparison

JuJu statuses are from the JuJu audit (tests documented in CI, not re-run). "Existing app" is the
InventoryOS codebase today.

| Capability | JuJu status | Existing app | InventoryOS requirement | Shared domain concepts | JuJu-specific requirements | Reusability | Required changes (for InventoryOS) | Integration / dependency considerations |
|---|---|---|---|---|---|---|---|---|
| Customer website, storefront | Implemented and verified | Marketing site only | Not in scope | — | Entire storefront, brand art, pickup slots | None | — | Stays JuJu's (C-13) |
| Menu presentation (sections, drops, art) | Implemented and verified | — | Not in scope | Product names, variations | Presentation, drops, Lab Friday | None | — | JuJu owns its menu |
| Products, sizes, modifiers | Implemented and verified (`product_sizes`, `modifiers`, `byo_bases`, ice levels, pumps) | Square variations only (`sold_items`) | RCP-FR-01, RCP-FR-03 | Product → variation → modifier with *scale* vs *add* effects | BYO bases, pumps, ice-level names | **Spec** | InventoryOS-owned catalog with generic modifier effects (scale/add/remove/substitute) | JuJu's model maps onto generic modifiers |
| Recipes | Partially implemented (per size/base/modifier/pump; no versions) | Flat, CLI-only | RCP-FR-02..17, versioned | Recipe lines in any unit; prepared items as ingredients; pour rounding | Share-based recipe-book authoring | **Spec** | Add versioning (JuJu lacks it too); keep JuJu's line kinds as test cases | `recipes:load` overwrites UI edits in JuJu (JuJu risk 9) |
| Ingredients and units | Implemented and verified (8 units, press yield) | Same-dimension units, packs | INV-FR-04..14 | Base units g/ml/each; **press yield = item-specific cross-dimension conversion** | — | **Spec** | Generalize press yield into ItemUnitConversion (INV-FR-05) | — |
| Ledger and balances | Implemented and verified (signed movements, cost snapshot, actor; balance = sum) — not DB-enforced append-only | **Missing** (balance overwrite) | INV-FR-15..23, DR-01..07 | Exactly the blueprint's model | — | **Spec — the primary reference for the engine rebuild** | Add transaction header, idempotency keys, reversals, location, DB-level append-only | — |
| Adjustments | Missing (counts correct) | Missing | INV-FR-19 | — | — | — | New in both | — |
| Counts | Partially implemented (variance movement under row lock, single item) | Partially implemented (overwrite) | REC-FR-01..09 | Count = observation producing a variance movement | — | **Spec** | Add sessions, expected snapshot, review | — |
| Low-stock alerts | Implemented and verified (role-filtered, acknowledged) | Implemented (Connected only) | CMD-FR-02, REC-FR-11..16 | Alert kinds, acknowledgement per key | — | **Concept** | Exceptions lifecycle (G15) | — |
| Production and batches | Implemented and verified (`make_batch`: `prep_out` inputs, `prep_in` output at input cost) | **Missing** | PRD-FR-01..14 | Prepared item = ordinary item; production = one transaction; cost carried from inputs | NYC raw-juice 2-day shelf life rule | **Spec** | Add actual-yield variance, batch codes, void window | — |
| Expiration | Partially implemented (batch auto write-off with inferred quantity; deliveries inferred FIFO) | Implemented (FEFO queue, date types, confidence) | BAT-FR-05..14 | Expiry queue, auto-dispose policy | — | **Existing app is stronger**; JuJu adds the auto-dispose policy | Movement-level allocation (neither has it) | — |
| Receiving | Partially implemented (per ingredient, no document) | Implemented (invoice photo, extraction, matching, review) | RCV-FR-01..16 | Receipt updates latest cost; supplier on receipt | — | **Existing app is stronger** | Ledger posting, single-shot apply | JuJu would gain invoice receiving from InventoryOS |
| Suppliers and purchasing | Partially implemented (supplier list, purchase plan with 15 % buffer, prepared → raw expansion) | Free-text supplier; reorder by supplier | PRC-FR-01..03, ROR-FR-01..08 | Purchase suggestion formula; prepared-item demand expansion (ROR-FR-06) | — | **Spec** | Supplier entity; formula transparency | — |
| Waste | Implemented and verified (reasons, cost snapshot, actor) | **Missing** | WST-FR-01..12 | Waste as its own movement kind with reason | JuJu's five reasons | **Spec** | Configurable reasons, batch link, void window, product-level waste | — |
| Kitchen queue, pickup capacity | Implemented and verified | — | Not in scope | — | Entirely JuJu | None | — | Stays JuJu's |
| Orders and sales (online) | Implemented and verified (sandbox) | — | Sales *ingestion* only (POS-FR-17) | Order line → consumption once per order, never block the line, retry gaps | Deduct at "Ready"; online orders are not Square orders | **Concept** | Per-source consumption trigger (PD-16) | JuJu would send sales via the generic sales API (POS-FR-16, R1.1) |
| POS walk-ins | Implemented but unverified (never against a real POS) | Square daily aggregates | POS-FR-05, -07 | Square order retrieval for itemization | Name-matching to JuJu menu | **Concept** | Mapping by external id, not name | — |
| Refunds | Implemented and verified (sandbox); no stock restore | Ignored | POS-FR-08 | **Refunds never restore stock automatically** — both agree with the blueprint | — | **Spec (confirms DR-26)** | Record returns; optional classification (R1.1) | — |
| Reporting, forecast, what-to-buy | Implemented and verified | Weekly report, health score, reorder | ANL, ROR | Weekday-average forecast; completeness labels on margins | Guest/account/walk-in segments | **Spec** | Inputs from ledger | — |
| Authentication | Implemented and verified (Supabase Auth, optional TOTP enforced via RLS once set) | Partial (C-32 to finish) | SAAS-A-01..10 | TOTP, last-owner guard | — | **Concept** | InventoryOS stays in-app (C-32) | Two identity systems if JuJu ever consumes InventoryOS |
| Roles and permissions | Implemented and verified (3 roles, RLS, cost hidden by column grants) | Missing | USER_ROLES_AND_PERMISSIONS | Cost hidden from staff; last-owner guard | — | **Concept** | 8 roles + location scope | — |
| Square integration | Payments, refunds, walk-ins via personal access token; HMAC webhooks; event-id dedupe | OAuth, catalog, counts, daily sales, write-back | Module 9 | Raw-body HMAC; dedupe by event id; reconcile job for lost events | Payments for its own storefront | **Concept** (JuJu's dedupe + reconcile pattern is what the existing app lacks) | Adapter rebuild (integration audit §4) | — |
| Notifications | Mocked or placeholder (providers unconfigured) | Email built, never delivered | NFR OBS, ANL-FR-09 | Retry with claim-before-send | SMS for pickup | Concept | — | — |
| Multi-tenancy, billing | Not applicable (single business) | Partial | SAAS | — | — | — | — | JuJu as a tenant would need none of its own |
| Deployment | Missing (Vercel cron config only) | Missing (Docker, never deployed) | NFR §8 | — | — | — | — | Independent deployments |

## 2. Shared versus JuJu-specific

| Shared domain (belongs in InventoryOS, JuJu is a reference) | JuJu-specific (never InventoryOS) |
|---|---|
| Signed-movement ledger with cost snapshot and actor · cross-dimension conversions (press yield) · recipes per variation with modifier effects · prepared items and production transactions · expiry write-off policy · waste reasons · count as variance · purchase suggestions with prepared → raw expansion · weekday forecast · refunds don't restore stock · cost hidden from staff · webhook dedupe + reconcile | Storefront and brand · menu presentation, drops, Lab Friday · pickup capacity and kitchen queue · loyalty and promotions · NYC raw-juice labelling rule · BYO bases, pumps and ice-level *names* (their *effects* are generic) · recipe-book authoring in shares · walk-in name matching · customer accounts and erasure |

## 3. Can InventoryOS eventually serve JuJu without JuJu losing its identity?

**Yes, in principle, for inventory and operations — not before InventoryOS R1.1 at the earliest.**
[INFERENCE from both audits]

| Requirement for JuJu to consume InventoryOS | Status | Blueprint hook |
|---|---|---|
| A way to send JuJu's sales (online + walk-ins) without Square owning them | Not built | Generic sales API (POS-FR-16, R1.1) |
| Consumption at JuJu's trigger ("Ready"), not at payment | Designed | Per-source consumption trigger (PD-16) |
| JuJu's line must never wait on a network call | Constraint | Fire-and-forget sales posting with retry; JuJu keeps its own queue |
| JuJu's modifier shapes (pumps, ice, BYO bases) expressible | Designed | Generic modifier effects (RCP-FR-03) |
| JuJu's stock reads (alerts bar, purchase plan) via API instead of SQL | Not built | Read API (Advanced / post-R1) |
| One identity for JuJu staff | Conflict | Separate logins, or SSO later (SAAS-A-10) |
| Migration of JuJu's ledger history | Possible | Opening balances + legacy history, as in [DATA_MIGRATION_ASSESSMENT](DATA_MIGRATION_ASSESSMENT.md#5-preserving-history-and-reconciling-balances) |

JuJu's storefront, kitchen and brand would stay entirely in JuJu. What would move is the
inventory back office (ledger, recipes, production, waste, receiving, purchasing).

## 4. Decision (C-42, approved 2026-11-04)

Keep **option A — parity by specification** until InventoryOS R1 ships: JuJu continues on its own
code; InventoryOS's engine rebuild uses JuJu's ledger, production and modifier designs as its
primary reference (they are the most complete implementation of the blueprint's core that exists
on this machine). Revisit **option B — JuJu as an InventoryOS tenant via the sales API** after
R1.1, when the generic sales API exists. Options C (shared library) and D (grow from JuJu's code)
remain ruled out by C-25 (Python foundation). This matches the blueprint's existing
recommendation; the audit found no evidence to change it.

## 5. What each side should take from the other

| InventoryOS takes from JuJu (as design) | JuJu could take from InventoryOS (later, as a service) |
|---|---|
| Ledger model and once-per-order consumption guard · production transaction · press-yield conversion · modifier effects · waste reasons and cost snapshot · webhook dedupe-after-success + reconcile job · cost hiding from staff · weekday forecast | Invoice photo receiving and item matching · FEFO expiry queue with date types · supplier entity and POs (R1.1) · count sessions and exceptions · recipe versioning · multi-location if JuJu opens a second store |
