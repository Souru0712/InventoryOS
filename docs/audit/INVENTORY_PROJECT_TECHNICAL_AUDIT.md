# Inventory project — technical audit

Phase 2, 2026-11-04. Read-only. Subject: the existing inventory application at
`C:\Users\oscar\.vscode\InventoryOS` (product name in code: **StockPulse**), audited against the
InventoryOS blueprint in [`docs/product/`](../product/README.md).

Labels: **[RUN]** executed this session · **[RUN-earlier]** executed in the 2026-11-04 baseline
audit and not repeated · **[CODE]** read in source · **[INFERENCE]** · **[UNKNOWN]**.

> **Baseline.** Application code is unchanged since commit `d722579` (2026-09-20); the only later
> commit adds documentation. The baseline audit ([00-INDEX](00-INDEX.md)) was done on the same code
> earlier today, so its [RUN] findings still describe this code. This document re-verifies the key
> ones and adds what the blueprint needs that the baseline did not measure.

## 1. Verification done this session

| Run | Result |
|---|---|
| Full suite, SQLite, `APP_BASE_URL=http://localhost:8000` pinned | **542 passed, 6 skipped** (the 6 are Postgres-only) [RUN] |
| Baseline probes R1–R8 ([probes/test_audit_probes.py](probes/test_audit_probes.py)) | **8/8 pass** — each confirms a defect still present [RUN] |
| Phase 2 probes PA1–PA5 ([probes/test_phase2_probes.py](probes/test_phase2_probes.py)) | **5/5 pass** — each confirms a blueprint gap [RUN] |
| Full suite on PostgreSQL 16 | 548/548 [RUN-earlier]; not repeated (needs Docker) |
| Local dev database `stockpulse.db`, read-only row counts | 2 tenants, 18 stock items, 0 POS connections, locations all `provider='demo'`; schema at migration head `948e22969d09` [RUN] |
| `git remote` | none — this machine holds the only copy [RUN] |
| Dependency vulnerability scan | **Not performed**: needs installing `pip-audit`, which this audit may not do |

## 2. Architecture overview [CODE]

| Aspect | Fact | Evidence |
|---|---|---|
| Shape | Modular monolith; one image, two processes (web, scheduler) | `Dockerfile`, `compose.yaml`, `app/orchestration/scheduler.py:176` |
| Language / runtime | Python 3.12 | `.venv` 3.12.10, `Dockerfile` |
| Backend | FastAPI ≥ 0.115 (Starlette), uvicorn | `pyproject.toml:6-26` |
| Frontend | Server-rendered Jinja templates, hand-written CSS, vanilla JS, ZXing barcode scanning; no build step | `app/web/templates/`, `app/web/static/` |
| API | HTML form routes only; **no JSON API** apart from webhooks (Square, Stripe) and `/healthz` `/readyz` | route list §4 |
| Database | SQLite (dev/tests) and PostgreSQL 16 (compose, production target) | `app/config.py`, `compose.yaml` |
| ORM / migrations | SQLAlchemy 2.0, Alembic: 13 revisions, single head | `migrations/versions/` |
| Exact quantities | `Qty` type = `NUMERIC(18,4)` on Postgres | `app/db.py:61` |
| Auth | Email + password, bcrypt, Starlette signed-cookie session (`SameSite=Lax`, Secure on https) | `app/security.py:20-30`, `app/main.py:60-64` |
| Authorization | One user → one tenant; `require_tenant` / `require_writable` dependencies; per-route `tenant_id` checks; no roles | `app/security.py:50-93` |
| Background jobs | APScheduler in its own process; Postgres advisory lock keeps one active scheduler | `app/orchestration/scheduler.py:116-176`, `app/db.py:169` |
| Scheduled jobs | Hourly Square sync for connected tenants; report sender; trial closer; health snapshots; snack-box housekeeping | `app/orchestration/scheduler.py:37-115` |
| Storage | Invoice/photo files on local disk or S3-compatible bucket | `app/storage.py` |
| Integrations | Square (OAuth, catalog, counts, orders, inventory writes, webhooks); Stripe (Checkout, Portal, webhooks); Anthropic (invoice and date reading); SMTP; Sentry | `app/providers/square/`, `app/web/billing.py`, `app/extract/`, `app/notify/email.py`, `app/monitoring.py` |
| Config | `pydantic-settings` from environment / `.env`; production refuses SQLite, placeholder secrets, plain http | `app/config.py` |
| Deployment | Dockerfile (unpinned dependency ranges, `WEB_CONCURRENCY=1`), compose replica with Postgres; **never deployed** | `Dockerfile`, `compose.yaml` |
| Source control / CI | Git, **no remote, no CI** | `git remote` empty |
| Size | 14,017 lines in `app/` (≈2,700 the private snack-box module); 34 tables (11 snack box); 53 product routes + 35 snack-box routes | `wc -l`, `app/models.py` |

