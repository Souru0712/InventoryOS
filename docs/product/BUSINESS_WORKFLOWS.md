# Business Workflows

Authoritative source for **end-to-end business processes**: who does what, what the system does,
and exactly what each step does to the inventory ledger. A feature is not complete because a record
can be created; it must participate correctly in its workflow [CONFIRMED].

References: entities and integrity rules (`DR-xx`) in [DOMAIN_MODEL](DOMAIN_MODEL.md); requirements
in [PRODUCT_SPECIFICATION](PRODUCT_SPECIFICATION.md); permissions in
[USER_ROLES_AND_PERMISSIONS](USER_ROLES_AND_PERMISSIONS.md).

## Conventions

- **Inventory transaction**: every inventory-changing event posts exactly one InventoryTransaction
  (header: type, source record, actor, idempotency key) containing one or more **ledger entries**
  (item, location, batch, signed base-unit quantity, cost snapshot). Balances are projections of
  entries (DR-01..DR-07).
- **Source record vs ledger**: the business record (waste event, receiving, production order…) says
  *what happened and why*; its ledger entries say *what it did to stock*. Reports read quantities
  and costs from ledger entries only, so a waste event and its movement are never counted twice
  (DR-33).
- Idempotency keys are `<source_type>:<source_id>[:r<revision>]`, unique per organization.
- **Modes:** C = Connected, S = Standalone. "All" means both. Native POS is future.
- **Release:** R1 launch · R1.1 first increment · Expansion · Future (see spec §Roadmap).
- Example business: a juice bar selling "Golden State" (carrot, pineapple, ginger juice) in 12 and
  16 oz, prepping ginger juice in batches.

## Index

| ID | Workflow | Rel | Modes |
|---|---|---|---|
| WF-01 | Business registration and onboarding | R1 | All |
| WF-02 | Initial inventory setup | R1 | All |
| WF-03 | Ingredient and product creation | R1 | All |
| WF-04 | Supplier creation | R1 | All |
| WF-05 | Recipe creation | R1 | All |
| WF-06 | Purchase order creation | R1.1 | All |
| WF-07 | Receiving inventory | R1 | All |
| WF-08 | Invoice recording | R1 | All |
| WF-09 | Inventory count | R1 | All |
| WF-10 | Inventory adjustment | R1 | All |
| WF-11 | Batch production | R1 | All |
| WF-12 | Ingredient consumption (shared engine) | R1 | All |
| WF-13 | Finished-goods creation | R1 | All |
| WF-14 | Expiration tracking | R1 | All |
| WF-15 | Sales ingestion from an external POS | R1 | C |
| WF-16 | Manual consumption recording (Standalone) | R1 | S |
| WF-17 | Refund and void handling | R1 (classification R1.1) | C (S for corrections) |
| WF-18 | Waste recording | R1 | All |
| WF-19 | Inventory reconciliation | R1 | All |
| WF-20 | Low-stock alerts | R1 | All |
| WF-21 | Reorder recommendations | R1 (PO R1.1) | All |
| WF-22 | Inter-location transfers | Expansion | All |
| WF-23 | Employee operations (daily routine) | R1 | All |
| WF-24 | Reporting and review | R1 | All |
| WF-25 | Operating mode change | R1 | C ↔ S |
| WF-26 | Correction of a posted record | R1 | All |
| WF-27 | Exception lifecycle | R1 | All |
| WF-28 | Product mapping and reprocessing | R1 | C (S for imports) |

```
Supplier ─► PO (R1.1) ─► Receiving ◄─ Invoice
                            │ receipt
                            ▼
 Opening count ─►   INVENTORY LEDGER   ◄─ Adjustment
                  ▲   ▲      ▲     ▲
   production in/out  │      │     └── count variance ◄─ Count ◄─ Reconciliation
                      │      └── waste ◄─ Waste event
       sale consumption ◄─ Consumption engine ◄─ Sales: POS (C) | daily entry / import (S) | native POS (future)
                            ▲
                         Recipes (versioned) ◄─ Products ◄─ Mapping
```

---

## WF-01 Business registration and onboarding

Steps:
1. Owner signs up (email, password) and verifies email.
2. Answers **"How do you take payments today?"** → Scenario A (Connected/Complete), B
   (Standalone), C (wants a POS → Standalone or supported POS + Complete; interest recorded).
3. Creates organization (business name) and first location (address, **time zone**, currency,
   business-day cutoff, open days).
4. Starts trial or checkout for the matching plan (PRICING_AND_PACKAGING).
5. Follows the onboarding checklist: WF-02 → WF-03/04/05 → (C: connect POS, WF-28) → alerts → WF-09
   opening count → invite team.

