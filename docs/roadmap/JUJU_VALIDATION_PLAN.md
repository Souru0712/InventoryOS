# JuJu Validation Plan (Stage 5: T5.01 to T5.04)

Status: proposed, 2026-11-05. Owner decisions are marked **[OWNER]**; unverified facts are marked [UNVERIFIED].
Sources: [JUJU_REFERENCE_AND_PARITY](../product/JUJU_REFERENCE_AND_PARITY.md), [JUJU_TECHNICAL_AUDIT](../audit/JUJU_TECHNICAL_AUDIT.md),
[JUJU_INVENTORYOS_CAPABILITY_COMPARISON](../audit/JUJU_INVENTORYOS_CAPABILITY_COMPARISON.md),
[BUSINESS_WORKFLOWS](../product/BUSINESS_WORKFLOWS.md), [PRODUCT_ACCEPTANCE_CRITERIA](../product/PRODUCT_ACCEPTANCE_CRITERIA.md),
JuJu `docs/recipes/juju-recipes.json` and `supabase/seed.sql` (read-only; JuJu commit `ae1e702`).

## 1. Purpose and boundaries

**Purpose.** JuJu is the one real juice-bar operation whose inventory rules are already written down. Stage 5 uses it as a
**reference tenant** to test whether InventoryOS, configured (not coded) for a juice bar, reproduces a working operation's
inventory results. It is evidence for the spec; it is not a customer and not a pilot (C-40 is unaffected: no pilots on the old engine).

| Rule | Source |
|---|---|
| JuJu keeps its brand, customer experience, business logic and requirements; it gets what it needs regardless of InventoryOS progress | C-13 |
| One configurable product, no per-customer code: a JuJu need that cannot be met by configuration becomes a generic requirement or stays in JuJu | C-30 |
| Parity by specification until R1 ships; revisit JuJu as a tenant via the generic sales API after R1.1 | C-42 |
| No repository, database, frontend or deployment merge; no code sharing (TypeScript vs Python) | C-25, capability comparison |
| Never invent recipe quantities, stock levels, prices or press yields; use only what JuJu's files contain | owner standing rule |
| InventoryOS never reads or writes JuJu's database or runs JuJu's scripts; JuJu results come from the owner | this plan |

**What JuJu validates:** inventory domain behaviour (ledger, recipes with modifiers, prepared items, batches, receiving, waste, counts,
expiry, purchase suggestions, cost hiding) and the **Standalone** operating path (WF-16 daily sales entry).
**What JuJu must not drive:**

| JuJu-specific, never enters InventoryOS | Why it stays out |
|---|---|
| Storefront, menu presentation, art, drops, Lab Friday votes | Brand and customer experience (C-13) |
| Pickup capacity, kitchen queue, kitchen display, order board | InventoryOS is not a kitchen display; relevant only to a future native POS (PD-35) |
| Loyalty (punch card), promotions, first-visit offer, customer accounts and erasure | Customer-facing, JuJu business logic |
| NYC raw-juice labelling and DOHMH storage rules | Local regulation; InventoryOS only offers a use-by label (BAT-FR-14) and configurable shelf life |
| Walk-in matching by item name | InventoryOS maps by external id (POS-FR-06) |
| Share-based recipe-book authoring in JuJu's JSON | Authoring method; InventoryOS only gains a generic import (RCP-FR-15, R1.1) |
| Guest/account/walk-in segments, team prep-time analytics | JuJu-only reporting |
| Deduct-at-Ready as hard-coded behaviour | Becomes a per-source setting (PD-16), not JuJu code |

A finding is promoted to InventoryOS only when a second business type could plausibly need it (the C-30 test). Otherwise it is logged "JuJu-specific".

## 2. Validation timeline and prerequisites

