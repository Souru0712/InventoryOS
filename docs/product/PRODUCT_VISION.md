# Product Vision

**Product:** InventoryOS · **Status:** Phase 1 product definition, 2026-11-04

Statement tags used across the package: **[CONFIRMED]** stated by the owner · **[PROPOSED]**
requirement proposed here · **[RECOMMENDED]** recommendation · **[ASSUMED]** inference to validate ·
**[OPEN]** unresolved, tracked in [PRODUCT_DECISIONS](PRODUCT_DECISIONS.md) · **[FUTURE]** possibility,
not committed. No customer validation or market research has been performed; segment profiles and
outcomes below are hypotheses.

---

## 1. Three separate entities

| | **InventoryOS** | **InventoryOS codebase (formerly StockPulse)** | **JuJu Juice** |
|---|---|---|---|
| What it is | The commercial SaaS product defined here — the new name for StockPulse (C-24) | The existing inventory application with Square integration | A separate juice brand/business with its own platform |
| Code | Built on the codebase to the right (C-25); no InventoryOS release yet | `InventoryOS` repository (formerly `POS-Inventory-Automation`) | `JuJu` repository |
| Role | — | **Chosen technical foundation** (C-25); Phase 2 audits it against this blueprint | **Real-world reference and demonstration environment**; must keep its own brand, customer experience, business logic and requirements |
| Not | — | Not assumed to meet the blueprint already; its Square integration does not define InventoryOS's integration architecture | Not the commercial product; not the definition of every customer's needs |

No shared repository, database, frontend or deployment is assumed between them [CONFIRMED].

## 2. Identity and positioning

| | |
|---|---|
| Name | InventoryOS |
| Category | **Inventory and operations management platform** for independent food-service businesses (vertical SaaS) — not a stock-counting app |
| Core value proposition | Connects every movement of ingredients, products, supplies and materials to the business activity that caused it — purchasing, receiving, production, sales, waste — so owners know what they have, what they should have, what it cost and where money is lost, with far less manual work |
| Primary problem | Inventory lives in disconnected spreadsheets, paper, POS screens and memory; nobody can say what *should* have been used versus what *was* used, so waste, over-portioning, theft, expiry losses and stockouts go unseen |
| One-line | Know what you have, what you used, what you lost and what to buy |

### Governing principles
1. **Operational source of truth** for inventory and its movements; every change traceable to its
   cause (ledger, not a mutable number).
2. **Self-maintaining** [CONFIRMED by owner for the existing app, carried over]: "Don't make the
   customer maintain the inventory system. Make the system maintain itself as much as possible."
   Prefer ingestion (POS, imports, invoice capture, scanning) over typing; push alerts.
3. **Works with or without a POS**: one engine; the operating mode only changes where sales data
   comes from [CONFIRMED].
4. **Honest numbers**: estimates are labelled; incomplete data is disclosed, never presented as
   verified [CONFIRMED].
5. **Explainable intelligence**: recommendations show their inputs; no opaque "AI says" [CONFIRMED
   by owner previously].

## 3. Questions the platform answers [CONFIRMED list]

| Question | Answered by |
|---|---|
| What inventory do we have? | Ledger balances per item and location (Inventory) |
| Where is it? | Location and storage area on balances and batches |
| How much should we have? | Min/max levels, reorder points (Inventory, Reordering) |
| How much should we have consumed? | **Theoretical consumption** from sales × recipes (Sales sources, Recipes) |
| What was actually consumed? | **Actual consumption** from counts: opening + in − out − closing (Reconciliation, Analytics) |
| What was wasted? | Waste events with reason, person, cost (Waste) |
| What is expiring? | Batches with expiry, FEFO, alerts (Expiration) |
| What needs to be reordered? | Explainable reorder suggestions (Purchasing) |
| What did a product or recipe cost? | Versioned recipes × cost snapshots (Recipes and costing) |
| How much should a batch consume? | Production recipes and orders (Production) |
| Where are discrepancies occurring? | Variances and exceptions by item, location, person, process (Reconciliation) |
| Which products, ingredients and locations are profitable? | Margin and location performance (Analytics) |
| Which processes create unnecessary cost? | Waste by reason/stage, variance by process, yield variance (Analytics) |

## 4. Customer problems and pain points