| Field | Detail |
|---|---|
| Trigger | Prospect signs up |
| Actor | Owner |
| Preconditions | None |
| Inputs | Email, password, business name, location details, POS situation |
| System behavior | Creates organization, location, Owner membership, subscription (trialing/active), entitlement set, onboarding checklist; restricts POS connection, invoice extraction and invitations until email is verified |
| Ledger effects | None |
| Records created | User, Organization, BusinessLocation, Membership (Owner), Subscription, SalesSource (mode default per answer), AuditEvent |
| Notifications | Verification email; welcome email with checklist |
| Exceptions | Email already registered → sign-in/join flow; unsupported POS named → Standalone with file import, POS name recorded as adapter demand |
| Failure handling | Checkout failure → organization stays in trial or pending state; nothing lost |
| Audit | Organization creation, plan selection, POS-situation answer |
| Outcome | A tenant ready for setup; checklist visible on the dashboard |

## WF-02 Initial inventory setup

Steps:
1. Choose item source: CSV import, quick-add grid, starter template (R1.1), or (C) POS catalog for
   resale goods.
2. Set base dimension and display/purchase units per item; add packages (case = 6 × 1 gal) and
   item-specific conversions (1 lb carrots → 340 ml juice).
3. Set storage areas, min/max levels, default shelf life, batch tracking for perishables.
4. Opening count (WF-09 in "opening" mode) → posts opening balances, optionally with batches and
   expiry dates.

| Field | Detail |
|---|---|
| Trigger | Onboarding, or adding a new product line |
| Actor | Owner, Administrator, Manager, Inventory manager |
| Preconditions | Location exists |
| Inputs | Item list, units, conversions, levels, opening quantities |
| System behavior | Validates units and conversions; previews imports with row errors; opening count snapshot |
| Ledger effects | One InventoryTransaction `opening_balance` per count session; one entry per counted item (+ batch) |
| Records created | InventoryItem, PackageUnit, ItemUnitConversion, StorageLocation, StockCount (opening), InventoryBatch (if dated), InventoryTransaction |
| Notifications | None |
| Exceptions | Unconvertible unit → row rejected with the missing conversion named |
| Failure handling | Import is all-or-nothing per confirmed batch of rows; failed rows listed for fix |
| Audit | Imports (file hash, rows), opening count |
| Outcome | Reliable starting balances; reliable variance reporting starts from the opening timestamp |

## WF-03 Ingredient and product creation

Steps:
1. **Inventory item**: name, type (raw ingredient · prepared item · finished good · packaging ·
   supply · resale good), category, units, supplier, levels, shelf life.
2. **Sellable product**: name, category, production mode (made-to-order / made-ahead),
   variations (12 oz, 16 oz) with informational price, modifiers (ice level = scale; ginger add-in
   = add; oat milk = substitute).
3. Connected: products can be created from the POS catalog during mapping (WF-28).

| Field | Detail |
|---|---|
| Trigger | Setup or menu change |
| Actor | Owner, Administrator, Manager, Inventory manager |
| Preconditions | Units configured |
| Inputs | Item/product attributes |
| System behavior | Enforces unique names per organization; base unit immutable after first movement |
| Ledger effects | None |
| Records created | InventoryItem / SellableProduct, ProductVariation, Modifier, AuditEvent |
| Notifications | None |
| Exceptions | Duplicate name → suggest existing |
| Failure handling | Validation errors inline |
| Audit | Creation and every edit (before/after) |
| Outcome | Catalog ready for recipes, receiving and sales |

## WF-04 Supplier creation

Steps:
1. Name, contact, ordering method, delivery days, lead time, minimum order.
2. Supplier items: supplier code/name, purchase unit and pack size, last price, preferred flag.

| Field | Detail |
|---|---|
| Trigger | New supplier, or first receipt from one |
| Actor | Owner, Administrator, Manager, Purchasing staff |
| Preconditions | Items exist (supplier items can be added later) |
| Inputs | Supplier details, supplier item mappings |
| System behavior | Supplier items convert purchase units to base units |
| Ledger effects | None |
| Records created | Supplier, SupplierItem, AuditEvent |
| Notifications | None |
| Exceptions | Duplicate supplier name → suggest existing |
| Failure handling | Inline validation |
| Audit | Create/edit/deactivate |
| Outcome | Receiving, POs and reorder suggestions can use the supplier |

## WF-05 Recipe creation

Steps:
1. Choose the owner: product variation (Golden State 16 oz), modifier effect (ginger add-in), or
   production recipe (ginger juice).
2. Add lines: item, quantity, unit (any convertible unit); optional packaging lines (cup, lid).
3. Yield/portion (production: 1 lb ginger → 225 ml; product: 1 serving); shelf life for production.
4. "Start from" another size, scaled (12 → 16 oz), then adjust.
5. Save → a new RecipeVersion effective now (or a chosen future time); recipe cost and estimated
   margin shown with missing-cost lines.

