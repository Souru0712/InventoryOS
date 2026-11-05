# JuJu Juice: Reference and Functionality Parity

Authoritative source for **JuJu's actual workflows** (as found in its repository on 2026-11-04)
and **its capability relationship with InventoryOS**. Facts here cite JuJu files; anything not
found in code is marked [ASSUMED] or [OPEN].

JuJu is a separate business and product [CONFIRMED]. For InventoryOS it is a **real-world reference
and demonstration environment** for validating operational needs — not the commercial product and
not the definition of every customer's needs. JuJu keeps every capability it needs as a business,
whether or not InventoryOS has it yet, and retains its own brand, customer experience, business logic
and requirements. No shared repository, database, frontend or deployment is assumed.

## 1. What JuJu is

| | Fact | Source |
|---|---|---|
| Business | One juice bar ("JuJu"), NYC; pressed and blended juices | `README.md`, `docs/mvp/JuJu_Line_Setup_Checklist.md` |
| Platform | Two separate websites on one database: **customer site** (`apps/customer`, marketing, menu, ordering, rewards) and **staff site** (`apps/staff`, orders board, kitchen display, inventory, analytics, team). Built and deployed separately; shared business rules in `src/` | `README.md` |
| Stack | Next.js (TypeScript) ×2, Supabase (Postgres, Auth, RLS, Realtime), Vercel (planned), Square (payments), Twilio + Resend (not configured) | `README.md`, `package.json` |
| Tenancy | **None.** "One storefront: no store ids, no multi-tenancy"; `store_settings` is a singleton | `README.md` §Product decisions; `20261031000100_menu.sql` |
| State | Built locally; **NO-GO** for launch (129 of 290 checklist items PASS). Remaining blockers are mostly non-code: hosting, backups, legal pages, real-money test, food-safety review. CI passes on GitHub | `PRE_LAUNCH_CHECKLIST.md` |
| Data entry state | Recipes untasted; press yields, opening stock and prices **not entered** | `PRE_LAUNCH_CHECKLIST.md` R17 |

## 2. JuJu workflows

### 2.1 Brand and product catalog
- Menu sections in the database: Signature, Classic, Seasonal, Blended, Build, Concord, Winter
  (`products.section` check). Recipe sheet: 15 drinks across Signature, Classics, Fall seasonal,
  Blended, Fall limited, Winter drop (`docs/recipes/juju-recipes.json`).
