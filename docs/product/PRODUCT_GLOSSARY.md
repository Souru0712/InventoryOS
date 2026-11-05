# Product Glossary

Authoritative source for **terminology**. Use these terms in UI copy, code, documentation and
conversations. "UI label" is what customers see where it differs from the domain term.

## Entities and projects

| Term | Definition |
|---|---|
| **InventoryOS** | The commercial SaaS product defined in this package. Built on the InventoryOS codebase (C-25); no InventoryOS release yet |
| **InventoryOS codebase (formerly StockPulse)** | The Python/FastAPI codebase in the `InventoryOS` repository (formerly `POS-Inventory-Automation`); InventoryOS's chosen foundation (C-25). "StockPulse" is the retired product name (C-24) |
| **JuJu / JuJu Juice** | A separate juice-bar business and its own customer + staff platform (`JuJu` repository) |
| **Snack-box project** | The owner's separate side project: curated and mystery boxes of Japanese snacks for the owner's convenience store. Its code sits inside the InventoryOS codebase today and moves to its own project (C-37). Not InventoryOS |
| **Organization** | A customer business; the tenant. UI: "business" |
| **Business location** | A physical site of an organization. UI: "location" |
| **Storage location** | An area inside a business location (walk-in, dry store, line fridge). UI: "storage area" |
| **Member** | A user's access to one organization, with a role |

## Items and units

| Term | Definition |
|---|---|
| **Inventory item** | Anything whose quantity InventoryOS tracks. Types: raw ingredient/material, prepared item, finished good, packaging, supply, resale good. UI: "item" |
| **Finished good** | An item made in-house and sold as a unit (bottled juice, packaged granola); produced like a prepared item, sold through a "1 ×" recipe |
| **Supply** | A consumable that is not an ingredient or packaging (cleaning supplies, gloves) |
| **Ingredient** | An inventory item used in a recipe (raw or prepared). Informal synonym for item in food contexts |
| **Prepared item** | An inventory item made in-house from other items via production (ginger juice, simple syrup, prepped fruit) |
| **Sellable product** | Something the business sells (a drink, a sandwich). Not an inventory item unless it is a resale good. UI: "product" |
| **Product variation** | A sellable variant, usually a size (12 oz, 16 oz). The grain that recipes and POS mappings attach to |
| **Modifier** | A sale-time option on a product (add-in, ice level, milk swap). Has an inventory effect: add, remove, substitute, scale, or none |
| **Base unit** | The unit an item's quantity is stored in: g (weight), ml (volume), or each (count) |
| **Display unit** | The unit an item is shown and counted in (lb, gal, case) |
| **Purchase unit** | The unit a supplier sells in (case of 6 × 1 gal) |
| **Package** | A business-defined unit equal to N of another unit (case, bag, bottle) |
| **Conversion** | A rule turning one unit into another. Same-dimension conversions are fixed constants; cross-dimension conversions (weight ↔ volume) exist only per item and only when entered |
| **Press yield** | A cross-dimension conversion for produce: volume of juice per weight of fruit (JuJu: mL per lb) |

## Stock and ledger