| Field | Detail |
|---|---|
| Trigger | New product, recipe change, or `unmapped_product` exception |
| Actor | Owner, Administrator, Manager, Inventory manager, Production staff (production recipes only) |
| Preconditions | Items and units exist |
| Inputs | Lines, yield, instructions, shelf life |
| System behavior | Rejects unconvertible units and prepared-item cycles; never alters history (new version) |
| Ledger effects | None |
| Records created | Recipe, RecipeVersion, RecipeLine, AuditEvent |
| Notifications | Price/margin impact shown in-app |
| Exceptions | Missing conversion → prompt to add it; missing cost → cost shown as incomplete |
| Failure handling | Draft saved; not effective until valid |
| Audit | Version created with diff |
| Outcome | Sales and production consume ingredients correctly from the effective time |

## WF-06 Purchase order creation (R1.1)

Steps:
1. From reorder suggestions (WF-21) or manually: supplier, lines (supplier item, quantity, expected
   price), expected delivery date.
2. Review; submit → email/PDF to supplier or mark "ordered by phone/portal".
3. Later receivings update received quantities → `partially_received` / `received`; close
   (short-close with reason) or cancel (only before any receipt).

```
draft → submitted → partially_received → received → closed
   └──────► cancelled (from draft/submitted, no receipts)
```

| Field | Detail |
|---|---|
| Trigger | Reorder need |
| Actor | Owner, Administrator, Manager, Purchasing staff |
| Preconditions | Supplier and supplier items exist |
| Inputs | Lines, quantities, dates |
| System behavior | Converts to base units; computes on-order quantity (submitted − received) for reorder logic |
| Ledger effects | **None** — POs never change inventory |
| Records created | PurchaseOrder, PurchaseOrderLine, AuditEvent |
| Notifications | Email to supplier; overdue PO alert past expected date |
| Exceptions | Email fails → PO stays draft with error |
| Failure handling | Retry send; never shown as submitted unless sent or marked |
| Audit | Every status change with actor |
| Outcome | Purchasing visible; on-order quantity prevents double ordering |

## WF-07 Receiving inventory

Steps:
1. Start receiving: supplier; optionally an open PO (R1.1) and/or invoice (WF-08).
2. Lines: item, **ordered** (PO), **invoiced**, **arrived**, **accepted** quantities; unit cost;
   expiry date and lot for batch-tracked items.
3. Record discrepancies: short, over, damaged, wrong item, price variance, not invoiced,
   invoiced-not-delivered.
4. Post.

Example: invoice says 20 lb carrots; 20 lb arrive; 2 lb rotten → accepted 18 lb, discrepancy
"damaged 2 lb", supplier credit expected.

| Field | Detail |
|---|---|
| Trigger | Delivery arrives |
| Actor | Employee, Inventory manager, Purchasing staff, Manager+ (cost overrides and discrepancy resolution) |
| Preconditions | Supplier exists (or created inline); items exist (or created inline) |
| Inputs | Lines with four quantities, costs, expiry, lot |
| System behavior | Enforces 0 ≤ accepted ≤ arrived; flags price variance beyond threshold; conditional `draft → posted` so a receiving posts once; updates current cost and supplier last price; shows affected recipes on price change |
| Ledger effects | One InventoryTransaction `receipt`; one `receipt` entry per accepted line (+ new batch); key `receiving:<id>` |
| Records created | Receiving, ReceivingLine, ReceivingDiscrepancy, InventoryBatch, InventoryTransaction, ItemCostHistory |
| Notifications | Price-change notice; discrepancy summary to Manager+ |
| Exceptions | `receiving_discrepancy` (unresolved), `price_variance` |
| Failure handling | Post fails → full rollback; second concurrent post → "already posted" |
| Audit | Poster, time, every quantity and cost |
| Outcome | Stock and costs reflect what was accepted; discrepancies tracked to resolution |

## WF-08 Invoice recording

Steps:
1. Upload photo/PDF (from phone camera or file); content hash dedupes.
2. Lines entered manually, or proposed by assisted extraction (enhancement, capped per tenant).
3. Match lines to items via learned supplier-item mappings; unmatched → match, create, or mark
   non-inventory (e.g. delivery fee).
4. Link to a receiving (new or existing); invoiced quantities and prices pre-fill it.

| Field | Detail |
|---|---|
| Trigger | Invoice received with or after a delivery |
| Actor | Employee, Inventory manager, Purchasing staff, Manager+ |
| Preconditions | Email verified (extraction); supplier exists |
| Inputs | File, invoice number, date, totals, lines |
| System behavior | Stores file privately; extraction runs off the request path; **upload never changes stock or cost until the linked receiving is posted** [CONFIRMED] |
| Ledger effects | None (receiving posts) |
| Records created | Invoice, InvoiceLine, learned SupplierItem mappings |
| Notifications | "Extraction ready for review" |
| Exceptions | Duplicate file → link to existing; extraction failed → manual entry; invoice total ≠ sum of lines → warning |
| Failure handling | Extraction timeout or cap reached → manual entry, never blocked |
| Audit | Uploader, extraction corrections (for quality measurement) |
| Outcome | Invoice is the record of cost; receiving reconciled against it |

## WF-09 Inventory count

Steps:
1. Start a count session: full, category, storage area, single item, or batch-level; optional blind
   mode (R1.1).
