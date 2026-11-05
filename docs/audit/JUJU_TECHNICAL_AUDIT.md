# JuJu Juice: Technical Audit (InventoryOS Phase 2)

## 1. Header

| | |
|---|---|
| Date | 2026-11-04 |
| Repository audited | `C:\Users\oscar\.vscode\JuJu` (branch `main`, working tree clean) |
| Commit audited | `ae1e702fed05ea2e2b38a0308928699ec8ff6e76` "Mark R1 fixed now that main is on GitHub" (2026-11-04 17:17 -0500) |
| Scope | Architecture, database, features, integrations, auth, tests/CI, reuse for InventoryOS, check of `docs/product/JUJU_REFERENCE_AND_PARITY.md` |
| Read-only | Yes. The only file written is this one. No package installs, migrations, Docker, Supabase, dev servers, tunnels, Playwright or git writes. |
| Secrets | `.env.local` was read for variable names only. No values appear here. Env vars are named, never quoted. |
| How verified | Read source, migrations, tests, CI config and repo docs (`README.md`, `PRE_LAUNCH_CHECKLIST.md`, `docs/PRODUCTION_SETUP.md`). Counts via grep/ls. **No tests were run** (see 7.3). |
| Labels | **Verified fact** (read in code), **Inference**, **Risk**, **Recommendation**, **Unknown** |
| Context | JuJu is a real NYC juice bar's own platform and a reference environment. It is not InventoryOS and is not deployed to production. |

Status vocabulary used in the feature table: Implemented and verified · Implemented but unverified · Partially implemented · Mocked or placeholder · Missing · Not applicable · Unknown due to insufficient evidence.

**Rule applied to "verified":** because nothing was run in this audit, a feature is "Implemented and verified" only if a test exercising it exists AND the repo records a passing CI run (`PRE_LAUNCH_CHECKLIST.md` row CI, run 4 on commit `34ea7ec`, plus run 6 on `main`). Those CI results are **documented, not re-run**. Code after `34ea7ec` is docs-only: **Verified fact** — `git diff --stat 34ea7ec..HEAD` shows one file changed, `PRE_LAUNCH_CHECKLIST.md` (checked by the lead auditor after this document was drafted).

## 2. Architecture

### 2.1 Summary

| Item | Finding | Evidence |
|---|---|---|
| Apps | Two Next.js apps: `apps/customer` (port 3000) and `apps/staff` (port 3001). Separate builds, deploys, cookies. | `apps/customer/package.json`, `apps/staff/package.json` (`next dev -p 3001`), `README.md` |
| Shared code | One root `src/` used by both via the `@/` alias: `src/domain` (pure rules + unit tests), `src/server` (DB/Square/notification logic), `src/lib` (Supabase clients, CSP). | `vitest.config.ts` alias, `tsconfig.json`, `ls src` |
| Language | TypeScript (`typescript ^7.0.2`), ESM (`"type": "module"`) | `package.json` |
| Framework versions (declared ranges) | `next ^16.4.3`, `react ^19.3.1`, `zod ^4.6.7`, `@supabase/supabase-js ^2.118.0`, `@supabase/ssr ^0.12.7`, `vitest ^5.0.5`, `vite ^8.3.5`, `@playwright/test ^1.64.1`, `supabase` CLI `^2.122.0`, `tsx ^4.23.15` | `package.json` |
| Package manager | npm workspaces (`"workspaces": ["apps/*"]`), `package-lock.json` | `package.json` |
| Node | `^24.22.2` required; `.node-version` = `24.22.2` | `package.json`, `.node-version` |
| Size | 231 tracked files, 25 commits; `src/server` + `src/domain` about 5,300 lines; 17 migrations (about 1,680 SQL lines) | `git ls-files`, `wc -l` |
| Database | Supabase local stack: Postgres, Auth, Realtime. Storage is excluded from `db:start` (`-x storage-api,...`); no `.storage` calls in source. | `package.json` `db:start`; grep |
| Realtime | Broadcast triggers (migration `20261101000600_live_broadcast.sql`) feed `apps/staff/app/LiveBoard.tsx`, `AlertTray.tsx`, `staffChannel.ts`, customer `AutoRefresh.tsx`. | files named |
| Mutations | Next.js **server actions** for staff screens (`apps/staff/app/*/actions.ts`: inventory, menu, orders, team, customers, account) and customer account. Stock-ledger writes go through the **service-role** client calling `SECURITY DEFINER` RPCs. | `apps/staff/app/inventory/actions.ts` |
| API routes (11) | Customer: `/api/account/{claim,export,me}`, `/api/cart/quote`, `/api/health`, `/api/lab/vote`, `/api/orders`, `/api/orders/lookup`, `/api/pickup/slots`. Staff: `/api/webhooks/square`, `/api/cron/reconcile`, `/api/health`, plus `/alerts`, `/board`, `/analytics/export` route handlers. | `find apps -name route.ts` |
| Background jobs | One cron: `GET /api/cron/reconcile` every 5 minutes, protected by `CRON_SECRET` (Bearer, timing-safe compare). Configured as a Vercel cron. No queue, no worker process. | `apps/staff/vercel.json`, `apps/staff/app/api/cron/reconcile/route.ts` |
| Reconcile does | Resolves unsettled Square payments, retries stalled refunds, expires unpaid order holds, retries notifications, retries missed stock deductions, auto-writes-off expired batches, nulls order IPs after 30 days, stamps `store_settings.last_reconcile_at`. | `src/server/payments.ts` `reconcile()` |
| Deployment config | Only `apps/staff/vercel.json` (cron). No customer `vercel.json`, no Dockerfile, no IaC. Production hosting not provisioned. | `ls`; checklist R2 |
| CI | `.github/workflows/ci.yml` (GitHub Actions, Ubuntu). Details in 7.2. | file |

### 2.2 Diagram