| Term | Definition |
|---|---|
| **Inventory transaction** | One posted business event (a receiving, a waste event, a day's sales…) grouping its ledger entries atomically under one idempotency key |
| **Movement / ledger entry** | One append-only record of a quantity change for one item at one location (and optionally one batch), inside an inventory transaction. UI: "stock change" / "history" |
| **Ledger** | The full set of movements; the source of truth for quantities |
| **Recorded stock** | Sum of all posted movements for an item at a location. UI: "on hand" |
| **Expected stock** | Recorded stock as of a specific instant, used as the comparison basis for a count |
| **Counted stock** | Quantity physically observed during a count |
| **Variance** | Counted − expected. Negative = shortage ("found short"), positive = surplus |
| **Available stock** | Recorded stock minus quantity in expired or on-hold batches |
| **Reserved stock** | Not used in InventoryOS (no reservation workflow) |
| **Adjustment** | A manual movement with a reason, not tied to receiving, waste, production, sales or counts |
| **Reversal** | A movement that exactly negates an earlier movement, used to correct history without editing it |
| **Opening balance** | The movement that sets an item's starting quantity at onboarding |
| **Idempotency key** | A unique identifier for a business event so that processing it twice has the effect of processing it once |
| **Cost snapshot** | The unit cost stored on a movement at the time it was posted |
| **Current unit cost** | An item's cost now (latest accepted receipt unless overridden) |
| **Cost basis** | The cost used in a calculation; defined per metric in [PRODUCT_SPECIFICATION §Metric definitions](PRODUCT_SPECIFICATION.md#metric-definitions) |

## Batches and expiration

| Term | Definition |
|---|---|
| **Batch** | A quantity of one item that shares an origin and expiry: one receiving line, one production order, or one manual entry. UI: "batch" (avoid "lot" except for supplier lot codes) |
| **Lot code** | The supplier's identifier printed on goods, stored on the batch |
| **Remaining (batch)** | Batch quantity not yet consumed, wasted or adjusted; derived from movements |
| **FIFO** | First in, first out: consume the earliest-received batch first |
| **FEFO** | First expired, first out: consume the earliest-expiring batch first |
| **Inferred allocation** | The system's assumption of which batch a consumption came from (POS sales) |
| **Explicit allocation** | A person chose the batch (waste of a specific container) |
| **Expiry date / use-by / best-before** | The date on a batch; the date type is recorded |
| **Shelf life** | Default number of days from receipt/production to expiry for an item |
| **Expired (unresolved)** | Past expiry with remaining quantity not yet accounted for |
| **Disposal** | Waste of an expired or unusable batch |

## Workflows

| Term | Definition |
|---|---|
| **Receiving** | Recording goods delivered by a supplier and posting accepted quantities |
| **Ordered / invoiced / arrived / accepted** | The four receiving quantities: on the purchase order, on the supplier's invoice, physically delivered, and taken into inventory |
| **Discrepancy** | A difference between any two of the four receiving quantities, or between invoiced and expected price |
| **Purchase order (PO)** | A request to a supplier; never changes inventory by itself |
| **Consumption** | Stock leaving through use. *Sales consumption* is computed from sales via recipes; *production consumption* is inputs used in production |
| **Expected / theoretical consumption** | Consumption computed by InventoryOS from recorded sales × recipe versions |
| **Actual usage** | Usage implied by counts over an interval: opening count + receipts + production output − production input − waste ± adjustments − closing count |
| **Recorded adjustment** | A deliberate correction (adjustment, count variance, reversal) — neither consumption nor waste |
| **Manual consumption / direct usage** | Ingredient usage recorded without a sale (staff meals, samples), Standalone |
| **Production / production order** | Turning inputs into a prepared item. UI: "batch" in juice-bar contexts ("make a batch") |
| **Yield** | Output quantity of a production order or recipe (expected vs actual) |
| **Made-to-order / made-ahead** | Whether a product's sale consumes raw ingredients (made-to-order) or a prepared item (made-ahead) |
| **Waste** | Stock lost to expiry, spoilage, error, remake, damage, spillage, overproduction or rejection; recorded with a reason. Never sales or production consumption |
| **Remake** | A product made again because the first was wrong; logged as waste because the sale already consumed one recipe |
| **Count / stock count** | A physical observation of quantities; a count session can cover all items, a category, an area or one item |
| **Reconciliation** | Comparing counted to expected, explaining variances and posting them |
| **Found short** | Total negative variance from counts — loss not explained by logged waste (JuJu term) |
| **Exception** | A recorded condition that needs attention (unmapped product, negative stock, high variance…) with a lifecycle |
| **Alert** | A notification about a condition (low stock, expiring); may accompany an exception |
| **Reorder point** | The on-hand level at which an item should be reordered |
| **Low-stock threshold** | The on-hand level at which a low-stock alert fires |
| **Lead time** | Days from ordering to delivery for a supplier |
| **Days of stock remaining** | On hand ÷ average daily use |

## Recipes

| Term | Definition |
|---|---|
| **Recipe** | The ingredient lines that make one product variation, modifier effect or prepared item |
| **Recipe version** | An immutable snapshot of a recipe effective from a point in time |
| **Recipe line** | One ingredient, quantity and unit in a recipe version |
| **Recipe cost** | Sum of line quantities × current unit costs, adjusted for yield. An estimate at today's costs |
| **Estimated gross margin** | (Price − recipe cost) ÷ price. Always labelled estimated |
| **Realized ingredient cost** | Sum of actual consumption at snapshot costs for a period |
| **Sold without a recipe** | A sale whose product (or modifier) had no recipe, so no consumption was recorded (JuJu term) |
| **Scale modifier** | A modifier that multiplies a base recipe (e.g. Light ice = 115 % of juice) |

## Integration

| Term | Definition |
|---|---|
| **POS** | Point-of-sale system (Square, Toast, Clover) |
| **Connection** | An authorized link between an organization and an external system |
| **External product / order / refund** | Records as imported from the POS, before InventoryOS interpretation |
| **Mapping** | Link from an external product or modifier to a product variation or modifier |
| **Sync** | Fetching external data; *webhook* = push notification from the provider; *reconciliation sync* = scheduled check that catches anything missed |
| **Write-back** | Sending stock/availability from InventoryOS to the POS (optional) |
| **Outbox** | Queue of outbound writes committed with the local change and sent with retries |
| **Walk-in** | (JuJu) A sale rung up on the Square POS at the counter that appears as a kitchen ticket |

## Operating modes and sales

| Term | Definition |
|---|---|
| **Operating mode** | Where a location's sales data comes from: Connected, Standalone, or (future) native POS. One engine for all modes |
| **Connected mode** | Sales come from an external POS through an adapter (Square first). Sold as InventoryOS Complete |
| **Standalone mode** | No POS: sales come from daily entry and file imports, plus operational records. Sold as InventoryOS Standalone |
| **Native POS mode** | Future: InventoryOS itself generates transactions. Would be sold as InventoryOS POS |
| **Sales source** | A configured origin of sales for a location (POS adapter, manual, file import, API, native POS); one source of record per business date |
| **Sales transaction** | One sale or one day's sales, normalized from any source, with lines per product variation |
| **Origin label** | Marker on every sales record and sales-derived figure: manual, imported, or POS-generated |
| **Daily sales entry** | Standalone record of quantity sold per product variation for one business day |
| **Cutover date** | The business date from which a location's new sales source takes over after a mode change |
| **Coverage** | Share of open business days with sales recorded (Standalone) |
| **Adapter** | Provider-specific integration code that translates a POS's data into InventoryOS's normalized model |

## Roles

| Term | Definition |
|---|---|
| **Owner** | Full control incl. subscription, ownership and deletion |
| **Administrator** | Owner minus subscription, ownership transfer and deletion |
| **Manager** | Runs a location's operations, sees costs, approves |
| **Inventory manager** | Items, counts, reconciliation, adjustments |
| **Purchasing staff** | Suppliers, purchase orders, receiving, reorder |
| **Production staff** | Production orders, batches, waste |
| **Employee** | Floor role: receive, waste, count, production; no costs |
| **Read-only** | Views inventory and reports; changes nothing |
| **Location scope** | The locations a membership's location-level permissions apply to |

## Commercial

| Term | Definition |
|---|---|
| **InventoryOS Complete** | Offering for businesses that keep their POS; Connected mode; pricing hypothesis $99/location/month |
| **InventoryOS Standalone** | Offering for businesses without a POS; Standalone mode; pricing hypothesis $99/month, one location |
| **InventoryOS POS** | Future native POS offering; pricing hypothesis $149–199/month starting; validation required |
| **Multi-location** | Expansion dimension for several locations; pricing hypothesis ~$149–199+/month |
| **Advanced** | Expansion dimension for beyond-core capabilities; pricing hypothesis ~$199–299+/month |
| **Plan** | A configured billing product (price lives in billing configuration, never in code) |
| **Entitlement** | What a subscription allows: modes, location count, capability flags, limits |
| **Read-only** | Account state after lapse: data visible and exportable, nothing can change |
| **Grace period** | Days of full access after a failed payment while Stripe retries |
