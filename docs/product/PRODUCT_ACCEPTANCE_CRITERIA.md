# Product Acceptance Criteria

Authoritative source for **when a capability is complete**. A capability is done when every R1
criterion for it passes in a production-like environment (real background jobs,
integrations in sandbox/test mode; production database engine), with the verification method shown. "Built" is not done.

Verification: **A** automated test in CI · **M** documented manual check with evidence ·
**A+M** both. Every criterion is tenant-scoped: it must also hold with a second organization
present whose data never appears.

## Command Center (CMD)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-CMD-01 | R1 | Given an item below its low-stock threshold, an expired-unresolved batch and an unmapped product, when the Owner opens the dashboard, then all three appear under "Needs attention" with links that open the exact record | A |
| AC-CMD-02 | R1 | Given 5 drinks sold without a recipe in the selected range, the dashboard shows a completeness notice naming the count and the consumption figures are labelled incomplete | A |
| AC-CMD-03 | R1 | An Employee user's dashboard response contains no cost, value or margin fields (checked at the API/response level, not only the rendered page) | A |
| AC-CMD-04 | R1 | Changing the date range to 7 days changes waste cost to the sum of waste in those 7 location-local days | A |
| AC-CMD-05 | R1 | With the POS sync failing for > 2 h, the dashboard shows the last successful sync time and a warning, not zero sales | A |
| AC-CMD-06 | R1 | In Standalone mode the dashboard shows the last business day with sales entered and lists missing days instead of POS sync status | A |

## Inventory (INV)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-INV-01 | R1 | An item can be created, edited and deactivated; a deactivated item with movements keeps its history and disappears from pick lists | A |
| AC-INV-02 | R1 | Entering 2 lb for an item with base unit g records 907.1847 g; entering 1 case (= 6 × 1 gal) for a volume item records 22,712.47 ml | A |
| AC-INV-03 | R1 | Entering 120 ml for a weight item without a conversion is rejected with a message naming the missing conversion; after adding "1 lb → 340 ml", it records 160.09 g | A |
| AC-INV-04 | R1 | For any item and location, recorded stock equals the sum of its movements after any sequence of receipts, sales, waste, production, counts and reversals (property test, ≥ 1,000 random sequences) | A |
| AC-INV-05 | R1 | There is no application path that updates or deletes a posted movement; attempting it via the API returns an error and the movement is unchanged | A |
| AC-INV-06 | R1 | Posting the same idempotency key twice produces one movement and returns the same result both times | A |
| AC-INV-07 | R1 | Two concurrent postings to the same item and location (e.g. waste and receipt submitted simultaneously, 50 trials) always produce both movements and the correct final balance | A |
| AC-INV-08 | R1 | A manual adjustment requires a reason; it appears in history with actor, reason, time and running balance | A |
| AC-INV-09 | R1 | Employees cannot record waste that would take recorded stock below zero; a Manager can, with an override reason, and a `negative_balance` exception opens | A |
| AC-INV-10 | R1 | A POS sale larger than on-hand posts in full, produces a negative balance, opens `negative_balance` and shows "count required" | A |
| AC-INV-11 | R1 | Nightly verification detects an injected mismatch between projection and movements and opens `balance_drift` without changing either | A |
| AC-INV-12 | R1 | CSV import of 150 items with 3 invalid rows shows a preview, imports 147, and reports the 3 row errors with reasons | A |