```
 Browser (customer) --> apps/customer :3000 --\                    +--------------------------+
   storefront, quote, orders API               \                   | Supabase (local only)    |
                                                +--> src/ ------>  | Postgres 29 tables, RLS  |
 Browser (staff/kitchen) --> apps/staff :3001 -/   domain (pure)   | Auth (+TOTP), Realtime   |
   server actions, /kitchen, /inventory          server (DB, Square)| RPCs: SECURITY DEFINER  |
        ^                                         lib (clients)     +--------------------------+
        |  Realtime broadcast                          |
        |                                              +--> Square API (sandbox; payments, refunds, orders)
 Square --webhook--> /api/webhooks/square (HMAC)       +--> Twilio SMS / Resend email (built, unconfigured)
 Vercel Cron (5 min) --> /api/cron/reconcile (CRON_SECRET)
```

### 2.3 Environment variable names by purpose (names only)

| Purpose | Names (from `.env.example`; `.env.local` has the same set) |
|---|---|
| Supabase | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` |
| Square | `SQUARE_ENVIRONMENT`, `SQUARE_APPLICATION_ID`, `SQUARE_ACCESS_TOKEN`, `SQUARE_LOCATION_ID`, `SQUARE_WEBHOOK_SIGNATURE_KEY`, `SQUARE_WEBHOOK_URL`, `SQUARE_WALK_IN_PRODUCTS` (optional, in `.env.example`; not in `.env.local` name list) |
| SMS | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` |
| Email | `RESEND_API_KEY`, `RESEND_FROM_EMAIL` |
| App / jobs | `APP_URL`, `CRON_SECRET` |
| Dev only | `DEV_SEED_PASSWORD`, `NEXT_PUBLIC_COOKIE_INSECURE` |
| Supabase config.toml refs | `SUPABASE_AUTH_SMS_TWILIO_AUTH_TOKEN` (template line in `supabase/config.toml:296`) |

**Verified fact:** `.env.local` is not tracked (`git ls-files` shows only `.env.example`). Square defaults to `sandbox` unless `SQUARE_ENVIRONMENT` is exactly `production` (`src/server/square.ts` `squareConfig`).

### 2.4 Scripts

| Script | Purpose |
|---|---|
| `dev:customer`, `dev:staff`, `build`, `start:*` | App lifecycle |
| `typecheck` (`tsc --noEmit`), `test` (`vitest run src`), `test:db` (`vitest run tests`), `test:e2e` (`playwright test`) | Checks |
| `db:start`, `db:stop`, `db:reset` (+ `scripts/seed-dev-users.ts`) | Local DB |
| `recipes:sheet`, `recipes:load` | Build the recipe sheet HTML; load `docs/recipes/juju-recipes.json` into recipes (replaces named recipes) |
| `square:smoke`, `tunnel` (ngrok, webhook path only) | Sandbox smoke test; webhook tunnel |

## 3. Database

### 3.1 Migrations (Verified fact: 17 files, `supabase/migrations`)

| # | File | Notable content |
|---|---|---|
| 1 | `20261031000100_menu.sql` | `store_settings` singleton (`id boolean ... check (id)`), `products`, `product_sizes`, `modifiers`, `byo_bases`, `drops`, `cup_sizes` |
| 2 | `20261031000200_identity_and_rls.sql` | `employees` (role enum staff/manager/owner), `customers`, `private.employee_role()`, auth trigger, **revokes default grants**, menu RLS |
| 3 | `20261031000300_orders.sql` | `orders`, `order_items`, `payments`, `refunds`, `webhook_events`, `notifications`, `place_order` (advisory lock on slot) |
| 4 | `20261031000400_store_operations.sql` | `place_order` v2, `keep_an_owner` trigger (last-owner guard) |
| 5 | `20261031000500_walk_ins.sql` | `record_walk_in` RPC |
| 6 | `20261031000600_inventory.sql` | `suppliers`, `ingredients`, `recipe_lines`, `inventory_movements`, `ingredient_stock` view, `record_order_usage`, `receive_stock`, `record_count` |
| 7 | `20261031000700_retention.sql` | `promotions`, `loyalty_transactions`, `favorites`, `customer_punches` view, `redeem_punches`, loyalty trigger |
| 8 | `20261031000800_analytics.sql` | reporting support |
| 9 | `20261101000100_batches.sql` | `prep_recipes`, `prep_recipe_lines`, `batches`, `make_batch`, `undo_batch`, `write_off_expired_batches`, `ice_factors`, widens movement kinds |
| 10 | `20261101000200_unpaid_order_limits.sql` | `place_unpaid_order` (per-IP/phone limits, advisory lock) |
| 11 | `20261101000300_health_and_erasure.sql` | `erase_customer`, health support |
| 12 | `20261101000400_two_step_sign_in.sql` | `private.employee_role()` requires `aal2` when a verified TOTP factor exists |
| 13 | `20261101000500_faster_reads.sql` | Indexes, `(select ...)` policy wrapping, `daily_usage`/`daily_demand` |
| 14 | `20261101000600_live_broadcast.sql` | Realtime broadcast triggers |
| 15 | `20261101000700_lab_votes.sql` | `lab_votes` |
| 16 | `20261101000800_staff_alerts.sql` | `alert_acks`, `announce_alerts_changed` trigger on `inventory_movements` |
| 17 | `20261101000900_featured_deals.sql` | featured promotions |

Plus `supabase/seed.sql` (126 lines: store settings, cup sizes, drops, 16 product rows, modifiers, BYO bases, ingredient names with no stock/prices). `supabase/snippets/` exists (not inspected).

### 3.2 Tables by domain (Verified fact: 29 distinct `create table` names, matching checklist row DB)

| Domain | Tables |
|---|---|
| Menu / catalog | `products`, `product_sizes`, `product_toppings`, `modifiers`, `byo_bases`, `byo_base_sizes`, `cup_sizes`, `drops`, `store_settings` |
| Identity | `employees`, `customers` (guests have no auth user) |
| Orders / payments | `orders`, `order_items`, `payments`, `refunds`, `webhook_events`, `notifications` |
| Inventory | `ingredients`, `suppliers`, `recipe_lines`, `inventory_movements`, `prep_recipes`, `prep_recipe_lines`, `batches` |
| Retention | `promotions`, `loyalty_transactions`, `favorites`, `lab_votes` |
| Ops | `alert_acks` |
| Views | `ingredient_stock`, `customer_punches` |

