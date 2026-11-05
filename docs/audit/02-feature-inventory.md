# 02 — Feature inventory

Classification:

- **A** existing and verified — passes tests in this audit's runs (externals mocked).
- **B** existing but unverified — code exists, but never exercised against the real external
  system, or has no meaningful test.
- **C** partially implemented.
- **D** missing and required for launch (see [10-product-gaps-and-mvp.md](10-product-gaps-and-mvp.md)).
- **E** post-launch improvement.
- **F** long-term expansion.

"Verified" here means *in this audit's runs* with Square, Stripe, Anthropic and SMTP mocked. No
external integration has ever been exercised live (`NEEDS-YOU.md` §1–6).

## Module 1 — Business Command Center

| Feature | Class | Evidence |
|---|---|---|
| Dashboard: health score, today's actions, money at risk | A | `app/web/dashboard.py:119`, `app/orchestration/health.py`, `tests/test_health_flow.py` |
| Low-stock and out-of-stock warnings | A | `app/transform/alerts.py`, `tests/test_alerts.py` |
| Expiry warnings | A | `app/web/dashboard.py:196`, `tests/test_expiry.py` |
| Square sync status and last failure | A | `app/web/dashboard.py:174` |
| Waste summary | D | No waste data exists (C7) |
| Recent activity feed | E | No feed; `inventory_events` has no UI |
| Outstanding tasks | C | Health "today's actions" only |
| Business KPIs (sales, margin) | C | Weekly report has sales units/net; no margin |

## Module 2 — Inventory Management

| Feature | Class | Evidence |
|---|---|---|
| Catalog import from Square | A | `app/orchestration/sync.py:93`, `tests/test_square_catalog.py` |
| Manual stock item creation | A | `app/web/dashboard.py:297` |
| **Edit / deactivate a stock item** | **D** | No route. Only create, count, thresholds exist |
| SKU and barcode identifiers, scan lookup, link | A | `app/transform/barcodes.py`, `app/web/scanning.py`, `tests/test_barcodes.py`, `tests/test_scanning.py` |
| Categories | C | Free-text string from Square or form; no management |
| Units: count / weight / volume, conversions, pack sizes | A | `app/units.py`, `tests/test_units.py`, `tests/test_receiving.py` |
| Stock balances | C | Mutable `stock_items.qty_base`; derived engine defects C2, C3 |
| Inventory adjustments (non-count) | D | No adjustment path; only a count can lower stock |
| Physical counts | C | Works; no user attribution, no movement event; failed Square push lost (C4) |
| Movement history | C | `inventory_events` written by barcode scan and snack boxes only; no UI |
| Multiple locations | C | Modelled; recipe consumption ignores location (C3) |
| Stock transfers | E | — |
| Inventory valuation | A | Latest-cost method, `app/transform/inventory.py:132` |

## Module 3 — Batch and Expiration

| Feature | Class | Evidence |
|---|---|---|
| Batch creation (invoice, scan, shelf label) | A | `stock_batches`, `app/orchestration/receiving.py:356`, `app/web/dashboard.py:223` |
| Lot / lot-code tracking | C | Batches exist; no lot-code field |
| Receiving dates, expiry, best-before, date type, read confidence | A | `app/models.py:479`, `tests/test_expiry_photos.py`, `tests/test_dates.py` |
| Batch quantities (derived by FEFO, not stored) | A | `app/transform/expiry.py`, `tests/test_expiry.py` |
| FEFO recommendations | A | `allocate_fefo` |
| Expiration action queue | A | `/app/expiring` |
| Expiry alert email | B | Rendered and tested; never delivered by a real provider |
| Markdown recommendations | A | `app/transform/promotions.py`, `tests/test_promotions*.py` |
| Disposal records | D | No waste/disposal entity (C7); a batch can only be hard-deleted (C9) |
| Date reading from packaging photos | B | `app/extract/expiry.py`; never run on real photos |

## Module 4 — Procurement and Receiving

| Feature | Class | Evidence |
|---|---|---|
| Supplier management | C | Free-text `supplier` on item and document; no entity |
| Supplier catalogs | F | — |
| Purchase orders | E | README: deliberately not built |
| Manual receiving (barcode scan) | A | `app/orchestration/scanning.py:219` |
| Invoice upload, dedupe by SHA-256, storage key | A | `app/orchestration/receiving.py:101`, `tests/test_storage.py` |
| Invoice OCR (Claude vision) | B | `app/extract/invoice.py`; never run on a real invoice |
| Product matching, alias learning | A | `app/transform/receiving.py`, `tests/test_receiving_flow.py` |
| Quantity verification (review screen) | A | `app/web/receiving.py:123` |
| Correction capture and extraction-quality report | A | `extraction_corrections`, `tests/test_invoice_ai.py` |
| Receiving records | C | Document + lines; stock effect is an `inventory_counts` row, not a receipt movement |
| Unit cost tracking | C | Latest cost overwrites `cost_per_base_cents`; no history table |
| Supplier price history | E | Recoverable from `document_lines` |
| Reorder recommendations + CSV | A | `app/web/health.py:50`, `tests/test_health_flow.py` |