2. Count in any unit, including packages + loose ("3 cases + 4 each"); each line timestamped.
3. System stores expected = recorded balance at each line's timestamp; computes variance (qty and
   cost).
4. Reasons for variances (optional for Employees; required for approving above-threshold lines).
5. Post.

| Field | Detail |
|---|---|
| Trigger | Scheduled count (weekly), spot check, `negative_balance` exception |
| Actor | Employee, Inventory manager, Manager+ (review) |
| Preconditions | Items exist |
| Inputs | Counted quantities, units, reasons |
| System behavior | Counts are observations; variances become movements; warns if the location is Standalone and today's sales are not yet entered |
| Ledger effects | One InventoryTransaction `count`; one `count_variance` entry per non-zero line; key `count:<id>` |
| Records created | StockCount, StockCountLine, InventoryTransaction |
| Notifications | Above-threshold variance → review exception to Manager+ |
| Exceptions | `count_variance_high` |
| Failure handling | Session saved as draft on connection loss; resume |
| Audit | Counter per line, poster, reviewer |
| Outcome | Recorded stock matches physical reality; variance recorded and explained |

## WF-10 Inventory adjustment

Steps:
1. Choose item, quantity (+/−), reason (found, correction, sample, internal use, returned to
   supplier, other), note.
2. Post; above-threshold adjustments create an Owner review exception (R1) or need approval
   (R1.1 option).

| Field | Detail |
|---|---|
| Trigger | Correction not covered by receiving, waste, production, sales or counts |
| Actor | Manager+, Inventory manager |
| Preconditions | Item exists |
| Inputs | Quantity, reason, note |
| System behavior | Reason mandatory; cannot cross zero without override permission |
| Ledger effects | InventoryTransaction `adjustment`; entries with reason; key `adjustment:<id>` |
| Records created | InventoryAdjustment, InventoryTransaction |
| Notifications | Review exception if ≥ threshold |
| Exceptions | Negative crossing without permission → refused |
| Failure handling | Atomic post |
| Audit | Actor, reason, approver |
| Outcome | Deliberate, attributed correction; never an untraceable edit |

## WF-11 Batch production

Steps:
1. Choose prepared item (ginger juice) and multiplier (×2); see required inputs (2 lb ginger),
   on-hand and expected yield (450 ml). Optionally plan first (production order `planned`, R2).
2. Confirm; enter actual yield (default expected: 450 ml; actual 400 ml).
3. Post; screen shows the use-by date/time to label the container.
4. Undo within 30 minutes by the recorder (reversal).

| Field | Detail |
|---|---|
| Trigger | Prep list or stock of prepared item low |
| Actor | Production staff, Employee, Manager+ |
| Preconditions | Production recipe exists |
| Inputs | Prepared item, multiplier, actual yield, notes |
| System behavior | Allocates inputs FEFO from batches; output cost = Σ input cost ÷ actual yield (unknown if any input unpriced); yield variance recorded; shortfall of inputs warns (PD-15) |
| Ledger effects | One InventoryTransaction `production`: `production_input` entries (−) per input batch and one `production_output` entry (+) creating a batch; key `production_order:<id>` |
| Records created | ProductionOrder (status `completed`), InventoryBatch (output), InventoryTransaction |
| Notifications | Low input stock alerts after posting |
| Exceptions | `negative_balance` if inputs short (when allowed); `missing_cost` |
| Failure handling | Atomic; undo = reversal transaction |
| Audit | Producer, actual yield, variance |
| Outcome | Raw stock reduced once; prepared stock with expiry created; traceable from output batch to input batches |

## WF-12 Ingredient consumption (shared engine)

Every sales source (WF-15, WF-16, future native POS) and waste of a product (WF-18) use one engine:

1. Input: sales transaction line (product variation, modifiers, quantity, occurred_at or business date).
2. Resolve the RecipeVersion effective at occurred_at.
3. Apply modifiers: scale (Light ice ×1.15 of the drink lines), add (ginger add-in lines),
   remove, substitute.
4. Production mode: made-ahead → consume the prepared/finished item; made-to-order → raw lines
   (never both, DR-18).
5. Convert to base units (yield/press conversions); unconvertible → gap.
6. Allocate batches (FEFO or FIFO), marked **inferred**.
7. Post `sale_consumption` entries; record gaps on the line.

| Field | Detail |
|---|---|
| Trigger | A sales line is recorded or reprocessed |
| Actor | System |
| Preconditions | Product mapped (C) or chosen (S); recipe exists |
| Inputs | Sales line |
| System behavior | Deterministic and idempotent per sales line revision; never blocks or drops a sale |
| Ledger effects | InventoryTransaction `sale` per sales transaction; `sale_consumption` entries; key `sale:<transaction_id>:r<revision>` |
| Records created | InventoryTransaction; consumption status on SalesTransactionLine |
| Notifications | Low-stock evaluation after posting |
| Exceptions | `unmapped_product`, `conversion_missing`, `negative_balance`, `batch_shortfall` |
| Failure handling | Failure leaves line status `pending`; retried by background job |
| Audit | Recipe version used per line |
| Outcome | **Theoretical consumption** recorded per sale, traceable to recipe version |