| # | Pain point | Consequence | InventoryOS answer |
|---|---|---|---|
| 1 | Actual quantities unknown | Stockouts, over-buying, emergency runs | Ledger + counts |
| 2 | Sales don't turn into ingredient use | Stock numbers drift; low-stock alerts never fire | Recipe-driven consumption (any sales source) |
| 3 | Recipes inconsistent or unwritten | Over-portioning, inconsistent product, unknown cost | Versioned recipes with staff recipe cards |
| 4 | Product cost unknown | Mispriced menu, invisible margin erosion | Recipe costing with price-change impact |
| 5 | Expiring stock | Direct loss on perishables with days of shelf life | Batches, FEFO, expiry queue and alerts |
| 6 | Waste unrecorded | Loss invisible, cannot be reduced | Fast attributed waste logging with cost |
| 7 | Deliveries vs invoices not reconciled | Paying for goods not received; wrong costs | Ordered / invoiced / arrived / accepted receiving |
| 8 | Reactive purchasing | Stockouts and rush orders | Reorder suggestions, purchase orders |
| 9 | No accountability for changes | Shrinkage and errors untraceable | Actor on every movement, roles, approvals |
| 10 | No consolidated view | Owner can't see what needs attention | Dashboard and exceptions |
| 11 | Information fragmented across tools | Hours of weekly spreadsheet work | One platform fed by POS, imports, receiving, production |
| 12 | Can't see where money is lost | No basis for decisions | Waste cost, variance cost, expiry loss, margins |

## 5. Target customers

### 5.1 Ideal customer profile (initial) [CONFIRMED focus; profile details ASSUMED]
- Independent, **single-location** juice bar, smoothie shop, café or beverage business.
- Owner-operator, 2–15 staff, ingredient-based products with recipes and some in-house prep.
- Perishables received several times per week; shelf lives measured in days.
- Either uses a POS (often Square) **or** runs without one by choice.
- Tracks stock in spreadsheets or not at all; feels waste and stockouts in cash.

### 5.2 Segments [ASSUMED — to validate]

| Segment | Business characteristics | Inventory complexity | Typical workflows | Main inventory problems | POS situation |
|---|---|---|---|---|---|
| 1. Independent juice bars | 1 store, fresh produce, pressed/blended drinks, high perishability | High: produce by weight, yields, prepared batches (shots, juices) | Daily produce receiving, morning prep, made-to-order + made-ahead, end-of-day disposal | Expiry loss, yield variance, over-pouring, unknown cost per drink | Often Square or similar; some none |
| 2. Smoothie shops | Frozen + fresh fruit, dairy/alt-milks, add-ins | Medium–high: many modifiers (boosts, milks), frozen stock | Weekly frozen deliveries, frequent fresh top-ups, blending to order | Modifier-driven consumption, add-in shrinkage | Usually a POS |
| 3. Cafés | Coffee plus food items, some baked goods bought in or made | Medium: milk, beans, syrups, pastries; packaging | Several suppliers, daily dairy, weekly dry goods | Milk/pastry waste, cups and packaging untracked | Usually a POS |
| 4. Coffee shops | Beverage-heavy, high volume, narrow menu | Medium: beans by weight, milk by volume, many milk/syrup modifiers | Bean roaster orders, dairy deliveries | Milk variance, syrup usage, modifier costing | Almost always a POS |
| 5. Bubble tea / specialty beverage | Tea bases, toppings (tapioca) cooked in batches with short holding times | High: batch-cooked toppings expire in hours, many customizations (sweetness, ice, toppings) | Batch cooking several times a day, timed disposal | Topping waste, batch timing, modifier complexity | Usually a POS |
| 6. Small food-service | Sandwich shops, kiosks, caterers, market stalls, small kitchens | Varies: recipes with sub-recipes, prepared items | Prep lists, production, catering orders | Prep waste, sub-recipe costing | Mixed; markets and caterers often no POS |
| 7. Multi-location operators | 2–10 stores, shared suppliers, possibly a commissary | High: transfers, per-store stock, central purchasing | Central ordering, transfers, per-store counts | Cross-store visibility, transfer accuracy, comparing stores | POS per store, sometimes mixed |
| 8. Growing / sophisticated operators | Expanding menus, wholesale or catering lines, accountant involved | High: forecasting, supplier performance, integrations | Formal purchasing, budgets, reporting to partners | Planning, automation, data access | POS plus other systems |