### 3.3 RLS and roles

| Item | Finding | Evidence |
|---|---|---|
| Default posture | Verified fact: `revoke all on all tables in schema public from anon, authenticated` and default privileges revoked, so each table must opt in. | `20261031000200_identity_and_rls.sql` |
| Roles | `employee_role` enum: `staff`, `manager`, `owner`. Helper `private.employee_role()` (SECURITY DEFINER, `search_path=''`, active employees only). | same file |
| Menu | Public read; manager/owner write. | `menu_write`, `menu_read` policies |
| Inventory tables | `staff_read` policy for any active employee; **no write policies for `authenticated`**. All writes via service role. | `20261031000600_inventory.sql` end |
| Cost hiding | Column-level grants omit `ingredients.cost_cents_per_base` and `inventory_movements.unit_cost_cents` for `authenticated`. Costs reach managers only through the server after `canEditMenu(role)`. | same; `src/server/auth.ts` |
| RPC exposure | Inventory RPCs: `revoke execute ... from public, anon, authenticated; grant ... to service_role`. | all of 6, 9 |
| Tenant concept | **None. Single business.** `store_settings` is a one-row table; no `store_id`/`org_id` column on any table. | migration 1; table list |
| Tests of RLS | `tests/rls.test.ts` (12 `it` blocks) | file |

### 3.4 Inventory ledger design

| Question | Finding | Evidence |
|---|---|---|
| Table | `inventory_movements`: `ingredient_id`, `kind`, signed `quantity` (base units), `unit_cost_cents` snapshot, `order_id`, `supplier_id`, `batch_id`, `expires_on`, `reason`, `note`, `created_by`, `created_by_name`, `created_at`. | `...000600_inventory.sql`, `...000100_batches.sql` |
| Kinds | `receive`, `sale`, `waste`, `count`, `prep_in`, `prep_out`. Sign checks in DB (`receive`, `prep_in` > 0; `sale`, `waste`, `prep_out` < 0; waste requires reason). | `inventory_movements_kind_check`, `_sign_check` |
| Balance | Derived: view `ingredient_stock` = `coalesce(sum(quantity),0)` per ingredient (`security_invoker`). No stored balance column. | migration 6 |
| Append-only? | **Partially. Convention, not enforcement.** No trigger or policy blocks UPDATE/DELETE on `inventory_movements`; service role can do both. `batch_id` is `on delete cascade`, and `undo_batch` does `delete from public.batches`, so a batch's movements are **hard-deleted**. No reversal kind exists. | `...000100_batches.sql`; trigger list contains only `announce_alerts_changed` (insert) for this table |
| Triggers | Only broadcast/alert triggers; no balance triggers. | `grep create trigger` |
| Concurrency | `receive_stock`, `record_count`, `make_batch`, `write_off_expired_batches` take `FOR UPDATE` on the ingredient row. `wasteAction` inserts directly without the lock. | functions; `actions.ts:97-113` |
| Idempotency | Sale deduction is once per order via `orders.stock_deducted_at` guard inside `record_order_usage`. No idempotency-key column on movements. Square webhooks deduped by `webhook_events.event_id`. | migration 6; `payments.ts` |
| Negative stock | Allowed; UI asks for a count. No DB constraint. | `README.md`; no check on sum |
| Location | No location dimension. | schema |

### 3.5 Units, recipes, batches, receiving, counts, waste, purchasing, forecasting

| Topic | Finding | Evidence |
|---|---|---|
| Units | 8 units mapped to 3 base units (g, ml, each): g, kg, oz, lb, ml, L, fl oz, each. `ingredients.base_unit` + `display_unit` constrained pairwise by CHECK. mL to weight only via `press_yield_ml_per_lb`. No packages/cases. | `src/domain/units.ts`; migration 6 |
| Recipe model | `recipe_lines` rows of kind `product` x size, `base` x size (build-your-own), `modifier` (one portion), `pump`. Unique index per (kind, target, size, ingredient). Quantity stored as entered with a unit. | migration 6; `src/domain/inventory.ts` |
| Ice scaling | `store_settings.ice_factors` default `{1.3,1.15,1,0.85}`; only the drink lines scale, add-ins and pumps do not. `roundPours` snaps to measurable steps. | migration 9; `inventory.ts` `scalePours`, `roundPours` |
| Recipe versioning | **None.** Lines are edited in place; `loadRecipeBook` deletes then inserts (`recipeBook.ts:57-61`). History survives only via cost snapshots on movements. | `src/server/recipeBook.ts` |
| Prepared items | `prep_recipes` (yield, `shelf_life_days` 1-60) + `prep_recipe_lines`; the prepared item is an ordinary `ingredients` row. Loop guard `wouldLoop`. | migration 9; `src/domain/batches.ts` |
| Batches | `batches` table (times, quantity, `expires_on`, maker, `written_off_at`, `written_off_quantity`). `make_batch` posts `prep_out` per input and `prep_in` for output at input cost (null if any input unpriced). | migration 9 |
| Expiry | Batches: `write_off_expired_batches` posts `waste`/`Expired`/actor "Automatic", amount **inferred** (oldest-first assumption). Deliveries: `expires_on` on receive movement; `remainingLots` infers FIFO remaining; alert only, no auto write-off. | migration 9; `inventory.ts` `remainingLots`; `src/server/inventory.ts` `expiringSoon` |
| Receiving | `receive_stock` RPC per ingredient (quantity, total cost, supplier, expiry, note); updates latest cost; returns old/new cost. No document, no PO link, no discrepancies, no lot code. | migration 6 |
| Counts | `record_count`: variance = counted - on_hand posted as a `count` movement under row lock. Single ingredient at a time; no sessions or review. | migration 6 |
| Waste | Reasons `Spoiled, Expired, Remake, Spilled, Other`; cost snapshot; actor name. Direct insert. | `actions.ts:14,97` |
| Suppliers | `suppliers` (name, contact, phone, email, notes, active). One `supplier_id` on each ingredient. No supplier-item table, no price history table (price is on movements). | migration 6 |
| Purchase orders | **None.** No table or code. | table list |
| Purchase suggestions | `purchasePlan` in `src/domain/analytics.ts`: expected use x (1 + `PURCHASE_SAFETY` 0.15) + low-stock level - on hand, rounded up; prepared items expanded to raw (`stockAsRaw`, `salesAsRaw`); `batchesToMake` for what-to-make. | `analytics.ts:14,372-449`; `batches.ts` |
| Forecast | `forecast`: average of up to last 4 same weekdays (`FORECAST_WEEKS=4`), needs 2 samples, booked orders set a floor. No seasonality/weather. | `analytics.ts:11-12,350` |
| Lot-level stock | Not tracked per delivery (comment in `inventory.ts` says so). | `inventory.ts` |