## WF-13 Finished-goods creation

Finished goods are items sold as units: bottled cold-pressed juice for the grab-and-go fridge,
house granola. They come from production (made in-house) or receiving (resale goods).

Steps:
1. Item type `finished_good`, sold by the unit, with shelf life.
2. Production order (WF-11) outputs N units (e.g. 24 × 16 oz bottles) as a batch with expiry.
3. The sellable product is made-ahead with a recipe of "1 × finished good" (+ optional label/cap).
4. Sales consume finished-good units FEFO; unsold expired units go through WF-14.

| Field | Detail |
|---|---|
| Trigger | Bottling/packing run |
| Actor | Production staff, Manager+ |
| Preconditions | Finished-good item and production recipe exist |
| Inputs | Units produced, actual yield |
| System behavior | As WF-11; output in units |
| Ledger effects | As WF-11 (`production_input` / `production_output` in units) |
| Records created | ProductionOrder, InventoryBatch |
| Notifications | Expiry alerts for finished-good batches |
| Exceptions | Yield variance high → info |
| Failure handling | As WF-11 |
| Audit | As WF-11 |
| Outcome | Sellable stock with traceable batches; no double counting with raw inputs |

## WF-14 Expiration tracking

```
batch created (receipt / production / opening) → daily evaluation at location day start →
alert window reached → notification → decision: use first | mark down (R2) | dispose | adjust →
event posted → batch resolved
```

Steps:
1. Batch gets expiry from entry or default shelf life (date; date-time for short-life prepared
   items, PD-40).
2. Daily job: batches entering the alert window → expiry alert; past expiry with remaining →
   `expired` + exception.
3. Manager decides from the expiry queue: **use first** (flag), **mark down** (R2, resale goods),
   **dispose** (WF-18, reason expired, batch explicit), **adjust** (batch count).
4. Optional per-business policy: auto-dispose expired prepared batches as waste (actor system) after
   pending sales consumption is posted; default off.

| Field | Detail |
|---|---|
| Trigger | Daily schedule; batch creation |
| Actor | System; Manager+, Inventory manager, Employee (dispose) |
| Preconditions | Batch-tracked items with expiry |
| Inputs | Expiry dates, alert window settings |
| System behavior | Expired batches never disappear; remaining stays until accounted for (DR-14) |
| Ledger effects | None for alerts; disposal posts `waste`; adjustment posts `count_variance`/`adjustment` |
| Records created | Alert, Exception, WasteEvent (on disposal) |
| Notifications | In-app alert bar; daily email digest |
| Exceptions | `batch_expired_unresolved` |
| Failure handling | Missed job run → next run catches up; job health monitored |
| Audit | Who decided what, or "system policy" |
| Outcome | Expiry losses visible, minimized and explicitly accounted |

## WF-15 Sales ingestion from an external POS (Connected)

Steps:
1. Webhook (hint) or scheduled reconciliation (guarantee) → fetch order via adapter.
2. Normalize to SalesTransaction + lines (variation, modifiers, quantity, completion time, location,
   line identity); store adapter staging record.
3. Only completed orders consume; discounts and comps do **not** change consumption (the drink was
   made); open/cancelled orders store with no consumption.
4. Map lines (WF-28); unmapped → stored with gap.
5. WF-12 consumption.
6. Order edited after completion → reversal + re-post of changed lines (new revision).

| Field | Detail |
|---|---|
| Trigger | POS order completed |
| Actor | System |
| Preconditions | Connection active; location mapped |
| Inputs | POS order payload |
| System behavior | Webhook signature verified; event id dedupe; line-identity dedupe; acknowledge fast, process in background |
| Ledger effects | Via WF-12 |
| Records created | SyncEvent, ExternalOrder (staging), SalesTransaction, SalesTransactionLine, InventoryTransaction |
| Notifications | Sync failure alerts; daily POS reconciliation summary |
| Exceptions | `unmapped_product`, `sync_failure`, `pos_reconciliation_mismatch` |
| Failure handling | Retries with back-off; cursor-based catch-up; daily count comparison |
| Audit | Raw event id, processing time, revision history |
| Outcome | Theoretical consumption within minutes of each sale, exactly once |

## WF-16 Manual consumption recording (Standalone)

Three record types, always labelled by origin (manual / imported) [CONFIRMED]:

| Record | When | Effect |
|---|---|---|
| **Daily sales entry** | At close: quantity sold per product variation (modifier counts optional) | WF-12 consumption for that business date |
| **Sales file import** | Upload CSV/XLSX from a card terminal, delivery platform or spreadsheet | One SalesTransaction per date; WF-12 |
| **Direct usage record** | Ingredient used without a recorded sale (staff meals, samples, catering by weight) | `manual_consumption` entries on the ingredient, reason required |