## Batches and expiration (BAT)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-BAT-01 | R1 | Posting a receiving line for a batch-tracked item with expiry creates one batch whose remaining equals accepted quantity | A |
| AC-BAT-02 | R1 | With two batches (A received first, expiring later; B received second, expiring sooner), a sale of an FEFO item consumes from B first; for a FIFO item, from A first | A |
| AC-BAT-03 | R1 | Sales allocations are marked inferred; waste with a chosen batch is marked explicit | A |
| AC-BAT-04 | R1 | For batch-tracked items, Σ batch remaining = recorded stock after any sequence of operations, or a `batch_shortfall` exception exists | A |
| AC-BAT-05 | R1 | A batch past its expiry with remaining > 0 has status expired, appears in the expiry queue and opens `batch_expired_unresolved`; it remains until a disposal, count or adjustment brings remaining to 0, then auto-resolves | A |
| AC-BAT-06 | R1 | With the alert window set to 2 days, a batch expiring the day after tomorrow (location time) triggers an expiry alert at the location's day start, once | A |
| AC-BAT-07 | R1 | With auto-disposal enabled for prepared items, an expired batch is written off as waste (reason expired, actor system) only after pending sales consumption is posted; with it disabled, nothing is written off automatically | A |
| AC-BAT-08 | R1 | Batch history lists every movement allocated to it with running remaining | A |

## Procurement (PRC)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-PRC-01 | R1 | A supplier with lead time and delivery days can be created; deactivating it keeps its receipts and history | A |
| AC-PRC-02 | R1 | An item can have two supplier items with different pack sizes and prices; receiving from either converts correctly to base units | A |
| AC-PRC-03 | R1.1 | A PO moves draft → submitted → partially_received (after a receiving of 6 of 10) → received (after 4 more) → closed; inventory changes only at the two receivings | A |
| AC-PRC-04 | R1.1 | A PO with a posted receipt cannot be cancelled; it can be short-closed with a reason | A |
| AC-PRC-05 | R1.1 | A failed PO email leaves the PO in draft with a visible error | A |

## Receiving (RCV)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-RCV-01 | R1 | A manual receiving with 3 lines posts 3 `receipt` movements with cost snapshots and updates each item's current cost | A |
| AC-RCV-02 | R1 | Uploading an invoice photo changes no balance, cost or sales figure until the receiving is posted | A |
| AC-RCV-03 | R1 | Uploading the same file twice links to the existing invoice instead of creating a second | A |
| AC-RCV-04 | R1 | Assisted extraction proposes lines that must each be reviewed; with extraction failing or disabled, the same receiving completes by manual entry | A+M |
| AC-RCV-05 | R1 | Two simultaneous "post" requests for one receiving result in exactly one set of movements; the second returns "already posted" | A |
| AC-RCV-06 | R1 | A line invoiced 10, arrived 9, accepted 8 posts +8 and records discrepancies short (1) and rejected (1) with reasons | A |
| AC-RCV-07 | R1 | An invoiced unit price 20 % above the supplier item's last price is flagged as a price variance | A |
| AC-RCV-08 | R1 | Correcting a posted receiving line from 10 to 8 creates a reversal of +10 and a new +8, both visible, original marked corrected | A |
| AC-RCV-09 | R1 | A learned match (supplier code → item) is suggested on the next invoice from the same supplier | A |
| AC-RCV-10 | R1 | Extraction requests beyond the per-org daily cap are refused with a clear message; manual entry still works | A |

## Waste (WST)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-WST-01 | R1 | On a 375 px screen an Employee user logs waste of a chosen item in ≤ 3 taps after selecting it (quantity, reason, save) | M |
| AC-WST-02 | R1 | Waste without a reason is rejected | A |
| AC-WST-03 | R1 | Waste records actor, time, reason, cost snapshot; it appears in the waste report and never in sales or production consumption totals | A |
| AC-WST-04 | R1 | Wasting 1 × "Golden State 16 oz" expands to the recipe version's ingredient lines as waste movements | A |
| AC-WST-05 | R1 | The logger can void within 30 minutes (reversal); after 30 minutes only Manager+ can, with a reason | A |
| AC-WST-06 | R1 | The waste report for a week shows totals by reason, item, product and person, and top 5 items by cost | A |
| AC-WST-07 | R1 | The waste view shows "found short at counts" separately from logged waste for the same period | A |
| AC-WST-08 | R1 | One waste event of 2 lb carrots appears as exactly 2 lb (and one cost) in every report — the event and its ledger entry are never summed | A |