## 4. Feature inventory

| Capability | Status | Evidence | Notes (JuJu-specific? limitations) |
|---|---|---|---|
| Customer website | Implemented and verified | `apps/customer/app/page.tsx`, `apps/customer/storefront/Storefront.tsx`; `e2e/customer.spec.ts` (15 tests); CI run 4 | JuJu-only. Single-page storefront app plus `/account`, `/orders`, `/orders/[token]`. Contact details are placeholders (R12, R14). |
| Menu | Implemented and verified | `src/server/menu.ts`; tables `products`, `product_sizes`, `drops`; `tests/rls.test.ts` | JuJu-only presentation (art, sections, drops). 16 seeded products. Menu editable on `/menu` (managers). |
| Product catalog | Implemented and verified | `supabase/seed.sql`; `apps/staff/app/menu/` | Menu items only; ingredients are a separate catalog. |
| Variants / modifiers | Implemented and verified | `product_sizes`, `modifiers`, `byo_bases`, `cup_sizes`; `src/domain/pricing.ts` + `pricing.test.ts` | Sizes 12/16/24 oz, add-ins, toppings, pumps, 4 ice levels. JuJu-specific shape. |
| Recipes | Partially implemented | `recipe_lines`; `apps/staff/app/inventory/recipes/page.tsx`; `tests/inventory.test.ts` | Works, but no versioning, no status/draft, untasted recipes and no press yields entered (R17). |
| Ingredient definitions | Implemented and verified | `ingredients`; `saveIngredientAction`; `inventory.test.ts` | No categories, barcodes, storage areas, packages. |
| Inventory items and balances | Implemented and verified | `ingredient_stock` view; `tests/inventory.test.ts` | Single location. Opening stock not entered in JuJu (R17). |
| Inventory ledger / history | Implemented and verified | `inventory_movements`; `tests/inventory.test.ts`, `tests/batches.test.ts` | Not DB-enforced append-only; batch undo deletes. |
| Adjustments | Missing | No manual adjust kind; only receive/waste/count/batch | Count acts as the correction mechanism. |
| Counts | Partially implemented | `record_count`; `countAction` | Variance movement under lock. No sessions, reasons, approval. |
| Low-stock alerts | Implemented and verified | `isLowStock` (`inventory.ts`); `src/domain/alerts.ts`; `tests/alerts.test.ts` | Per-ingredient `low_stock_at`; no auto reorder. |
| Production and batch records | Implemented and verified | `make_batch`, `batches`; `tests/batches.test.ts` (33), `src/domain/batches.test.ts` | Expected yield only, no actual-yield variance. |
| Expiration handling | Partially implemented | `write_off_expired_batches`; `expiringSoon` | Batches auto-written-off (inferred qty); deliveries inferred FIFO, alert only. No FEFO allocation. |
| Receiving | Partially implemented | `receive_stock`; `receiveAction` | Per ingredient; no document/invoice/PO/discrepancy. |
| Suppliers / purchasing | Partially implemented | `suppliers`, `saveSupplierAction`; `purchasePlan` | Contact list + suggestions. No POs, no on-order quantity, no price-history table. |
| Waste | Implemented and verified | `wasteAction`; `wasteReport`; `inventory.test.ts` | No batch or product-level waste; no void window. |
| Employee workflows | Implemented and verified | `/orders`, `/inventory`, `/customers`, `/account`, `/team`, `/status`; `e2e/staff.spec.ts` (14) | JuJu-specific roles and screens. |
| Kitchen workflows (ticket/queue) | Implemented and verified | `src/domain/line.ts`, `pickup.ts`; `apps/staff/app/kitchen/`; `LiveBoard.tsx`; `tests/staff-orders.test.ts`, `board.test.ts` | JuJu-only (pickup capacity, fire-ahead, start-by ordering). |
| Orders and sales: online | Implemented and verified (sandbox) | `src/server/orders.ts`, `place_order`; `tests/orders.test.ts` (34), `order-limits.test.ts`, `payments.test.ts` (38) | Card payment e2e skipped in CI (no Square credentials); sandbox smoke is documented only. |
| Orders and sales: POS walk-ins | Implemented but unverified against real POS | `src/server/walkins.ts`, `src/domain/walkin.ts`; `tests/walkins.test.ts` (20) | Tests use synthetic Square payloads. `docs/PRODUCTION_SETUP.md` states a real POS sale "has never been tested". |
| Refunds | Implemented and verified (sandbox) | `refundOrder`, `cancelOrder`, `submitRefund` in `payments.ts`; `payments.test.ts` | Refunds do **not** restore stock (no inventory code in the refund path). Partial refunds keep punches. |
| Reporting / analytics | Implemented and verified | `src/domain/analytics.ts` (471 lines); `analytics.test.ts` (52); `tests/analytics.test.ts`; `/analytics/export` CSV | Sales, products, ingredients, waste, team, forecast, what-to-buy, batches. Segments (guest/account/walk-in) are JuJu-only. |
| Authentication | Implemented and verified | Supabase Auth; `src/server/auth.ts`; `tests/team.test.ts`, `two-step.test.ts` | Email+password. Email confirmation disabled (`config.toml:227`, R20). |
| Authorization / roles | Implemented and verified | enum + RLS; `canEditMenu`; `tests/rls.test.ts` | Three roles, single business. |
| MFA | Implemented and verified | `apps/staff/app/account/TwoStep.tsx`, `actions.ts`; migration 12; `tests/two-step.test.ts` (7) | Optional TOTP (R32). Enforced in DB when a factor is verified. |
| Square: auth model | Implemented (config) | `squareConfig()` in `src/server/square.ts` | **Personal access token + location id from env. No OAuth.** Single merchant. |
| Square: webhooks | Implemented and verified (synthetic) | `apps/staff/app/api/webhooks/square/route.ts`; `payments.test.ts` | Signed delivery through a tunnel verified once on 2026-10-31 (documented). |
| Square: signature verification | Implemented and verified | `verifyWebhookSignature` (HMAC-SHA256 over URL + raw body, `timingSafeEqual`); 503 if key/URL unset, 401 if bad | `square.ts` |
| Square: idempotency | Implemented and verified | `webhook_events.event_id` check-then-record-after-success (`handleSquareEvent`); charge idempotency key; refund key = `refunds.id`; downstream ops conditional | Concurrent duplicate deliveries could both run; downstream handlers are idempotent (**Inference**). |
| Notifications / email | Mocked or placeholder (providers unconfigured) | `src/server/notifications.ts` (Twilio, Resend); `notifications` table | Code is real with retry (max 5) and claim-before-send; with no keys it records `skipped`. No real message ever sent (R11). |
| Deployment | Missing | `apps/staff/vercel.json` only; checklist R2, R4, R7 | No staging/production, backups, monitoring. |
| Suppliers' invoices / OCR | Missing | no code | |
| Multi-tenant / billing | Not applicable | single business | |
| Loyalty, promotions, drops, Lab Friday | Implemented and verified | `loyalty.ts`, `lab.ts`, `tests/loyalty.test.ts` (40), `tests/lab.test.ts`, `tests/deals.test.ts` | JuJu-only; irrelevant to InventoryOS. |
| Customer data export/erasure | Implemented and verified | `erase_customer`; `/api/account/export`; `tests/erasure-and-status.test.ts` | |