| Step | Task | Earliest | Prerequisites | Exit evidence |
|---|---|---|---|---|
| V0 | Paper parity check: §3 matrix vs the spec; every JuJu workflow has requirement IDs | now | Roadmap Stage 5 approved | No unmapped row; gaps logged as proposed decisions |
| V1 | **T5.01** Reference tenant seeded | after **M3** (products, versioned recipes, consumption engine, batches, waste, production, suppliers, receiving) | M1 tenancy, M2 ledger; a recipe load path (RCP-FR-15 bulk import is R1.1, so use a fixture loader or API script until then); owner data (§4.3) | Seed report: counts of items/products/modifiers/recipes loaded vs source; zero silent drops |
| V2 | Engine-level scenarios (JV-01..28 that need no UI) | M3 | V1 | Results logged per scenario |
| V3 | **T5.02** Scripted operating day(s), Standalone style, through UI/API | **M4** (Standalone end-to-end) | V1; count, daily sales entry and purchase-suggestion screens; owner able to run the same script in JuJu | Parity report (§5) with every difference classified |
| V4 | **T5.03** Feedback triage | continuous from V1; formal review at M4 and at G-R1 | Feedback log (§6) | Every item categorized with a proposed disposition |
| V5 | Re-run V3 on the release candidate | before **G-R1** (M6) | Same script, new build | Regression report; no open Class A difference (§5.4) |
| V6 | **T5.04** JuJu-as-tenant decision | after **R1.1 (M7)**, which includes **T4B.03 generic sales API**, **and** JuJu is live | JuJu launch (its `PRE_LAUNCH_CHECKLIST.md` is NO-GO today); real operating history; shadow design (§7) | **[OWNER]** decision recorded in PRODUCT_DECISIONS.md |

Stage 5 must never gate G-R1 by itself: V0 to V5 produce evidence. A failed scenario is triaged; it blocks launch only if it is a Class A defect in an R1 requirement.
Items that can delay Stage 5 without delaying InventoryOS: JuJu go-live, owner time for data entry and tasting, JuJu's food-safety review.

## 3. Workflow validation matrix

Scenario IDs `JV-xx`. "Fail means": **IOS gap** (InventoryOS lacks or mis-implements a generic requirement), **JuJu-specific** (stays in JuJu),
**config** (setting or data entry), **bug** (spec met on paper, behaviour wrong).

### 3.1 Recipes, sizes, modifiers, ice, pours

| ID | JuJu workflow | InventoryOS requirements | Scenario | Expected result | Pass criterion | Fail means |
|---|---|---|---|---|---|---|
| JV-01 | Recipe per product x size (e.g. Golden State 12/16 oz pressed) | RCP-FR-01, RCP-FR-02, AC-RCP-01 | Create product with variations and mL recipe lines from seeded data | Saves only if every unit converts; lines differ per size by that size's regular mL | Line quantities equal JuJu's within tolerance (§5.3) | bug, or config (missing conversion) |
| JV-02 | "Start from" scaling one size to another | RCP-FR-09, AC-RCP-06 | Start 16 oz from 12 oz | Lines scaled 16/12 and editable | Scaling exact; JuJu's regular mL per size are not proportional (blended 240/315), so editing must be possible | IOS gap only if edit is blocked |
| JV-03 | Ice scaling (No 130 %, Light 115 %, Regular 100 %, Extra 85 %); add-ins and pumps not scaled | RCP-FR-03, AC-RCP-04, WF-12 step 3 | Sell one 16 oz drink at each ice level with one add-in and one pump | Drink lines scaled; add-in and pump lines unchanged | Consumption equals JuJu's at all four levels | IOS gap if scale cannot exclude add lines; bug otherwise |
| JV-04 | Ice inferred from typed POS modifiers, else Regular | POS-FR-06, WF-28, WF-16 | Daily sales entry has no modifier detail | Regular assumed; modifier counts optional | Documented difference (Class C, §5.4) | JuJu-specific |
| JV-05 | Pour rounding to measurable steps (2.5/5 mL; 0.1/0.5/5 g), largest pour absorbs the difference | none in R1 (R2 candidate per comparison) | Compare unrounded vs rounded consumption for the same sale | InventoryOS posts unrounded | Difference attributable to rounding only | JuJu-specific; log "candidate R2" |
| JV-06 | BYO: base x size (Orange, Green apple, Carrot, Pineapple), add-ins (Ginger, Lemon, Turmeric, Mint, Beet), toppings (Jelly, Mandarin segments, Frozen grapes), sugar pump | RCP-FR-03 (add/scale/substitute), RCP-FR-04 | Model BYO as a product whose base is a substitute modifier, or one product per base **[OWNER]** | One sale = base lines + add lines | Consumption equals JuJu's for 3 BYO combinations | IOS gap if neither model works |
| JV-07 | Recipe versioning (JuJu has none; `recipes:load` overwrites) | RCP-FR-07, AC-RCP-02, DR-16 | Change one share after a "tasting"; sell before and after | Before-edit sales use v1, after use v2 | AC-RCP-02 passes | bug. JuJu cannot reproduce this; compare InventoryOS to itself |
| JV-08 | Recipe cost, margin, price-change impact | RCP-FR-10, RCP-FR-11, RCP-FR-13, AC-RCP-03, AC-RCP-07 | Receive Orange at a changed cost | Affected recipes and margin change shown; costless lines listed | Cost equals JuJu's from the same prices | bug |
| JV-09 | Sold without a recipe | RCP-FR-12, AC-RCP-08 | Sell a product with no recipe | Exception with sale count; sale never blocked | Same gap list as JuJu "Sold without a recipe" | bug |