### Layering

```
providers/square/  HTTP to Square only            extract/   photo → structured data (Anthropic)
transform/         pure functions, no ORM, no I/O  ← the codebase's strongest asset
load/              idempotent tenant-scoped upserts
orchestration/     wiring: sync, receiving, alerts, billing, scheduler, reports
web/               FastAPI routers + Jinja
```

The `transform/` purity rule is consistently followed, which is why ~200 business-rule tests run
without a database [CODE, RUN].

## 3. Feature inventory against the brief's list

Statuses: Implemented and verified · Implemented but unverified · Partially implemented · Mocked or
placeholder · Missing · Not applicable · Unknown. "Verified" = exercised by a test that passed in
this session's run, with external systems mocked. Nothing external has ever run live.

| Feature | Status | Source / symbols | Tables | Routes | Tests | Limitations and risks |
|---|---|---|---|---|---|---|
| Inventory items | Partially implemented | `create_item` `app/web/dashboard.py:297`; Square import `app/load/upsert.py:86-134` | `stock_items` | `GET/POST /app/items/new` | `test_web.py` | **No edit or deactivate route**; creation needs a location, which only Square creates (G1, PA1) |
| Ingredients | Partially implemented | Same as items; no item type (raw / prepared / packaging) | `stock_items` | — | — | No item types; no prepared items |
| Products (sellable) | Partially implemented | `SoldItem` `app/models.py:171`; written only by Square sync `app/load/upsert.py:74` or CLI `app/cli.py:338` | `sold_items` | none | `test_square_catalog.py` | No product UI; no variations/modifiers model of its own |
| Units of measure | Implemented and verified | `app/units.py:36-52` (`UNITS`, `to_base`, `convert`) | — | — | `test_units.py` | Same-dimension only; no item-specific cross-dimension conversions (INV-FR-05); no tbsp/tsp (G7) |
| Stock quantities | Partially implemented | `StockItem.qty_base` `app/models.py:214`; 7 writers | `stock_items` | — | many | Mutable balance; derived-engine defects C2, C3 [RUN probes R1, R2] |
| Inventory adjustments | Missing | — | — | — | — | Only a count can lower stock |
| Inventory counts | Partially implemented | `app/web/dashboard.py:395` (single item), `app/web/scanning.py:221` | `inventory_counts` | `POST /app/items/{id}/count`, `/app/scan/count` | `test_scanning.py` | No count sessions, no variance record, no attribution on dashboard count; Square push failure lost (C4) |
| Inventory history | Partially implemented | `InventoryEvent` `app/models.py:455` written by scans and snack boxes only | `inventory_events` | none | `test_scanning.py` | Not a ledger; cannot reconstruct balances (C6) |
| Low-stock alerts | Implemented and verified (Connected only) | `app/transform/alerts.py`, `evaluate_and_store` `app/orchestration/alerts.py:41` | `alerts` | dashboard | `test_alerts.py`, `test_email.py` | **Evaluated only inside a Square sync** (`app/orchestration/sync.py:182`, `scheduler.py:37-45`) — no alerts for a tenant without Square (G2) |
| Expiration tracking | Implemented and verified | `app/transform/expiry.py` (`allocate_fefo`), `/app/expiring` `app/web/dashboard.py:196` | `stock_batches` | `/app/expiring*` | `test_expiry.py`, `test_dates.py` | Batch remaining derived by FEFO, not stored; batch removal is a hard delete (C9, probe R6) |
| Suppliers | Partially implemented | Free-text `supplier` on items and documents | `stock_items`, `documents` | — | — | No supplier entity (DB7) |
| Receiving | Implemented and verified (with defects) | `app/orchestration/receiving.py` (`save_upload`, `parse_document`, `apply_document`), `app/orchestration/scanning.py:219` | `documents`, `document_lines`, `stock_aliases` | `/app/receiving*`, `/app/scan/receive` | `test_receiving_flow.py`, `test_invoice_ai.py` | Double-apply race (C5, probe R4); Square push failure erased (C4, probe R3); stored XSS via image content type (S3, probe R7) |
| Purchase records | Partially implemented | Invoice documents + lines are the only purchase record | `documents`, `document_lines` | — | — | No purchase orders (R1.1 in blueprint) |
| Recipes | Partially implemented | `RecipeComponent` `app/models.py:240` — one quantity per (sold item, stock item); only `app/cli.py:351` writes it | `recipe_components` | none | `test_inventory.py` | **No recipe UI**; no versions, yields, modifiers, prepared items (G5) |
| Ingredient consumption | Partially implemented | `consumption()` / `consumed_since()` `app/transform/inventory.py:60,93`; `refresh_derived_quantities` `app/orchestration/views.py:257` | `sales_daily` | `POST /app/recalculate` | `test_inventory.py` | Daily aggregates; same-day loss (C2); location-blind (C3) [RUN probes] |
| Batches | Implemented and verified | `StockBatch` `app/models.py:479` | `stock_batches` | `/app/expiring/label`, `/app/expiring/{id}/remove` | `test_expiry.py`, `test_scanning.py` | No lot code; no production batches; hard delete |
| Production | Missing | — | — | — | — | No production orders, prepared items or yields (G6) |
| Waste | Missing | — | — | — | — | No waste events (C7); predictive "waste risk" alerts only |
| Reporting | Partially implemented | Weekly report `app/orchestration/reporting.py`, `app/transform/reports.py`; health score `app/transform/health.py`; reorder list + CSV `app/web/health.py:50,75` | `health_snapshots` | `/app/report`, `/app/health`, `/app/reorder(.csv)` | `test_health_flow.py` | Email never delivered live; no waste, margin, variance or consumption reports |
| User management | Missing | Signup creates exactly one user per tenant `app/web/auth.py:66-69` | `users` | `/signup`, `/login`, `/logout` | `test_web.py` | No invites, roles, password change/reset (S2); one person cannot join two businesses (PA3) |
| Location management | Partially implemented | `Location` `app/models.py:157`; created only by Square sync `app/load/upsert.py:43` or CLI `app/cli.py:277` | `locations` | none | — | No UI; Square-shaped (`provider_location_id` required); plan limit sets `is_active` (`app/orchestration/subscriptions.py:43`) |
| POS integration | Implemented but unverified (live) | `app/providers/square/`, `app/orchestration/sync.py`, `app/web/webhooks.py` | `provider_connections`, `sync_runs`, `webhook_events` | `/connect/square*`, webhook | `test_square_*.py`, `test_webhooks.py` | Mocked HTTP only; never run against a Square sandbox seller; see [integration audit](INVENTORY_PROJECT_INTEGRATION_AUDIT.md) |