## 5. Integrations

| Integration | Finding | Evidence |
|---|---|---|
| Square (payments) | Web Payments SDK token (`sourceId`) charged via `POST /v2/payments`, `autocomplete: true`, `reference_id` = local payment id. API version pinned `2026-09-16`. Retries 3x on 5xx/429; ambiguous outcome returns `unknown` and reconcile resolves. | `square.ts` `createPayment`, `API_VERSION` |
| Square (refunds) | `POST /v2/refunds`, idempotency key = local refund id; reconcile retries stalled refunds. | `submitRefund`, `reconcile` |
| Square (walk-ins) | On a payment with no local record, `recordWalkIn` accepts only `COMPLETED` payments at the configured location whose `application_details.square_product` is in `SQUARE_WALK_IN_PRODUCTS` (default `SQUARE_POS`), retrieves the Square order, matches line items to the JuJu menu by **name** and variation size, inserts via `record_walk_in`. Full POS refund sets the order `cancelled`. | `src/server/walkins.ts`, `src/domain/walkin.ts` |
| Square (catalog / inventory / sales sync) | **None.** JuJu does not read Square catalog or push inventory; Square holds payments, not itemized online sales. | grep: no `/v2/catalog` or inventory calls in `src/server/square.ts` |
| Events subscribed | `payment.created/updated`, `refund.created/updated` per README. Handler does not branch on `event.type`; it acts on `data.object.payment` / `.refund`. | `payments.ts` `handleSquareEvent` |
| Environment | Sandbox only. `npm run square:smoke` passed in sandbox (checklist row SQUARE, documented). Production Square, real charge and real POS sale never run (R9). | `PRE_LAUNCH_CHECKLIST.md` |
| Twilio SMS | Plain `fetch` to Twilio REST; unconfigured; A2P 10DLC registration not started. | `notifications.ts`; README |
| Resend email | Plain `fetch` with `Idempotency-Key`; unconfigured; Supabase auth email only the local catcher (no production SMTP). | `notifications.ts`; `config.toml` |
| Payments (other) | None. | |

## 6. Authentication and authorization

| Topic | Finding | Evidence |
|---|---|---|
| Staff vs customer separation | Two apps, separate cookies; staff pages 404 on the customer site and the reverse; a customer session does nothing on staff. Staff proxy redirects any unauthenticated request to `/login` except webhook, cron and health paths. Both are documented SEP results, **not re-run**. | `apps/staff/proxy.ts`; checklist row SEP |
| Customer identity | Optional Supabase Auth account; `on_auth_user_created` trigger inserts a `customers` row for every auth user (including staff). Guests have no auth user; order pages use a private `public_token`. | migration 2 |
| Staff identity | Supabase Auth user plus `employees` row (`active`, `role`, `must_change_password`). No email invite; owner creates user with a temporary password shown once (`addEmployee`). Reset also removes MFA factors. | `src/server/team.ts` |
| Session checks | `currentEmployee()` calls `getUser()`, redirects to `/login/code` if `nextLevel=aal2` and `currentLevel!=aal2`, and to `/account` if a temp password is set. | `src/server/auth.ts` |
| MFA | TOTP only (`config.toml` `[auth.mfa.totp]` enabled; phone MFA disabled). `private.employee_role()` returns null unless `aal2` when a verified factor exists, so RLS enforces it. Optional, even for owners (R32). Config comment says MFA is a Pro-plan feature. | migration 12; `config.toml:299-308` |
| Roles | `staff` (read stock, run line, log waste/receive/count/batch), `manager` (menu, ingredients, recipes, costs, analytics, promotions), `owner` (team, settings). Last-owner guard trigger `keep_an_owner`. | `canEditMenu`; migration 4 |
| RLS | See 3.3. Writes mostly by service role after server-side role check, so authorization correctness lives in application code for inventory. | `actions.ts` `asManager`, `currentEmployee` |
| Hardening | CSP with per-request nonce, HSTS, frame/nosniff/referrer/permissions headers (`src/lib/security.ts`, `proxy.ts`). Sign-in cookie cannot be HttpOnly (R30). Unpaid-order limits trust `x-real-ip` (R31). | files; checklist |