## Module 5 — Waste and Adjustments

| Feature | Class | Evidence |
|---|---|---|
| Waste events, standard reasons, cost of waste, attribution | **D** | Nothing exists (C7) |
| Spoilage / expired / prep error / remake / damage | **D** | Would be waste reasons |
| Refund-related adjustments | E | Refunds are ignored entirely (see 06) |
| Waste analytics | E | Needs waste events first |
| Waste-risk alerts (predictive, from expiry) | A | `AlertKind.WASTE_RISK`, `app/transform/alerts.py` |

## Module 6 — Recipes and Production

| Feature | Class | Evidence |
|---|---|---|
| Recipe data model | A | `recipe_components`, `tests/test_inventory.py` |
| **Recipe creation UI** | **D** | Only `app/cli.py:351` (demo seed) creates recipes (P1) |
| Ingredient consumption | C | Hourly, from daily buckets; same-day loss (C2), location-blind (C3) |
| Recipe versions | E | — |
| Production records / prep items (e.g. pressed juice) | E | — |
| Recipe costing, gross margin | E | — |
| Theoretical vs actual variance | E | Needs ledger (C6) |

## Module 7 — Sales and POS

| Feature | Class | Evidence |
|---|---|---|
| Square OAuth, encrypted tokens, refresh | A / B live | `app/providers/square/oauth.py`; never run against the sandbox |
| Catalog sync, deleted-variation handling | A | `tests/test_square_catalog.py` |
| Product mapping | C | POS-tracked SKUs auto-map; everything else needs recipes (P1) |
| Completed-order sales sync | A | Hourly, 60-day re-read, `app/orchestration/sync.py:170` |
| Refunds / returns | C | Ignored; neither restored nor netted out of sales |
| Cancellations | A | Only `COMPLETED` orders are read |
| Webhooks: signature, stale-count guard, catalog debounce | A | `tests/test_webhooks.py`, `tests/test_square_catalog.py` |
| Duplicate event protection | A (absolute counts) | No event-id record for inventory webhooks |
| Receiving → Square ADJUSTMENT with idempotency key | A | `tests/test_square_writeback.py` |
| Retry of failed Square writes | D | Swallowed (C4) |
| Reconciliation | A | Hourly full sync, `test_the_hourly_sync_recovers_a_sale_whose_webhook_never_arrived` |
| Extensible POS adapter | C | `provider` column and normalize contract; no adapter interface |

## Module 8 — Analytics

| Feature | Class |
|---|---|
| Valuation, expiry losses (money at risk), health score + 30-day history | A |
| Weekly report (render) | A; delivery B |
| Turnover, waste cost, profitability, margins, supplier price changes, discrepancies, theoretical vs actual, purchasing trends | E (waste cost: D once waste exists) |
| Forecasting, reorder optimisation, predictive insights | F |

## Module 9 — Staff and Permissions

| Feature | Class | Evidence |
|---|---|---|
| Owner / manager / employee roles | D (minimal) | `users.tenant_id` one-to-one; email globally unique; README: "no role-based permissions" |
| Location assignments | E | — |
| Activity history / audit log | C | `inventory_events` (partial), `extraction_corrections` |
| Approval workflows | E | — |

## Module 10 — Administration and Billing

| Feature | Class | Evidence |
|---|---|---|
| Organisation settings (name, timezone, report day/hour) | D | No UI; report schedule is a DB edit (README "Onboarding" step 6) |
| Location settings | C | Synced from Square only |
| User management, password change, password reset | D | None exist |
| POS connection: connect | A | `app/web/dashboard.py:491` |
| POS connection: disconnect / reconnect status | D | No route |
| Notification preferences | A | `app/web/settings.py:55`, `tests/test_email.py` |
| Stripe checkout, portal, lifecycle webhooks | A / B live | `tests/test_billing.py` (recorded payloads); never run in Stripe test mode |
| Entitlements, trial, read-only lapse | A | `tests/test_entitlements.py` — built for two tiers, brief says one plan (decision D1) |
| Data export | D | Reorder CSV only |
| Cancellation | A | Read-only state |
| Data deletion | D | None |
| Admin tools | C | CLI only (`app/cli.py`) |

## Not part of the product

Snack-box shop (`app/orchestration/boxes.py`, `customers.py`, `referrals.py`, `app/web/boxes.py`,
`app/web/shop.py`, four `app/transform/*` modules, 11 tables): ~2,700 lines, 35 of the 88 routes.
Gated by `SNACK_BOX_TENANT_IDS`. It shares the Square connection and widens its OAuth scopes (S6).