Also present and outside the blueprint's launch scope: barcode lookup/linking
(`app/transform/barcodes.py`, verified), invoice and date extraction with Claude
(`app/extract/`, never run on real documents), markdown promotions (`app/transform/promotions.py`,
blueprint R2), business health score (`app/transform/health.py`).

**Not part of InventoryOS:** the private snack-box shop (`app/orchestration/boxes.py`,
`customers.py`, `referrals.py`, `app/web/boxes.py`, `app/web/shop.py`, 11 tables), gated by
`SNACK_BOX_TENANT_IDS`. It widens the Square OAuth scopes for every merchant (S6).

## 4. Product routes (53) [CODE]

| Area | Routes |
|---|---|
| Auth | `GET/POST /signup`, `GET/POST /login`, `GET /logout` (state change on GET, S4) |
| Billing | `POST /billing/checkout`, `GET /billing/success`, `POST /billing/portal`, `POST /billing/webhook` |
| Dashboard and items | `GET /app`, `GET /app/expiring`, `POST /app/expiring/label`, `POST /app/expiring/perishable`, `POST /app/expiring/{batch_id}/remove`, `GET/POST /app/items/new`, `POST /app/items/{id}/count`, `POST /app/items/{id}/thresholds`, `GET /app/report`, `POST /app/sync`, `POST /app/recalculate` |
| Square | `GET /connect/square`, `GET /connect/square/callback`, `POST` webhook (`NOTIFICATION_PATH`) |
| Health and reorder | `GET /app/health`, `GET /app/reorder`, `GET /app/reorder.csv` |
| Promotions | `GET /app/promotions`, `POST /app/promotions/{accept,dismiss,snooze}`, `POST /app/promotions/{id}/end` |
| Receiving | `GET/POST /app/receiving`, `GET /app/receiving/{id}`, `GET /app/receiving/{id}/image`, `POST /app/receiving/{id}/reparse`, `POST /app/receiving/{id}/apply` |
| Scanning | `GET /app/scan`, `GET /app/scan/lookup`, `POST /app/scan/{link,receive,count,expiry}`, `GET /app/scan/codes/{stock_item_id}` |
| Settings | `GET /app/settings`, `POST /app/settings/email` |
| Public | `/`, `/pricing`, `/terms`, `/privacy`, `/healthz`, `/readyz` |

