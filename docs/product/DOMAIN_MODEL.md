# Domain Model

Authoritative source for **conceptual entities, relationships, ownership, lifecycle and data
integrity rules**. This is a conceptual model, not a database schema; physical design happens
after the Phase 2 foundation decision (PD-02).

## 1. Modelling decisions against the brief's entity list

| Brief entity | Decision | Reason |
|---|---|---|
| Role, Permission | Permission keys and the eight **preset roles** are code-defined; a **Role** table holding custom permission sets is added for the Advanced entitlement | Presets cover R1/R1.1; custom roles are an Advanced capability (PD-11) |
| Inventory transaction vs ledger entry | **InventoryTransaction** (header: type, source record, actor, idempotency key) groups one or more **InventoryMovement** ledger entries posted atomically | One business event = one transaction; idempotency and reversal operate on the whole event |
| Ingredient, Product, Tenant, Refund, Reconciliation record, POS connection, POS product mapping | Mapped to model entities in §1a | Avoid synonyms in code |
| UnitOfMeasure, UnitConversion | System units + same-dimension factors are **code constants**; **PackageUnit** (org-defined) and **ItemUnitConversion** (item-specific, incl. cross-dimension) are data | Constants never vary; item-specific conversions must be explicit (INV-FR-05) |
| InventoryLocation | Renamed **StorageLocation** (walk-in, dry store, line fridge) | Avoid confusion with BusinessLocation |
| InventoryLedgerEntry | **InventoryMovement** | Domain term used by both existing apps |
| StockBalance | **Derived projection** (cached per item × location), rebuildable from movements | DR-03 |
| InventoryAdjustment | Small header (reason, approval) whose lines are `adjustment` movements | Approvals attach to the header |
| BatchMovement | **Merged into InventoryMovement** (`batch_id` + `allocation_mode`); multi-batch consumption = several movements with one source ref | One ledger, no parallel batch ledger to drift |
| ExpirationAlert | Generic **Alert** (type `expiry`) | Same delivery mechanism as low-stock |
| Receiving discrepancies | **ReceivingDiscrepancy** child of ReceivingLine | A line can be both short and over-priced |
| SellableProduct | Plus **ProductVariation** and **Modifier**, **ModifierEffect** | Sizes and modifiers drive consumption (JuJu: size, ice, add-ins, pumps) |
| RecipeIngredient | **RecipeLine** | — |
| ProductionInput / ProductionOutput | **Ledger entries of the ProductionOrder's InventoryTransaction**; the order stores expected and actual yield | Avoid duplicating the ledger |
| FinishedBatch | **InventoryBatch** with `source = production` | Prepared items are ordinary items (PRD-FR-01) |
| SyncError | Status + error on **SyncEvent / SyncRun**, surfaced as **Exception** | One place to look |
| Reconciliation, ReconciliationLine | **Merged into StockCount / StockCountLine** (expected, variance, posted movement) | A reconciliation is the posted result of a count |
| ExceptionResolution | Fields on **Exception** + AuditEvent trail | — |
| CostSnapshot | Unit-cost snapshot **on every movement** + **ItemCostHistory** (effective-dated) | Historical reports stable (DR-19) |
| InventoryValuation, OperationalMetric | **Derived** at query time; optional daily ValuationSnapshot in R2 | No speculative storage |
| ReportDefinition | **Not modelled** (reports are code-defined) | Speculative |
| ExternalOrder, ExternalOrderLine | Kept only as **adapter staging** (Connected mode); normalized into source-agnostic **SalesTransaction / SalesTransactionLine** | Standalone and future POS modes produce sales with no POS; Square must not be the domain foundation [CONFIRMED 2026-11-04] |
| Subscription | Plus **Plan** and **Entitlement** | Plan catalogue (Complete, Standalone, later POS, Multi-location, Advanced) with configurable entitlements |
| — (added) | **SalesSource, SalesTransaction, SalesTransactionLine, SalesReturn, ExternalModifier, SyncRun, OutboundCommand, Alert, ItemCostHistory, PackageUnit, ItemUnitConversion, Plan, Entitlement** | Needed by confirmed workflows and operating modes |

## 1a. Brief terms → model entities

