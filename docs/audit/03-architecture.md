# 03 — Architecture: current state, recommendation, deployment

## Current state [CODE]

| Aspect | Fact |
|---|---|
| Shape | Modular monolith, one image, two processes (web, scheduler) |
| Size | 14,017 lines in `app/` (≈2,700 of them the private snack-box module), 6,617 lines of tests, 45 templates, 88 routes (35 snack box) |
| Runtime | Python 3.12, FastAPI 0.141 / Starlette 1.6, SQLAlchemy 2.0, Alembic, APScheduler 3.11, uvicorn |
| Frontend | Server-rendered Jinja, hand-written CSS (`app/web/static/landing.css`), vanilla JS, ZXing for barcodes. No HTMX, no Tailwind, no build step |
| Database | SQLite in dev; PostgreSQL 16 via `compose.yaml`. 34 tables, 13 Alembic revisions, single head |
| Jobs | APScheduler in a dedicated process; Postgres advisory lock makes extra copies standby (`app/db.py:169`) |
| Storage | Invoice photos behind a key, local volume or S3-compatible (`app/storage.py`) |
| Integrations | Square (OAuth, catalog, counts, orders, inventory write, webhooks), Stripe (checkout, portal, webhooks), Anthropic (invoice + date reading), SMTP, Sentry |
| Source control | Git, **no remote** — the only copy of the code is this machine (O1) |
| CI | None |

### Layering

```
providers/square/   ingestion + write-back (HTTP only)
extract/            photo -> structured data (Anthropic)
transform/          pure functions, no ORM, no I/O       <- well tested
load/               idempotent tenant-scoped upserts
orchestration/      wiring: sync, receiving, alerts, billing, scheduler
web/                FastAPI routers + Jinja
```

The separation is real and consistently followed: `transform/` takes and returns plain data,
which is why most business rules are unit-tested without a database. This is the codebase's
strongest asset and should be preserved.

### Where the architecture is weak

1. **Inventory state is a mutable balance, not a ledger.** `stock_items.qty_base` is assigned by
   seven code paths (Square sync, derived recompute, invoice receiving, scan receiving, manual
   count, scan count, snack boxes). See [04](04-database-and-ledger.md#inventory-ledger-assessment).
2. **Side effects to Square happen inside the request, before commit, and failures are
   swallowed.** No outbox, no retry (C4).
3. **Blocking I/O inside `async def` routes** (C8): `upload_invoice` and `apply_receipt`
   (`app/web/receiving.py:82,182`), `scan_expiry` (`app/web/scanning.py:309`) and
   `square_webhook` (`app/web/webhooks.py:110`) are
   `async def` but call synchronous SQLAlchemy, the synchronous Anthropic client (default timeout
   600 s) and synchronous httpx with `time.sleep` back-off. Each blocks the event loop for every
   request in that worker. The Dockerfile defaults to `WEB_CONCURRENCY=1`. [CODE; not load-tested]
4. **Sales are consumed as daily aggregates** (`sales_daily`), recomputed hourly. Recipe
   consumption cannot be placed in time within a day, which causes C2, and carries no location,
   which causes C3.
5. **No tenant-scoping primitive.** Every route repeats `db.get(...)` + `tenant_id !=` by hand.
   All 53 product routes were checked and do it; nothing enforces it for the next route (S8).
6. **Private module inside the product.** The snack-box shop shares models, migrations, OAuth
   scopes and the webhook router with the SaaS.

## Recommended architecture

Keep: FastAPI, server-rendered Jinja, SQLAlchemy/Alembic, PostgreSQL, the
`transform/` purity rule, APScheduler in one dedicated process. No rewrite is justified.

Change, in dependency order:

| # | Change | Why | Size |
|---|---|---|---|
| R1 | **Append-only `inventory_movements` ledger**; `qty_base` becomes a projection updated in the same transaction | Fixes C6, enables waste, variance, audit, idempotency | L — needs approval (D3) |
| R2 | **Order-level sales ingestion** (`pos_sales` per order line with `closed_at`, `location_id`, refund linkage) feeding consumption movements | Fixes C2, C3; makes refunds explicit | L |
| R3 | **POS outbox**: commit locally first, enqueue Square write with idempotency key, scheduler drains with retry and surfaces failures | Fixes C4 | M |
| R4 | **Conditional state transitions** (`UPDATE … WHERE status <> 'applied' RETURNING`) or `SELECT … FOR UPDATE` on documents | Fixes C5 | S |
| R5 | **Sync routes or worker offload**: make blocking routes plain `def` (FastAPI runs them in a threadpool) now; move invoice extraction to a job later | Fixes C8 | S now, M later |
| R6 | **Memberships** (`user_id, tenant_id, role, location_id NULL`) replacing `users.tenant_id`; `require_role()` dependency; a `scoped(db, Model, tenant)` helper | Staff, roles, S8 | M |
| R7 | **Waste module** on top of R1 (movement type `waste`, reason enum, cost snapshot, actor) | P2 | M |
| R8 | **POS adapter interface** only when a second POS is funded | Brief; README already defers it | — |
| R9 | **Extract the snack-box module** into a separate app/deployment, or at minimum a separate Square OAuth app with its own scopes | S6, focus | M — decision D6 |

Frontend: add HTMX only where full-page reloads hurt (receiving review, counts, waste entry on a
phone). Do not adopt Tailwind: the design system was rebuilt on 2026-09-19 (`22ee0b5`) and a CSS
framework swap has no customer value.

Background processing: APScheduler is sufficient for 21–100 tenants. Add a `jobs` table (or reuse
the outbox) for invoice extraction and Square retries before considering Celery/RQ/Redis.

## Deployment and infrastructure recommendations

| Area | Recommendation | Cost |
|---|---|---|
| Source control | Push to a **private GitHub repo today** (O1) | $0 |
| CI | GitHub Actions: SQLite suite + Postgres service container on every push | $0 |
| Host | One container platform with managed Postgres: Render or Fly.io. Two services (web, scheduler) from the existing image | ~$15–40/mo |
| Database | Managed Postgres 16 with daily automated backups + point-in-time recovery; quarterly restore drill | included / ~$7–20 |
| Photos | Cloudflare R2 (S3 API, no egress fees) — `STORAGE_BACKEND=s3` already supported | ~$0–1 |
| Email | One transactional provider via SMTP now (Resend or Postmark), bounce webhook into existing `email_suppressions` | $0–15 |
| Errors | Sentry free tier (`SENTRY_DSN`, scrubbing already implemented) | $0 |
| Uptime | External check on `/readyz` (Better Stack / UptimeRobot free) | $0 |
| DNS/TLS | Cloudflare in front of the host | $0 |
| Logs | JSON logs to the platform's log drain; request id per request | $0 |
| Builds | Lock dependencies (`uv lock` or `pip-compile`); the Dockerfile currently installs unpinned ranges (O5) | $0 |
| SMS (Twilio) | Not before customers ask | — |

Production process model: web `WEB_CONCURRENCY=2` after R5; scheduler exactly 1 (a second is a
safe standby). Migrations already run under an advisory lock at startup.