Steps (daily entry): choose business date → grid of active variations, most-sold first → enter
quantities → save → consumption posted with occurred_at = end of business day.
Steps (import): upload → choose/save column mapping → learned product matching → preview
(unmatched rows, days already entered) → confirm.

| Field | Detail |
|---|---|
| Trigger | End of day, or catching up past days |
| Actor | Owner, Administrator, Manager (Employees if allowed, PD-37) |
| Preconditions | Location in Standalone mode for that date (DR-31) |
| Inputs | Quantities per variation, or a file |
| System behavior | Re-saving a day = correction (reversal + re-post, new revision); re-importing an identical file does nothing; dates owned by another sales source refused; open days without sales flagged |
| Ledger effects | `sale_consumption` via WF-12 (key `sale:<transaction_id>:r<revision>`); `manual_consumption` entries for direct usage (key `usage:<id>`) |
| Records created | SalesSource (manual/file_import), SalesTransaction, SalesTransactionLine, ImportFile, InventoryTransaction |
| Notifications | Morning reminder when yesterday has no sales entered (optional) |
| Exceptions | `sales_missing`, `sales_source_overlap`, `unmapped_product` (import names) |
| Failure handling | Import preview before any posting; partial files rejected per row, not silently |
| Audit | Who entered/imported, file hash, revisions |
| Outcome | Theoretical consumption at daily granularity, clearly labelled as manual or imported; with no sales at all, usage still measured from counts (WF-19) |

## WF-17 Refund and void handling

Steps:
1. A refund or void arrives from the POS (C), or an operator corrects entered sales (S → WF-26).
2. The system deduplicates by provider refund/void id and links it to the original
   SalesTransaction; an unknown original is stored as an orphan and matched later.
3. It applies the case rule below: voids of completed orders reverse that order's consumption;
   refunds record a SalesReturn and reduce net sales but never restore stock on their own.
4. A Manager+ may classify a refunded item as **Restock** or **Waste** (R1.1); only Restock posts a
   reversal.
5. Daily reports show refunds, voids and any classifications.

| Case | Behaviour | Stock |
|---|---|---|
| Cancelled/voided before completion | Stored; no consumption | No change |
| Completed order voided | Reversal of its consumption [ASSUMED A-07: void = not made] | Restored |
| Full or partial refund | SalesReturn recorded and linked; net sales reduced | **No automatic restore** (DR-26) |
| Operator classifies a refunded item (R1.1) | **Restock** (unopened bottled juice) → reversal of that line's consumption; **Waste** (drink already made) → refund-related waste category for reporting, no extra stock change | As chosen |
| Remade drink after a complaint | Logged as waste, reason `remake` (WF-18) | Remake ingredients out |
| Standalone correction of entered sales | WF-26 | Reversal + re-post |

| Field | Detail |
|---|---|
| Trigger | POS refund/void event; operator action |
| Actor | System; Manager+ (classification) |
| Preconditions | Original transaction known (else stored as orphan, matched later) |
| Inputs | Refund/void payload or classification |
| System behavior | Idempotent by refund id; never restocks without an explicit decision |
| Ledger effects | None for refunds; `reversal` for voids and restock classifications |
| Records created | SalesReturn, InventoryTransaction (reversals) |
| Notifications | Refund summary in daily report |
| Exceptions | Orphan refund (original not found) → info exception |
| Failure handling | Retry; reconciliation sync |
| Audit | Classification actor and reason |
| Outcome | Sales figures net of refunds; stock only changes when reality changed |

## WF-18 Waste recording

Steps:
1. Choose item (raw, prepared, finished) or sellable product (expands to its recipe version).
2. Quantity + unit; reason: spoilage, expiration, preparation waste, production loss, damaged,
   remake, customer return, refund-related, quality-control disposal, spilled, overproduction, other
   (configurable list).
3. Optional batch (explicit), related order/production order, note, photo.
4. Save; undo within 30 minutes by the logger.

| Field | Detail |
|---|---|
| Trigger | Something is thrown out, spilled, remade or rejected |
| Actor | Any role with `waste.record` |
| Preconditions | Item or product exists |
| Inputs | Item/product, quantity, unit, reason, batch, related record |
| System behavior | Reason mandatory; cost snapshot per line; Employees cannot cross zero (count first) |
| Ledger effects | One InventoryTransaction `waste`; one `waste` entry per item line; key `waste:<id>`. The WasteEvent holds the why; entries hold the quantity — reports read entries (DR-33) |
| Records created | WasteEvent, WasteLine, InventoryTransaction |
| Notifications | Large waste (≥ threshold value) → Manager+ info |
| Exceptions | `missing_cost` |
| Failure handling | Atomic; offline entry queued on device and posted with original timestamp (R1.1) |
| Audit | Logger, time, reason, voids |
| Outcome | Loss visible by what, how much, why, who, cost |

## WF-19 Inventory reconciliation

```
expected (theoretical) → physical count → variance → investigation → review/approval →
adjustment posted → updated stock → actual usage computed
```