## Recipes (RCP)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-RCP-01 | R1 | A Manager creates a recipe for a 16 oz variation with 3 ingredients in mixed units; it saves only if every unit converts | A |
| AC-RCP-02 | R1 | Editing a recipe creates version 2; a sale timestamped before the edit consumes per version 1, after per version 2, including a late-arriving sale | A |
| AC-RCP-03 | R1 | Recipe cost shows the sum of lines at current costs and lists lines without cost; margin is labelled estimated | A |
| AC-RCP-04 | R1 | A "Light ice" modifier with scale 1.15 makes the base recipe lines 115 % while an add-in modifier adds its own lines unscaled | A |
| AC-RCP-05 | R1 | A recipe may use a prepared item; a cycle (A uses B, B uses A) is rejected | A |
| AC-RCP-06 | R1 | "Start from 12 oz" pre-fills a 16 oz recipe scaled by 16/12 for editing | A |
| AC-RCP-07 | R1 | A receipt that raises an ingredient's cost shows the affected recipes and their margin change | A |
| AC-RCP-08 | R1 | Products sold without a recipe are listed with sale counts and link to recipe creation | A |
| AC-RCP-09 | R1 | Employees can view recipe cards and instructions; the response contains no cost fields | A |

## Production (PRD)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-PRD-01 | R1 | Recording ×2 of "Ginger juice" (1 lb ginger → 225 ml, shelf life 2 days) posts −2 lb ginger and +450 ml ginger juice in one transaction, with a batch expiring 2 days later | A |
| AC-PRD-02 | R1 | Entering actual yield 400 ml records variance −50 ml and output cost = input cost ÷ 400 ml | A |
| AC-PRD-03 | R1 | If any input has no cost, output cost is unknown (not understated) | A |
| AC-PRD-04 | R1 | A made-ahead product's sale consumes the prepared item only; a made-to-order product's sale consumes raw lines only — never both | A |
| AC-PRD-05 | R1 | A void within 30 minutes reverses all run movements and the output batch has remaining 0 | A |
| AC-PRD-06 | R1 | Production inputs are allocated FEFO from input batches | A |
| AC-PRD-07 | R1 | A run of 24 bottled juices (finished good) creates a 24-unit batch; selling one bottle consumes 1 unit of that batch and no raw ingredients | A |

## POS integration (POS)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-POS-01 | R1 | Connect via Square OAuth (sandbox), choose a location, see an import summary of items, variations and modifiers; tokens stored encrypted | A+M |
| AC-POS-02 | R1 | A completed sandbox order of 2 mapped items posts consumption within 5 minutes of completion | M (sandbox) |
| AC-POS-03 | R1 | The same order delivered by webhook 3 times and found again by reconciliation produces consumption once | A |
| AC-POS-04 | R1 | An order cancelled before completion produces no consumption | A |
| AC-POS-05 | R1 | A refund is recorded, linked to the order, reduces net sales and changes no stock | A |
| AC-POS-06 | R1 | An unmapped variation opens `unmapped_product`; after mapping and reprocessing the period (with preview), consumption posts with the original occurred_at, once | A |
| AC-POS-07 | R1 | A missed webhook is recovered by scheduled reconciliation within 60 minutes | A |
| AC-POS-08 | R1 | Daily reconciliation detects a deliberately skipped order and opens `pos_reconciliation_mismatch` | A |
| AC-POS-09 | R1 | A revoked authorization marks the connection revoked, opens a critical `sync_failure`, and emails the Owner | A |
| AC-POS-10 | R1 | A webhook with an invalid signature is rejected and does nothing | A |
| AC-POS-11 | R1 | The same Square merchant + location cannot be connected to two organizations | A |
| AC-POS-12 | R1 | Business logic tests run against normalized orders with no Square payload types (adapter boundary) | A |