| Brief term | Model entity |
|---|---|
| Tenant / Organization | Organization |
| Location | BusinessLocation (with StorageLocation areas inside) |
| Ingredient | InventoryItem of type raw ingredient or prepared item |
| Product | SellableProduct + ProductVariation (finished goods and resale goods are InventoryItems mapped by a "1 ×" recipe) |
| Inventory transaction | InventoryTransaction |
| Inventory ledger entry | InventoryMovement |
| Production order | ProductionOrder |
| Feature entitlement | Entitlement |
| POS provider | Code-defined adapter catalogue (`square` first) referenced by IntegrationConnection.provider |
| POS connection | IntegrationConnection |
| POS product mapping | ProductMapping |
| Sales transaction / line | SalesTransaction / SalesTransactionLine |
| Refund | SalesReturn |
| Reconciliation record | StockCount + StockCountLine (expected, variance, review) and, for POS, the daily POS reconciliation result on SyncRun |

## 2. Context map (module ownership)

| Context (module) | Owns (sole writer) | Reads |
|---|---|---|
| Identity & Tenancy (STF, ADM) | Organization, BusinessLocation, User, Membership, Invitation, Subscription, Plan, Entitlement, AuditEvent, Settings | — |
| Catalog (INV) | InventoryItem, InventoryCategory, PackageUnit, ItemUnitConversion, StorageLocation, ItemCostHistory | Supplier |
| Ledger (INV) | **InventoryTransaction, InventoryMovement**, StockBalance projection, Transfer + TransferLine (Expansion) | everything posts *through* the ledger API |
| Batches (BAT) | InventoryBatch | Movements |
| Procurement (PRC) | Supplier, SupplierItem, PurchaseOrder, PurchaseOrderLine | Items |
| Receiving (RCV) | Receiving, ReceivingLine, ReceivingDiscrepancy, Invoice, InvoiceLine | PO, Items |
| Waste (WST) | WasteEvent, WasteLine, WasteReason | Items, Recipes |
| Recipes (RCP) | SellableProduct, ProductVariation, Modifier, ModifierEffect, Recipe, RecipeVersion, RecipeLine | Items |
| Production (PRD) | ProductionOrder | Recipes, Batches |
| Sales (POS) | SalesSource, SalesTransaction, SalesTransactionLine, SalesReturn (daily entries, imports, adapter output, future InventoryOS POS) | Recipes |
| Integration adapters (POS) | IntegrationConnection, ExternalProduct, ExternalModifier, ExternalOrder + ExternalOrderLine (staging), ProductMapping, SyncEvent, SyncRun, OutboundCommand | Sales |
| Reconciliation (REC) | StockCount, StockCountLine, Exception | Ledger |
| Notifications (CMD/ANL) | Alert | All |

**Rule:** only the Ledger context writes InventoryMovement; other contexts call it with a source
reference and idempotency key. No module updates a balance directly.

## 3. Overview diagram

```mermaid
erDiagram
  Organization ||--o{ BusinessLocation : has
  Organization ||--o{ Membership : has
  User ||--o{ Membership : holds
  Organization ||--|| Subscription : pays
  Organization ||--o{ InventoryItem : owns
  InventoryItem ||--o{ ItemUnitConversion : converts
  InventoryItem ||--o{ InventoryMovement : changes
  BusinessLocation ||--o{ InventoryMovement : at
  InventoryItem ||--o{ InventoryBatch : batched
  InventoryBatch ||--o{ InventoryMovement : allocated
  Supplier ||--o{ SupplierItem : sells
  InventoryItem ||--o{ SupplierItem : bought_as
  Supplier ||--o{ PurchaseOrder : receives
  PurchaseOrder ||--o{ PurchaseOrderLine : lines
  Receiving ||--o{ ReceivingLine : lines
  Receiving }o--o| PurchaseOrder : against
  Receiving }o--o| Invoice : documents
  ReceivingLine ||--o{ ReceivingDiscrepancy : flags
  ReceivingLine ||--o| InventoryBatch : creates
  SellableProduct ||--o{ ProductVariation : sizes
  ProductVariation ||--|| Recipe : made_by
  Modifier ||--o| Recipe : effect_recipe
  Recipe ||--o{ RecipeVersion : versions
  RecipeVersion ||--o{ RecipeLine : lines
  RecipeLine }o--|| InventoryItem : uses
  ProductionOrder }o--|| RecipeVersion : follows
  ProductionOrder ||--|| InventoryBatch : outputs
  IntegrationConnection ||--o{ ExternalProduct : syncs
  ExternalProduct ||--o| ProductMapping : mapped
  ProductMapping }o--|| ProductVariation : to
  BusinessLocation ||--o{ SalesSource : sells_through
  IntegrationConnection ||--o| SalesSource : feeds
  SalesSource ||--o{ SalesTransaction : records
  SalesTransaction ||--o{ SalesTransactionLine : lines
  SalesTransaction ||--o{ SalesReturn : returned
  SalesTransactionLine }o--o| ProductVariation : of
  Subscription }o--|| Plan : on
  StockCount ||--o{ StockCountLine : lines
  WasteEvent ||--o{ WasteLine : lines
  Exception }o--|| Organization : raised_in
```

