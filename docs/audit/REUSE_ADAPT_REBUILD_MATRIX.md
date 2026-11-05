# Reuse / adapt / rebuild matrix

Phase 2, 2026-11-04. Read-only. The foundation decision is already made: InventoryOS is built on
this codebase (C-25, confirmed again 2026-11-04). This matrix decides **component by component**
what to keep, change or replace inside it. Evidence and labels as in the
[technical audit](INVENTORY_PROJECT_TECHNICAL_AUDIT.md).

Classifications (from the brief): Reuse as-is · Reuse with modifications · Refactor · Replace ·
Rebuild · Do not reuse · Requires further investigation.

## 1. Matrix

| Component | Classification | Evidence | Rationale | Risks |
|---|---|---|---|---|
| **Architecture shape** (modular monolith, web + scheduler processes) | Reuse as-is | `Dockerfile`, `app/orchestration/scheduler.py:176` | Simplest shape that satisfies the blueprint (NFR MNT-01); 21–500 tenants need no services split | Module boundaries must follow DOMAIN_MODEL §2 as the code grows |
| **Layering rule** (`transform/` pure, `load/` idempotent) | Reuse as-is | ~200 pure tests [RUN] | The codebase's strongest asset; makes the new engine testable | New modules must keep the rule |
| **Domain model** | Refactor | [DB audit §3](INVENTORY_PROJECT_DATABASE_AUDIT.md#3-domain-model-compatibility-with-the-blueprint) | Tenancy, items, batches, documents fit; users, locations, products, sales, recipes do not | Large schema change; mitigated by migration tests |
| **Database** (Postgres, SQLAlchemy, Alembic, `Qty`) | Reuse with modifications | `app/db.py:48,61,169`, `tests/test_migrations.py` | Sound; add `timestamptz`, CHECK constraints, delete rules, locking | Naive→aware timestamp migration touches every table |
| **Inventory engine** (`qty_base` writers, derived recompute, `sales_daily`) | Replace | 7 writers; probes R1–R6 | No ledger (C6); daily aggregates cause C2/C3; replaced by InventoryTransaction + movements | Every screen reading `qty_base` must read the projection; keep `qty_base` as the projection column to limit churn |
| **Units** (`app/units.py`) | Reuse with modifications | `tests/test_units.py` [RUN] | Exact constants and base units are right; add tbsp/tsp and item-specific conversions | Conversions must be versioned or snapshotted for history |
| **Recipe engine** (`RecipeComponent`, `consumption()`) | Rebuild | `app/models.py:240`, `app/transform/inventory.py:60-163` | Flat, unversioned, location-blind, Square-keyed; blueprint needs versions, modifiers, prepared items, yields | Recipe resolution is the heart of theoretical consumption — test heavily |
| **Batch management** (`stock_batches`, FEFO) | Reuse with modifications | `app/models.py:479`, `app/transform/expiry.py` | Fields and FEFO rules are right; remaining must come from movement allocations; no hard delete | Changing "derived remaining" to "allocated remaining" changes expiry figures |
| **Expiration logic** (queue, thresholds, date types, confidence) | Reuse with modifications | `app/web/dashboard.py:196-270`, `tests/test_expiry.py`, `tests/test_dates.py` | Matches BAT-FR-09/10; disposal becomes waste | — |
| **Receiving** (upload, dedupe, extraction, matching, review, aliases) | Refactor | `app/orchestration/receiving.py`, `app/transform/receiving.py`, `tests/test_receiving_flow.py` | Flow and matching are good; posting must create receipt movements, be single-shot (C5) and not depend on Square (C4) | Extraction never run on a real invoice — quality unknown |
| **Invoice/date extraction** (`app/extract/`) | Requires further investigation | `app/extract/invoice.py`, `app/extract/expiry.py`; mocked tests only | Code is sound; value depends on real-document accuracy and cost | Anthropic cost per tenant (S9); run a real-invoice trial before relying on it |
| **Waste tracking** | Rebuild (new) | None exists (C7) | — | — |
| **Production** | Rebuild (new) | None exists (G6) | — | — |
| **Square HTTP client, OAuth, token encryption, webhook verification** | Reuse with modifications | `app/providers/square/*`, `tests/test_webhooks.py` | Correct and tested; split scopes (S6), add revoke, move retries off the request (C8) | Never run live |
| **Square normalize/load seam** | Reuse with modifications | `app/transform/square_normalize.py`, `app/load/upsert.py` | Becomes the adapter's output; emit order lines instead of daily rows; fix SQ8 | — |
| **Square sync orchestration** (`run_sync`, write-backs) | Refactor | `app/orchestration/sync.py` | Rebuild around order-line cursor, outbox and adapter interface; keep run bookkeeping (`sync_runs`) | Behaviour change for anyone relying on hourly re-read (nobody yet) |
| **Authentication** | Reuse with modifications | `app/web/auth.py`, `app/security.py`; C-32 | Owner decided to finish in-app: add verification, reset, throttling, server sessions, TOTP, CSRF | Security code ownership; needs focused tests |
| **Authorization / tenancy** | Refactor | `app/security.py:50-93`; S8 | Add memberships, roles, permission keys, location scope, central scoping (C-33) | Every route touched once |
| **Billing** (Stripe lifecycle) | Reuse with modifications | `app/orchestration/stripe_events.py`, `app/orchestration/subscriptions.py`, `tests/test_billing.py` | Lifecycle, grace, read-only and webhook ordering are right; plan catalogue and quantity billing change | Never run in Stripe test mode |
| **Entitlements** | Refactor | `app/entitlements.py` | Keep `allows()` as the single check; replace hard-coded catalogue with data; stop gating core (E-05) | — |
| **Frontend** (Jinja, CSS design system, vanilla JS, ZXing) | Reuse with modifications | `app/web/templates/`, `app/web/static/` | Server rendering suits the operational screens; add small progressive enhancement where reloads hurt (counts, waste) | Accessibility not yet audited (UX-01) |
| **API** | Rebuild (new, R1.1) | No JSON API exists | Needed for the generic sales API (POS-FR-16) and later Advanced API access | Keep it out of R1 per C-29 |
| **Background jobs** (APScheduler + advisory lock) | Reuse with modifications | `app/db.py:169`, `tests/test_deployment.py` | Leader lock is right; add a jobs/outbox table and run alerts for every tenant (G2) | Scheduler is single-process; fine at launch scale |
| **Notifications** (SMTP, templates, preferences, suppressions) | Reuse with modifications | `app/notify/email.py`, `tests/test_email.py` | Complete pipeline; never delivered by a real provider | Deliverability unknown until a provider is configured |
| **Reporting** (weekly report, health score, reorder) | Reuse with modifications | `app/transform/reports.py`, `app/transform/health.py`, `app/web/health.py` | Rule engines are reusable; inputs must come from the ledger and sales transactions | Health score definitions may not match blueprint metrics — review |
| **Promotions / markdowns** | Reuse as-is (dormant until R2) | `app/transform/promotions.py`, tests | Matches BAT-FR-16 (R2) | Keep out of R1 surface |
| **Storage** (local/S3) | Reuse as-is | `app/storage.py`, `tests/test_storage.py` | Fits SAAS-T-07 once S3 (content type) is fixed in the serving route | — |
| **Observability** (Sentry with scrubbing, health endpoints) | Reuse with modifications | `app/monitoring.py`, `app/web/ops.py` | Add JSON logs, request ids, integrity metrics (OBS) | — |
| **Testing** | Reuse with modifications | 542/6 SQLite [RUN], 548 Postgres [RUN-earlier] | Strong suite; fix T1 hermeticity; add CI, Postgres in CI, isolation suite, browser smoke tests | Today's tests encode C2 as correct (`test_consumption_only_counts_days_after_the_count`) — must be rewritten with the engine |
| **Deployment** (Dockerfile, compose, startup guards, migrations under lock) | Reuse with modifications | `Dockerfile`, `compose.yaml`, `app/config.py` | Pin dependencies (O5), push to a remote (O1), add CI, backups (O2), host | No deployment has ever happened |
| **CLI** | Reuse with modifications | `app/cli.py` | Founder console at launch (ADM-FR-16); demo seed useful for tests | Must stop being the only writer of recipes |
| **Snack-box module** | Do not reuse | `app/orchestration/boxes.py` et al., 11 tables, 35 routes | Not InventoryOS; tenant-specific code contradicts C-30; widens Square scopes (S6) | Removing it needs a migration and owner approval (C-37, approved) |
| **Marketing site** | Reuse with modifications | `app/web/marketing.py`, templates | Rebrand to InventoryOS; prices from configuration (G3) | — |

## 2. Recommendation

**Keep the shell, replace the core.** Concretely:

```
KEEP / ADAPT                                   REPLACE / BUILD NEW
  monolith, layering, Postgres/Alembic           inventory ledger (transactions + movements)
  units, batches, expiry queue                   sales model (sources, transactions, lines, returns)
  receiving flow + matching + extraction         recipes (versions, modifiers, prepared items)
  Square transport, OAuth, webhook HMAC          production, waste, count sessions, exceptions
  Stripe lifecycle, allows(), read-only lapse    memberships, roles, permissions, audit events
  scheduler + lock, email, storage, Sentry       Standalone capture, onboarding, export/deletion
  Jinja UI + design system, CLI                  adapter interface + outbox
REMOVE: snack-box module
```

This matches the baseline audit's conclusion ("no rebuild is warranted",
[01](01-executive-audit.md#summary)) with one sharpening the blueprint forces: the **inventory
core is a replacement, not a fix**, because the blueprint's ledger, recipe versioning and
SalesTransaction model change the core data shape. Patching C2/C3 inside the daily-aggregate
engine would be wasted work.

**Why not a rewrite.** Roughly half of the application — receiving, extraction, batches, expiry,
units, Square transport, billing lifecycle, scheduler, storage, email, deploy guards and most of
the tests covering them — maps onto blueprint requirements with modification. None of the defects found lives
in those parts' design. **Why not preserve everything.** The parts that are wrong are wrong in
their data shape (balance overwrite, daily aggregates, Square-owned catalog), which no amount of
patching turns into a ledger.

**Order (consistent with C-27):** safety net (remote, CI, hermetic tests) → tenancy, memberships,
auth (C-32) → ledger + item/units/recipes/production/waste/counts (engine) → Standalone capture
and onboarding → Square adapter rebuild on SalesTransaction → billing catalogue, export, audit →
deploy and pilot. Phase 3 turns this into a plan.