Steps:
1. After a count (WF-09), the reconciliation view lists variances by value.
2. For each large variance, the system shows likely causes: unrecorded waste, unmapped sales, recipe
   changes, receiving discrepancies, conversion gaps, days without sales entered (S).
3. Reviewer sets reason, approves; resolves linked exceptions.
4. **Actual usage** for the interval = opening count + receipts + production output − production
   input − waste ± adjustments − closing count; compared with theoretical consumption where sales
   data exists.

| Field | Detail |
|---|---|
| Trigger | Count posted |
| Actor | Manager+, Inventory manager |
| Preconditions | At least one count (two for actual usage) |
| Inputs | Variance reasons, approvals |
| System behavior | Never edits a balance directly; separates theoretical consumption, actual usage and recorded adjustments |
| Ledger effects | Variances already posted by WF-09; approvals change status only |
| Records created | Reconciliation fields on StockCount/lines, ExceptionResolution |
| Notifications | Weekly variance summary |
| Exceptions | Recurring variance on the same item → info |
| Failure handling | — |
| Audit | Reviewer, reasons |
| Outcome | Discrepancies explained and trending down; Standalone businesses get actual usage even without sales |

## WF-20 Low-stock alerts

Steps:
1. After every posting, evaluate the item: recorded ≤ min level → low; ≤ 0 → out.
2. Create/refresh Alert (dedup by item); clear when restocked.
3. Deliver: in-app alert bar immediately; email digest at the configured time.

| Field | Detail |
|---|---|
| Trigger | Any posting; daily digest schedule |
| Actor | System; recipients configured |
| Preconditions | Min level set |
| Inputs | Balances, levels |
| System behavior | No duplicate alerts; acknowledged alert re-raises next day if still low |
| Ledger effects | None |
| Records created | Alert |
| Notifications | In-app; email digest |
| Exceptions | Stale or negative stock → "count first" alert instead |
| Failure handling | Email failure retried; suppression list respected |
| Audit | Acknowledgements |
| Outcome | Nobody discovers a stockout mid-service |

## WF-21 Reorder recommendations

Steps:
1. For items below reorder point or projected to cross it within lead time + cover days: average
   daily use (sales consumption, or count-based usage in Standalone when sales are missing), lead
   time, cover days, safety %, on hand, on order (R1.1).
2. Suggested quantity = max(0, avg daily use × (lead time + cover) × (1 + safety) + min level − on
   hand − on order), rounded up to the supplier's purchase unit; formula shown.
3. Owner edits → CSV by supplier (R1) or draft POs (R1.1).

| Field | Detail |
|---|---|
| Trigger | Reorder view, weekly email |
| Actor | Owner, Manager, Purchasing staff |
| Preconditions | Usage history ≥ 7 days or count-based usage |
| Inputs | Edits to suggested quantities |
| System behavior | Never orders automatically [CONFIRMED]; negative/stale stock → "count first" |
| Ledger effects | None |
| Records created | CSV export or PurchaseOrder drafts |
| Notifications | Weekly reorder digest |
| Exceptions | No supplier on item → grouped under "no supplier" |
| Failure handling | — |
| Audit | Generated POs reference the suggestion inputs |
| Outcome | Explainable, proactive purchasing |

## WF-22 Inter-location transfers (Expansion)

Steps:
1. Sending location creates transfer: items, quantities, batches (e.g. prepared juice from the main
   kitchen to store 2).
2. Send → `transfer_out` at sender; transfer `in_transit`.
3. Receiving location confirms received quantities → `transfer_in`; differences recorded as transfer
   discrepancy (waste or variance at the responsible location).

| Field | Detail |
|---|---|
| Trigger | Store needs stock from another store |
| Actor | Managers of both locations, Inventory manager |
| Preconditions | Multi-location entitlement; user has access to both locations or each side acts separately |
| Inputs | Items, quantities, batches, received quantities |
| System behavior | Batches and expiry travel with the stock; cost snapshot carried |
| Ledger effects | `transfer_out` (−) at sender on send; `transfer_in` (+) at receiver on confirm; key `transfer:<id>:out` / `:in` |
| Records created | Transfer, TransferLine, InventoryTransaction ×2 |
| Notifications | Receiver notified on send; overdue in-transit alert |
| Exceptions | Received ≠ sent → transfer discrepancy |
| Failure handling | In-transit stock visible, never lost |
| Audit | Sender, receiver, quantities |
| Outcome | Stock moves between stores with full traceability |

## WF-23 Employee operations (daily routine)

| Time | Task | Workflow | Device |
|---|---|---|---|
| Opening | Check alert bar (expiring today, low stock) | WF-14, WF-20 | Shared tablet |
| Morning | Receive produce delivery from invoice photo | WF-07, WF-08 | Phone |
| Prep | Record ginger-juice batch; label use-by | WF-11 | Tablet |
| Service | Log spills and remakes | WF-18 | Phone/tablet |
| Close | Dispose expired batches; enter day's sales (S); spot count | WF-14, WF-16, WF-09 | Tablet |