## 7. Tests and CI

### 7.1 Inventory of tests (Verified fact: counts by grep of `it(`/`test(` at line start; `it.each` rows expand at run time so these undercount)

| Suite | Files | `it`/`test` blocks | Needs |
|---|---|---|---|
| Unit: `src/domain/*.test.ts` (`npm test`) | 16 | 251 | Pure functions; no DB/network (**see 7.3 for the caveat on running**) |
| Database/integration: `tests/*.test.ts` (`npm run test:db`) | 21 | 369 | Local Supabase (Docker), `.env.local`, admin and signed-in clients |
| Browser e2e: `e2e/customer.spec.ts`, `staff.spec.ts` | 2 (+`global-setup.ts`, `helpers.ts`) | 29 | Built apps, local DB, Playwright browsers; projects chromium, iphone (WebKit), android, and firefox in CI only |

### 7.2 CI configuration (`.github/workflows/ci.yml`)

Triggers: push to `main`/`launch-fixes`, pull requests, manual. Single job on `ubuntu-latest` (45 min timeout), concurrency-cancel. Steps in order: checkout, setup-node from `.node-version`, `npm ci`, `typecheck`, `npm test` (unit), raise the local port range then `npm run db:start`, write throwaway `.env.local` from `supabase status`, `db:reset` + `npm run test:db`, `npm run build`, install Chromium/Firefox/WebKit, `db:reset` + `npm run test:e2e`, upload `test-results/` on failure. No deploy step, no lint step, no secrets used (Square card tests skipped).

### 7.3 Results (documented, not re-run)

| Result | Source |
|---|---|
| CI run 4 on commit `34ea7ec`: passed in 25 min: typecheck, 291 unit, 418 database tests, both builds, 107 browser runs passed, 8 skipped (card payments need Square credentials; some flows run in one browser only). | `PRE_LAUNCH_CHECKLIST.md` row CI |
| Local: "709 of 709 passed: 291 unit, 418 against the database" on Node 24.22.2; line coverage rules 99%, server logic 93%, all measured code 61%. | rows TESTS, COV |
| Local e2e: 87 runs, 85 passed, 2 skipped (commit `a45bea6`). | row E2E |
| Checklist cites run 6 on `main` as passing (R1 row). | `PRE_LAUNCH_CHECKLIST.md` |
| Count difference | My grep counts 251 unit and 369 DB blocks vs documented 291 and 418; the gap is plausibly `it.each` expansion (**Inference**, not confirmed). |

**Why tests were not run:** `node -v` returns **v24.15.0**, below the required 24.22.2 (older 24.x aborts, per README and checklist R29). `node_modules` exists, but condition (a) failed, so no tests were run. Not attempted: any DB, e2e or unit run.

**Coverage gaps (Verified fact from file lists):** no unit tests for `src/server/*` (covered only through DB tests); no test of `undo_batch` semantics as a ledger-integrity property beyond `batches.test.ts` (not read in detail); screens are covered by e2e only. Real iPhone/Android/counter tablet untested (R13).

## 8. Classification lists

### 8.1 Implemented
Customer storefront and ordering; menu/variants/modifiers; online ordering with capacity lock and unpaid-order limits; Square card payments, refunds, webhooks, reconcile (sandbox); POS walk-in ingestion (synthetic-tested); kitchen board and line workflow; ingredient ledger with derived balances; receive, count, waste; recipes per size/base/modifier/pump with ice scaling; prepared-item batches with use-by and auto write-off; staff alerts; reporting (sales, ingredients, waste, forecast, what-to-buy); roles, RLS, optional TOTP; loyalty, promotions, drops, Lab Friday; customer export/erasure; CI pipeline.

### 8.2 Partially implemented
Recipes (no versioning); counts (single-item, no sessions); expiry (deliveries inferred, no FEFO); receiving (no document/PO); suppliers (contact list only); notifications (code complete, providers unconfigured); MFA (optional); staff onboarding (temp password, no invite email); waste (no batch/product-level).

### 8.3 Planned but not implemented (from repo docs)
Evidence: `PRE_LAUNCH_CHECKLIST.md` open issues and `docs/PRODUCTION_SETUP.md` "Later". No `TODO`/`FIXME` markers exist in `src`, `apps`, `scripts`, `tests`, `e2e`, `supabase` (grep returned none).

| Item | Source |
|---|---|
| Production/staging environment, domain, SSL | R2 |
| Backups and restore rehearsal | R4 |
| Error reporting, uptime monitoring, off-site alerts | R7 |
| Real Square transaction and real POS sale | R9; `PRODUCTION_SETUP.md` |
| Food-safety review (DOHMH), allergen info, batch shelf life confirmation | R10 |
| Real SMS/email send, reset email provider | R11 |
| Privacy policy, terms, refund policy, FAQ | R3, R14 |
| Real store address/hours | R12 |
| Real-device testing | R13 |
| Tasting, yield tests, opening stock, prices | R17 |
| Email verification at sign-up decision | R20 |
| Vercel Pro for 5-minute cron | R22 |

### 8.4 Missing (not planned in repo docs; compared to InventoryOS scope)
Purchase orders and on-order quantity; invoice upload/extraction; manual adjustment type; ledger reversal events; multi-location; lot codes and FEFO allocation; recipe versions; production actual-yield variance; supplier items and price history table; exception lifecycle; settings audit log; email staff invitations; multi-tenancy/billing; Square OAuth and catalog/inventory sync; categories, barcodes, packages.