| Segment | Staff requirements | Purchasing | Production | Reporting | Willingness to pay [ASSUMED] | Essential | Optional |
|---|---|---|---|---|---|---|---|
| 1. Juice bars | Line staff log waste/batches on phones; owner sees money | Daily produce, 2–4 suppliers | **Essential** (prepared juices/shots) | Waste, expiry loss, cost per drink | Moderate; $99 justified by produce waste avoided | Batches/expiry, recipes with yields, waste, receiving | POs |
| 2. Smoothie shops | Similar | Weekly frozen + fresh | Light (pre-portioned packs) | Margin per smoothie, add-in usage | Moderate | Modifier recipes, waste | Production |
| 3. Cafés | Baristas, shift leads | Several suppliers, standing orders | Light (cold brew, syrups) | Food waste, COGS estimate | Moderate | Recipes, waste, reorder | Batches for food |
| 4. Coffee shops | High staff turnover | Roaster + dairy | Light (cold brew) | Milk/bean variance | Moderate | POS mapping with modifiers, counts | Production |
| 5. Bubble tea | Batch cooks, line | Specialty importers, long lead times | **Essential** (toppings with hours of shelf life) | Topping waste, batch yield | Moderate | Production with hour-level shelf life, modifiers | POs |
| 6. Small food-service | Small team | Mixed | Often essential | Prep waste, costing | Lower to moderate | Recipes/sub-recipes, waste | POS integration |
| 7. Multi-location | Store managers + central owner | Consolidated | Possible commissary | Location comparison | Higher; needs centralized view | Location-scoped stock and roles, transfers | Advanced forecasting |
| 8. Sophisticated | Purchasing/inventory roles | Formal POs, supplier analysis | Varies | Advanced, exports, API | Highest | Granular roles, POs | API, forecasting |

Gap noted [OPEN, PD-40]: hour-level shelf life (bubble-tea toppings) is not covered by date-only
expiry; the domain allows expiry timestamps for prepared items (DR-20 note).

### 5.3 Customer types by POS situation [CONFIRMED]

| Scenario | Situation | Offering | Operating mode |
|---|---|---|---|
| **A** | Already has a POS (e.g. Square) and keeps it | **InventoryOS Complete** | Connected |
| **B** | No POS and doesn't want one | **InventoryOS Standalone** | Standalone |
| **C** | No POS but wants one | **InventoryOS POS** — future, validation required | Native POS (future) |

Scenario C is **not** "connect Square" (that is A). It is a possible native POS offering, validated
before any build commitment (PD-35). Until then a Scenario C prospect is offered Standalone, or a
supported POS plus Complete, and the interest is recorded as validation evidence (PD-36).
Packaging and prices: [PRICING_AND_PACKAGING](PRICING_AND_PACKAGING.md).

## 6. Customer journeys [ASSUMED — to validate in pilots]

### Journey A — Juice bar on Square → Complete
| Stage | What happens | Value moment |
|---|---|---|
| Discover | Owner throws out produce weekly and doesn't know drink costs | — |
| Evaluate | Sees expiry and waste-cost demo; trial | "It knows my sales already" |
| Onboard (≤ 45 min) | Connects Square → catalog imported → adds produce and prepared items → recipes for top 10 drinks with sizes → opening count | First dashboard with real stock |
| First week | Sales deduct ingredients automatically; staff log waste and ginger-shot batches on phones; first delivery received from invoice photo | First expiry alert acted on |
| Steady state | Weekly count shows variance; reorder list by supplier; weekly email | Waste cost visible and falling |
| Expand | Opens second store → Multi-location; asks for forecasting → Advanced | — |

