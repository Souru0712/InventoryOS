# Product Specification

Authoritative source for **what InventoryOS must do**. Workflows: [BUSINESS_WORKFLOWS](BUSINESS_WORKFLOWS.md).
Entities and integrity rules (`DR-xx`): [DOMAIN_MODEL](DOMAIN_MODEL.md). Permissions: [USER_ROLES_AND_PERMISSIONS](USER_ROLES_AND_PERMISSIONS.md).
Measurable completion tests (`AC-xx`): [PRODUCT_ACCEPTANCE_CRITERIA](PRODUCT_ACCEPTANCE_CRITERIA.md). Terms: [PRODUCT_GLOSSARY](PRODUCT_GLOSSARY.md).

## Conventions

**Requirement IDs:** `<MODULE>-FR-nn`. Module codes: CMD, INV, BAT, PRC, RCV, WST, RCP, PRD, POS,
REC, ROR, ANL, STF, ADM, STD.

**Release tag** (see [§Initial release definition](#initial-release-definition)):
`R1` launch (required to sell the $99 single-location Complete and Standalone offerings) · `R1.1`
first staged increment after launch · `R2` later core · `Expansion` multi-location · `Advanced`
advanced entitlement · `Future` not committed (incl. native POS).

**Source column:** `B` stated in the Phase 1 brief [CONFIRMED] · `R` recommendation · `J` proven
need in JuJu · `S` exists in the StockPulse codebase · `O` prior owner decision for the existing
app (memory/README). Rows tagged only `R` are recommendations; contested ones are in
[PRODUCT_DECISIONS](PRODUCT_DECISIONS.md).

**Universal requirements (apply to every module):**

| ID | Requirement |
|---|---|
| U-01 | Every inventory-changing action creates ledger movements traceable to its originating business event (DR-01..DR-06) |
| U-02 | Every write is tenant-scoped and permission-checked on the server, not only hidden in the UI |
| U-03 | Every record shows who created/changed it and when (actor + timestamp) |
| U-04 | Quantities accept any valid unit for the item; storage is in the item's base unit (DR-10) |
| U-05 | No silent failure: a failed write, sync or calculation is logged and surfaced as an exception or error message with context |
| U-06 | Estimates are labelled as estimates; metrics with incomplete inputs say what is missing |
| U-07 | Mobile-usable at 375 px width for every Employee-permitted workflow |
| U-08 | Dates and "today" use the location's time zone; stored timestamps are UTC-aware (DR-20) |
| U-09 | Every capability works in Standalone mode (no POS) unless it inherently consumes POS data; such capabilities degrade with an explanation, never an error (DR-29) |
| U-10 | No module outside the integration adapters references a specific POS provider; Square is the first adapter, not the foundation [CONFIRMED 2026-11-04] |

---

## Operating modes

[CONFIRMED 2026-11-04] Standalone and Connected are **operating modes of one product and one
engine**, not separate products or codebases. The engine (ledger, recipes, batches, receiving,
waste, production, counts, exceptions, reporting) is identical in every mode; only the **sales
source** differs.

| Mode | Offering | Where transactions come from | Release |
|---|---|---|---|
| **Connected** | Complete ($99/location/month) | External POS through an adapter (Square first; others later) | R1 |
| **Standalone** | Standalone ($99/month) | Manual daily sales entry, sales file imports, and operational records (receiving, waste, production, counts) | R1 |
| **POS** | InventoryOS POS ($149–199/month starting) | InventoryOS itself generates transactions | Future — validation required (PD-35) |

Rules:
- A location's mode is derived from its sales source of record (DR-31). Mode is per location, so a
  future multi-location business can mix modes.
- Switching Standalone ↔ Connected keeps all history and uses a cutover date (WF-25).
- Multi-location and Advanced capabilities are enabled by **entitlements**, independent of mode
  ([SAAS_REQUIREMENTS §4.1](SAAS_REQUIREMENTS.md#41-plan-catalogue-pricing-hypotheses)).

### Capability availability by mode

| Capability | Connected | Standalone | Note for Standalone |
|---|---|---|---|
| Items, units, ledger, batches, expiry | ✓ | ✓ | — |
| Suppliers, receiving, invoices | ✓ | ✓ | — |
| Waste, production, counts, exceptions | ✓ | ✓ | — |
| Recipes, costing, estimated margins | ✓ | ✓ | — |
| Expected consumption from sales | ✓ automatic, per order line | ✓ from daily entries / imports | Granularity is a business day; accuracy depends on entry discipline |
| Theoretical vs actual usage | ✓ | Partial | Only for days with sales entered |
| **Count-based actual usage** (ANL-FR-14) | ✓ | ✓ primary usage measure | Works with no sales data at all |
| Reorder suggestions | ✓ | ✓ | Average daily use from sales entries, or count-based usage when sales are missing |
| POS sync status, catalog sync, refunds from POS | ✓ | — | Not applicable |
| Missing-sales-day detection | — | ✓ | `sales_missing` exception |

---

## Module 1 — Command Center / Dashboard (CMD)

**Purpose.** Answer, on one screen: what requires attention, what is running low, what is
expiring, what changed, what is costing money, what to do next.
**Users.** Owner, Manager (full); Employee (operational subset, no money).
**Data ownership.** Owns no business data. Reads projections from every module; owns only
dashboard preferences.
**Workflows.** Entry point to all workflows in [BUSINESS_WORKFLOWS](BUSINESS_WORKFLOWS.md).

| ID | Requirement | Rel | Src |
|---|---|---|---|
| CMD-FR-01 | "Needs attention" list of open exceptions (M10), ranked by severity then money at risk, each linking to its record | R1 | B |
| CMD-FR-02 | Low-stock and out-of-stock items with on-hand, threshold and days of cover | R1 | B S J |
| CMD-FR-03 | Expiring (within alert window) and expired-unresolved batches with quantity and cost at risk | R1 | B S J |
| CMD-FR-04 | Current inventory value with its cost basis stated (ANL-FR-01) | R1 | B S |
| CMD-FR-05 | Recent waste (count and cost, last 7 days) linking to the waste log | R1 | B |
| CMD-FR-06 | Pending receiving (invoices uploaded but not applied) and, from R1.1, open purchase orders | R1 / R1.1 | B |
| CMD-FR-07 | Unresolved count variances above threshold | R1 | B |
| CMD-FR-08 | Sales-source status — Connected: last successful POS sync, failures, unmapped products; Standalone: last business day with sales entered, missing days | R1 | B S |
| CMD-FR-09 | Recent activity feed (movements, receipts, counts, waste, recipe changes) filterable by type and actor | R1 | B |
| CMD-FR-10 | Date-range filter for money metrics (today, 7 d, 30 d, custom) | R1 | B |
| CMD-FR-11 | Onboarding checklist until setup is complete ([SAAS_REQUIREMENTS](SAAS_REQUIREMENTS.md#5-onboarding)) | R1 | R |
| CMD-FR-12 | Warnings visually distinct from information; each warning states the recommended next action | R1 | B |
| CMD-FR-13 | Data-completeness banner when inputs are missing (e.g. "12 drinks sold without a recipe; ingredient use reads low") | R1 | B J |
| CMD-FR-14 | Employee view hides all cost and value figures | R1 | J |
| CMD-FR-15 | Production activity: batches made today, expiring prepared items, yield variance | R1 | B |
| CMD-FR-16 | Receiving activity: deliveries received, discrepancies open | R1 | B |
| CMD-FR-17 | Location summaries and comparison | Expansion | B |

**Business rules.** A metric whose inputs are incomplete is shown with a completeness indicator,
never as a verified figure (U-06). Exceptions resolve at the source record, not on the dashboard.
**Failure conditions.** Projection unavailable → show last-computed time and a stale warning, not
zeros. Sync down → banner with last success time.
**Dependencies.** All modules; M10 for exceptions.
**Acceptance.** AC-CMD-01..06. **Reporting.** None owned; links into M12.

---

## Module 2 — Inventory Management (INV)

**Purpose.** Reliable quantities and complete movement history for every inventory item.
**Users.** Owner, Manager (manage items); Employee (view, count, adjust within permission).
**Data ownership.** InventoryItem, InventoryCategory, UnitOfMeasure, ItemUnitConversion,
StorageLocation, **InventoryLedgerEntry** (the single writer API for all modules), StockBalance
projection.
**Workflows.** All; ledger posting is shared infrastructure.

### Item catalog

| ID | Requirement | Rel | Src |
|---|---|---|---|
| INV-FR-01 | Create, edit, deactivate (never hard-delete if referenced) inventory items | R1 | B S(create only) J |
| INV-FR-02 | Item fields: name, category, item type (raw ingredient/material · prepared item · finished good · packaging · supply · resale good), base unit dimension (count/weight/volume), display unit, storage location, preferred supplier, status (active/inactive), barcode(s)/SKU(s), notes | R1 | B S J |
| INV-FR-03 | Categories managed per business (create, rename, merge, archive) | R1 | B |
| INV-FR-04 | Purchase units and usage units per item (e.g. buy "case of 6 × 1 gal", use "fl oz") with pack-size conversion | R1 | B S |
| INV-FR-05 | Item-specific cross-dimension conversions (e.g. 1 lb carrots → 340 mL juice; 1 L honey → 1,420 g) entered explicitly; never assumed | R1 | B J |
| INV-FR-06 | Minimum (low-stock) and maximum (par) levels and reorder point per item per location | R1 | B S J |
| INV-FR-07 | Current unit cost per item (from latest accepted receipt unless overridden), with cost history | R1 | B S J |
| INV-FR-08 | Bulk create/edit via CSV import with preview and row-level validation errors; quick-add multi-row grid | R1 | R |
| INV-FR-08a | Starter item templates per business type (juice bar, café) with common units and conversions | R1.1 | R |
| INV-FR-09 | Barcode lookup to find an item on a phone camera | R1 | S |
| INV-FR-10 | Search and filter by name, category, supplier, status, low-stock, expiring | R1 | B |

### Units and conversions

| ID | Requirement | Rel | Src |
|---|---|---|---|
| INV-FR-11 | Built-in units: g, kg, oz, lb, ml, L, fl oz, gal, qt, pt, cup, tbsp, tsp, each, plus business-defined packages (case, bottle, bag, box) defined as N × another unit | R1 | B S J |
| INV-FR-12 | Same-dimension conversions are exact constants; cross-dimension only via INV-FR-05 | R1 | B |
| INV-FR-13 | An entry in an unconvertible unit is rejected with a message naming the missing conversion (DR-11) | R1 | B J |
| INV-FR-14 | Changing an item's base unit after movements exist is blocked; create a new item and transfer instead | R1 | R |

### Ledger and balances

| ID | Requirement | Rel | Src |
|---|---|---|---|
| INV-FR-15 | Append-only ledger; movement types: `receipt`, `sale_consumption`, `production_input`, `production_output`, `waste`, `count_variance`, `adjustment`, `transfer_out`, `transfer_in`, `pos_correction`, `opening_balance`, `reversal` | R1 (transfers R2) | B J |
| INV-FR-16 | Each movement records: org, location, item, batch (if known), signed base-unit quantity, unit-cost snapshot, source type + source reference, actor (or "system"), reason, occurred_at, recorded_at, idempotency key | R1 | B J |
| INV-FR-17 | Recorded balance per item per location = sum of posted movements; any cached balance is updated in the same transaction and verified nightly (DR-03) | R1 | B J |
| INV-FR-18 | Corrections only by reversal or new adjustment movements; no edit or delete of posted movements (DR-02) | R1 | B |
| INV-FR-19 | Manual adjustment with mandatory reason (found, correction, sample, transfer-in-from-outside, other) and note | R1 | B |
| INV-FR-20 | Movement history per item with filters (type, date, actor, batch) and running balance | R1 | B |
| INV-FR-21 | Duplicate protection: posting with an existing idempotency key returns the original result and posts nothing (DR-05) | R1 | B |
| INV-FR-22 | Concurrency: two simultaneous postings against one item+location serialize; neither is lost (DR-07) | R1 | B J |
| INV-FR-23 | Negative recorded balance allowed only from POS-driven consumption; raises a "count required" exception. Manual waste, production input and adjustments cannot drive a balance below zero without a Manager+ override (DR-08) | R1 | B J |
| INV-FR-24 | Inventory value per item and total, using the cost basis in ANL-FR-01 | R1 | B S |
| INV-FR-25 | Stock transfers between locations of one organization (WF-22) | Expansion | B |

**Business rules.** DR-01..DR-12 in [DOMAIN_MODEL](DOMAIN_MODEL.md#7-data-integrity-rules).
**Failure conditions.** Unknown unit → reject. Concurrent conflicting write → retry once then
error with "changed, refresh". Ledger/projection mismatch found by nightly check → exception
`balance_drift`, never auto-overwrite.
**Permissions.** View: all roles. Edit items: Manager+. Adjust: Manager+ (Employee only via waste,
count, receiving). See [USER_ROLES_AND_PERMISSIONS](USER_ROLES_AND_PERMISSIONS.md).
**Dependencies.** M14 (location, units), M13 (actor).
**Acceptance.** AC-INV-01..12. **Reporting.** Valuation, movement history, consumption (M12).

---

## Module 3 — Batches and Expiration (BAT)

**Purpose.** Track stock at batch/lot level so the system knows what expires when and what was
actually used or lost.
**Users.** All roles (create/consume via other workflows); Manager+ (dispose, edit dates).
**Data ownership.** InventoryBatch, BatchAllocation (movement ↔ batch), ExpirationAlert policy.

| ID | Requirement | Rel | Src |
|---|---|---|---|
| BAT-FR-01 | Batches created by receiving (one per accepted receiving line with expiry or lot), production output, and manual entry (e.g. opening stock) | R1 | B S J |
| BAT-FR-02 | Batch fields: item, location, source reference (receiving line / production order / manual), supplier, lot code, received/produced at, expiry date + date type (use-by / best-before / sell-by), received quantity, remaining quantity (derived), unit cost, storage location, status | R1 | B S |
| BAT-FR-03 | Item setting "tracks batches" (on for perishables). Non-batch items still receive and consume normally | R1 | R |
| BAT-FR-04 | Default shelf life per item (and per prepared item) pre-fills expiry; editable at receipt/production | R1 | J S |
| BAT-FR-05 | Consumption allocation: FEFO for items with expiry dates, FIFO by received date otherwise; configurable per item | R1 | B S J |
| BAT-FR-06 | Allocations from POS consumption are marked **inferred**; allocations chosen by a person (waste of a specific batch, disposal) are **explicit** | R1 | R J |
| BAT-FR-07 | Batch status: `active` → `depleted` (remaining = 0) / `expired` (past date, remaining > 0, unresolved) → `resolved` (remaining accounted for). `on_hold` for quality quarantine | R1 | B |
| BAT-FR-08 | An expired batch never disappears: remaining quantity stays until a disposal (waste), use, adjustment or count resolves it (DR-14) | R1 | B |
| BAT-FR-09 | Alert thresholds: business default (e.g. 2 days) with per-item/category override | R1 | B S J |
| BAT-FR-10 | Expiry queue: expiring soon and expired-unresolved, sorted by date, with quantity and cost at risk and actions: use first, mark down, dispose, adjust | R1 | B S |
| BAT-FR-11 | Optional per-business policy: auto-dispose expired prepared batches as waste (reason `expired`, actor `system`) at day close; off by default | R1 | J (JuJu does this) |
| BAT-FR-12 | Batch history: every movement allocated to it, with running remaining | R1 | B |
| BAT-FR-13 | Batch adjustment (correct remaining via count of that batch) | R1 | B |
| BAT-FR-14 | Use-by label text for prepared batches (screen display) | R1 | J |
| BAT-FR-15 | Printable labels | Future | J (not built) |
| BAT-FR-16 | Markdown suggestion for expiring resale goods (transparent rule, not AI) | R2 | S O |
| BAT-FR-17 | Expiry date capture from a packaging photo (assisted, confirmed by user) | R2 | S |

**Business rules.** DR-13..DR-15. Sum of batch remaining = item recorded balance for batch-tracked
items (DR-13); any difference is an unassigned-quantity exception, not silently absorbed.
**Failure conditions.** Allocation finds no batch with remaining → consume from "unbatched"
quantity and raise exception `batch_shortfall`.
**Dependencies.** M2 ledger; M5 receiving; M8 production.
**Acceptance.** AC-BAT-01..08. **Reporting.** Expiration losses, batch yield (M12).

---

## Module 4 — Procurement and Supplier Management (PRC)

**Purpose.** Organize suppliers and purchasing.
**Users.** Owner, Manager.
**Data ownership.** Supplier, SupplierItem, PurchaseOrder, PurchaseOrderLine.

| ID | Requirement | Rel | Src |
|---|---|---|---|
| PRC-FR-01 | Supplier records: name, contact person, phone, email, ordering method (email/phone/portal/rep), delivery days, lead time (days), minimum order, notes, active | R1 | B J(partial) |
| PRC-FR-02 | Supplier items: supplier's product name/code, purchase unit and pack size, last price, preferred flag; many suppliers per item | R1 | B S(aliases) |
| PRC-FR-03 | Purchase history per supplier and item (from receipts) | R1 | B |
| PRC-FR-04 | Purchase orders: create (manually or from reorder suggestions), lines with item, supplier unit, quantity, expected price, expected delivery date | R1.1 | B |
| PRC-FR-05 | PO statuses: `draft` → `submitted` → `partially_received` → `received` → `closed`; `cancelled` from draft/submitted; a received PO with short lines can be closed with "short-closed" reason | R1.1 | B |
| PRC-FR-06 | Send PO by email (PDF/plain) or mark "ordered by phone/portal"; no supplier API required | R1.1 | B |
| PRC-FR-07 | Receiving references a PO and updates received quantities per line | R1.1 | B |
| PRC-FR-08 | Supplier price change detection (new price vs last price, %) | R1 | B J |
| PRC-FR-09 | Supplier API ordering | Future | B |

**Business rules.** POs never change inventory; only receiving does. Cancelling a PO with receipts
is not allowed — close it instead. Supplier deactivation keeps history.
**Failure conditions.** Email send fails → PO stays `draft` with error; never shown as submitted.
**Dependencies.** M2 items; M5 receiving; M11 suggestions.
**Acceptance.** AC-PRC-01..05. **Reporting.** Purchasing history, supplier price changes.

---

## Module 5 — Receiving and Invoice Management (RCV)

**Purpose.** Ensure goods received match what was ordered and invoiced; post accepted quantities
to inventory in a controlled way.
**Users.** Employee (receive), Manager+ (resolve discrepancies, edit costs).
**Data ownership.** Receiving, ReceivingLine, Invoice (document + file), InvoiceLine,
ReceivingDiscrepancy.

Receiving distinguishes four quantities per line [CONFIRMED]: **ordered** (PO, if any) ·
**invoiced** (invoice line) · **arrived** (physically delivered) · **accepted** (posted to
inventory). Rejected = arrived − accepted, with reason.

| ID | Requirement | Rel | Src |
|---|---|---|---|
| RCV-FR-01 | Manual receiving without PO or invoice: supplier, lines (item, quantity, unit, cost, expiry, lot) | R1 | B S J |
| RCV-FR-02 | Invoice upload (photo/PDF), stored as the record, deduplicated by content hash | R1 | B S O |
| RCV-FR-03 | Invoice lines entered manually or proposed by assisted extraction; every proposed line reviewed before posting | R1 | B S O |
| RCV-FR-04 | Item matching: supplier item code/name → inventory item, learned from confirmed matches; unmatched lines require a choice (match, create item, ignore as non-inventory) | R1 | B S |
| RCV-FR-05 | Quantity verification: arrived and accepted entered per line (default = invoiced) | R1 | B |
| RCV-FR-06 | Price verification: invoiced unit price vs supplier item last price and PO price; variance beyond threshold flagged | R1 | B |
| RCV-FR-07 | Discrepancy types: short, over, damaged, wrong item, price variance, not invoiced, invoiced not delivered. Each recorded with reason; summary per receiving | R1 | B |
| RCV-FR-08 | Partial receiving: a receiving can be saved as draft and completed later; PO-linked partial deliveries in R1.1 | R1 / R1.1 | B |
| RCV-FR-09 | Posting creates one `receipt` movement per accepted line (and batch if tracked) with unit cost snapshot, idempotent on receiving line id (DR-05) | R1 | B |
| RCV-FR-10 | A receiving is posted exactly once even under concurrent submissions (DR-07) | R1 | B (StockPulse defect C5) |
| RCV-FR-11 | Accepted cost updates the item's current unit cost and supplier item last price; price change is shown with affected recipes | R1 | J S |
| RCV-FR-12 | Corrections after posting: reversal + re-post of affected lines, attributed, with reason; original preserved | R1 | B |
| RCV-FR-13 | Uploading an invoice never changes inventory or sales data until a person posts it [CONFIRMED] | R1 | B |
| RCV-FR-14 | Assisted extraction usage capped per tenant per day; extraction failure falls back to manual entry | R1 | R |
| RCV-FR-15 | Credit memo / supplier return: reverse accepted quantity (`adjustment`, reason `returned_to_supplier`) linked to the receiving | R1.1 | R |
| RCV-FR-16 | Barcode scan to add lines while receiving | R1 | S |

**Business rules.** Accepted ≤ arrived. Rejected quantity never enters inventory. Cost basis of a
receipt = invoiced line total ÷ accepted base quantity unless the discrepancy resolution says
otherwise (e.g. supplier credit expected).
**Failure conditions.** Extraction fails/times out → document stays `needs_entry`, user enters
lines. Post fails mid-way → whole receiving rolls back; nothing partially posted.
**Dependencies.** M2, M3, M4 (R1.1 PO link).
**Acceptance.** AC-RCV-01..10. **Reporting.** Receiving discrepancies, supplier price changes.

---

## Module 6 — Waste and Loss Management (WST)

**Purpose.** Record and analyze inventory loss distinctly from sales and production.
**Users.** Employee (log), Manager+ (edit/void, reports).
**Data ownership.** WasteEvent, WasteLine, WasteReason (configurable list).

| ID | Requirement | Rel | Src |
|---|---|---|---|
| WST-FR-01 | Log waste in ≤ 3 taps after choosing the item on a phone: item (raw, prepared, or **sellable product** — expands to its recipe), quantity, unit, reason, optional note/photo | R1 | B J |
| WST-FR-02 | Default reasons: spoilage, expiration, preparation waste, production loss, damaged goods, remake, customer return, refund-related, quality-control disposal, spilled, overproduction, other. Business can rename/add; reason list is versioned so reports stay stable | R1 | B J |
| WST-FR-03 | Actor recorded automatically; on shared devices the person can be selected (PD-14) | R1 | B J |
| WST-FR-04 | Batch association: explicit if chosen, else inferred FEFO | R1 | B |
| WST-FR-05 | Cost snapshot per line = quantity × cost basis at event time (ANL-FR-01) | R1 | B J |
| WST-FR-06 | Waste posts `waste` movements; never counted as sales or production consumption | R1 | B |
| WST-FR-07 | Void within 30 minutes by the logger (reversal); afterwards Manager+ only, with reason | R1 | J (30-min undo) |
| WST-FR-08 | Waste log list with filters (date, reason, item, actor) and totals | R1 | B |
| WST-FR-09 | Waste report answers: what, how much, why, by whom, cost, top items/products by loss | R1 | B |
| WST-FR-10 | "Found short at counts" shown alongside logged waste as unexplained loss (from count variances) | R1 | J |
| WST-FR-11 | Optional link to a related sales order or production order | R1 | B |
| WST-FR-12 | No double counting: the waste event is the business record, its ledger entries are the stock effect; all waste quantities and costs in reports come from ledger entries only (DR-33) | R1 | B |

**Business rules.** Reason mandatory. Quantity > 0. Waste of a sellable product records both the
product and the expanded ingredient lines (recipe version at event time).
**Failure conditions.** Item without cost → waste recorded, cost marked "unknown", counted in
completeness.
**Dependencies.** M2, M3, M7 (product expansion).
**Acceptance.** AC-WST-01..08. **Reporting.** Waste cost, expiration losses, top loss.

---

## Module 7 — Recipes and Product Costing (RCP)

**Purpose.** Connect sellable products to ingredient requirements and cost.
**Users.** Manager+ (edit), Employee (view recipe cards without costs).
**Data ownership.** SellableProduct, ProductVariation, Modifier, Recipe, RecipeVersion,
RecipeLine, ModifierEffect, CostSnapshot (recipe).

| ID | Requirement | Rel | Src |
|---|---|---|---|
| RCP-FR-01 | Sellable product catalog: name, category, variations (e.g. 12 oz / 16 oz / 24 oz), price per variation (informational; POS is price authority), active | R1 | B J |
| RCP-FR-02 | Recipe per product variation: lines of item + quantity + unit (any convertible unit) | R1 | B J |
| RCP-FR-03 | Modifiers with inventory effect: **add** (extra ingredients, e.g. "+ ginger shot"), **remove** (omit a line), **substitute** (swap ingredient), **scale** (multiply base recipe, e.g. light ice ×1.15), **none** | R1 (add, none, scale) / R1.1 (remove, substitute) | B J |
| RCP-FR-04 | Recipes may use prepared items (e.g. ginger juice) as ingredients | R1 | J |
| RCP-FR-05 | Yield and portion: recipe yield quantity (e.g. batch makes 2 L; drink is 1 serving) | R1 | B |
| RCP-FR-06 | Preparation instructions (text, steps) shown on staff recipe card | R1 | B J |
| RCP-FR-07 | Versioning: every saved change creates a new RecipeVersion effective from save time (or a chosen future time); consumption uses the version effective at the sale's occurred_at (DR-16) | R1 | B |
| RCP-FR-08 | Recipe history: list versions with who/when/what changed | R1 | B |
| RCP-FR-09 | "Start from" another variation, scaled by size, then edit | R1 | J |
| RCP-FR-10 | Recipe cost = Σ(line quantity in base units × ingredient cost basis), adjusted for yield; shows which lines have no cost | R1 | B J |
| RCP-FR-11 | Estimated gross margin per variation = price − recipe cost, labelled "estimated at today's costs" | R1 | B J |
| RCP-FR-12 | Recipe completeness: products sold without a recipe listed as an exception with count of affected sales | R1 | J |
| RCP-FR-13 | Ingredient price change shows affected recipes and margin change | R1 | J |
| RCP-FR-14 | Packaging (cups, lids, straws) can be recipe lines so margins include them | R1 | J |
| RCP-FR-15 | Bulk recipe import (CSV/JSON) | R1.1 | J (JuJu loads recipes from JSON) |
| RCP-FR-16 | Version comparison view (diff of two versions, cost delta) | R2 | B |
| RCP-FR-17 | Recipe cost split into ingredient cost and packaging cost | R1 | B |
| RCP-FR-18 | Cost history per recipe: cost at each version and at each ingredient price change | R1.1 | B |

**Business rules.** Editing a recipe never changes past consumption, waste expansion or
production records (DR-16). A recipe line whose unit is unconvertible for its item blocks save
(DR-11). Circular prepared-item references are rejected (DR-17).
**Failure conditions.** Missing recipe at sale time → sale recorded, no consumption, exception
`unmapped_product` with the order reference; reprocessing after mapping is allowed for that
period with confirmation (POS-FR-14).
**Dependencies.** M2 items/units; M9 product mapping.
**Acceptance.** AC-RCP-01..09. **Reporting.** Recipe cost, estimated margin.

---

## Module 8 — Production and Preparation (PRD)

**Purpose.** Track transformation of ingredients into prepared inventory (batches of juice,
syrup, sauce, prepped produce).
**Users.** Employee (record), Manager+ (define prep recipes, void after window).
**Data ownership.** ProductionRecipe (a RecipeVersion whose output is an inventory item),
ProductionOrder, ProductionInput, ProductionOutput.

| ID | Requirement | Rel | Src |
|---|---|---|---|
| PRD-FR-01 | Prepared items are ordinary inventory items (type `prepared`) so recipes, counts, waste and reports work unchanged | R1 | J |
| PRD-FR-02 | Production recipe: inputs, expected yield (quantity + unit), shelf life days | R1 | B J |
| PRD-FR-03 | Record a run: choose prepared item, multiplier (e.g. ×1.5), see required inputs and availability, confirm | R1 | B J |
| PRD-FR-04 | Actual yield entry (defaults to expected); variance = actual − expected recorded | R1 | B |
| PRD-FR-05 | Posting: `production_input` movements (allocated FEFO from input batches) and one `production_output` movement creating a batch with expiry = production date + shelf life | R1 | B J |
| PRD-FR-06 | Output unit cost = Σ input cost ÷ actual yield; if any input lacks cost, output cost is "unknown" (not understated) | R1 | J |
| PRD-FR-07 | Void within 30 minutes by recorder (reversal of all movements); afterwards Manager+ with reason | R1 | J |
| PRD-FR-08 | Production orders: R1 creates and completes in one step (status `completed`); R2 adds `planned` → `in_progress` → `completed` / `cancelled` with production lists | R1 / R2 | B |
| PRD-FR-09 | "What to make" suggestion: expected use within shelf life − on hand, in batch-size steps | R1.1 | J |
| PRD-FR-10 | Production variance report (expected vs actual yield by item, by person) | R2 | B |
| PRD-FR-11 | Configurable production mode per product: made-to-order (sale consumes raw ingredients) vs made-ahead (sale consumes the prepared item) | R1 | B J |
| PRD-FR-12 | Batch identification: human-readable batch code per output batch (e.g. GJ-1105-1) shown on labels and history; traceability output batch → input batches | R1 | B |
| PRD-FR-13 | Production adjustment after completion (yield correction) via correction workflow (WF-26) | R1 | B |
| PRD-FR-14 | Finished goods (sold as units) produced and tracked like prepared items (WF-13) | R1 | B |
| PRD-FR-15 | Hour-level shelf life (expiry date-time) for short-life prepared items, e.g. cooked toppings (PD-40) | R1.1 | R |

**Business rules.** No double counting [CONFIRMED]: inputs leave stock at production; a sale of a
drink using the prepared item consumes the prepared item, not the raw inputs (DR-18). Input
availability shortfall warns but does not block (production happened physically); it raises a
negative-balance exception as INV-FR-23 allows for system-observed events — **decided: C-44 (PD-15)**
whether to treat production like POS (allow) or like manual (block).
**Dependencies.** M2, M3, M7.
**Acceptance.** AC-PRD-01..07. **Reporting.** Production yield, batches made/used/wasted.

---

## Module 9 — Sales Sources and POS Integrations (POS)

**Purpose.** Bring sales into the engine from whichever source the location's operating mode uses
— an external POS (Connected), manual entry or imports (Standalone), or, in future, InventoryOS's
own POS — as one normalized sales model. Detailed integration requirements:
[INTEGRATION_REQUIREMENTS](INTEGRATION_REQUIREMENTS.md).

### Sales source model (all modes)

| ID | Requirement | Rel | Src |
|---|---|---|---|
| POS-FR-17 | Every sale, from any source, becomes a SalesTransaction with lines (product variation, modifiers, quantity, occurred_at or business date, location, source); consumption reads only this model (DR-30) | R1 | B |
| POS-FR-18 | Each location has one sales source of record per business date (DR-31); its operating mode is derived from it | R1 | B |
| POS-FR-19 | Switch Standalone ↔ Connected with a cutover business date; history retained; overlapping entries refused or flagged `sales_source_overlap` (WF-25) | R1 | B |

### Standalone sales capture

Specified in [Module 15 — Standalone Operations](#module-15--standalone-operations-std).

### Connected-only features

POS connection and health, POS catalog sync, mapping from a POS catalog, automatic order-line
consumption, POS refunds/voids/discounts, POS reconciliation and write-back exist only in Connected
mode. Everything else is mode-independent (U-09).

### Connected — POS integrations (Square first)


| ID | Requirement | Rel | Src |
|---|---|---|---|
| POS-FR-01 | Square OAuth connect, encrypted token storage, refresh, disconnect, revoked-token detection | R1 | B S |
| POS-FR-02 | Connection status page: connected merchant, location mapping, last sync, errors | R1 | B S |
| POS-FR-03 | Map each tracked Square location to an InventoryOS location; tracked count limited by the location entitlement | R1 | B S |
| POS-FR-04 | Catalog sync: items, variations, modifiers → ExternalProduct/ExternalModifier records | R1 | B S |
| POS-FR-05 | Order ingestion at order-line level with occurred_at, location, variation, modifiers, quantity; webhook + scheduled reconciliation | R1 | B |
| POS-FR-06 | Product mapping UI: external variation → product variation (→ recipe); external modifier → modifier effect; suggestions by name | R1 | B J |
| POS-FR-07 | Expected consumption posted per order line via recipe resolution, idempotent on external line identity | R1 | B |
| POS-FR-08 | Refunds/returns recorded as ExternalRefund linked to the order; **no automatic stock restore**; optional operator classification of returned items as restock or waste (PD-10) | R1 (record) / R1.1 (classify) | B S J |
| POS-FR-09 | Cancellations/voids before completion produce no consumption; a completed-then-voided order produces reversal movements | R1 | B |
| POS-FR-10 | Sync history and error log with retry; failed items visible as exceptions | R1 | B |
| POS-FR-11 | Duplicate event prevention by event id and by order-line identity | R1 | B |
| POS-FR-12 | Daily reconciliation: count of orders/lines in Square vs ingested for the business day; mismatch → exception | R1 | B |
| POS-FR-13 | Optional write-back of availability/counts to Square for items Square tracks (C-34) | R1.1 | S O |
| POS-FR-14 | Reprocess consumption for a period after fixing a mapping, as reversal + re-post, with confirmation | R1 | R |
| POS-FR-15 | Adapter boundary: business logic consumes normalized order events, never Square payloads | R1 | B |
| POS-FR-16 | Generic sales API for first-party ordering sites (e.g. JuJu) and unsupported POS systems, same normalized model (file import is STD-FR-03) | R1.1 | B R |
| POS-FR-26 | Discounts and comps change sales value, never consumption; voids before completion consume nothing; completed-then-voided orders reverse consumption (WF-17) | R1 | B |
| POS-FR-27 | Integration health per connection: last success, lag, error rate, unprocessed events, token status; alerts on thresholds | R1 | B |

### Future — InventoryOS POS mode

| ID | Requirement | Rel | Src |
|---|---|---|---|
| POS-FR-25 | InventoryOS POS generates SalesTransactions natively (source `inventoryos_pos`); payment processing via a third party; hardware separate. Requirements are written only after validation (PD-35); nothing in R1 may block it | Future | B |

**Dependencies.** M7 recipes, M2 ledger, M10 exceptions. **Acceptance.** AC-POS-01..12, AC-SAL-01..08.

---

## Module 10 — Reconciliation and Exception Management (REC)

**Purpose.** Detect differences between expected and physical reality and turn them into
manageable, resolvable exceptions.
**Users.** Employee (count), Manager+ (review, approve, resolve), Owner.
**Data ownership.** StockCount, StockCountLine, Reconciliation (count session result),
ReconciliationLine, Exception, ExceptionResolution.

### Counts

| ID | Requirement | Rel | Src |
|---|---|---|---|
| REC-FR-01 | Count session: full, by category, by storage location, or single item; phone-friendly list in storage order | R1 | B S J |
| REC-FR-02 | Count entry in any convertible unit, including packages + loose (e.g. 3 cases + 4 each) | R1 | B |
| REC-FR-03 | Expected quantity snapshot at the count timestamp (recorded balance as of that instant) stored per line; movements during an open session are accounted for by timestamp | R1 | B |
| REC-FR-04 | Blind counting option (hide expected from counter) | R1.1 | R |
| REC-FR-05 | Variance = counted − expected, in quantity and cost | R1 | B J |
| REC-FR-06 | Variance reason per line (optional for Employees, required for Manager approval of lines above threshold): unrecorded waste, spillage/over-pour, receiving error, recipe error, theft suspected, count error, unknown | R1 | B |
| REC-FR-07 | Posting: one `count_variance` movement per non-zero line, attributed to counter; counts are observations, variances are movements (DR-12) | R1 | B J |
| REC-FR-08 | Review: lines whose variance exceeds a business threshold (value or %) create an exception for Manager+ review after posting (non-blocking). Approval-before-posting mode is an option (PD-12) | R1 (post-review) / R1.1 (pre-approval) | B |
| REC-FR-09 | Reconciliation history: past sessions, totals, per-line detail | R1 | B |
| REC-FR-10 | Batch-level count for batch-tracked items (optional) | R1.1 | B |

### Exceptions

| ID | Requirement | Rel | Src |
|---|---|---|---|
| REC-FR-11 | Exception record: type, severity, subject (item/batch/order/receiving/sync), detected_at, money impact (if known), status (`open` → `acknowledged` → `resolved` / `dismissed`), resolution note, resolver | R1 | B |
| REC-FR-12 | R1 exception types: `unmapped_product`, `negative_balance`, `count_variance_high`, `batch_expired_unresolved`, `batch_shortfall`, `sync_failure`, `pos_reconciliation_mismatch`, `receiving_discrepancy`, `price_variance`, `balance_drift`, `missing_cost`, `conversion_missing`, `sales_missing`, `sales_source_overlap` | R1 | B J |
| REC-FR-13 | Exceptions are de-duplicated by (type, subject); a recurring condition updates the open exception rather than creating a new one | R1 | J |
| REC-FR-14 | Auto-resolve when the condition clears (e.g. product mapped; batch resolved), recording "resolved by system" | R1 | J |
| REC-FR-15 | Resolution actions link to the fix (map product, post count, dispose batch) — resolving never just edits a number | R1 | B |
| REC-FR-16 | Exceptions visible to Employees only when they are operational (no costs) | R1 | J |

**Dependencies.** All modules raise exceptions. **Acceptance.** AC-REC-01..09.
**Reporting.** Stock discrepancies, variance trend.

---

## Module 11 — Reordering and Purchasing Intelligence (ROR)

**Purpose.** Help owners decide what to buy, with explainable suggestions.
**Users.** Owner, Manager.

| ID | Requirement | Rel | Src |
|---|---|---|---|
| ROR-FR-01 | Per item: on hand, threshold, reorder point, average daily use (configurable window, default 28 days; from sales consumption, or count-based usage when Standalone sales are missing), days of stock remaining | R1 | B S J |
| ROR-FR-02 | Suggested order quantity = max(0, (avg daily use × (lead time + cover days) × (1 + safety %)) + threshold − on hand − on order), rounded up to purchase unit | R1 (on-order from R1.1) | B J S |
| ROR-FR-03 | Every suggestion shows its inputs and formula ("why this number") | R1 | B |
| ROR-FR-04 | Suggestions grouped by supplier with order-by date from lead time and delivery days | R1 | B S |
| ROR-FR-05 | Negative or stale on-hand → suggestion replaced by "count first" | R1 | J |
| ROR-FR-06 | Prepared items: raw-ingredient demand derived from prepared-item demand (JuJu precedent); batches already made reduce raw need | R1.1 | J |
| ROR-FR-07 | Export suggestions to CSV; create PO from selection | R1 (CSV) / R1.1 (PO) | B S |
| ROR-FR-08 | Never purchases automatically [CONFIRMED] | R1 | B |
| ROR-FR-09 | Demand forecast (weekday-aware) feeding average daily use | R2 | J |

**Acceptance.** AC-ROR-01..05.

---

## Module 12 — Analytics and Business Intelligence (ANL)

**Purpose.** Turn operational records into decisions. Every metric has a documented formula,
cost basis and data source; estimates are labelled.
**Users.** Owner, Manager. Employee: none with money.

| ID | Requirement | Rel | Src |
|---|---|---|---|
| ANL-FR-01 | **Cost basis rule** (applies to every metric): historical metrics use the unit-cost snapshot on each movement; current valuation uses each item's current unit cost (latest accepted receipt). PD-08 tracks the alternative (batch/FIFO actual cost) | R1 | B J S |
| ANL-FR-02 | Inventory valuation (current and as-of date) | R1 | B S |
| ANL-FR-03 | Ingredient consumption by item and period (sales vs production vs waste split) | R1 | B J |
| ANL-FR-04 | Waste cost by reason, item, product, actor, period | R1 | B J |
| ANL-FR-05 | Expiration losses (waste with reason `expired` + expired-unresolved at risk) | R1 | B S |
| ANL-FR-06 | Recipe cost and estimated gross margin by product variation | R1 | B J |
| ANL-FR-07 | Count variance (stock discrepancies) by item and period, quantity and cost | R1 | B J |
| ANL-FR-08 | Receiving discrepancies and supplier price changes | R1 | B |
| ANL-FR-09 | Weekly email summary to owner: stock value, waste cost, money at risk, low stock, exceptions open | R1 | S O |
| ANL-FR-10 | Theoretical vs actual usage (expected consumption vs consumption implied by counts) | R1.1 | B |
| ANL-FR-11 | Inventory turnover, purchasing history, production yield, reorder performance | R2 | B |
| ANL-FR-12 | Realized ingredient cost of sales for a period from actual consumption snapshots, with % of sales lines that had complete recipes | R1 | J |
| ANL-FR-13 | CSV export of every report table | R1 | B J |
| ANL-FR-14 | Count-based actual usage per item per count interval (formula below); works with no sales data and is Standalone's primary usage measure | R1 | B |
| ANL-FR-15 | Supplier costs and purchasing trends by supplier, category and item | R1.1 | B |
| ANL-FR-16 | Location performance comparison (waste, variance, margin, usage per location) | Expansion | B |
| ANL-FR-17 | Demand and inventory forecasting | Advanced | B J |

Formulas: [§Metric definitions](#metric-definitions). **Acceptance.** AC-ANL-01..08.

---

## Module 13 — Staff and Permissions (STF)

Full matrix: [USER_ROLES_AND_PERMISSIONS](USER_ROLES_AND_PERMISSIONS.md).

| ID | Requirement | Rel | Src |
|---|---|---|---|
| STF-FR-01 | Invite by email with role; invitation expires (7 days); revocable | R1 | B |
| STF-FR-02 | Roles: Owner, Administrator, Manager, Employee, Read-only (R1); Inventory manager, Purchasing staff, Production staff presets (R1.1, core); custom roles (Advanced) — PD-11 | R1 / R1.1 | B J |
| STF-FR-03 | Backend-enforced permissions per operation | R1 | B J |
| STF-FR-04 | Employee attribution on every movement and record | R1 | B J |
| STF-FR-05 | Activity history filterable by person | R1 | B |
| STF-FR-06 | Access revocation takes effect on the next request (sessions invalidated) | R1 | B J |
| STF-FR-07 | Always at least one active Owner (cannot remove/demote the last) | R1 | J |
| STF-FR-08 | Approval workflows: count variance above threshold (R1 post-review), adjustment above threshold, voids after window | R1 / R1.1 | B |
| STF-FR-09 | Shared-device mode: quick person switch by PIN for attribution on a counter tablet | R1.1 | J (gap noted in JuJu) |
| STF-FR-10 | Location-scoped memberships (all locations or a list); every location-level permission checked against scope | R1 (model) / Expansion (UI) | B |

**Acceptance.** AC-STF-01..08.

---

## Module 14 — Business Administration (ADM)

| ID | Requirement | Rel | Src |
|---|---|---|---|
| ADM-FR-01 | Business profile: name, address, contact email, logo (optional) | R1 | B |
| ADM-FR-02 | Location: name, address, **time zone**, business-day cutoff hour, currency | R1 | B |
| ADM-FR-03 | Units: default display units (metric/US), custom packages | R1 | B |
| ADM-FR-04 | Categories, waste reasons, adjustment reasons | R1 | B |
| ADM-FR-05 | Alert settings: expiry window, low-stock email digest time, recipients, variance thresholds | R1 | B S |
| ADM-FR-06 | Recipe settings: modifier scaling factors (e.g. ice levels), default production mode | R1 | J |
| ADM-FR-07 | POS connections (M9) | R1 | B |
| ADM-FR-08 | User management (M13) | R1 | B |
| ADM-FR-09 | Subscription: plan (Complete / Standalone), location quantity, status, payment method, invoices, plan change, cancel — via Stripe portal | R1 | B S |
| ADM-FR-10 | Data export: items, recipes, movements, batches, waste, counts, receipts (CSV, zip) at any time incl. read-only state | R1 | B |
| ADM-FR-11 | Business audit history: settings changes, role changes, connection changes | R1 | B |
| ADM-FR-12 | Account deletion request with confirmation; executed per retention policy (PD-07) | R1 | B |
| ADM-FR-13 | Multi-location administration (Multi-location expansion, entitlement-gated) | Expansion | B |
| ADM-FR-14 | Operating mode per location: sales source of record, open days, cutover date (WF-25) | R1 | B |
| ADM-FR-15 | Plan and entitlements view: what the subscription includes, limits and usage (locations, extraction cap) | R1 | B |
| ADM-FR-16 | Tenant administration (founder console): find tenant, subscription and sync health, re-run jobs, audited time-boxed support access | R1 | B |

**Acceptance.** AC-ADM-01..06.

---

## Module 15 — Standalone Operations (STD)

**Purpose.** Make InventoryOS a complete, useful product for businesses with no POS — not a
stripped-down trial [CONFIRMED]. Every core module works without a POS (U-09, DR-29); this module
adds the ways sales and usage are recorded without one and keeps the data's origin honest.
**Users.** Owner, Administrator, Manager (sales entry and import); Employees if allowed (PD-37).
**Data ownership.** SalesSource (`manual`, `file_import`), their SalesTransactions, ImportFile,
column mappings, direct usage records.
**Workflows.** WF-16, WF-19, WF-25.

| ID | Requirement | Rel | Src |
|---|---|---|---|
| STD-FR-01 | No POS dependency: onboarding, items, recipes, receiving, production, waste, counts, reconciliation, reorder, reports and billing complete with no POS configured or credentials present (DR-29) | R1 | B |
| STD-FR-02 | Daily sales entry: for a business date, quantity sold per product variation (modifier counts optional) in a fast grid on phone or tablet, most-sold first; saving posts consumption via WF-12 | R1 | B |
| STD-FR-03 | Sales file import (CSV/XLSX from a card terminal, cash register, delivery platform or spreadsheet): saved column mapping per source, learned product-name matching, preview with unmatched rows and already-entered days, idempotent re-import | R1 | B |
| STD-FR-04 | Direct usage record: ingredient used without a recorded sale (staff meals, samples, catering by weight), reason required → `manual_consumption` | R1 | B |
| STD-FR-05 | Correcting a saved day replaces it by reversal + re-post, attributed, with reason (WF-26) | R1 | B |
| STD-FR-06 | Open business days without sales raise `sales_missing`; optional morning reminder | R1 | R |
| STD-FR-07 | **Origin labelling**: every sales record and every sales-derived figure shows whether it came from manual entry, import or a POS; Standalone reports state coverage (days with sales ÷ open days) — never presented as automated POS data [CONFIRMED] | R1 | B |
| STD-FR-08 | Count-based actual usage (ANL-FR-14) is the primary usage measure and works with zero sales data | R1 | B |
| STD-FR-09 | Reorder suggestions fall back to count-based usage where sales data is incomplete, and say so | R1 | B |
| STD-FR-10 | Theoretical vs actual usage computed only over days with sales entered; other days excluded and disclosed | R1.1 | B |
| STD-FR-11 | Adopting a POS later is a mode change with a cutover date and no re-setup (WF-25) | R1 | B |

**Limitations stated to customers.** Sales granularity is a business day; accuracy depends on entry
discipline; automatic per-order reconciliation needs a POS.
**Failure conditions.** Import row errors are listed, never silently dropped; a day owned by another
sales source is refused (`sales_source_overlap`).
**Dependencies.** M2 ledger, M7 recipes, M9 sales source model, M10 counts and exceptions.
**Acceptance.** AC-SAL-01..08.

---

## User experience and application structure

Principles: operational speed first (every Employee task opens within 2 taps of the employee home;
waste is saved within 3 taps of choosing the item, WST-FR-01); mobile-first for
floor tasks, tablet for counts and receiving, desktop for reports and setup; consistent glossary
terms; no visual design system or frontend framework is prescribed here [CONFIRMED]; WCAG 2.1 AA
(NFR UX-01).

| Interface | User goal | Main actions | Required data | Status indicators |
|---|---|---|---|---|
| Owner dashboard | Know what needs attention and what money is at risk | Resolve exceptions, open alerts, jump to reports | Exceptions, low stock, expiring, waste cost, stock value, sales-source status | Severity colour + label; data-completeness banner; last sync / last sales day |
| Manager dashboard | Run today's shift | Act on expiring batches, low stock, pending receiving, counts due | Same, scoped to location, today's production and receiving | Due/overdue counts; open discrepancies |
| Inventory | Find an item and trust its quantity | Search, filter, view history, adjust, count | Balances, levels, batches, movements | Low/out/negative badges; "count first" |
| Recipe management | Define and cost products | Create/edit recipes, start from another size, view versions | Items, units, conversions, costs | Missing-cost lines; unconvertible units; margin estimate label |
| Production | Record a batch fast and label it | Pick prepared item, multiplier, confirm, enter yield, undo | Production recipe, input availability | Input shortfall warning; use-by date; yield variance |
| Receiving | Get a delivery into stock correctly | Snap invoice, review lines, enter arrived/accepted, record discrepancies, post | Supplier, PO, invoice lines, items | Extraction state; price variance flags; posted badge |
| Waste entry | Log a loss in seconds | Pick item/product, quantity, reason, save, undo | Items, reasons, recent items | Saved confirmation; undo countdown |
| Expiration view | Stop expiry losses | Use first, dispose, adjust, mark down (R2) | Batches by expiry, quantity, cost at risk | Expiring today/soon/expired |
| Purchasing | Order the right amount | Review suggestions, edit, export CSV, create PO (R1.1) | Usage, levels, suppliers, lead times, on order | "Why this number"; count-first; overdue PO |
| Reporting | Decide from trustworthy numbers | Pick report, range, location; export | Ledger, snapshots, counts | Estimate vs verified labels; coverage (Standalone) |
| Employee interface | Do floor tasks without admin screens | Receive, waste, batch, count, daily sales (if allowed) | Task-specific lists | Big buttons; no costs |
| Organization admin | Configure the business | Locations, team, roles, POS/mode, plan, settings, export | Org settings, entitlements | Plan limits used; connection health |
| Onboarding | Reach first value in < 45 min | Choose POS situation, import items, recipes, connect POS or choose sales entry, opening count | Templates, import previews | Checklist progress |

| Interface | Empty state | Error states | Mobile | Accessibility |
|---|---|---|---|---|
| Owner dashboard | Onboarding checklist with next step | Stale data banner (sync down, sales missing) | Single column, exceptions first | Text labels with colour; screen-reader summaries |
| Manager dashboard | "All clear" with last check time | Same | Single column | Same |
| Inventory | "Add or import items" | Unconvertible unit; concurrent change ("refresh") | Search-first list | Keyboard search; table semantics |
| Recipe management | "Create recipes for your top sellers" (ranked by sales if available) | Unit/conversion errors named; cycle rejected | Read on phone; edit on tablet+ | Form labels; error summaries |
| Production | "Define a prepared item" | Input shortfall; missing cost | Phone-first, large controls | Large targets; no time limits except undo |
| Receiving | "Receive your first delivery" | Duplicate invoice; extraction failed → manual; already posted | Camera capture; line review in cards | Alt text on invoice image; accessible tables |
| Waste entry | Recent items empty → search | Would go negative → "count first" | Phone-first | 44 px targets |
| Expiration view | "Nothing expiring" | Job not run → stale warning | Phone list | Date read as words |
| Purchasing | "Not enough history — count or enter sales" | No supplier on item | Tablet | Formula readable as text |
| Reporting | Explains what data is needed | Report too large → background export | Summary cards; tables on desktop | Data tables with headers; CSV alternative |
| Employee interface | Shows allowed tasks only | Permission denied message names who can do it | Primary target | Simple language |
| Organization admin | — | Plan limit reached; POS auth failed | Desktop-first | — |
| Onboarding | Step 0 question | Import row errors; POS connect failed → retry or Standalone | Works on tablet | Progress announced |

---

## Metric definitions

All money in the business currency. "Cost basis" per ANL-FR-01. Every metric shows period, location
and completeness.

| Metric | Formula | Cost basis | Source | Label |
|---|---|---|---|---|
| Inventory valuation (current) | Σ items: max(recorded balance, 0) × current unit cost | Current unit cost | Ledger + items | Estimate if any item lacks cost |
| Inventory valuation (as of D) | Σ items: balance at D × unit cost effective at D (cost history) | Cost history | Ledger + cost history | Same |
| Waste cost | Σ waste movements: \|qty\| × snapshot unit cost | Snapshot | Ledger (`waste`) | Verified when all snapshots present |
| Expiration loss | Waste cost where reason = expired; plus "at risk" = Σ expired-unresolved batch remaining × batch unit cost | Snapshot / batch | Ledger, batches | At-risk is an estimate |
| Recipe cost | Σ lines: qty_base × current unit cost of ingredient ÷ recipe yield servings | Current | Recipe version + items | "Estimated at today's costs" |
| Estimated gross margin | (variation price − recipe cost) ÷ price | Current | Recipe, product price | Estimate |
| Realized ingredient cost of sales | Σ `sale_consumption` movements: \|qty\| × snapshot | Snapshot | Ledger | Shows % of sold lines with complete recipe |
| Count variance | counted − expected (qty); × snapshot unit cost at count (value) | Snapshot | Count lines | Verified |
| Unexplained loss ("found short") | Σ negative count variances, value | Snapshot | Ledger (`count_variance`) | Verified |
| Count-based actual usage | opening count + receipts + production output − production input − waste ± adjustments − closing count, per item per count interval | Snapshot (for value) | Counts + ledger | Verified for the interval; needs two counts |
| Days of stock remaining | on hand ÷ average daily use (window) | — | Ledger | Estimate |
| Suggested order qty | ROR-FR-02 | — | Ledger, supplier | Estimate, inputs shown |
| Production yield variance | actual yield − expected yield (qty, %) | — | Production orders | Verified |
| Supplier price change | (new unit price − previous unit price) ÷ previous | Receipt prices | Receiving lines | Verified |
| Inventory turnover (R2) | consumption cost (period) ÷ average inventory value (period) | Snapshot | Ledger | Estimate |

Historical reports must not change because a current price changes (they use snapshots).

---

## Initial release definition

### Dependency order (what must exist before what)

```
ADM business/location/time zone ──► STF auth, roles ──► INV items, units, conversions
                                                        │
                                                        ▼
                                   INV ledger (append-only, idempotent, concurrent-safe)
                     ┌──────────────┬──────────┬───────┴────────┬──────────────┐
                     ▼              ▼          ▼                ▼              ▼
              BAT batches     RCV receiving  WST waste     REC counts     RCP recipes (versioned)
                     │              │                            │              │
                     └──────┬───────┘                            │       ┌──────┴──────┐
                            ▼                                    │       ▼             ▼
                     PRD production ◄────────────────────────────┼── Sales mapping PRD prep recipes
                                                                 │       │
                                                                 │       ▼
                                                                 │  Sales consumption (expected, any source)
                                                                 ▼       │
                                                     REC exceptions ◄────┘
                                                                 │
                                         ROR suggestions ◄───────┤
                                         ANL reports ◄───────────┤
                                         CMD dashboard ◄─────────┘
                    ADM subscription/billing (parallel; gates write access)
```

### Release plan [CONFIRMED 2026-11-04 — C-29]

Phase 3 priority [CONFIRMED 2026-11-04]: **Complete and Standalone first**; InventoryOS POS evaluated
separately; then multi-location and Advanced.

| # | Capability (brief §16) | R1 launch | Staged later |
|---|---|---|---|
| 1 | Business setup and access control | Org, one location, Owner/Administrator/Manager/Employee/Read-only, invites, backend permissions, location-scoped model | Specialist presets, PIN switch (R1.1); custom roles (Advanced) |
| 2 | Inventory item management | Full CRUD, units, conversions, categories, barcode lookup, CSV import + quick-add (onboarding depends on it) | Starter templates (R1.1) |
| 3 | Movement tracking | Full ledger, adjustments, history | Transfers (R2) |
| 4 | Batches and expiration | Batches from receiving/production/manual, FEFO, expiry queue, alerts, disposal | Markdown suggestions, photo date capture (R2) |
| 5 | Suppliers and receiving | Supplier entity + items, manual + invoice receiving with 4-quantity model and discrepancies | **Purchase orders + PO-linked receiving (R1.1)** |
| 6 | Recipes and product mapping | Versioned recipes, add/scale modifiers, prepared-item ingredients, costing | Remove/substitute modifiers, bulk import (R1.1); diff view (R2) |
| 7 | Sales sources | **Connected:** Square OAuth, catalog, order-line ingestion, webhooks + reconciliation, refunds recorded. **Standalone:** daily sales entry, sales file import, missing-day detection, mode switching | Square write-back, generic sales API (R1.1); other POS adapters (Future) |
| 8 | Expected consumption | Per order line, versioned, idempotent, reprocess after mapping | — |
| 9 | Waste tracking | Full | — |
| 10 | Physical reconciliation | Count sessions, variance posting, post-review exceptions | Blind count, pre-approval, batch counts (R1.1) |
| 11 | Low-stock and expiration alerts | In-app + email digest | SMS/push (Future) |
| 12 | Operational dashboard | Full R1 set | — |
| 13 | Basic reporting | Valuation, consumption, waste, expiry loss, recipe cost/margin, variance, receiving, weekly email, CSV | Theoretical vs actual (R1.1); turnover etc. (R2) |
| 14 | Subscription management | Complete ($99/location) and Standalone ($99) via Stripe, entitlements, lifecycle, read-only lapse, export | Multi-location and Advanced plans (expansion) |
| + | Production (simple, single-step) | **Included in R1** — juice bars/cafés prep daily (JuJu proves it) | Multi-step status, variance analytics (R2) |
| + | Reorder suggestions | Included (explainable, CSV) | PO creation (R1.1), forecast (R2) |
| + | Count-based usage | Included (Standalone's primary usage measure) | — |
| + | InventoryOS POS | **Not in R1** | Separate validation track (PD-35); build only after validation |
| + | Multi-location, Advanced | Location-aware data model and entitlements in R1 | Plans and features per Phase 3 roadmap |

**Why POs are staged, not dropped:** manual and invoice receiving already deliver the receiving
workflow's integrity (ordered is optional; invoiced/arrived/accepted are mandatory). Receiving is
designed with a nullable PO reference so R1.1 adds POs without reworking receiving. Foundational
workflows (ledger, versioned recipes, counts-as-observations, idempotent ingestion) are **not**
staged — they are expensive to retrofit.

## Roadmap boundaries

| Stage | Scope | Gate to start |
|---|---|---|
| **Initial commercial launch (R1)** | The $99 single-location product in **both** Standalone and Connected (Square) modes: core modules, Module 15, Square adapter, entitlements and billing for Complete and Standalone | Phase 2 audit of the chosen codebase; remaining P0 decisions closed |
| **Post-launch improvements (R1.1)** | POs and PO-linked receiving, Square write-back, generic sales API, specialist role presets, PIN switch, blind counts, count pre-approval, refund classification, bulk recipe import, starter templates, hour-level shelf life, purchasing trends | Launch stable; pilot feedback |
| **Multi-location expansion** | Several locations per organization, location-scoped roles UI, transfers, centralized and comparative reporting, consolidated purchasing, location performance, multi-location pricing | Paying single-location customers asking; PD-33 |
| **Advanced capabilities** | Forecasting, automated replenishment, advanced procurement, supplier performance, advanced reporting, API access, expanded integrations (Toast, Clover, …), custom automation, custom roles, extended audit history, configurable rules | ≥ 3 paying customers asking for the same capability; PD-34 |
| **Future native POS** | Checkout, menu, orders, transactions, payment-processor integration, receipts, tax, hardware | Validation (PD-35) and the owner's $1,000 revenue gate; never a launch dependency [CONFIRMED] |

### Standalone vs Connected implementation sequence [CONFIRMED 2026-11-04 — C-27]

Both modes are in R1, built in this order:
1. Core engine (ledger, items, recipes, batches, receiving, waste, production, counts) — both need it.
2. Standalone sales capture (Module 15) — no external dependency; proves the consumption path end
   to end and that nothing depends on Square; serves Scenario B pilots.
3. Square adapter (Connected) — catalog sync, mapping and automatic ingestion onto the proven path.

Reasons: Connected needs everything Standalone needs plus an adapter; the existing app's Square code
has never run against a live Square account. Reusable Square code found by Phase 2 shortens step 3;
it does not change the order.

### Native POS vs external POS integration

| | External POS integration (Connected) | Native InventoryOS POS (Future) |
|---|---|---|
| What is built | Adapter: auth, sync, mapping, webhooks, reconciliation, health | Checkout UI, menu and pricing, order lifecycle, tax calculation, receipts, refunds, tips, cash handling, end-of-day close, payment-processor integration, card readers and hardware, offline operation |
| Regulatory exposure | Provider API terms | Payment compliance (processor-led), sales tax rules, receipt and consumer rules |
| Operational burden | Mapping support | Business-critical uptime during service; hardware support |
| Costs | Moderate, mostly upfront | Large build and ongoing operations; processing and hardware costs passed through, not in the subscription |
| Reuse of the engine | — | Native sales enter as SalesTransactions (POS-FR-25); no engine change |

## Feature prioritization matrix

H high · M medium · L low. Launch necessity: **Must** (R1) · Should · Later (R1.1) · Expansion ·
Advanced · Future.

| Feature | Customer value | Revenue impact | Operational importance | Technical complexity | Dependencies | Risk | Launch necessity |
|---|---|---|---|---|---|---|---|
| Inventory ledger and balances | H | H | H | M | Items, units | Wrong numbers destroy trust | Must |
| Items, units, conversions | H | M | H | M | — | Bad conversions corrupt data | Must |
| Recipes (variants, modifiers, versions) | H | H | H | M–H | Items | Consumption accuracy | Must |
| Consumption engine | H | H | H | M | Recipes, ledger | Double counting | Must |
| Standalone sales capture | H (Scenario B) | H | H | L–M | Engine | Manual data quality | Must |
| Square adapter | H (Scenario A) | H | H | M–H | Engine, mapping | Unproven live API; rate limits | Must |
| Batches, expiry, FEFO | H | H | H | M | Ledger, receiving | Inferred allocation misread as exact | Must |
| Receiving (4 quantities) and invoices | H | M | H | M | Items, suppliers | — | Must |
| Assisted invoice extraction | M–H | M | M | M | Receiving | AI cost and accuracy | Should (capped, PD-25) |
| Waste | H | H | H | L | Ledger | Low | Must |
| Production (single-step) and finished goods | H (juice, bubble tea) | M | H | M | Recipes, batches | Double counting | Must |
| Counts and reconciliation, actual usage | H | M | H | M | Ledger | Low | Must |
| Exceptions | M–H | M | H | M | All modules | Alert fatigue | Must |
| Dashboard | H | M | H | M | All modules | Misleading metrics | Must |
| Low-stock and expiry alerts | H | M | H | L | Ledger, batches | Email deliverability | Must |
| Reorder suggestions | M–H | M | M | L | Usage history | Bad advice from bad data | Must (explainable) |
| Core reports and export | H | M | M | M | Ledger | Estimates misread | Must |
| Roles (5) and permissions | M | M | H | M | Auth | Data exposure | Must |
| Billing and entitlements | — | H | H | M | Auth | Billing errors | Must |
| Tenant isolation | — | H (trust) | H | M | Architecture | Cross-tenant leak is existential | Must |
| Purchase orders | M | L–M | M | M | Suppliers, receiving | Low | Later (R1.1) |
| Square write-back | M | M | M | M | Outbox | Overwriting POS data | Later (R1.1) |
| Specialist role presets | L–M | L | M | L | Roles | Low | Later (R1.1) |
| Transfers and multi-location | H (segment 7) | H | M | M–H | Location scope | Scope creep | Expansion |
| Forecasting and automation | M | M–H | L | H | History | Wrong forecasts | Advanced |
| API access | L–M | M | L | M | Auth, rate limits | Security | Advanced |
| Native POS | H (Scenario C) | Unvalidated | — | Very high | Everything + payments | Scope explosion | Future (validation) |

## Future capabilities

Not committed; not to distract from R1 [CONFIRMED]: **InventoryOS POS (validation required, PD-35)**;
additional POS integrations (Toast, Clover);
AI-assisted invoice processing beyond R1's reviewed extraction; demand forecasting; intelligent
reorder; advanced profitability; multi-location management and transfers UI; advanced supplier
analytics; automated exception detection (anomaly models); production planning; advanced BI;
printable labels; SMS/push; supplier API ordering; accounting exports (QuickBooks/Xero).

Candidates for the **Advanced** entitlement: forecasting, automation, advanced analytics, APIs,
integrations (PD-34).