### 8.5 JuJu-specific
Customer storefront and brand art; pickup-slot capacity model; kitchen display and queue; loyalty punches, promotions, featured deal, drops with member early access, Lab Friday; NYC raw-juice labeling (`src/domain/batchJuice.ts`: ginger and turmeric, 2-day shelf life); ice levels and pump model; BYO bases; recipe-book authoring in shares (`docs/recipes/juju-recipes.json`, `recipeBook.ts`); walk-in name matching; guest/account/walk-in segments; press yield for fruit.

### 8.6 Potentially reusable by InventoryOS (as concepts/specs; **no code reuse**: TypeScript/Next/Supabase vs Python/FastAPI)

| Concept | Where | Why useful |
|---|---|---|
| Balance = sum of signed movements with cost snapshot and actor | `inventory_movements`, `ingredient_stock` | Core ledger spec; add append-only enforcement and reversals |
| Once-per-order deduction guard + recorded gaps + never block the line + retry job | `record_order_usage`, `orderUsage`, `retryMissedDeductions` | Sales-consumption design |
| Count as variance movement under row lock | `record_count` | Reconciliation pattern |
| Cross-dimension conversion (press yield) | `units.ts` `toBase` | Item-specific conversion rule |
| Prepared items as ordinary items with production transaction | `make_batch`, `prep_recipes` | Production spec |
| Scale modifiers (ice) vs add modifiers (pump), pour rounding | `scalePours`, `roundPours` | Modifier semantics |
| Cost hidden by column grants | `...000600_inventory.sql` grants | Permission pattern |
| Purchase plan and weekday-average forecast with completeness labels | `purchasePlan`, `forecast`, margin on complete-recipe orders | Reorder/forecast spec |
| Webhook handling: verify raw body HMAC, dedupe by event id recorded after success, reconcile job for lost events | `square.ts`, `payments.ts` | Integration pattern for Square adapter |
| Alerts with role-filtered kinds, acknowledgement per key | `src/domain/alerts.ts` | Exceptions/notifications spec |

## 9. Technical observations and risks

| # | Label | Observation | Evidence |
|---|---|---|---|
| 1 | Verified fact | Single-tenant by construction: singleton `store_settings`, no tenant column anywhere, global unique indexes (`ingredients_name_idx`, `suppliers_name_idx`), one Square merchant in env. Retrofitting multi-tenancy would touch every table and policy. | migrations 1, 6 |
| 2 | Risk | Ledger integrity depends on server discipline: service role can UPDATE/DELETE movements; `undo_batch` hard-deletes. The parity doc already flags this as a deliberate InventoryOS difference. | 3.4 |
| 3 | Risk | Authorization for inventory writes is in application code (service role bypasses RLS). A missing role check in an action would not be caught by RLS. `receiveAction`, `wasteAction`, `countAction` allow any active employee by design. | `actions.ts` |
| 4 | Verified fact | Expiry write-off quantity is inferred (oldest-first assumption), not measured. | `write_off_expired_batches` |
| 5 | Risk | Stock never stops the line and can go negative; a recipe gap means stock reads high. By design, but reports depend on recipe completeness. | `orderUsage`, README |
| 6 | Risk | Walk-in matching by item name and variation text; renaming a Square item silently produces "ignored/nothing to make". Never run against a real POS. | `walkin.ts`, `PRODUCTION_SETUP.md` |
| 7 | Risk | Webhook dedupe records the event only after success; concurrent redelivery can double-run handlers (downstream appears idempotent). | `handleSquareEvent` |
| 8 | Risk | Reconcile cron is the only background mechanism; depends on Vercel Pro (R22). Stopped job is surfaced by `/api/health` and an alert. | checklist |
| 9 | Risk | `recipes:load` overwrites UI recipe edits. | `recipeBook.ts:57-61` |
| 10 | Risk | Production readiness: checklist status NO-GO, 129 PASS / 47 PARTIAL / 47 FAIL / 57 NOT YET / 10 N/A of 290. Blockers largely non-code (R2, R3, R4, R7, R9, R10, R11). | `PRE_LAUNCH_CHECKLIST.md` |
| 11 | Risk | Email not verified at sign-up (`enable_confirmations = false`); MFA optional; temp password flow, no invite email. | `config.toml`, R20, R32 |
| 12 | Inference | **Blockers to JuJu consuming InventoryOS as a service:** (a) JuJu deducts at "Ready", not payment; InventoryOS needs per-source triggers. (b) JuJu's online orders are not Square orders. (c) JuJu's line must not depend on a network call; deduction is currently a same-database transaction. (d) Reads of stock, recipes and costs are direct SQL/RLS across `src/server` and staff pages (`ingredient_stock`, `recipe_lines`) and would need an API facade. (e) Two identity systems. (f) Ledger history migration, and mapping JuJu's `recipe_lines` kinds to InventoryOS modifiers. None are technical impossibilities. |
| 13 | Verified fact | Tech debt is modest: 25 commits, consistent layering (pure `domain/`, `server/`, thin actions), 0 TODO markers, strict typecheck per checklist. `database.types.ts` is generated; some RPC args cast (`as number`) because generated types disallow null (`actions.ts:77`). | files |
| 14 | Risk | Node-version fragility: Node 24.15 on Windows crashes scripts (R29); this machine is on 24.15.0 so local runs will fail. | `node -v`; R29 |

## 10. Discrepancies with `docs/product/JUJU_REFERENCE_AND_PARITY.md`

Most claims verified. Differences and unverifiable items:

| # | Claim in parity doc | Finding | Type |
|---|---|---|---|
| 1 | §3 matrix: ledger "Append-only ledger ... ✅" with gap "undo deletes movements" | Correct on the gap, but the ledger is **not DB-enforced append-only** at all (no trigger or policy against UPDATE/DELETE; service role writes). "✅" overstates. | Overstated |
| 2 | §2.5/§3: "Waste ... Gaps: no batch choice" and "Concurrency safety ✅ Row locks in count/receive/batch" | Waste insert takes **no row lock** (direct insert, `actions.ts:97-113`); locks exist in `receive_stock`, `record_count`, `make_batch`, `write_off_expired_batches`. | Imprecise |
| 3 | §2.3: full POS refund "removes the ticket" | Sets order status `cancelled` with reason "Refunded on the Square POS" (`walkins.ts` `cancelRefundedWalkIn`); the row remains. | Imprecise |
| 4 | §2.3: walk-in "via `payment.*` webhook" | Handler is event-type agnostic; any payload with `data.object.payment` or `.refund` is processed. Walk-ins only count `COMPLETED` payments from products in `SQUARE_WALK_IN_PRODUCTS` (default `SQUARE_POS`). Not stated in the doc. | Incomplete |
| 5 | §2.3: walk-ins "appear as a ticket seconds later" | Behavior is true only per design; **never tested against a real POS** (`PRODUCTION_SETUP.md`). Also, a payment older than 60 minutes is recorded as already picked up and deducted immediately (`STALE_AFTER_MIN`). Neither is in the doc. | Unverified / incomplete |
| 6 | §1 State: "129 of 290 checklist items PASS ... CI passes on GitHub" | Matches the checklist; the CI claim is documented, not re-run here. | Verified from docs only |
| 7 | §1 Stack: "Twilio SMS and Resend email (not configured)"; §2.8 "built, not configured" | Consistent with code. The notification code also records `skipped` on the order. | OK |
| 8 | §2.4: "Recipe sheet: 15 drinks" (§2.1) | `docs/recipes/juju-recipes.json` has 15 drinks, 4 bases, 8 extras, 2 prepared; the seeded menu has **16** products (`tests/rls.test.ts` expects 16). Not a contradiction, but the two are not the same list. | Clarification |
| 9 | §2.1: sections "Signature, Classic, Seasonal, Blended, Build, Concord, Winter" | Matches `products.section` check. OK. | OK |
| 10 | §2.3: "Optional TOTP", "Last owner protected" | Verified (`TwoStep.tsx`, migration 12; `keep_an_owner`). Doc omits that MFA is **enforced by RLS** once a factor is verified. | Incomplete |
| 11 | §3 "Square catalog sync / OAuth ❌ Personal token" | Verified: token in env, no catalog calls. | OK |
| 12 | §3 "Purchase orders ❌" | Verified: no table or code. | OK |
| 13 | §3 "Reorder suggestions ✅ ... Demand forecast ✅" | Verified (`purchasePlan`, `forecast`); weekday average needs at least 2 samples, 4 weeks max. Output depends on recipes and counts not yet entered in JuJu. | OK, with caveat |
| 14 | §3 "Batch entity for deliveries 🟡; FEFO 🟡" | Delivery lots are inferred and there is **no FEFO allocation** at all, only an oldest-first assumption; "🟡" is generous for FEFO. | Slightly generous |
| 15 | §3 "Command center dashboard 🟡" | Not assessed beyond doc. Alerts bar, `/status`, `/analytics` exist (files present). | Unverified detail |
| 16 | §1: "Remaining blockers are mostly non-code" | Consistent with checklist R-table. | OK |
| 17 | §7 "Not inspected: e2e tests, UI components" | This audit read only e2e names/counts, not bodies; UI components still largely unread. | Carried over |
| 18 | §3a "Customer-facing website ... Menu partially InventoryOS-relevant" etc. | Design judgments, not code facts. Not verifiable here. | Unverifiable |
| 19 | §6 constraint 2: "JuJu's online orders are not Square orders" | Verified: online card payments are created directly with `POST /v2/payments` (no Square Order object); only POS walk-ins retrieve Square orders. | OK |
| 20 | §2.2: "Pickup slots 15-minute buckets, 7 drinks capacity, prep 4 min + 2 per extra" | `seed.sql:3-5` does not set slot, capacity or prep values (they come from column defaults in migrations not read, or from `src/domain/types.ts` `slotMinutes`); numbers not confirmed. | Unknown |
| 21 | Doc §2.2: "unpaid limits per IP/phone/email/account; ≤ 8 drinks" | Checklist R5 says 5 per address, 3 per phone/email/account, 8 drinks per order, card orders unlimited. Consistent. | OK |

## 11. Limitations of this audit

| # | Limitation |
|---|---|
| 1 | **No tests run.** Node is 24.15.0 (< 24.22.2 required). All pass/fail results are from `PRE_LAUNCH_CHECKLIST.md`, labelled documented, not re-run. |
| 2 | Not executed anywhere: apps, database, migrations, Square, Playwright. Behavior is from reading code. |
| 3 | Not read in detail: UI components (`apps/*/app/**/*.tsx` beyond filenames and a few greps), `src/domain/pricing.ts`, `pickup.ts`, `walkin.ts` internals, most of migrations 3, 5, 7, 8, 11, 13-17 (only function/table/policy names were grepped), `supabase/snippets`, e2e and DB test bodies, `docs/mvp/*`, `scripts/*`. |
| 4 | RLS behavior was inferred from policies and grants, not exercised. The SEP, HDR, CSRF, RACE and FLOOD results are documented-only. |
| 5 | Test counts are grep estimates (251 unit, 369 DB, 29 e2e); documented totals are 291 and 418. |
| 6 | Framework versions are declared ranges from `package.json`; installed versions were not checked. |
| 7 | Commits after `34ea7ec` were later diffed: only `PRE_LAUNCH_CHECKLIST.md` changed, so CI run 4 covers the audited code. |
| 8 | `.env.local` was inspected for names only; whether its Square values are sandbox was not checked, and code defaults to sandbox unless `production` is explicit. |
| 9 | The GitHub CI runs cited were not opened; claims rely on the checklist text. |