- Methods: **Pressed** or **Blended**. Cup sizes 12 / 16 / 24 oz (24 oz blended only in the recipe
  book's regular volumes).
- **Build Your Own**: bases (e.g. Orange, Green apple, Carrot) × size, plus add-ins (Ginger, Lemon,
  Turmeric, …) and toppings, sugar pumps (free pumps then priced), four ice levels.
- **Drops**: limited menus with pre-order and member early access (`drops.member_opens_at`).
- Product art and customer-facing ingredient labels are brand data, separate from stock items
  (`products.ingredient_labels`, `art`).

### 2.2 Customer website and ordering
- Guest checkout plus optional free account (punch-card rewards: 8 punches → free drink).
- Pay by card (Square Web Payments; card data stays in Square's iframe) or pay at pickup
  (≤ 8 drinks; unpaid-order limits per IP/phone/email/account).
- Pickup slots: 15-minute buckets, 7 drinks capacity, prep 4 min + 2 per extra drink; ASAP or
  scheduled; capacity checked inside a per-slot DB lock; unpaid card orders hold 10 minutes.
- Order statuses: `pending_payment → placed → preparing → ready → picked_up`, plus `cancelled`,
  `expired` (`src/domain/orders.ts`). Orders snapshot items and prices.
- Promotion codes, featured deal, first-visit offer (one offer per order), favorites/order again,
  Lab Friday votes, contact tab. Customer data export and deletion.

### 2.3 Employee platform and kitchen
- `/orders` counter board (New, Making, Ready; scheduled orders; cancel, refund, pause ordering);
  `/kitchen` mounted display (size, ice, pumps, add-ins, ingredients, cup name, most urgent first);
  queue ordered by start-by time; scheduled orders join 10 minutes before start.
- Line: New → Making → Ready → Picked up, one tap each; one step back allowed; pickup undo 30 min.
- **Walk-ins**: a Square POS sale appears as a ticket seconds later (via `payment.*` webhook →
  retrieve Square order → match item names/variation size to the JuJu menu); counts against pickup
  capacity; full POS refund removes the ticket (`src/server/walkins.ts`).
- Live alerts bar on every staff screen: order late, batch expiring today/tomorrow, ingredient low
  (everyone); job stopped, refund failed, payment unsettled, message not sent (managers/owners).
  Staff alerts carry no amounts. `/status` for background failures.
- `/team` (owner): add people with a temporary password shown once (no invite email yet), change
  roles, remove access, reset passwords. Optional TOTP two-step sign-in. Last owner protected.

### 2.4 Ingredients, recipes and quantities
- `ingredients`: base unit g / ml / each, display unit, low-stock level, **press yield (mL per lb)**,
  latest cost per base unit, one supplier, active (`20261031000600_inventory.sql`).
- `recipe_lines` by kind: `product` × size, `base` × size (BYO), `modifier` (one portion at every
  size/ice), `pump`. Quantities stored as entered (e.g. 120 mL) so a new yield test corrects every
  recipe at once.
- **Ice scaling**: recipes are written for Regular; No ice 130 %, Light 115 %, Extra 85 %
  (`store_settings.ice_factors`). Add-ins/pumps don't scale. POS sales infer ice from typed
  modifiers ("Light ice"); otherwise Regular (`src/domain/inventory.ts` `iceLevelOf`).
- Pours rounded to measurable steps (2.5/5 mL; 0.1/0.5/5 g) with the largest pour absorbing the
  difference (`roundPours`).
- Units: g, kg, oz, lb, ml, L, fl oz, each; mL ↔ weight only via press yield (`src/domain/units.ts`).
- Recipes are authored in a JSON recipe book (shares per drink, regular mL per method/size) and
  loaded with `npm run recipes:load`, which **replaces** recipes it names; UI edits on
  `/inventory/recipes` are overwritten by the next load. "Start from…" scales one size to another.

### 2.5 Production, batches, shelf life, expiration
- **Prepared items** (ginger juice, turmeric juice) are ordinary ingredients with a prep recipe
  (yield, shelf life days 1–60) (`20261101000100_batches.sql`).
- `make_batch`: one transaction posts `prep_out` for inputs and `prep_in` for the output at input
  cost (cost null if any input unpriced) and returns the use-by date to write on the container.
- `undo_batch`: within 30 minutes, **deletes** the batch; its movements are removed by cascade.
- `write_off_expired_batches` (scheduled every 5 min with reconcile): after retrying missed sales
  deductions, writes off the inferred remaining of batches past use-by as `waste`, reason
  `Expired`, actor `Automatic`; assumes oldest-first use.
- Raw deliveries: `expires_on` stored on the receive movement; remaining per delivery is
  **inferred** FIFO (`remainingLots`) — "stock isn't tracked per delivery". Expiring deliveries
  alert; they are not auto-written off.
- Shelf life of 2 days for ginger/turmeric juice is an unverified assumption pending NYC DOHMH
  review (`PRE_LAUNCH_CHECKLIST.md` R10).

### 2.6 Inventory
- **Ledger**: `inventory_movements` kinds `receive`, `sale`, `waste`, `count`, `prep_in`,
  `prep_out`; signed base-unit quantity; `unit_cost_cents` snapshot; order/supplier/batch links;
  `created_by` + name; DB sign checks. **On-hand = sum of movements** (`ingredient_stock` view).
- Sales deduction **at Ready**, once per order (`orders.stock_deducted_at` guard in
  `record_order_usage`); gaps (no recipe, unconvertible unit) recorded on the order
  (`stock_gaps`) and shown as "Sold without a recipe". Failed deduction never stops the line;
  reconcile retries.
- Receiving: per ingredient (quantity, total cost, supplier, expiry, note) via `receive_stock`;
  updates latest cost; reports old → new cost and affected drinks.
- Count: `record_count` posts the variance (counted − on-hand) under a row lock.
- Waste: reasons Spoiled, Expired, Remake, Spilled, Other; cost snapshot; actor
  (`apps/staff/app/inventory/actions.ts`). Remakes logged as waste.
- Stock may go negative; the screen asks for a count.
- Refunds/cancellations do **not** restore stock.

### 2.7 Reporting
Sales (net of discounts/refunds), products, ingredients (margin from actual deducted cost, only
orders with complete recipes), waste incl. "found short at counts", team prep time, forecast
(average of last four same weekdays), **what to buy** (expected use + 15 % + low-stock level − on
hand), **what to make** (prepared items in half-batch steps within shelf life), batches report,
segments (guest / account / walk-in), CSV daily sales (`src/domain/analytics.ts`, `README.md`).

### 2.8 Integrations
- **Square**: single merchant via **personal access token + location id** in env (no OAuth);
  card payments and refunds; webhooks `payment.created/updated`, `refund.created/updated`;
  reconcile job catches missed events. Square holds **payments tagged with the order number, not
  itemized sales** — itemization deliberately deferred to avoid tax-rounding disagreement.
- Twilio SMS and Resend email: built, not configured. Supabase Realtime broadcast for live screens.

### 2.9 Business-specific requirements (JuJu-only)
NYC raw-juice labeling and storage rules; allergen information; pickup-capacity model; drink
customization (size, base, add-ins, toppings, pumps, ice); line station layout; Lab Friday; punch
rewards; member early access to drops.

## 3. Capability matrix

Legend — **JuJu status:** ✅ supports · 🟡 partial · ❌ lacks · ➖ not applicable.
**Variation:** JuJu needs a brand/business-specific variation of the InventoryOS capability.
**Share:** the capability should be one shared implementation/spec with InventoryOS.
**Exclusive:** stays JuJu-only. **StockPulse** column (context for Phase 2, from
[audit 02](../audit/02-feature-inventory.md)): A verified · B unverified ·
C partial · D missing.

| InventoryOS capability | JuJu status | Evidence / gap | Variation | Share | Exclusive | StockPulse |
|---|---|---|---|---|---|---|
| Command center dashboard | 🟡 | Alerts bar + `/status` + analytics; no single "needs attention" view | No | Yes | No | A (health score) |
| Item catalog (CRUD, types, status) | 🟡 | Ingredients CRUD, active flag, prepared items; **no categories, barcodes, storage areas** | No | Yes | No | C (no edit) |
| Units, packages, conversions | 🟡 | 8 units + press yield; **no packages/cases** | Yes — press yield as a conversion | Yes | No | A |
| Append-only ledger, balance = Σ movements | ✅ | `inventory_movements`; cost snapshot; actor. Gaps: no location, no idempotency key column, **undo deletes movements**, no reversal type | No | Yes | No | **D (no ledger, C6)** |
| Manual adjustments with reason | ❌ | Only receive/count/waste/batch | No | Yes | No | D |
| Concurrency safety | ✅ | Row locks in count/receive/batch; conditional order transitions | No | Yes | No | D (C5) |
| Negative-stock handling | ✅ | Allowed; asks for a count | No | Yes | No | — |
| Batch entity for deliveries | 🟡 | Expiry on receipt; remaining inferred FIFO; no lot codes | No | Yes | No | A |
| Prepared/production batches | ✅ | `make_batch`, shelf life, use-by label, 30-min undo | No | Yes | No | E (none) |
| FEFO allocation | 🟡 | Oldest-first assumption only | No | Yes | No | A |
| Expiry alerts | ✅ | Today/tomorrow batch alert; expiring deliveries list | No | Yes | No | A/B |
| Expired-stock accountability | ✅ (batches) / 🟡 (deliveries) | Auto write-off as `waste/Expired` for batches only | **Yes — auto-disposal policy on** | Yes | No | D (hard delete, C9) |
| Supplier records | 🟡 | Contact list; one supplier per ingredient | No | Yes | No | C (free text) |
| Supplier items, price history | 🟡 | Cost per receipt movement; price-change notice | No | Yes | No | C |
| Purchase orders | ❌ | "No purchase orders, so it can't subtract what's on its way" | No | Yes | No | E (not built) |
| Receiving document (multi-line, 4-quantity) | 🟡 | Per-ingredient receive; no document, no discrepancies | No | Yes | No | C |
| Invoice upload / assisted extraction | ❌ | — | No | Yes | No | A/B (Claude vision) |
| Waste logging | ✅ | Reasons, actor, cost. Gaps: no batch choice, no product-level waste, no void window | Yes — JuJu reason list | Yes | No | **D (C7)** |
| Recipes per size/variation | ✅ | product × size; BYO base × size | Yes — BYO base model | Yes | No | C (no UI, P1) |
| Modifier effects | ✅ | Add-ins/toppings/pumps (add); ice (scale) | **Yes — ice levels as scale modifiers; pumps as add modifier** | Yes | No | D |
| Prepared items as ingredients | ✅ | Ginger/turmeric juice in drink recipes | No | Yes | No | D |
| Recipe versioning | ❌ | Lines overwritten; `recipes:load` replaces; cost history preserved by snapshots only | No | Yes | No | D (DB9) |
| Recipe costing, margin | ✅ | Recipe cost from latest prices; realized margin from snapshots | No | Yes | No | E |
| Recipe authoring from a recipe book (shares → mL) | ✅ | `juju-recipes.json`, `recipeBook.ts` | **Yes — JuJu's share-based authoring** | Import format only (RCP-FR-15) | Authoring method: Yes | — |
| Pour rounding to measurable steps | ✅ | `roundPours` | Yes | Candidate (R2) | — | — |
| Production yield variance | ❌ | Yield is expected only | No | Yes | No | — |
| Sales ingestion → consumption | ✅ (own orders) / 🟡 (POS) | Own orders deduct at Ready; POS walk-ins via payment → order → name match | **Yes — trigger "Ready" and JuJu order source** | Yes (via generic import) | Order capture: Yes | C (daily aggregates, C2/C3) |
| Square catalog sync / OAuth | ❌ | Personal token; menu owned by JuJu | No (JuJu owns its menu) | — | — | A/B |
| Refund handling | ✅ | Records refunds; never restores stock | No | Yes | No | C (ignored) |
| Physical counts | 🟡 | Single-item count → variance; no sessions, reasons or review | No | Yes | No | C |
| Exceptions | 🟡 | "Sold without a recipe", "Batch probably not recorded", status page; no lifecycle | No | Yes | No | C |
| Reorder suggestions | ✅ | What to buy / what to make | No | Yes | No | A |
| Demand forecast | ✅ | Weekday average | No | Yes (R2) | No | F |
| Analytics (inventory side) | ✅ | Ingredients, waste, found short, batches | No | Yes | No | A/E |
| Analytics (sales, segments, team) | ✅ | Guest/account/walk-in, prep time | — | — | **Yes** | — |
| Roles and permissions | ✅ | staff/manager/owner; RLS; costs hidden from staff in DB | No | Yes (model) | No | D (P5) |
| Staff invitations by email | ❌ | Temp password shown once | No | Yes | No | D |
| MFA | ✅ | Optional TOTP | No | Yes | No | D |
| Audit history (non-movement) | 🟡 | Actor on movements/orders; no settings audit | No | Yes | No | C |
| Multi-tenancy, billing, onboarding | ➖ | Single business; not needed for JuJu | — | — | — | A (billing) |
| Customer website, ordering, payments, pickup capacity, loyalty, promotions, drops, Lab Friday, kitchen display, order notifications | ✅ | — | — | **No** | **Yes** | — |

## 3a. Capability matrix — shared vs JuJu-specific

| Capability | Needed by JuJu? | Relevant to InventoryOS? | Shared domain capability? | JuJu-specific implementation? | Customer-facing or internal? | Dependencies | Notes |
|---|---|---|---|---|---|---|---|
| Customer-facing website | Yes (has it) | No | No | Yes — entirely JuJu's | Customer-facing | Menu, ordering | Never part of InventoryOS |
| Menu (display, art, sections, drops) | Yes | Partly (product catalog only) | Product/variant data could be shared | Yes — presentation, drops, art, pricing display | Customer-facing | Products | InventoryOS stores products for recipes; JuJu owns its menu presentation |
| Products and variants | Yes | Yes | **Yes** (product, variation, modifier, scale/add effects) | Yes — BYO bases, pumps, ice levels as configuration | Internal + customer-facing | Recipes | JuJu's model maps to InventoryOS's generic modifiers |
| Recipes | Yes | Yes | **Yes** (versioned recipes, prepared items, yields) | Yes — share-based recipe-book authoring, pour rounding | Internal | Ingredients, units | InventoryOS adds versioning JuJu lacks |
| Ingredients | Yes | Yes | **Yes** (items, units, press-yield conversions) | No | Internal | Units | — |
| Inventory (ledger, counts, adjustments) | Yes | Yes | **Yes** | No | Internal | Ingredients | JuJu already has a ledger; InventoryOS adds reversals, locations, idempotency keys |
| Batch production | Yes | Yes | **Yes** (production orders, output batches) | Labels/use-by wording per NYC rules | Internal | Recipes, inventory | — |
| Expiration | Yes | Yes | **Yes** (batches, FEFO, alerts, disposal policy) | Auto-disposal policy turned on | Internal | Batches | Shelf lives pending DOHMH review |
| Receiving | Yes | Yes | **Yes** (4-quantity receiving, invoices) | No | Internal | Suppliers, items | JuJu lacks invoices and discrepancies |
| Suppliers | Yes | Yes | **Yes** | No | Internal | — | JuJu has a contact list only |
| Waste | Yes | Yes | **Yes** | JuJu reason list | Internal | Inventory | — |
| Employees (team, roles, MFA) | Yes | Yes | Role model shared conceptually | JuJu's own auth and staff accounts | Internal | Auth | Separate identity systems (C-42: parity by specification until R1) |
| Kitchen workflows (order board, kitchen display, queue, pickup capacity) | Yes | No (InventoryOS is not a kitchen display) | No | Yes — entirely JuJu's | Internal | Orders | Would only be relevant to a future native POS |
| Orders and sales (online ordering, payments, walk-ins, refunds, loyalty) | Yes | As a **sales source** only | Sales transaction model (via sales API) | Yes — ordering, payments, loyalty stay JuJu's | Customer-facing + internal | Payments (Square) | JuJu would send order lines at "Ready" through POS-FR-16 |
| Reporting (inventory side: usage, waste, margins, purchasing) | Yes | Yes | **Yes** | No | Internal | Ledger | — |
| Reporting (sales segments, team prep time, Lab votes) | Yes | No | No | Yes | Internal | Orders | JuJu-only |
| Business administration (store settings, hours, tax) | Yes | Partly (location, time zone) | Location settings concept | Yes — tax, hours, pickup rules | Internal | — | JuJu's tax and pickup settings stay JuJu's |

**Common domain functionality** (candidate for one shared implementation): items and units, ledger,
batches and expiry, recipes and versions, production, receiving and suppliers, waste, counts and
reconciliation, inventory reporting, reorder logic.
**JuJu-specific business functionality**: customer website and menu presentation, ordering and
payments, pickup capacity and queue, kitchen display, loyalty and promotions, drops and Lab votes,
NYC labeling rules, JuJu's recipe-book authoring.

## 4. What InventoryOS should learn from JuJu

JuJu is ahead of the existing inventory app on the inventory domain itself. These JuJu designs are
adopted into the InventoryOS spec:

| JuJu design | InventoryOS requirement |
|---|---|
| On-hand = Σ movements, cost snapshot per movement, actor on every movement | INV-FR-15..17, DR-01..03, DR-19 |
| Counts post a variance movement under a lock | REC-FR-07, DR-12, DR-07 |
| Deduction once per order, gaps recorded, never blocks the line, retried | WF-15, POS-FR-07, DR-08 |
| Recipes per size + modifiers + ice scaling | RCP-FR-02/03 (scale modifiers) |
| Press yield = explicit item-specific cross-dimension conversion | INV-FR-05, DR-11 |
| Prepared items are ordinary ingredients; batch = inputs out, output in at input cost | PRD-FR-01/05/06, DR-18 |
| Auto write-off of expired batches as an explicit system waste event | BAT-FR-11 (optional policy) |
| Costs withheld from staff at the data layer | §4 of [USER_ROLES_AND_PERMISSIONS](USER_ROLES_AND_PERMISSIONS.md) |
| "What to buy" accounts for prepared batches already made | ROR-FR-06 |
| Margin labelled by completeness ("orders whose recipes were complete") | U-06, ANL-FR-12 |

Where InventoryOS deliberately differs from JuJu:

| JuJu behaviour | InventoryOS rule | Why |
|---|---|---|
| `undo_batch` deletes the batch and its movements | Undo = reversal movements (DR-02) | Brief: preserve history |
| Recipes overwritten in place | Versioned (DR-16) | Brief: no retroactive change |
| Expired batches always auto-written off | Optional policy, default off; otherwise explicit decision (BAT-FR-11) | Brief: explicit accounting; other businesses may repurpose |
| Delivery lots inferred | Real batches per receiving line for batch-tracked items | Brief: batch-level tracking |
| No location on movements | Location on every movement | Multi-location readiness |

## 5. What JuJu would gain from InventoryOS

Invoice capture and assisted extraction; multi-line receiving with discrepancies; purchase orders
and on-order quantities; real delivery batches with FEFO; manual adjustments; count sessions with
reasons and review; recipe versioning; product-level waste and batch-specific waste; exception
lifecycle and a command center; supplier items and price history; email invitations; settings
audit; production actual-yield variance.

**JuJu must keep** its customer site, ordering, payments, pickup capacity, kitchen display, line
workflow, loyalty, promotions, drops, Lab Friday, notifications, menu/brand data and JuJu-specific
recipe authoring [CONFIRMED].

## 6. Possible future relationships (no commitment)

Decided 2026-11-04 (C-42): option A, parity by specification, until InventoryOS R1 ships; revisit B after R1.1. Because InventoryOS is built on the Python codebase (C-25), options C and
D are ruled out unless JuJu is rewritten; A and B remain:

| Option | Description | Benefits | Costs / risks |
|---|---|---|---|
| **A. Parity by specification** | JuJu and InventoryOS stay independent; JuJu implements InventoryOS rules in its own code when it needs them | Zero coupling; JuJu launch unaffected | Two implementations of the same logic in two languages — what the brief wants to avoid long-term |
| **B. JuJu as an InventoryOS tenant for back-of-house** | JuJu keeps customer site, orders board and kitchen; sends order lines to InventoryOS at "Ready" through the generic sales source (POS-FR-16); inventory screens move to or are embedded from InventoryOS | One implementation; JuJu dogfoods the product daily | JuJu staff use a second app or an integration surface; network dependency must never block the line (async, retried); migrate JuJu's ledger history; InventoryOS must support JuJu's modifier model (spec does: scale/add modifiers, prepared items) |
| **C. Shared domain library** — ruled out by C-25 | Same inventory code imported by both | True single implementation | **Blocked by language mismatch** (existing inventory app is Python; JuJu is TypeScript) unless InventoryOS is built in TypeScript or one side is rewritten |
| **D. InventoryOS grown from JuJu's codebase** — ruled out by C-25 | Generalize JuJu's inventory schema to multi-tenant; JuJu becomes tenant #1; its customer site stays a separate app | JuJu's inventory model is closest to this spec | Retrofitting tenancy into a single-store schema; rebuild SaaS shell, Square OAuth/catalog sync, invoice extraction and Stripe that the existing app already has |

Constraints any option must respect:
1. JuJu's line is never blocked by InventoryOS availability ("stock never stops the line").
2. JuJu's online orders are not Square orders — a Square adapter alone would miss them; JuJu
   needs a first-party order source.
3. JuJu deducts at **Ready**, not at payment — InventoryOS must support a per-source consumption
   trigger (PD-16).
4. JuJu's Square account is used for payments only (tagged, not itemized); walk-in matching is by
   item name. Neither should be changed by InventoryOS work.
5. No change to JuJu's repository, schema or deployment is part of Phase 1.
6. In operating-mode terms JuJu is neither a Square-connected (Complete) business nor manual
   Standalone: its own ordering platform generates transactions, so it would connect through the
   sales API (POS-FR-16), not the Square adapter.
7. JuJu is the closest existing reference for what an **InventoryOS POS** would involve (order
   capture, kitchen tickets, payment through a processor, refunds, capacity). It is evidence for
   PD-35 only; JuJu's customer experience is not moved into InventoryOS.
8. Repository boundary: no repository merge is recommended — there is no evidence it would help,
   and the two codebases use different languages.

## 7. Files inspected (JuJu)

`README.md`; `PRE_LAUNCH_CHECKLIST.md` (results, release gates, open issues); `docs/PRODUCTION_SETUP.md`
(Later list); `docs/mvp/JuJu_Line_Setup_Checklist.md` (station 03); `docs/recipes/juju-recipes.json`;
`supabase/migrations/20261031000100_menu.sql`, `…000300_orders.sql` (orders table),
`…000500_walk_ins.sql` (header), `…000600_inventory.sql`, `20261101000100_batches.sql`, table and
function index of all migrations; `supabase/seed.sql` (sizes, drops, modifiers, bases);
`src/domain/inventory.ts`, `src/domain/units.ts`, export signatures of `batches.ts`,
`analytics.ts`, `orders.ts`, `walkin.ts`, `alerts.ts`, `line.ts`, `pickup.ts`, `recipeBook.ts`;
`src/server/inventory.ts`, `src/server/walkins.ts`, `src/server/payments.ts` (advance, reconcile),
`src/server/square.ts` (config, function list); `apps/staff/app/inventory/actions.ts` (waste).
Not inspected: `docs/mvp/JuJu Juice Bar.html`, UI components, e2e tests.