### Journey B — Smoothie stand without a POS → Standalone
| Stage | What happens | Value moment |
|---|---|---|
| Discover | Takes payments on a card terminal; tracks stock on paper | — |
| Onboard | Imports items from a spreadsheet; sets recipes; opening count; chooses daily sales entry | Recipe costs for the first time |
| Daily | Enters quantities sold per smoothie at close (or uploads the terminal's item report); logs waste | Ingredient use calculated from recipes |
| Weekly | Counts; count-based usage shows actual use even on days without sales entered; reorder suggestions | Knows what was really used and lost |
| Change | Adopts Square later → switches to Complete at a cutover date, keeping history | No re-setup |

### Journey C — Café that wants a POS (today)
| Stage | What happens |
|---|---|
| Discover | Has no POS, wants one with inventory built in |
| Today | Offered Standalone now, or a supported POS (e.g. Square) + Complete; interest in InventoryOS POS recorded |
| Future [FUTURE] | If InventoryOS POS launches after validation, migrates to it; sales then come from InventoryOS itself |

### Journey D — Two-store operator → Multi-location [FUTURE]
Central owner sees both stores, transfers prepared items from the main store, compares waste and
margins by store, assigns store managers to their own location only.

## 7. Competitive positioning [ASSUMED — no market research performed]

| Alternative the customer uses today | Its limit | InventoryOS position |
|---|---|---|
| Spreadsheets and paper | Manual, error-prone, no link to sales or cost | Automatic consumption, ledger, alerts |
| POS-native stock features | Typically count sellable items; weak on ingredients, recipes, yields, batches, expiry and receiving reconciliation | Ingredient-level operations layer on top of the POS |
| Enterprise restaurant inventory/back-office suites | Built for chains; cost and setup effort high for one store | Practical setup in under an hour, priced for one location |
| Generic inventory apps | Not food-service aware: no recipes, yields, modifiers, shelf life | Food-service domain built in |

Competitor names, features and prices must be researched before use in sales material; none are
asserted here.

## 8. Differentiators [CONFIRMED themes]

- Recipe-driven consumption including sizes, modifiers and prepared items.
- Batch-level tracking with FEFO and explicit expiry accountability.
- Purchase ↔ invoice ↔ receiving reconciliation.
- Waste as a first-class event with reason, person and cost.
- Reconciliation that explains variances instead of overwriting numbers.
- Operational exception management ("what needs attention and why").
- Same engine with or without a POS.
- Practical automation sized for independent operators.

## 9. Measurable outcomes

### For customers [PROPOSED targets; unvalidated]
| Outcome | Measure | Target after 8 weeks |
|---|---|---|
| Less waste | Waste cost as % of purchases | Down vs first 2 weeks |
| More accurate stock | Absolute count variance value | Down vs first count |
| Fewer stockouts | Items hitting zero during service | Down |
| Less admin time | Owner-reported hours on inventory per week | Down |
| Known product cost | % of sold items with a complete recipe cost | ≥ 90 % |

### For the business [PROPOSED]
| Measure | Target |
|---|---|
| Paying customers | 21 (C-26); $1,000 MRR gates any new revenue product |
| Monthly logo churn | < 3 % after month 3 |
| Weekly active owners | ≥ 80 % |
| Time to first value | Recipes for top sellers + opening count within 7 days |
| Support load | < 1 founder-hour per customer per month after onboarding |

## 10. Product boundaries — what InventoryOS is not

| InventoryOS is not… | Instead |
|---|---|
| A POS (at launch) | Integrates with external POS (Connected) or runs without one (Standalone); native POS is [FUTURE] |
| A payment processor | Payments stay with the POS/processor |
| A customer ordering website, loyalty or marketing tool | Businesses keep their own (JuJu keeps its customer site) |
| An accounting system | Provides valuation, COGS estimates and exports |
| A warehouse/WMS or ERP | No bins, EDI, manufacturing MRP |
| A stock-counting app | Counts are one input to a ledger-based operations platform |
| Built around Square | Square is the first adapter, not the foundation [CONFIRMED] |
| Built around JuJu | JuJu is a reference environment, not the definition of all customers |

## 11. Commercial summary

Pricing hypotheses (not published prices): **Complete $99/location/month**, **Standalone
$99/month (one location)**, **InventoryOS POS $149–199/month starting** (future, validation
required, pass-through costs excluded), **Multi-location ~$149–199+**, **Advanced ~$199–299+**.
The initial commercial launch prioritizes a valuable **$99 single-location** product in both
Standalone and Connected modes [CONFIRMED]. Full matrix and validation questions:
[PRICING_AND_PACKAGING](PRICING_AND_PACKAGING.md).