**No routes exist for:** recipes, products, locations, suppliers, waste, production, adjustments,
count sessions, staff/invitations, password reset, data export (beyond the reorder CSV), audit
history, Square disconnect, sales entry or import.

## 5. Code quality [CODE, RUN]

| Area | Assessment | Evidence |
|---|---|---|
| Separation of concerns | **Strong.** Pure `transform/` layer, idempotent `load/`, thin routes | §2 |
| Tests | Strong for pure rules and DB integration; 0 browser end-to-end; 0 live external; suite not hermetic (T1: developer `.env` breaks 46 tests unless `APP_BASE_URL` is pinned) | [07-tests](07-tests.md), §1 |
| Migrations | Disciplined: model/migration diff test, single head, downgrade, upgrade over populated data | `tests/test_migrations.py` |
| Error handling | Explicit in most paths; **Square write failures swallowed** (C4) | `app/orchestration/receiving.py:387-402` |
| Concurrency | No row locks or conditional updates anywhere (DB4) | probe R4 |
| Async correctness | Blocking I/O in `async def` routes (C8) | `app/web/receiving.py:82,182`, `app/web/scanning.py:309`, `app/web/webhooks.py:110` |
| Timestamps | Naive `timestamp without time zone` everywhere (DB3) | probe PA5 |
| Config hygiene | Settings validated at startup; production guards; secrets via env | `app/config.py` |
| Dependencies | Unpinned ranges; non-reproducible image (O5) | `pyproject.toml`, `Dockerfile` |
| Linting / type checking | **No linter or type-checker configuration found** (`pyproject.toml` has none; no CI) | `pyproject.toml` |
| Documentation | Extensive README and `NEEDS-YOU.md`; baseline audit; no architecture decision records | repo root |

## 6. Technical observations

1. **The code is a single-POS, POS-first product.** Locations, sellable products, sales and the
   scheduled job that evaluates alerts all originate from Square (G1, G2). The blueprint's
   Standalone mode is not a configuration away; it needs new capture paths and an onboarding that
   does not start with "Connect Square".
2. **The inventory core must be replaced, not patched.** A ledger is the blueprint's foundation
   (DR-01..07); the existing balance-overwrite model has seven writers and no movement history
   (C6). The good news: the writers are few and well located (see the
   [database audit](INVENTORY_PROJECT_DATABASE_AUDIT.md#4-inventory-update-logic)).
3. **The SaaS shell is real but thin.** Tenancy columns, billing lifecycle, entitlements and
   read-only lapse exist and are tested; memberships, roles, location scoping, a data-driven plan
   catalogue and auth hardening do not ([SaaS readiness](SAAS_READINESS_AUDIT.md)).
4. **Most surrounding machinery is reusable**: units, FEFO/expiry rules, receiving with invoice
   extraction and item matching, alert rules, scheduler with leader lock, storage, Stripe
   lifecycle, Sentry scrubbing, deploy guards ([reuse matrix](REUSE_ADAPT_REBUILD_MATRIX.md)).
5. **Operational fragility outranks code issues**: no git remote, no CI, no backups, nothing
   live (O1, O2; [risk register](TECHNICAL_RISK_REGISTER.md)).

## 7. Finding IDs

Baseline IDs (C, S, SQ, DB, O, T, P) keep their meaning from [01-executive-audit](01-executive-audit.md#findings-register).
Phase 2 adds **G1–G15** (gaps against the blueprint), defined in the
[traceability matrix](INVENTORYOS_REQUIREMENTS_TRACEABILITY.md#3-gap-register-g1g15) and ranked in the
[risk register](TECHNICAL_RISK_REGISTER.md).