## Sales sources and Standalone (SAL)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-SAL-01 | R1 | With no POS connection and no POS credentials configured, every R1 workflow (items, receiving, waste, production, counts, recipes, daily sales, reports, billing) completes end to end | A |
| AC-SAL-02 | R1 | Entering 40 × "Golden State 16 oz" for business date D posts consumption per the recipe version effective on D, once; saving the same values again changes nothing | A |
| AC-SAL-03 | R1 | Correcting day D from 40 to 35 reverses the original consumption and posts the new, both visible in history with a reason | A |
| AC-SAL-04 | R1 | Importing a CSV of 7 days × 20 products with 2 unmatched names shows a preview, posts matched rows and lists the 2; re-importing the same file creates no duplicate consumption | A |
| AC-SAL-05 | R1 | An open business day with no sales by the next day's cutoff opens `sales_missing`; entering the day resolves it | A |
| AC-SAL-06 | R1 | Switching a location from Standalone to Connected at cutover D: POS sales post from D; a manual entry dated ≥ D is refused; prior history is intact | A |
| AC-SAL-07 | R1 | Business-logic code contains no provider-specific types outside the adapter package (static check in CI) | A |
| AC-SAL-08 | R1 | Every sales record and sales-derived report shows its origin (manual / imported / POS), and Standalone reports show coverage "N of M open days" | A |

## Reconciliation and exceptions (REC)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-REC-01 | R1 | A count session by storage area lists items in storage order and accepts "3 cases + 4 each" | A |
| AC-REC-02 | R1 | A count line's expected equals recorded stock at the line's timestamp, unaffected by a sale posted one minute later | A |
| AC-REC-03 | R1 | Posting counts creates one `count_variance` per non-zero line and none for zero variance; counted values are stored as observations | A |
| AC-REC-04 | R1 | A variance of −$40 (threshold $25) opens `count_variance_high` for Manager review; a −$10 variance does not | A |
| AC-REC-05 | R1 | Reconciliation history shows each session with totals and line detail | A |
| AC-REC-06 | R1 | Exceptions de-duplicate: the same unmapped product sold 10 times yields one open exception with count 10 | A |
| AC-REC-07 | R1 | Exceptions auto-resolve when their condition clears, recording "resolved by system" | A |
| AC-REC-08 | R1 | Resolving an exception requires an action or a dismissal reason; no exception resolution edits a quantity directly | A |
| AC-REC-09 | R1 | Employees see operational exceptions without amounts; financial exceptions are not returned to them | A |

## Reordering (ROR)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-ROR-01 | R1 | For an item using 2 kg/day (28-day average), lead time 2 days, cover 3 days, safety 15 %, threshold 3 kg, on hand 5 kg, purchase unit 5 kg: suggestion = ceil((2×5×1.15 + 3 − 5)/5)×5 = 10 kg | A |
| AC-ROR-02 | R1 | Every suggestion displays its inputs and formula | A |
| AC-ROR-03 | R1 | An item with negative or never-counted stock shows "count first" instead of a quantity | A |
| AC-ROR-04 | R1 | Suggestions export to CSV grouped by supplier | A |
| AC-ROR-05 | R1 | No code path places an order with a supplier without a user action | A |

## Analytics (ANL)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-ANL-01 | R1 | Changing an item's current cost does not change any past report figure (waste cost, realized cost, variance cost) | A |
| AC-ANL-02 | R1 | Inventory valuation equals Σ max(recorded, 0) × current cost and states its cost basis and items without cost | A |
| AC-ANL-03 | R1 | Consumption report splits sales, production and waste for a period and matches the ledger totals exactly | A |
| AC-ANL-04 | R1 | Realized ingredient cost of sales shows the % of sold lines with complete recipes | A |
| AC-ANL-05 | R1 | Every report uses location-local business days (with the cutoff hour) | A |
| AC-ANL-06 | R1 | Every report table exports to CSV with the same figures | A |
| AC-ANL-07 | R1 | The weekly summary email is delivered to Owners with stock value, waste cost, money at risk, low stock and open exceptions | M |
| AC-ANL-08 | R1 | With counts of 100 units (day 1) and 40 (day 8), receipts of 60 and waste of 10 between, actual usage = 100 + 60 − 10 − 40 = 110 with no sales data present | A |