### 3.2 Conversions, prepared items, batches

| ID | JuJu workflow | InventoryOS requirements | Scenario | Expected result | Pass criterion | Fail means |
|---|---|---|---|---|---|---|
| JV-10 | Press yield (mL per lb) as the only mL-to-weight bridge | INV-FR-05, INV-FR-12, INV-FR-13, AC-INV-02, AC-INV-03 | (a) mL recipe line for a weight item without yield; (b) add owner-supplied yield, retry | (a) rejected naming the missing conversion; (b) accepted | (a) AC-INV-03; (b) base-unit quantity equals JuJu's | IOS gap if conversion cannot be added later and applied; config if data missing |
| JV-11 | Prepared item as ordinary ingredient (Ginger/Turmeric juice in drinks) | PRD-FR-01, RCP-FR-04, AC-RCP-05 | Use Ginger juice in Golden State; attempt a cycle | Works; cycle rejected | Both pass | bug |
| JV-12 | `make_batch`: inputs out, output in at input cost, use-by returned | WF-11, PRD-FR-02..06, AC-PRD-01..03, BAT-FR-04, BAT-FR-14 | Ginger juice x2 (1 lb Ginger gives 225 ml, 2 days, per JuJu's file) | -2 lb Ginger, +450 ml output batch, use-by shown | Output quantity, cost and use-by equal JuJu's | bug |
| JV-13 | Actual yield differs from expected (JuJu has no variance) | PRD-FR-04, AC-PRD-02 | Record actual 400 ml for a x2 run | Variance -50 ml; cost per ml = input cost / 400 ml | Compare with JuJu only for the expected-yield run | InventoryOS addition, not a failure |
| JV-14 | Unpriced input gives unknown output cost | PRD-FR-06, AC-PRD-03 | Batch with an unpriced input | Output cost unknown, never understated | Matches JuJu (cost null) | bug |
| JV-15 | Undo within 30 minutes (JuJu deletes; InventoryOS reverses) | PRD-FR-07, AC-PRD-05, DR-02 | Undo a batch | Reversal movements; history kept | Net balance equals JuJu's post-undo balance | designed difference (Class C) |
| JV-16 | Made-ahead: drink consumes the prepared item, not raw ginger | PRD-FR-11, AC-PRD-04, DR-18 | Sell Golden State | Consumes Ginger juice only | No double deduction of Ginger | bug |
| JV-17 | Prepared stock insufficient when selling | PD-15, INV-FR-23, AC-INV-10 | Sell more than the batch holds | Posts in full, negative balance, "count required" exception | Same sign and size as JuJu (stock may go negative) | bug |

### 3.3 Receiving, waste, counts, expiry

| ID | JuJu workflow | InventoryOS requirements | Scenario | Expected result | Pass criterion | Fail means |
|---|---|---|---|---|---|---|
| JV-18 | Receive per ingredient: quantity, total cost, supplier, expiry, note | WF-07, RCV-FR-01, RCV-FR-09, RCV-FR-11, AC-RCV-01 | Receive 3 lines manually | +accepted qty, cost snapshot, current cost updated, old-to-new cost shown with affected drinks | Balance and latest cost equal JuJu's | bug |
| JV-19 | Receiving beyond JuJu (four quantities, discrepancies, invoice photo) | RCV-FR-02, RCV-FR-05..07, AC-RCV-06 | 10 invoiced, 9 arrived, 8 accepted | +8, two discrepancies | No JuJu comparison; logged "JuJu would gain" | n/a |
| JV-20 | Waste reasons Spoiled, Expired, Remake, Spilled, Other; cost snapshot; actor | WF-18, WST-FR-02, WST-FR-05, AC-WST-02, AC-WST-03 | One waste per reason | `waste` movements with reason, actor, cost | Quantity and cost per reason equal JuJu's | config if reasons not configurable; IOS gap otherwise |
| JV-21 | Remake logged as waste | WF-17, WST-FR-02 | Log a remake | Ingredient lines out as waste | Same ingredient totals as JuJu | bug |
| JV-22 | Waste of a product vs an ingredient | WST-FR-01, AC-WST-04 | Waste 1 x Golden State 16 oz | Expands to recipe-version lines | JuJu logs ingredients only; compare per ingredient | none (InventoryOS superset) |
| JV-23 | Count posts variance (counted minus on-hand) under lock | WF-09, REC-FR-03, REC-FR-05, REC-FR-07, AC-REC-02, AC-REC-03 | Count 5 items after the scripted day | One variance movement per non-zero line | Variance per item equals JuJu's `record_count` | bug |
| JV-24 | "Found short at counts" shown beside waste | WST-FR-10, AC-WST-07 | Compare views | Shown separately | Totals equal JuJu analytics | bug |
| JV-25 | Concurrent writes (receive and count together) | INV-FR-22, AC-INV-07 | 50 parallel postings | None lost | AC-INV-07 | bug (engine) |
| JV-26 | Expired prepared batch auto-written off as waste (reason Expired, actor Automatic) after missed sales are retried | WF-14, BAT-FR-11, AC-BAT-07 | Enable auto-dispose on the reference tenant; let a ginger batch pass use-by | Waste of inferred remaining, actor system | Same remaining quantity as JuJu (oldest-first assumption) | IOS gap if the policy cannot be enabled for prepared items |
| JV-27 | Default without the policy | BAT-FR-08, AC-BAT-05 | Policy off, batch expires | Remains; `batch_expired_unresolved` exception | Documented difference | none (Class C) |
| JV-28 | Expiring raw deliveries (JuJu infers FIFO lots, alerts only) | BAT-FR-01, BAT-FR-05, BAT-FR-09, AC-BAT-02, AC-BAT-06 | Two receipts with different expiry; sell | FEFO allocation, alert at window | JuJu cannot reproduce FEFO; compare totals only | none |

### 3.4 Purchasing, sales, roles, alerts

| ID | JuJu workflow | InventoryOS requirements | Scenario | Expected result | Pass criterion | Fail means |
|---|---|---|---|---|---|---|
| JV-29 | "What to buy": expected use + 15 % + low-stock level - on hand, prepared-to-raw expansion | WF-21, ROR-FR-01..03, ROR-FR-06, AC-ROR-01, AC-ROR-02 | Same history and levels in both; run suggestions | Raw demand includes demand from prepared items; batches already made deducted | Quantities equal JuJu's when InventoryOS parameters are set to JuJu's formula (15 % safety) | IOS gap if the formula is not expressible via ROR-FR-02 parameters; config otherwise |
| JV-30 | Forecast: average of last four same weekdays | ROR-FR-01 (28-day average) | Compare demand estimates | Different method | Difference explained, not a defect; weekday forecast is an R2 candidate | IOS gap (R2) |
| JV-31 | "What to make" prepared items in half-batch steps | PRD-FR-09 (R1.1) | Run after M7 | Suggestion within shelf life | Compare with JuJu | IOS gap if absent at R1.1 |
| JV-32 | Daily sales (JuJu deducts per order at Ready; Standalone enters per day) | WF-16, WF-12, POS-FR-17, PD-16 | Enter the day's quantity per variation once | Consumption posted at end of business day | Daily total per ingredient equals the sum of JuJu's per-order deductions | bug; trigger-time difference is Class C |
| JV-33 | Re-entering a day | WF-16, WF-26 | Edit quantities | Reversal + re-post, new revision | Net equals a single correct entry | bug |
| JV-34 | Refund does not restore stock | POS-FR-08, AC-POS-05, WF-17 | Record a refund | Net sales reduced, stock unchanged | Matches JuJu | bug |
| JV-35 | Roles staff/manager/owner; cost hidden from staff at the data layer | USER_ROLES_AND_PERMISSIONS §4-5, AC-CMD-03, AC-RCP-09, AC-REC-09 | Employee calls dashboard, recipe, waste and receiving APIs | No cost, value or margin field in any response | Response-level check, not UI | bug (security) |
| JV-36 | Last-owner protection, optional TOTP | SAAS-A-01..10 (M1) | Remove the only Owner | Refused | Pass | bug |
| JV-37 | Alerts: low stock, batch expiring today/tomorrow, batch probably not recorded | WF-20, WF-14, CMD-FR-02, REC-FR-11..16 | Trigger each | Alert shown; Employee sees no amounts | Alert set equals JuJu's | IOS gap if an alert kind is missing |

## 4. Reference tenant data plan (T5.01)

### 4.1 Sources and one-way import

| JuJu source (read-only) | Contents | Feeds |
|---|---|---|
| `docs/recipes/juju-recipes.json` | `iceFactors` [1.3, 1.15, 1, 0.85]; `regularMl` (pressed 12: 255, 16: 340; blended 12: 240, 16: 315, 24: 475); `prepared` (Ginger juice: 1 lb Ginger gives 225 ml, 2 days; Turmeric juice: 0.5 lb Turmeric gives 100 ml, 2 days; both flagged as estimates); 15 `drinks` with shares; 4 `bases`; 8 `extras`; `pump` | Items, prepared items and production recipes, products, recipes, modifiers |
| `supabase/seed.sql` | Ingredient names with base and display unit (e.g. Carrot g/lb, Coconut water ml/L); `products` and `product_sizes` (sections, sizes, menu prices); `modifiers`; `byo_bases` and `byo_base_sizes`; `cup_sizes` | Items, variations, informational sale prices, modifier names |
| Migrations (`*inventory*`, `*batches*`) | Movement kinds, waste reasons, rules | Configuration only, not data |

Verified absent from the files (the seed's own comment says "no stock, prices or recipes"): opening stock, ingredient costs, per-ingredient suppliers,
low-stock levels, produce press yields, reorder lead times.

The import is a **one-way, one-time script** run by InventoryOS tooling against copies of those two files held as fixtures in the InventoryOS repo (or read from the JuJu path read-only):

| Rule | Detail |
|---|---|
| Direction | JuJu files to InventoryOS only; InventoryOS never writes to JuJu and never reads its live database |
| No live link | No webhook, API call, shared credential, shared auth or sync job |
| Provenance | Seeded records carry `source = juju_reference_import`, source file and JuJu commit id; re-import creates new versions, never silent overwrites (RCP-FR-07) |
| Drift | Later JuJu changes are re-imported only on owner request; the seed report lists differences |
| Isolation | Reference org is an ordinary tenant (M1), flagged internal, excluded from billing and metrics, deletable as a whole |
| Secrets | None needed; no JuJu credentials enter InventoryOS |

### 4.2 Concept mapping

| JuJu concept | InventoryOS entity | Notes |
|---|---|---|
| `ingredients` (base unit g/ml/each, display unit) | Inventory item with base and usage unit (INV-FR-02, INV-FR-04) | Raw type; Ginger juice and Turmeric juice are type prepared (PRD-FR-01) |
| Press yield (mL per lb) | Item-specific conversion (INV-FR-05) | Only Ginger (225 ml per lb) and Turmeric (200 ml per lb, derived from 100 ml / 0.5 lb) are derivable from files; produce yields are not entered in JuJu (checklist R17) |
| `prepared` / prep recipes | Production recipe: inputs, expected yield, shelf life (PRD-FR-02, BAT-FR-04) | 2-day shelf life is JuJu's unverified assumption pending DOHMH review (R10) |
| `products` x `product_sizes` | Product with variations 12/16 oz (24 oz blended) (RCP-FR-01) | Menu price stored as information only |
| `recipe_lines` kind `product` | Recipe lines per variation (RCP-FR-02) | Each share x `regularMl[method][size]` / 100 gives mL per line [UNVERIFIED: inferred from the file header; `src/domain/recipeBook.ts` not read] |
| `recipe_lines` kind `base` (BYO) | Recipe from a base modifier with substitute/add effect (RCP-FR-03) | Seed default: one product per base (no invented structure); alternative **[OWNER]** |
| `recipe_lines` kind `modifier` (Add-in, Topping) | Modifier with add effect and `extras` quantity | e.g. Ginger add-in 7.5 ml Ginger juice; Jelly 40 g; one portion at every size |
| `recipe_lines` kind `pump` | Modifier with add effect (Sugar syrup 7.5 ml per pump) | Free vs priced pumps are pricing, not inventory |
| `store_settings.ice_factors` | Four scale modifiers: No ice 1.3, Light 1.15, Regular 1.0, Extra 0.85 | Scale applies to drink lines only (AC-RCP-04) |
| `inventory_movements` kinds | `receipt`, `sale_consumption`, `waste`, `count_variance`, `production_output`, `production_input` (INV-FR-15) | Movements are not migrated in Stage 5 (§7.5) |
| Waste reasons Spoiled, Expired, Remake, Spilled, Other | Configurable waste reasons (WST-FR-02) | Map to defaults (spoilage, expiration, remake); Spilled and Other as business-defined |
| Roles staff/manager/owner | Employee/Manager/Owner (USER_ROLES_AND_PERMISSIONS §2) | Test users in the reference org only |
| Drops, art, ingredient labels, sections | Not imported (sections may become categories, INV-FR-03) | Brand data |

### 4.3 To be supplied by the owner (never invented)

| Data | Needed for | State in JuJu |
|---|---|---|
| Produce press yields (mL per lb) per pressed ingredient | JV-01, JV-10, all mL-to-weight consumption | Not entered (R17) |
| Opening stock per ingredient | JV-23, JV-32 | Not entered |
| Ingredient costs / supplier prices | JV-08, JV-12, JV-18, cost parity | Not entered |
| Low-stock levels per ingredient | JV-29, JV-37 | Not entered |
| Supplier names per ingredient | JV-18, JV-29 | Not in seed |
| Post-tasting recipe shares | JV-07 | Recipes final but untasted |
| BYO modelling preference | JV-06 | n/a |
| Operating-day script values | §5 | Owner creates |

Until supplied, the tenant runs a **structure-only** seed (items, Ginger/Turmeric conversions, recipes, modifiers). Scenarios needing missing data are marked
BLOCKED-ON-OWNER, never filled with placeholder numbers. Numbers the owner invents for the script are labelled "script values", not JuJu data.

## 5. Parity comparison method (T5.02)

### 5.1 Script
The owner writes one **operating-day script** as a CSV of ordered rows: event type (receive, prep batch, sale line with size/ice/add-ins, waste with reason, close count),
item or product, quantity, unit, cost where relevant, timestamp. Coverage: 2 days; every ice level; one BYO order; one add-in and one pump; one Ginger juice batch whose
use-by falls on day 3; one remake; one spill; one raw delivery at a changed cost; a closing count each day with a deliberate variance. The same file is the only input to both systems.

### 5.2 Execution

| Side | Who runs it | How |
|---|---|---|
| JuJu | **Owner**, in JuJu's own environment (local or staging) when available; sales as test orders taken to Ready; other events via JuJu's inventory screens | Exports: stock levels, per-ingredient deducted usage, waste cost, found-short at counts, purchase plan |
| InventoryOS | Reference tenant, Standalone mode: WF-07, WF-11, WF-18, WF-16, WF-09, WF-21 | Outputs: ledger balances, consumption by item, waste by reason, variance lines, suggestions |

InventoryOS never drives JuJu, never connects to its database, and keeps JuJu exports only inside the comparison report (no customer data involved).

### 5.3 Comparison and tolerance (proposed **[OWNER]**; fixed before the first run, never tuned afterwards)

| Figure | Method | Tolerance |
|---|---|---|
| Ending balance per ingredient (base unit) | Direct | 0.5 % or 1 base unit, whichever is larger |
| Consumption per ingredient per day | Direct | 0.5 %; more only where explained by pour rounding (JV-05), bounded by the rounding step per pour |
| Waste quantity and cost by reason | Direct | Quantity exact; cost 0.5 % or 1 cent per line |
| Count variance | Direct | Exact in base units, given equal on-hand |
| Batch output quantity, cost, use-by | Direct | Quantity and use-by exact; cost 0.5 % |
| Purchase suggestion | After aligning parameters (JV-29) | Exact after rounding to the same unit |
| Cost of goods / margin | Only sales with complete recipes | 0.5 % |

### 5.4 Discrepancy classes

| Class | Meaning | Disposition |
|---|---|---|
| A | InventoryOS wrong or violates an R1 requirement | Fix before G-R1; add a regression test tied to an AC ID |
| B | InventoryOS lacks a generic behaviour JuJu has | Propose a requirement (§6); owner decides |
| C | Designed difference (reversal vs delete, versioning, trigger time Ready vs end of day, unrounded pours, FEFO vs inferred FIFO, weekday forecast) | Documented, no action |
| D | JuJu-side defect or ambiguity exposed by the comparison | Reported to the owner for JuJu's own backlog; never changes InventoryOS |
| E | Input or data problem (missing yield, wrong unit in the script) | Fix script/seed, rerun |

Report row: figure, JuJu value, InventoryOS value, difference, class, scenario ID, feedback ID.

## 6. Feedback process (T5.03)

Log: `docs/roadmap/JUJU_FEEDBACK_LOG.md` (created when the first item arrives), one row per finding.

| Field | Content |
|---|---|
| ID, date, reporter | `JF-NNN`, who found it |
| Scenario | `JV-xx` or free text |
| Observed vs expected | Facts with both values |
| Evidence | Report row, screenshot, path |
| Category | bug, InventoryOS gap, JuJu-specific, configuration issue |
| Requirement IDs | Existing FR/AC/WF affected |
| Severity | Blocks G-R1 / needs a decision / informational |
| Proposed disposition | Fix, new requirement, defer (release), reject, log only |
| Decision | Owner answer, date, PRODUCT_DECISIONS.md ID |

| Category | Test | Destination |
|---|---|---|
| Bug | Spec is clear and behaviour deviates | Fix task in the current stage; regression test |
| InventoryOS gap | Generic need missing from the spec and plausible for other juice bars or cafes (C-30) | **Proposed decision** with draft requirement ID, suggested release (R1, R1.1, R2) and cost; owner approves before it enters requirements and roadmap |
| JuJu-specific | Brand, customer, local-law or JuJu-process need | Stays in JuJu; noted in §1 |
| Configuration issue | Achievable by settings or data | Fix the reference tenant; feed onboarding guidance or starter templates (INV-FR-08a) |

Rules: no feedback silently changes scope; every gap is a proposed decision first. A JuJu need never outranks another segment's need without a decision (C-30).
Cadence: weekly during V2-V3, then at each milestone review and at G-R1.

## 7. JuJu-as-tenant decision (T5.04)

Option B of the parity doc: JuJu keeps storefront, order board and kitchen; it sends order lines to InventoryOS at "Ready" through the generic sales API (POS-FR-16, T4B.03).

### 7.1 Entry criteria (all required)

| Criterion | Evidence |
|---|---|
| R1.1 shipped incl. T4B.03; sales API documented, idempotent, authenticated per integration | M7 exit |
| JuJu live with several weeks of real operating data | JuJu checklist no longer NO-GO |
| Stage 5 V3/V5 closed with no open Class A/B items on core workflows | Parity reports |
| Per-source consumption trigger supports Ready (PD-16) | AC for PD-16 |
| JuJu can post sales without ever waiting on InventoryOS | Design review of JuJu's outbound poster |
| Decision rests on JuJu's business merit, not InventoryOS dogfooding alone | **[OWNER]** |

### 7.2 Risks

| Risk | Mitigation |
|---|---|
| Two identity systems (Supabase Auth vs InventoryOS in-app auth, C-32) | Separate logins in shadow; SSO only via SAAS-A-10 later |
| JuJu's line must never wait on a network call | Fire-and-forget posting with JuJu-side outbox and retry; InventoryOS idempotent on external order-line id (POS-FR-11) |
| Deduct-at-Ready (PD-16) vs Square "completed" | Per-source setting; `occurred_at` = Ready time |
| Double deduction while both are live | Shadow: JuJu is system of record; InventoryOS figures labelled "shadow" |
| Walk-ins are not JuJu online orders | Walk-ins keep JuJu's matching and are posted as normal order lines **[OWNER confirms]** |
| Ledger history migration | Opening balance plus optional legacy history per DATA_MIGRATION_ASSESSMENT §5; JuJu's `undo_batch` deletions mean history is not fully reversible |
| Two recipe sources | After cutover InventoryOS is the only source (decision needed); JuJu's `recipes:load` retired |
| Product scope pressure | C-30 test on every JuJu-driven change |

### 7.3 Shadow mode
1. JuJu stays system of record; its Ready order lines are copied to InventoryOS via the sales API; receiving, batches, waste and counts are mirrored by owner entry or script.
2. The §5.3 comparison runs daily on live data, reviewed weekly; exit target: N consecutive weeks with no Class A/B differences **[OWNER sets N]**.
3. Sales API failure never reaches the line: JuJu logs and retries; InventoryOS reconciliation detects gaps.
4. JuJu change limited to an outbound poster, in JuJu's repo, by the owner.

### 7.4 Cutover and rollback

| Step | Detail |
|---|---|
| Cutover | Owner decision; freeze date; opening balances from a count; InventoryOS becomes inventory system of record; JuJu inventory screens read-only or retired |
| Rollback trigger | Divergence above tolerance, sales API outage affecting staff, or owner choice |
| Rollback | JuJu's own inventory resumes (kept intact and read-only during shadow); post-cutover InventoryOS movements re-entered as one count; nothing deleted on either side |
| Exit | Staying on option A permanently is a valid outcome; record it as a decision |

### 7.5 Ledger history
Stage 5 does not migrate JuJu's `inventory_movements`. If B is chosen, history migration is a separate task with opening balances at cutover.

## 8. Shared vs separate functionality

| Shared domain (spec in InventoryOS, JuJu as reference) | JuJu-specific (never InventoryOS) |
|---|---|
| Signed-movement ledger, cost snapshot, actor | Storefront, menu presentation, drops, Lab Friday |
| Press yield as item-specific conversion | Pickup capacity, kitchen queue and display |
| Recipes per variation with scale/add modifiers; prepared items | Loyalty, promotions, customer accounts |
| Production transaction, shelf life, expiry write-off policy | NYC raw-juice labelling rule |
| Receiving, suppliers, price history | Share-based recipe-book authoring (import format only) |
| Waste reasons, counts as variances | Walk-in name matching |
| Purchase suggestion with prepared-to-raw expansion | Pour rounding (R2 candidate only if a second customer needs it) |
| Cost hidden from staff at the data layer | BYO/pump/ice-level names (their effects are generic) |
| Refunds never restore stock; webhook dedupe + reconcile | Guest/account/walk-in segments, team prep time |

## 9. Risks and owner dependencies

| ID | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| JR-1 | JuJu not live for a long time; Stage 5 stalls | High / low | V1-V5 do not need go-live; only V6 does |
| JR-2 | Owner time for yields, stock, costs and script | High / medium | Structure-only seed first; BLOCKED-ON-OWNER status |
| JR-3 | Recipes untasted; shares change | Medium / low | Treat as versions (JV-07); re-seed on request |
| JR-4 | Parity noise from designed differences | Medium / medium | Agree the Class C list before the first run |
| JR-5 | JuJu defects mistaken for InventoryOS defects | Medium / low | Class D; check against JuJu migration SQL and spec |
| JR-6 | Scope creep from JuJu feedback | Medium / high | §6 rules; C-30 test |
| JR-7 | JuJu behaviour known from docs and code reading, not run (CI results not re-run) | Medium / medium | Owner supplies actual JuJu outputs |
| JR-8 | JuJu shelf lives (2 days) are unverified assumptions pending DOHMH review | Medium / low | Treated as configuration, not truth |

| Owner dependency | Needed by |
|---|---|
| Approve this plan and the §5.3 tolerances | V0 |
| Supply the §4.3 data | V1-V3 |
| Write the operating-day script and run it in JuJu | V3 |
| Choose BYO modelling | V2 |
| Decide T5.04 (after R1.1 and JuJu live) | V6 |