## 4. Entity catalogue

Every tenant-owned entity carries `organization_id` (DR-09); location-scoped ones also carry
`location_id`. "Lifecycle" lists states; "Constraints" lists invariants beyond FK integrity.

### 4.1 Business and identity

| Entity | Purpose / key attributes | Lifecycle | Constraints |
|---|---|---|---|
| Organization | The tenant (one business). name, owner contact, created_at, deletion_requested_at | active → read_only (billing) → pending_deletion → deleted | ≥ 1 active Owner membership (DR-27) |
| BusinessLocation | A physical site. name, address, time_zone (IANA), business_day_cutoff, currency, open days, operating mode (derived from its sales source of record) | active / archived | Active count ≤ location entitlement |
| User | A person's global identity. email (unique, verified), name, auth credentials, MFA | invited → active → disabled | One user may belong to many orgs |
| Membership | User × Organization × role × location scope (all locations, or a list) | active / revoked | Unique (user, org); scope ⊆ organization's locations |
| Invitation | email, role, token hash, expires_at, invited_by | pending → accepted / expired / revoked | Single use |
| Subscription | Stripe customer/subscription ids, plan, location quantity, add-ons, status, current_period_end, trial_end, grace_until | see [SAAS_REQUIREMENTS](SAAS_REQUIREMENTS.md#4-billing) | One per org; webhook-driven, ordered by event time |
| Plan | Catalogue entry: code (complete / standalone / pos / multi_location / advanced), price reference, billing basis (per location / per business) | active / retired | Prices are hypotheses (C-05); changed by configuration |
| Entitlement | Resolved capabilities for an organization: allowed modes, max locations, advanced capability flags | derived from subscription | One server-side check answers every entitlement question (SAAS-B-09) |
| AuditEvent | actor, action, subject type/id, before/after summary, at, ip | append-only | Never edited (DR-23) |
| Settings | Org/location config: expiry window, thresholds, units, modifier scale factors, reasons | versioned via AuditEvent | — |

### 4.2 Inventory

| Entity | Purpose / key attributes | Lifecycle | Constraints |
|---|---|---|---|
| InventoryItem | name, category, type (raw ingredient/material / prepared / finished good / packaging / supply / resale), base unit (g / ml / each), display unit, tracks_batches, allocation (FEFO/FIFO), default shelf life, storage location, preferred supplier, min/max levels and reorder point per location, current unit cost, barcodes | active ↔ inactive | Base unit immutable after first movement (INV-FR-14); name unique per org (case-insensitive) |
| InventoryCategory | name, sort | active / archived | — |
| PackageUnit | Org-defined package: name (case, bag), = N × unit | — | N > 0; base dimension fixed |
| ItemUnitConversion | Item-specific: from qty+unit = to qty+unit (e.g. 1 lb → 340 ml juice; case = 6 × 1 gal); source note | effective-dated | Both sides > 0; cross-dimension only here (DR-11) |
| StorageLocation | Area within a business location (walk-in, dry, line) | — | — |
| ItemCostHistory | item, unit cost (per base unit), effective_from, source (receipt line / manual) | append-only | — |
| **InventoryTransaction** | org, type (receipt / sale / production / waste / count / adjustment / usage / transfer / opening / reversal), source_type, source_id, revision, actor_user_id? (null = system), occurred_at, recorded_at, idempotency_key, reverses_transaction_id? | **append-only** | Key unique per org (DR-05); all entries posted atomically |
| **InventoryMovement** (ledger entry) | transaction, org, location, item, batch?, allocation_mode (explicit / inferred / none), type, qty_base (signed), unit_cost snapshot, reason?, reverses_movement_id? | **append-only** | DR-01..DR-08; belongs to exactly one transaction |
| StockBalance (projection) | item × location: recorded qty, last movement id, updated_at | rebuilt any time | = Σ movements (DR-03) |
| InventoryAdjustment | reason, note, actor, approval status, approver | draft → posted (→ corrected) | Lines are `adjustment` movements |

**Movement types and sign**

| Type | Sign | Source |
|---|---|---|
| `opening_balance` | + / − | Onboarding count |
| `receipt` | + | ReceivingLine |
| `sale_consumption` | − | SalesTransactionLine (any source) |
| `production_input` | − | ProductionOrder |
| `production_output` | + | ProductionOrder |
| `waste` | − | WasteLine (or system expiry policy) |
| `count_variance` | + / − | StockCountLine |
| `adjustment` | + / − | InventoryAdjustment |
| `manual_consumption` | − | Direct usage record (Standalone, STD-FR-04) |
| `transfer_out` / `transfer_in` | − / + | Transfer (Expansion, WF-22) |
| `pos_correction` | + / − | POS-owned count difference, only if PD-09 adopts POS-owned SKUs |
| `reversal` | opposite of reversed | Correction (WF-26) |

### 4.3 Batches

| Entity | Purpose / key attributes | Lifecycle | Constraints |
|---|---|---|---|
| InventoryBatch | item, location, batch code (human-readable), source (receipt / production / manual / opening / transfer), source_id, supplier, lot_code, received_or_produced_at, expiry_date, date_type, initial qty, unit cost, storage location, status | active → depleted · active → expired → resolved · on_hold ↔ active | Remaining = Σ movements with this batch_id ≥ 0 (DR-13, DR-15); never deleted while it has movements (DR-24) |
| Alert | type (low_stock, out_of_stock, expiry, expired, sync, …), subject, audience (roles), first_seen, last_seen, acknowledged_by/at | open → acknowledged → cleared | Dedup by (type, subject) |

### 4.4 Procurement and receiving

| Entity | Purpose / key attributes | Lifecycle | Constraints |
|---|---|---|---|
| Supplier | name, contacts, ordering method, delivery days, lead time, minimum order | active ↔ inactive | Name unique per org |
| SupplierItem | supplier, item, supplier code/name, purchase unit + pack size, last unit price, preferred | active ↔ inactive | Unique (supplier, supplier code) when code present |
| PurchaseOrder (R1.1) | supplier, location, number, expected date, status, sent_at, notes | WF-06 | Cannot cancel after a receipt |
| PurchaseOrderLine (R1.1) | item/supplier item, qty (supplier unit), expected unit price, received qty (derived) | — | qty > 0 |
| Invoice | supplier, number, date, total, file key, content hash, extraction status, extracted payload | uploaded → extracted / needs_entry → reviewed → linked | Unique (org, content hash) |
| InvoiceLine | description, supplier code, qty, unit, unit price, line total, confidence | — | — |
| Receiving | supplier, location, PO?, invoice?, received_at, received_by, status | draft → posted → (corrected) | Posted exactly once (DR-25) |
| ReceivingLine | item, invoice line?, PO line?, ordered qty?, invoiced qty, arrived qty, accepted qty, unit, unit cost, expiry, lot | — | 0 ≤ accepted ≤ arrived (DR-25) |
| ReceivingDiscrepancy | line, type (short / over / damaged / wrong_item / price / not_invoiced / not_delivered), qty, amount, reason, resolution (credit expected / accepted / returned) | open → resolved | — |

### 4.5 Recipes and production

| Entity | Purpose / key attributes | Lifecycle | Constraints |
|---|---|---|---|
| SellableProduct | name, category, production mode (made-to-order / made-ahead), active | active ↔ inactive | — |
| ProductVariation | product, name/size, price (informational), recipe | active ↔ inactive | — |
| Modifier | name, group (e.g. ice level, add-in), effect type (add / remove / substitute / scale / none), scale factor, effect recipe | — | scale factor > 0 |
| Recipe | Owner of versions for a ProductVariation, a Modifier (add effect) or a prepared item (production recipe) | — | Exactly one owner |
| RecipeVersion | recipe, version no, effective_from, yield qty + unit (production) / servings, shelf life days (production), instructions, created_by | draft → effective → superseded | Immutable once effective and used (DR-16) |
| RecipeLine | version, item, qty, unit, optional flag, substitution group | — | Unit convertible for item (DR-11); no cycles (DR-17) |
| ProductionOrder | output item (prepared or finished good), recipe version, multiplier, expected yield, actual yield, yield variance, made_by, made_at, output batch, status | R1: `completed` in one step (→ corrected); R2: planned → in_progress → completed / cancelled | Output cost unknown if any input unpriced; traceable to input batches via its transaction |
| Transfer (Expansion) | from location, to location, status, sent_by/at, received_by/at | draft → in_transit → received (→ discrepancy) | Both locations in one organization |
| TransferLine | item, batch, qty sent, qty received | — | Batch and cost travel with stock |
| ImportFile | sales or item import: file key, content hash, column mapping, row results | uploaded → previewed → posted / rejected | Unique (org, content hash, purpose) |

### 4.6 Sales and integration

| Entity | Purpose / key attributes | Lifecycle | Constraints |
|---|---|---|---|
| SalesSource | location, type (`pos_adapter` / `manual` / `file_import` / `api` / `inventoryos_pos`), connection?, active_from (cutover business date), active_to, source of record flag | active → retired | One source of record per location per business date (DR-31) |
| SalesTransaction | source, location, source reference (order id, file row set, entry id), occurred_at **or** business date (daily entries), status (completed / voided), revision | recorded → corrected / voided | Unique (source, source reference, revision) |
| SalesTransactionLine | transaction, line key, product variation, modifiers, qty, consumption status (posted / gap / skipped), recipe version used | — | Unique (transaction, line key) |
| SalesReturn | transaction, source reference (e.g. refund id), lines, amount, status, classification (restock / waste, R1.1) | recorded → classified | Unique (source, source reference); never auto-restocks (DR-26) |
| IntegrationConnection | provider, external merchant id, external location id ↔ BusinessLocation, encrypted tokens, scopes, status, last success/failure | connected → error / revoked → disconnected | One active connection per (provider, external merchant, external location) across all orgs (DR-22) |
| ExternalProduct | connection, external item id, external variation id, name, variation name, active | synced | Unique (connection, external variation id) |
| ExternalModifier | connection, external modifier id, name, list | synced | Unique (connection, external id) |
| ProductMapping | external product → ProductVariation · external modifier → Modifier; mapped_by, at | active / superseded | One active mapping per external id |
| ExternalOrder (staging) | Provider order as received: connection, external order id, state, occurred_at, location, version; normalized into one SalesTransaction | ingested → normalized | Unique (connection, external order id) |
| ExternalOrderLine (staging) | order, line uid, external variation, modifiers, qty | — | Unique (order, line uid) |
| SyncEvent | inbound webhook: provider event id, type, received_at, processed_at, status, error | received → processed / failed (retry) | Unique (provider, event id) (DR-21) |
| SyncRun | scheduled sync: kind, started/finished, counts, status, error | running → success / failed | — |
| OutboundCommand (R1.1) | outbox for POS writes: type, payload, idempotency key, attempts, next_attempt_at, status | pending → sent / failed → dead | Committed in the same transaction as the local change |

### 4.7 Waste and reconciliation

| Entity | Purpose / key attributes | Lifecycle | Constraints |
|---|---|---|---|
| WasteEvent | location, recorded_by, occurred_at, reason, note, photo?, sellable product? | posted → voided/corrected | Reason required |
| WasteLine | event, item, batch?, qty, unit, cost snapshot | — | qty > 0 |
| WasteReason | name, code, active, system flag | — | System codes (expired, remake…) not deletable |
| StockCount | location, scope (full / category / area / item), started_by, started/posted at, status | open → posted → (corrected) | — |
| StockCountLine | count, item, batch?, counted qty, unit, counted_at, expected qty (snapshot at counted_at), variance, variance cost, reason, review status | — | Posted variance movement linked |
| Exception | type, severity, subject, detected_at, last_seen, impact estimate, status, resolution, resolved_by/at | open → acknowledged → resolved / dismissed | Unique open (org, type, subject) |

## 5. Stock quantity definitions

| Term | Definition | Where it lives |
|---|---|---|
| **Recorded stock** | Σ posted movements for item × location. The book quantity, including expected consumption from sales | StockBalance projection |
| **Expected stock** | Recorded stock **as of a specific instant** (e.g. a count line's timestamp), with a list of known gaps in that period (unmapped sales, missing conversions) | Computed; stored on StockCountLine |
| **Counted stock** | Quantity physically observed at an instant | StockCountLine |
| **Variance** | Counted − expected | StockCountLine → `count_variance` movement |
| **Available stock** | max(0, recorded stock − remaining in batches that are expired or on hold) | Computed |
| **Theoretical consumption** | Consumption the system calculates from recorded sales × recipe versions (`sale_consumption`), and from production inputs | Ledger |
| **Actual usage** | Usage implied by counts over an interval: opening count + receipts + production output − production input − waste ± adjustments − closing count | Computed between two counts |
| **Recorded adjustments** | Deliberate corrections (`adjustment`, `count_variance`, `reversal`) — neither consumption nor waste | Ledger |
| **Reserved stock** | **Not implemented.** No confirmed workflow reserves ingredients (JuJu's pickup capacity reserves line time, not ingredients). Revisit only with a real workflow | — |

## 6. Exceptions

| Type | Detected when | Auto-resolves when | Fix link |
|---|---|---|---|
| `unmapped_product` | Sale line or catalog item without mapping | Mapped | Mapping screen (+ reprocess) |
| `negative_balance` | Recorded < 0 | Count or receipt brings ≥ 0 | Count item |
| `count_variance_high` | Posted count line beyond threshold | Manager reviews | Count line |
| `batch_expired_unresolved` | Expiry passed, remaining > 0 | Remaining = 0 | Expiry queue |
| `batch_shortfall` | Consumption found no batch remaining | Count of item | Count item |
| `sync_failure` | Sync run/event failed (or no success > threshold) | Next success | Connection page |
| `pos_reconciliation_mismatch` | Daily POS vs ingested count differs | Re-sync matches | Sync history |
| `receiving_discrepancy` | Discrepancy unresolved after post | Resolution recorded | Receiving |
| `price_variance` | Invoiced price beyond threshold | Acknowledged | Receiving line |
| `balance_drift` | Nightly Σ movements ≠ projection | Projection rebuilt and verified | Support / admin |
| `missing_cost` | Item used/wasted without cost | Cost entered | Item |
| `conversion_missing` | Recipe/entry unit unconvertible | Conversion added | Item conversions |
| `sales_missing` | Standalone: open business day with no sales by the next day's cutoff | Sales entered for that day | Daily sales entry |
| `sales_source_overlap` | Sales from two sources for one location and business date | One source's entries reversed | Sales source settings |

## 7. Data integrity rules

| ID | Rule |
|---|---|
| DR-01 | Every inventory change is an InventoryMovement with `source_type` + `source_id` of its originating record. No other path changes quantity |
| DR-02 | Movements are append-only. No UPDATE of quantity/cost/type and no DELETE in application code. Corrections are `reversal` movements referencing the original (WF-26) |
| DR-03 | Recorded balance = Σ movements. Any cached balance is updated in the same transaction as the movement insert; a nightly job verifies and raises `balance_drift` on mismatch — never auto-overwrites |
| DR-04 | Movement qty ≠ 0; sign constrained by type (§4.2 table) at the database level |
| DR-05 | Each InventoryTransaction has an `idempotency_key` unique per organization. Re-posting an existing key returns the original transaction and writes nothing |
| DR-06 | A source record's posted movements are immutable; the source record is marked `corrected` when reversed |
| DR-07 | Concurrency: status transitions are conditional (`… WHERE status = 'draft'`); postings to one item × location serialize (row lock on the balance/item). No lost updates, no double posts |
| DR-08 | Negative recorded balance: permitted only from system-observed events (POS consumption; production per PD-15); raises `negative_balance`. Manual waste/adjustment/production cannot cross zero without Manager+ override with reason |
| DR-09 | Tenant isolation: every tenant-owned row has `organization_id`; every reference stays within one organization; every query is scoped by a central mechanism, not per-route convention (PD-18 decides mechanism) |
| DR-10 | Quantities stored in item base unit (g, ml, each) as exact decimals (fixed-point, ≥ 4 dp); money in minor units or fixed-point; no binary floating point |
| DR-11 | Conversions: same dimension via constants; cross-dimension only via an ItemUnitConversion for that item. Unconvertible entries are rejected (manual) or recorded as gaps (system) |
| DR-12 | Counts are observations; they generate `count_variance` movements for the difference and never set a balance directly |
| DR-13 | Batch conservation: for batch-tracked items, Σ batch remaining = recorded balance (positive part). Unbatched quantity is explicit and raises `batch_shortfall` |
| DR-14 | An expired batch remains until its remaining quantity is accounted for by a movement (waste, consumption, count, adjustment) |
| DR-15 | Batch remaining is derived from movements, never edited directly |
| DR-16 | RecipeVersions are immutable once effective and referenced. Consumption, waste expansion and production reference the version used. Edits create a new version; history never changes |
| DR-17 | Prepared-item recipes cannot form cycles (A uses B uses A) |
| DR-18 | No double consumption: a product is made-to-order (sale consumes raw lines) or made-ahead (sale consumes the prepared item). Production inputs leave stock once, at production |
| DR-19 | Historical cost preservation: every movement stores its unit-cost snapshot; ItemCostHistory is append-only. Reports for past periods use snapshots |
| DR-20 | Time: instants stored as UTC-aware timestamps; business dates (reports, "today", expiry) computed in the location time zone; expiry stored as a calendar date, or as a date-time for short-life prepared items (PD-40). Event time (`occurred_at`) is distinct from processing time (`recorded_at`) |
| DR-21 | External events de-duplicated by provider event id; external entities unique per (connection, external id); order-line consumption keyed by external line identity |
| DR-22 | One active connection per external merchant + location across all organizations; webhooks route deterministically |
| DR-23 | Non-movement changes to master data, settings, roles, mappings and recipes write AuditEvents |
| DR-24 | Referenced master data (items, suppliers, products, batches) is deactivated, not deleted. Hard deletion only via the account-deletion process |
| DR-25 | Receiving: 0 ≤ accepted ≤ arrived per line; a receiving posts exactly once |
| DR-26 | Refunds never automatically restore stock; restock happens only by explicit operator classification |
| DR-27 | An organization always has ≥ 1 active Owner |
| DR-28 | Files (invoices, photos) are stored under tenant-scoped keys and served only after an authorization check; never by guessable public URL |
| DR-29 | No entity, rule or workflow outside the integration adapters requires a POS connection or references a POS provider. Every R1 workflow works with no POS configured |
| DR-30 | Every sale, whatever its source, enters as a SalesTransaction; consumption, reporting and reorder logic read only SalesTransactions, never adapter records |
| DR-31 | Each location has exactly one sales source of record per business date; sales from any other source for that date are refused or flagged `sales_source_overlap`, so manual and POS sales are never both consumed |
| DR-32 | Plan and entitlement never change the data model or business rules — they only allow or refuse operations (location count, Advanced capabilities) |
| DR-33 | Business records (waste event, receiving, production order, count, sales transaction) explain *why*; their ledger entries are the only source of quantities and costs in reports. A waste event and its movement are never both summed |
| DR-34 | Every sales record carries its origin (`pos_adapter`, `manual`, `file_import`, `api`, `inventoryos_pos`); origin is immutable and shown wherever sales-derived figures appear |
| DR-35 | Every location-scoped record is readable and writable only by members whose location scope includes that location (or org-wide roles) |
| DR-36 | Prices are never stored in domain logic; plan prices live in billing configuration (E-01) |