Requirements: each task reachable in ≤ 2 taps from the employee home; no cost data shown to
Employees; "logged by" person picker on shared devices (PD-14); works on a 375 px screen; large
touch targets.

Steps:
1. The employee opens the employee home on a phone or the shared tablet and, on a shared device,
   picks who they are (PD-14).
2. The home shows only the tasks their role and location allow, with today's alerts on top.
3. They perform each task through its own workflow (table above); each task saves independently,
   so an interrupted shift loses nothing already saved.
4. At close they dispose of expired batches, enter the day's sales (S, if permitted) and do any
   spot count due.

| Field | Detail |
|---|---|
| Trigger | Start of shift; an alert; a delivery arrives; end of day |
| Actor | Employee (also Production staff, Inventory manager or Manager doing floor tasks) |
| Preconditions | Active membership with location access; location set up; for daily sales entry (S), the `sales.record` permission |
| Inputs | Task-specific: invoice photo and quantities, batch multiplier and yield, waste item/quantity/reason, counts, daily sales |
| System behavior | Filters tasks by role and location; hides costs from Employees; records the acting person on every record; each task posts through its own workflow's ledger rules |
| Ledger effects | None of its own; the ledger effects are those of WF-07, WF-09, WF-11, WF-14, WF-16 and WF-18 |
| Records created | The records of the underlying workflows, each with actor and device context |
| Notifications | Alert bar on the employee home; manager notified of large waste or variances per thresholds (WF-18, WF-19) |
| Exceptions | Task not permitted → message naming who can do it; would go negative → "count first"; offline or network error → nothing posts and the employee is told to retry (offline queueing is R1.1) |
| Failure handling | Failed saves are shown, never silently dropped; undo window on waste and production |
| Audit | Actor and, on shared devices, the selected person on every record |
| Outcome | The day's operational events recorded by the people who did them, without admin screens |

## WF-24 Reporting and review

Steps (weekly owner review, ~15 minutes):
1. Weekly email: stock value, waste cost by reason, expiry loss, open exceptions, reorder list,
   sales-data coverage (S).
2. Open dashboard → resolve exceptions → review variance from the latest count → check recipe
   margins affected by price changes.
3. Export CSVs for the accountant if needed.

| Field | Detail |
|---|---|
| Trigger | Weekly schedule; owner opens reports |
| Actor | Owner, Administrator, Manager |
| Preconditions | Data exists; completeness disclosed |
| Inputs | Date range, location |
| System behavior | Uses snapshot costs for history; labels estimates; states coverage |
| Ledger effects | None |
| Records created | None (exports logged) |
| Notifications | Weekly summary email |
| Exceptions | — |
| Failure handling | Long reports run as background exports |
| Audit | Exports logged |
| Outcome | Owner decides from trustworthy numbers in minutes |

## WF-25 Operating mode change

| Change | Steps | Effect |
|---|---|---|
| Standalone → Connected | Connect POS, map location, choose cutover business date D | POS sales dated ≥ D consume; manual/import entries ≥ D refused; earlier days keep manual sales; POS catalog mapped to existing products |
| Connected → Standalone | Disconnect POS or change source of record; D = day after the last fully ingested POS day | Manual/import entry enabled from D; history retained |
| Plan follows mode | Standalone ↔ Complete plan change at the cutover (PD-39) | No data migration — one engine |

One sales source of record per location per business date (DR-31). A past cutover first reverses
overlapping manual days, with preview. Audit: actor, D, plan change.

## WF-26 Correction of a posted record

1. User with permission opens a posted record (receiving, waste, production, count, adjustment,
   daily sales) → Correct.
2. Edits corrected values; gives a reason.
3. One InventoryTransaction: `reversal` entries negating the originals (referencing them) + new
   entries; original marked `corrected`, both visible.

Within 30 minutes the recorder may void (reversal only) without Manager approval. No posted entry
is ever edited or deleted (DR-02).

## WF-27 Exception lifecycle

```
condition detected → open (dedup by type + subject) → acknowledged → resolved by an action |
dismissed with reason | auto-resolved when the condition clears
```

Severity: **critical** (`balance_drift`, `sync_failure` > 24 h), **warning** (negative balance, high
variance, expired unresolved, unmapped product, sales missing), **info** (price change). Catalogue in
[DOMAIN_MODEL §6](DOMAIN_MODEL.md#6-exceptions). Resolving never edits a quantity directly.

## WF-28 Product mapping and reprocessing

1. Unmapped POS variation/modifier (C) or unmatched import name (S) → `unmapped_product` with sales
   count.
2. Map to a product variation / modifier effect (create inline if needed).
3. Optionally reprocess a period (default: since the last count of affected ingredients, max 30
   days, PD-17) with a preview of consumption to be posted.
4. Post via WF-12 with original occurred_at; idempotent with the same keys.

Audit: mapper, time, reprocessed range. Reprocessing changes past expected figures, so the variance
report recomputes and says so.