## Staff and permissions (STF)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-STF-01 | R1 | An emailed invitation creates a membership with the chosen role; it expires after 7 days and cannot be reused | A |
| AC-STF-02 | R1 | For every permission key in [USER_ROLES_AND_PERMISSIONS](USER_ROLES_AND_PERMISSIONS.md), a role without it receives 403 from the server for the operation | A |
| AC-STF-03 | R1 | Every movement and record shows its actor; system actions show "system" | A |
| AC-STF-04 | R1 | Revoking a member blocks their next request within 1 minute, including existing sessions | A |
| AC-STF-05 | R1 | The last active Owner cannot be removed or demoted | A |
| AC-STF-06 | R1 | Activity history filters by person and type | A |
| AC-STF-07 | R1 | A user in two organizations sees only the selected organization's data after switching | A |
| AC-STF-08 | R1 | A Manager scoped to location A receives 404/403 for location B's items, counts, reports and exports (verified with a two-location test organization) | A |

## Administration (ADM)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-ADM-01 | R1 | Setting the location time zone to America/Los_Angeles moves "today" boundaries and expiry evaluation accordingly | A |
| AC-ADM-02 | R1 | Alert settings (expiry window, recipients, digest time, variance threshold) take effect on the next evaluation | A |
| AC-ADM-03 | R1 | Full data export produces a zip of CSVs and invoice files covering every entity in SAAS-D-01, also in read-only state | A |
| AC-ADM-04 | R1 | Settings, role and connection changes appear in the business audit history | A |
| AC-ADM-05 | R1 | An Owner deletion request can be cancelled for 7 days, then removes tenant data and files and revokes POS tokens | A+M |
| AC-ADM-06 | R1 | Modifier scale factors (e.g. ice levels) are editable and apply to sales after the change only | A |

## SaaS (SAAS)

| ID | Rel | Criterion | Verify |
|---|---|---|---|
| AC-SAAS-01 | R1 | For every resource type and file, a member of org A gets 404/403 for org B's identifiers on read, write and download | A |
| AC-SAAS-02 | R1 | Unverified users cannot connect a POS, upload invoices or invite staff | A |
| AC-SAAS-03 | R1 | Login is throttled after repeated failures per account and per IP | A |
| AC-SAAS-04 | R1 | Password reset token is single-use and expires; changing password revokes other sessions | A |
| AC-SAAS-05 | R1 | Stripe test mode: checkout → active; renewal; failed payment → past_due with full access for the grace period → read-only; resubscribe → full access, no data lost | A+M |
| AC-SAAS-06 | R1 | Stripe webhooks delivered out of order or twice leave the correct final subscription state | A |
| AC-SAAS-07 | R1 | In read-only state every write endpoint returns a clear refusal while views, reports, export and billing work | A |
| AC-SAAS-08 | R1 | Complete and Standalone expose the same core capabilities; entitlement configuration alone (no code change) sets max locations and capability flags; exceeding the location entitlement is refused with a clear message | A |
| AC-SAAS-09 | R1 | A non-founder completes onboarding (Square-connected café, ≤ 150 items, top 10 recipes, opening count) in < 45 minutes | M |
| AC-SAAS-10 | R1 | A standalone business (no POS) completes onboarding and records receiving, waste, production and counts | M |
| AC-SAAS-11 | R1 | Stripe test mode: switching Standalone → Complete at a cutover date changes the plan with proration and keeps all data | A+M |
| AC-SAAS-12 | R1 | Changing a plan price in billing configuration changes what new subscribers pay without any code deployment; a grep finds no plan prices in domain code | A |

## Non-functional

The production readiness standard in [NON_FUNCTIONAL_REQUIREMENTS §8](NON_FUNCTIONAL_REQUIREMENTS.md#8-production-readiness-standard)
is the acceptance gate for launch. Performance targets PERF-01..07 are verified by a load test at
design headroom before launch (M).

## Workflow-level success conditions

A workflow is complete when its outcome holds end to end, in every mode it applies to
([BUSINESS_WORKFLOWS](BUSINESS_WORKFLOWS.md)).

| Workflow | Success condition |
|---|---|
| WF-01 Onboarding | A new business answers the POS question and lands on the right plan and mode with a checklist |
| WF-02 Initial setup | Items imported with valid units; opening balances posted; variance tracking starts at the opening timestamp |
| WF-03–05 Catalog, suppliers, recipes | A product can be sold (C) or entered (S) and consumes the right ingredients from its effective recipe version |
| WF-06 POs (R1.1) | On-order quantity reduces reorder suggestions; POs never change stock |
| WF-07–08 Receiving and invoices | Accepted quantities and costs posted once; discrepancies tracked; invoice upload alone changes nothing |
| WF-09–10 Counts, adjustments | Balances match physical reality; every correction attributed with a reason |
| WF-11, WF-13 Production, finished goods | Inputs consumed once; output batch with expiry and traceability; no double counting at sale |
| WF-12 Consumption engine | Same sale processed twice = consumed once; gaps recorded, sale never dropped |
| WF-14 Expiry | No expired batch disappears without an explicit or policy-driven event |
| WF-15 POS ingestion | Every completed POS order consumed exactly once within 60 minutes worst case |
| WF-16 Standalone recording | Daily sales and usage recorded and labelled by origin; coverage disclosed |
| WF-17 Refunds/voids | Sales net of refunds; stock changes only on voids or explicit restock |
| WF-18 Waste | Every loss visible by what, how much, why, who, cost — counted once |
| WF-19 Reconciliation | Variances explained; actual usage available from two counts with or without sales |
| WF-20–21 Alerts, reorder | Low stock alerted once per condition; suggestions show their formula and never auto-order |
| WF-22 Transfers (Exp.) | Stock in transit is visible; sent − received differences are recorded |
| WF-23 Employee operations | Each daily task reachable in ≤ 2 taps; no costs shown to Employees |
| WF-24 Review | Owner completes the weekly review in ~15 minutes from trustworthy, labelled figures |
| WF-25–28 Mode change, correction, exceptions, mapping | History preserved; no double sales across sources; nothing edited in place |

## Business-level requirements

| ID | Requirement | Verify |
|---|---|---|
| BR-01 | Both $99 single-location offerings (Complete, Standalone) can be purchased, onboarded and used end to end without the founder | M (pilot) |
| BR-02 | Standalone is a complete product: a no-POS pilot business uses it weekly for 4 weeks and gets waste cost, actual usage and reorder suggestions | M (pilot) |
| BR-03 | No essential capability is gated by plan (PRICING_AND_PACKAGING §4) | A (entitlement tests) |
| BR-04 | Prices and plans change by configuration only (E-01) | A |
| BR-05 | The native POS is not required by any R1 workflow and no R1 requirement depends on it | Review |
| BR-06 | JuJu's repository, deployment and customer experience are unchanged by InventoryOS R1 | Review |
| BR-07 | Square can be disconnected and the location keeps working in Standalone mode | A |
| BR-08 | Validation evidence is captured: POS situation at signup, Scenario C interest, unsupported POS names, conversion by offering | A (analytics events) |
| BR-09 | Every customer-facing figure that is an estimate says so; Standalone figures never claim POS-grade automation | Review + A |
