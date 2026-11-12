# Development task breakdown

Phase 3, 2026-11-05. **The implementation source of truth for what to build next.** Planning only:
every task is *Not started* or *Ready*; none is complete. Stages, milestones and gates:
[MASTER_DEVELOPMENT_ROADMAP](MASTER_DEVELOPMENT_ROADMAP.md). Requirement IDs (`*-FR-*`, `AC-*`,
`DR-*`, `WF-*`, `SAAS-*`, NFR IDs) refer to [`docs/product/`](../product/README.md); finding IDs
(C, S, SQ, DB, O, T, G, TR, PA) to [`docs/audit/`](../audit/README.md).

## Conventions

- **Size:** S = one focused session · M = 2–3 sessions · L = 4+ sessions (split into sub-tasks
  `Tn.nn-a/-b` when it becomes Ready).
- **Status:** Not started · Ready · In progress · Blocked · In review · Completed · Deferred
  ([governance](README.md#4-governance)). A task is **Ready** only when its dependencies are
  Completed, its decisions are approved and its acceptance criteria are concrete.
- **Paths:** existing paths are cited from the audit; paths marked *(proposed)* do not exist yet.
- **Tests:** every task adds or updates tests; "Tests" lists the minimum. Every task ends with the
  full suite green in CI on SQLite and Postgres.
- P2+ tasks are specified to outline level and are fully elaborated when their stage starts.
- **Milestones** are defined in [MASTER_DEVELOPMENT_ROADMAP §2–3](MASTER_DEVELOPMENT_ROADMAP.md#2-stages-at-a-glance)
  (authoritative); the index records each task's milestone. Summary:

| Milestone | Means | Stage | Gate |
|---|---|---|---|
| M0 Safe to build | Hermetic tests, CI, locked deps, security quick fixes, write-back off, snack-box out, staging | 0 | G0 |
| M1 Tenancy & access | Scoped data access, isolation suite, locations, memberships, permissions, auth, audit | 1 | G1 |
| M2 Ledger engine | Item model, units, ledger + posting service, counts, adjustments, exceptions, dashboard | 1 | G2 |
| M3 Operations engine | Catalog, versioned recipes, sales model, consumption engine, batches, waste, production, suppliers, receiving | 2 | G3 |
| M4 Standalone end-to-end | Daily sales, imports, reports, onboarding, no-POS end-to-end suite; free Standalone pilots may start | 2 | G4 |
| M5 Square connected | Adapter, connection, mapping, ingestion, webhooks, refunds, reconciliation, mode switch, sandbox run | 3 | G5 |
| M6 Launch-ready | Billing catalogue, lifecycle, export, deletion, console, production, monitoring, contract migration, launch review | 4 | **G-R1** (first paying customers) |
| M7 R1.1 | First post-launch increment | 4B | — |
| M8 / M9 / M10 | Multi-location / Advanced / native POS go-no-go | 6 / 7 / 8 | stage gates |

## Index

| ID | Task | Pri | Size | Milestone | Depends on | Status |
|---|---|---|---|---|---|---|
| T0.01 | Hermetic test configuration | P0 | S | M0 | — | Completed (2026-11-05; green in CI run #3) |
| T0.02 | CI on GitHub Actions (SQLite + Postgres 16) | P0 | S | M0 | T0.01 | Completed (2026-11-05; protection proven: direct push rejected, failing PR blocked) |
| T0.03 | Lock dependencies, audit, Dependabot | P0 | S | M0 | T0.02 | Completed (2026-11-05; image installs the lock, pip-audit and lock-match checks required) |
| T0.04 | Coding standards: ruff + mypy | P1 | S | M0 | T0.02 | Completed (2026-11-05; ruff and mypy required checks on main) |
| T0.05 | Extract the snack-box module (C-37) | P0 | M | M0 | T0.02, owner repo | Completed (2026-11-06; module in its own repository, 53 routes, five Square scopes) |
| T0.06 | Security quick fixes (S3, S10, S11, S12) | P0 | S | M0 | T0.01 | Completed (2026-11-05; probe R7 retired) |
| T0.07 | Async correctness (C8) | P0 | S | M0 | T0.01 | Completed (2026-11-05; no async routes, 60 s Anthropic timeout) |
| T0.08 | Disable Square write-back; no swallowed failures (C-34, C4) | P0 | S | M0 | T0.01 | Completed (2026-11-05; write-back only, Square count overwrite left to T1.18 by the owner) |
| T0.09 | Staging environment on the chosen host | P1 | M | M0 | T0.02, ID-02 | Deferred (C-49: until T3.11 needs a public URL; render.yaml on main, Blueprint and C-48 photo storage to do) |
| T0.10 | Structured logs and request ids | P1 | S | M0 | T0.01 | Completed (2026-11-06; JSON logs with request, tenant and user ids) |
| T0.11 | Governance scaffolding (ADRs, status tracking) | P1 | S | M0 | — | Completed (2026-11-06; one ADR per approved ID, enforced by the docs checker) |
| T1.01 | Schema hygiene: timestamptz, constraints, delete rules | P0 | M | M1 | T0.02, T0.05 | Completed (2026-11-06; DM-2 applied, probe PA5 retired) |
| T1.02 | Scoped data-access layer (C-33) | P0 | M | M1 | T1.01 | Completed (2026-11-06; no raw query in routes or services, enforced by a test) |
| T1.03 | Tenant-isolation test suite | P0 | M | M1 | T1.02 | Completed (2026-11-06; every id route refused across businesses, in both CI test jobs) |
| T1.04 | Organization and location model | P0 | M | M1 | T1.02 | Completed (2026-11-06; locations without a POS, local business days, PA1 retired) |
| T1.05 | Memberships and org switching | P0 | M | M1 | T1.02 | Completed (2026-11-06; one login, several businesses, PA3 retired) |
| T1.06 | Permission keys and role presets | P0 | M | M1 | T1.05 | Completed (2026-11-07; every route declares a key, costs follow the role) |
| T1.07 | Server-side sessions, CSRF, POST logout | P1 | M | M1 | T1.05 | Completed (2026-11-07; revocable sessions, CSRF on every form, timeouts approved) |
| T1.08 | Email verification, password reset and change | P1 | M | M1 | T1.07 | Completed (2026-11-07; confirmation gates, single-use reset, password change) |
| T1.09 | Login throttling | P1 | S | M1 | T1.07 | Completed (2026-11-11; database counters per account and address, generic refusals) |
| T1.10 | Invitations and team management | P1 | M | M1 | T1.06, T1.08 | Completed (2026-11-11; emailed single-use invitations, team page, last-Owner guard) |
| T1.11 | TOTP MFA | P1 | M | M1 | T1.07 | Completed (2026-11-11; optional authenticator codes, recovery codes, Owner prompt) |
| T1.12 | Audit events | P1 | S | M1 | T1.05 | Completed (2026-11-12; append-only history, filtered screen) |
| T1.13 | Item model split (C-38) | P0 | L | M2 | T1.04 | Completed (2026-11-12; split tables, backfill, readers moved) |
| T1.14 | Item management UI | P1 | M | M2 | T1.13, T1.06 | Completed (2026-11-12; item and category screens at /app/catalog) |
| T1.15 | Units, packages and conversions | P0 | M | M2 | T1.13 | Completed (2026-11-12; resolver, packages, conversions, editor) |
| T1.16 | Ledger schema and posting service | P0 | L | M2 | T1.01, T1.13, T1.15 | Completed (2026-11-12; ledger tables, posting service, concurrency proven on Postgres) |
| T1.17 | Opening-balance migration | P0 | M | M2 | T1.16 | Completed (2026-11-12; the ledger opens equal to every item's quantity) |
| T1.18 | Route existing writers through the ledger | P0 | M | M2 | T1.17 | In review (PR open; two owner decisions pending: R6 and `sales_daily`, see ADR-0042) |
| T1.19 | Exception model | P1 | M | M2 | T1.06, T1.16 | Not started |
| T1.20 | Jobs table; scheduled evaluation for every tenant | P1 | M | M2 | T1.19 | Not started |
| T1.21 | Nightly ledger verification | P1 | S | M2 | T1.20 | Not started |
| T1.22 | Adjustments and movement history | P1 | M | M2 | T1.18, T1.06 | Not started |
| T1.23 | Count sessions | P1 | L | M2 | T1.22, T1.19 | Not started |
| T1.24 | Cost basis and valuation | P1 | M | M2 | T1.16 | Not started |
| T1.25 | Core dashboard on ledger and exceptions | P1 | M | M2 | T1.19, T1.24 | Not started |
| T2.01 | Owned product catalog (C-36) | P0 | M | M3 | T1.06, T1.13 | Not started |
| T2.02 | Versioned recipes | P0 | L | M3 | T2.01, T1.15 | Not started |
| T2.03 | Recipe costing, margins, completeness | P1 | M | M3 | T2.02, T1.24 | Not started |
| T2.04 | Sales model and sources | P0 | M | M3 | T2.01, T1.04 | Not started |
| T2.05 | Consumption engine | P0 | L | M3 | T2.02, T2.04, T1.18 | Not started |
| T2.06 | Batch allocation refactor | P1 | L | M3 | T1.18 | Not started |
| T2.07 | Waste module | P1 | M | M3 | T2.06, T2.02 | Not started |
| T2.08 | Expiry queue and auto-dispose | P1 | M | M3 | T2.07 | Not started |
| T2.09 | Production runs | P1 | L | M3 | T2.02, T2.06 | Not started |
| T2.10 | Suppliers and supplier items | P1 | M | M3 | T1.13 | Not started |
| T2.11 | Receiving on the ledger | P1 | L | M3 | T2.10, T2.06, T1.20 | Not started |
| T2.12 | Standalone daily sales entry | P1 | M | M4 | T2.05 | Not started |
| T2.13 | Sales file import | P1 | M | M4 | T2.12 | Not started |
| T2.14 | Direct usage records | P1 | S | M4 | T2.05 | Not started |
| T2.15 | Reorder suggestions on ledger usage | P1 | M | M4 | T2.10, T2.05 | Not started |
| T2.16 | Reports I: consumption, waste, expiry, variance | P1 | M | M4 | T2.07, T1.23 | Not started |
| T2.17 | Reports II: margin, cost of sales, receiving, count-based usage, CSV | P1 | M | M4 | T2.16, T2.03, T2.11 | Not started |
| T2.18 | Weekly summary email | P1 | S | M4 | T2.16 | Not started |
| T2.19 | Settings module | P1 | M | M3 | T1.04, T1.12 | Not started |
| T2.20 | Onboarding flow | P1 | L | M4 | T2.12, T2.13, T1.14 | Not started |
| T2.21 | Employee home and floor screens | P1 | M | M3 | T2.07, T2.09 | Not started |
| T2.22 | Standalone end-to-end suite | P0 | M | M4 | T2.20 | Not started |
| T3.01 | Adapter interface and boundary check | P1 | M | M5 | T2.04 | Not started |
| T3.02 | Square connection lifecycle | P1 | M | M5 | T3.01, T1.12 | Not started |
| T3.03 | Location mapping and catalog sync | P1 | M | M5 | T3.02 | Not started |
| T3.04 | Product mapping and reprocessing | P1 | M | M5 | T3.03, T2.05 | Not started |
| T3.05 | Order-line ingestion | P1 | L | M5 | T3.04, T1.20 | Not started |
| T3.06 | Webhooks with dedupe via jobs | P1 | M | M5 | T3.05 | Not started |
| T3.07 | Refunds, voids, discounts | P1 | M | M5 | T3.05 | Not started |
| T3.08 | Reconciliation and failure recovery | P1 | M | M5 | T3.06 | Not started |
| T3.09 | Integration health and sales-source status | P1 | S | M5 | T3.08 | Not started |
| T3.10 | Mode switching with cutover | P1 | M | M5 | T3.05, T2.12 | Not started |
| T3.11 | Square sandbox end-to-end run | P1 | S | M5 | T3.10, T0.09 | Not started |
| T3.12 | Remove the legacy Square sync path | P1 | S | M5 | T3.11 | Not started |
| T4.01 | Plan and entitlement data model (C-41) | P1 | M | M6 | T1.06 | Not started |
| T4.02 | Stripe catalogue and per-location billing | P1 | M | M6 | T4.01 | Not started |
| T4.03 | Subscription lifecycle policies | P1 | S | M6 | T4.02, ID-14 | Not started |
| T4.04 | Stripe test-mode lifecycle run | P1 | S | M6 | T4.03, T0.09 | Not started |
| T4.05 | Full data export | P1 | M | M6 | T2.17 | Not started |
| T4.06 | Account deletion and retention | P1 | M | M6 | T4.05 | Not started |
| T4.07 | Founder console and audited support access | P1 | M | M6 | T1.12, T1.20 | Not started |
| T4.08 | Plan and entitlements view | P1 | S | M6 | T4.01 | Not started |
| T4.09 | Production environment, backups, email | P1 | M | M6 | T0.09 | Not started |
| T4.10 | Monitoring and alerting | P1 | M | M6 | T4.09 | Not started |
| T4.11 | Rebrand to InventoryOS | P1 | S | M6 | — | Not started |
| T4.12 | Legal and support pages | P1 | S | M6 | owner content | Not started |
| T4.13 | Accessibility review and fixes | P1 | M | M6 | Stage 2 UI | Not started |
| T4.14 | Load test at design headroom | P1 | M | M6 | T4.09 | Not started |
| T4.15 | Invoice extraction trial on real invoices | P1 | S | M6 | T2.11, OA-4 | Not started |
| T4.16 | Contract migration (DM-16) | P1 | S | M6 | T1.05, T1.13, T1.18, T3.12 | Not started |
| T4.17 | Launch gate review | P1 | S | M6 | all P1, incl. T4.16 | Not started |
| T4B.01–13 | R1.1 increment (outline) | P2 | — | M7 | G-R1 | Not started |
| T5.01–04 | JuJu validation (outline; plan in JUJU_VALIDATION_PLAN) | P2 | — | M3, M4, M7 | M3/M4/M7 | Not started |
| T6.01–05 | Multi-location (outline) | P3 | — | M8 | Stage 6 gate | Not started |
| T7.01–07 | Advanced (outline) | P3 | — | M9 | Stage 7 gate | Not started |
| T8.01–04 | Native POS feasibility (outline) | Deferred | — | M10 | Stage 8 gate | Deferred |
---

## Stage 0 — Stabilize and foundation

### T0.01 Hermetic test configuration · P0 · S
- **Objective:** the suite passes on any machine, with or without a developer `.env`.
- **Business reason:** every later "verified" depends on trustworthy tests; today a developer `.env` fails 46 tests (T1).
- **Requirements:** MNT-03; audit T1.
- **Technical:** in `tests/conftest.py` set every `Settings` field explicitly (including `APP_BASE_URL=http://testserver`, Square, Stripe, S3, SMTP to test values) and disable env-file loading when `APP_ENV=test`.
- **Existing code:** `tests/conftest.py:1-30`, `app/config.py:12`.
- **Likely files:** `tests/conftest.py`, `app/config.py`.
- **DB · API · UI:** none.
- **Security:** test values only; never real keys in fixtures.
- **Depends on:** —
- **Acceptance:** with the developer `.env` present and no extra environment variables, the suite reports the same result as the pinned run (542 passed, 6 Postgres-only skipped on SQLite).
- **Tests:** a test asserting settings ignore `.env` under test.
- **Done when:** merged; T0.02 runs it in CI.

### T0.02 CI on GitHub Actions · P0 · S
- **Objective:** every push and pull request runs the suite on SQLite and on a Postgres 16 service, including migration tests (ID-03, ID-18).
- **Business reason:** no CI exists (TR-14); Postgres-only tests are skipped locally.
- **Requirements:** MNT-04.
- **Technical:** workflow with two jobs; Postgres job sets `TEST_DATABASE_URL` (already supported by `tests/conftest.py`); audit probes in `docs/audit/probes/` are **not** collected; required status check on `main` (owner enables in GitHub settings).
- **Existing code:** `tests/conftest.py` (`TEST_DATABASE_URL`), `compose.yaml` (postgres:16-alpine).
- **Likely files:** `.github/workflows/ci.yml` *(proposed)*.
- **DB · API · UI:** none.
- **Security:** no secrets needed; actions pinned to commit SHAs.
- **Depends on:** T0.01.
- **Acceptance:** both jobs green on `main`; Postgres job runs 548 tests with 0 skipped; a failing test blocks merge (verified once on a branch).
- **Tests:** the workflow itself.
- **Done when:** required check active.

### T0.03 Lock dependencies, audit, Dependabot · P0 · S
- **Objective:** reproducible builds and visible vulnerabilities (ID-05).
- **Business reason:** unpinned ranges make images non-reproducible (O5); no dependency scan was possible in the audit (U6).
- **Requirements:** SEC-15.
- **Technical:** lockfile from `pyproject.toml`; Dockerfile installs from the lock; `pip-audit` step in CI; Dependabot for pip and GitHub Actions, weekly.
- **Existing code:** `pyproject.toml:6-29`, `Dockerfile` (generates requirements from ranges).
- **Likely files:** lockfile *(proposed)*, `Dockerfile`, `.github/dependabot.yml` *(proposed)*, CI workflow.
- **DB · API · UI:** none.
- **Security:** critical CVEs fail CI unless documented in an ADR with a fix date.
- **Depends on:** T0.02.
- **Acceptance:** image build installs exact locked versions; `pip-audit` runs in CI with no unaddressed critical finding.
- **Tests:** CI step.
- **Done when:** lockfile committed; Dependabot PRs appear.

### T0.04 Coding standards: ruff + mypy · P1 · S
- **Objective:** one formatter/linter and gradual type checking (ID-04).
- **Business reason:** consistent, reviewable changes across many sessions; no linter or type config exists (technical audit §5).
- **Requirements:** MNT-04.
- **Technical:** ruff (lint + format) and mypy configured in `pyproject.toml`; mypy strict for new modules (`transform/`, access, ledger), lenient elsewhere; one mechanical formatting commit, separate from behaviour changes.
- **Existing code:** none.
- **Likely files:** `pyproject.toml`, CI workflow.
- **DB · API · UI:** none. **Security:** ruff security rules (bandit set) enabled.
- **Depends on:** T0.02.
- **Acceptance:** CI runs ruff and mypy and passes on `main`.
- **Tests:** CI steps.
- **Done when:** documented in `CLAUDE.md` commands.

### T0.05 Extract the snack-box module (C-37) · P0 · M
- **Objective:** the snack-box code moves to its own repository; InventoryOS no longer contains it.
- **Business reason:** tenant-specific code contradicts C-30; it widens Square scopes for every merchant (S6); removing 35 routes and 11 tables shrinks Stage 1 refactoring.
- **Requirements:** C-37, SAAS-T-11.
- **Technical:** (1) owner creates a private repository for the snack-box project; (2) copy the module, its templates, models and tests there (history via `git subtree split` optional) — making it runnable is the snack-box project's own work; (3) remove from InventoryOS: `app/orchestration/boxes.py`, `customers.py`, `referrals.py`, `app/web/boxes.py`, `app/web/shop.py`, snack-box transform modules, templates, the 11 models, `SNACK_BOX_TENANT_IDS` gating (`app/entitlements.py:224-237`), `box_housekeeping` (`app/orchestration/scheduler.py:100`), payment/refund webhook branch (`app/web/webhooks.py:129-143`), scopes `ORDERS_WRITE`, `PAYMENTS_READ`, `PAYMENTS_WRITE` (`app/providers/square/oauth.py:18-21`), `create_payment_link` (`app/providers/square/client.py:144`).
- **Existing code:** as listed; tests `tests/test_snack_box_*.py`, `tests/snack_box_support.py`.
- **DB:** forward migration dropping the 11 tables (destructive; approved by C-37; local data is demo only). **API:** 35 routes removed. **UI:** shop and box templates removed.
- **Security:** OAuth scope set reduced to five.
- **Migration step:** DM-1 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T0.02; owner creates the repository.
- **Acceptance:** no snack-box symbol remains (grep); 53 product routes; migration tests pass; OAuth scopes are the five product scopes.
- **Tests:** migration test; route-count test; existing product tests green.
- **Done when:** both repositories committed; InventoryOS CI green.
- **Note:** on the critical path (T1.01 depends on it). Not bypassed without a documented, owner-approved alternative (e.g. a temporary branch) recorded as a decision.

### T0.06 Security quick fixes · P0 · S
- **Objective:** close the stored-XSS hole and add baseline web hardening.
- **Business reason:** S3 is a verified vulnerability (probe R7); headers missing (S10).
- **Requirements:** SEC-08, SEC-09, SEC-13; S3, S10, S11, S12.
- **Technical:** serve invoice images with an allow-listed type from the stored suffix, `X-Content-Type-Options: nosniff` and `Content-Disposition`; sniff uploads by content (Pillow / PDF magic) with a 15 MB limit; security-headers middleware (CSP, HSTS on https, frame-ancestors, referrer policy); remove the full `Settings` object from Jinja globals and expose only safe values.
- **Existing code:** `app/web/receiving.py:154-164`, `app/orchestration/receiving.py:119`, `app/web/templating.py:52`, `app/main.py`.
- **DB · API · UI:** none beyond headers.
- **Security:** this is the task.
- **Depends on:** T0.01.
- **Acceptance:** an `.jpg` uploaded as `text/html` is rejected or served as an image with `nosniff`; every response carries the headers; templates still render.
- **Tests:** replace probe R7 with a test asserting the safe behaviour; header test on representative routes.
- **Done when:** probe R7 retired.

### T0.07 Async correctness · P0 · S
- **Objective:** no blocking I/O inside `async def` routes (C8).
- **Business reason:** one slow invoice extraction (Anthropic default timeout 600 s) stalls every request on that worker.
- **Requirements:** PERF-01, REL-09.
- **Technical:** make `upload_invoice`, `apply_receipt`, `scan_expiry`, `square_webhook` plain `def` (FastAPI runs them in a threadpool); Anthropic client timeout ≤ 60 s; extraction moves to a job in T1.20.
- **Existing code:** `app/web/receiving.py:82,182`, `app/web/scanning.py:309`, `app/web/webhooks.py:110`, `app/extract/invoice.py`.
- **DB · API · UI:** none. **Security:** none.
- **Depends on:** T0.01.
- **Acceptance:** with a mocked extractor sleeping 5 s, a concurrent `/healthz` answers in < 200 ms.
- **Tests:** concurrency test as described.
- **Done when:** merged.

### T0.08 Disable Square write-back; no swallowed failures · P0 · S
- **Objective:** apply C-34 now: nothing is pushed to Square; any remaining Square failure is recorded and visible.
- **Business reason:** a failed push today erases a delivery at the next sync (C4, probe R3).
- **Requirements:** C-34; POS-FR-13 (R1.1).
- **Technical:** setting `SQUARE_WRITE_BACK` default off; remove calls from receiving apply, dashboard count and scan flows; keep `push_*` functions for the R1.1 outbox; failures in remaining Square calls recorded on `sync_runs` and logged with context.
- **Existing code:** `app/orchestration/receiving.py:387-402`, `app/orchestration/sync.py:208-325`, `app/orchestration/scanning.py`, `app/web/dashboard.py:395-433`.
- **DB · API · UI:** none; dashboard copy no longer promises Square updates.
- **Security:** none.
- **Depends on:** T0.01.
- **Acceptance:** receiving and counts make no Square write call (mock asserts zero calls); probe R3 scenario can no longer lose a delivery.
- **Tests:** replace probe R3 with the zero-call test.
- **Done when:** merged; `NEEDS-YOU.md` notes write-back is off until R1.1.

### T0.09 Staging environment on the chosen host · P1 · M
- **Objective:** a staging environment deployed automatically from `main` (ID-02).
- **Business reason:** nothing has ever been deployed (TR-26); live integration runs (Square sandbox, Stripe test mode) need a public HTTPS endpoint.
- **Requirements:** NFR §8 gates 4–8 (prepared, not passed here).
- **Technical:** web service (2 instances), worker (1), managed Postgres 16 with PITR, invoice photos in Postgres kept until received plus 7 days (C-48, ADR-0024; replaces the ID-12 bucket), Sentry project, uptime check on `/readyz`; deploy on green CI; secrets only in host environment; staging protected from indexing.
- **Existing code:** `Dockerfile`, `compose.yaml` process model, `/healthz` `/readyz` (`app/web/ops.py:18-24`), startup guards (`app/config.py`), advisory-lock scheduler.
- **DB:** migrations at startup under the existing lock. **API · UI:** none.
- **Security:** HTTPS only; no secrets in the repository; Square sandbox and Stripe test keys only.
- **Depends on:** T0.02; ID-02 approved; owner creates host, storage and Sentry accounts.
- **Acceptance:** staging `/readyz` returns 200; exactly one active scheduler; a forced error appears in Sentry; restore-from-PITR procedure written (drill in T4.09).
- **Tests:** smoke check in the deploy pipeline.
- **Done when:** URL recorded in the README of `docs/roadmap/`.
- **Deferred (C-49, 2026-11-06):** `render.yaml` is on `main` (PR #17). The Blueprint, its
  secrets, the C-48 photo storage and the acceptance checks wait until before T3.11, the first
  task that needs a public HTTPS endpoint. G0 is met without this task.

### T0.10 Structured logs and request ids · P1 · S
- **Objective:** JSON logs with request id, tenant id and user id; no personal payloads (OBS-01).
- **Business reason:** plain logs without request ids (O4) make staging and production issues hard to trace.
- **Requirements:** OBS-01, OBS-02.
- **Technical:** logging config + middleware adding a request id; ids propagated into jobs; Sentry scrubbing kept.
- **Existing code:** `app/main.py:37`, `app/monitoring.py`.
- **DB · API · UI:** response header `X-Request-ID`.
- **Security:** never log bodies, cookies, tokens or emails.
- **Depends on:** T0.01.
- **Acceptance:** every log line is JSON with `request_id`; a test request with a password field leaves no trace of it in logs.
- **Tests:** log capture test.
- **Done when:** merged.

### T0.11 Governance scaffolding · P1 · S
- **Objective:** ADR folder and template; approved IDs recorded as ADRs; task status workflow in place.
- **Business reason:** many sessions will change this code; decisions must be findable ([governance](README.md#4-governance)).
- **Requirements:** MNT-07.
- **Technical:** `docs/adr/` *(proposed)* with template and one ADR per approved ID-nn; status updates in this file's index plus an `EXECUTION-LOG.md` entry per task.
- **Existing code:** `docs/EXECUTION-LOG.md`.
- **DB · API · UI · Security:** none.
- **Depends on:** — (IDs approved, C-43).
- **Acceptance:** ADRs exist for every approved ID; `CLAUDE.md` points to them.
- **Tests:** link check script (as used in Phases 1–2).
- **Done when:** merged.

---

## Stage 1 — Core foundation

### T1.01 Schema hygiene: timestamptz, constraints, delete rules · P0 · M
- **Objective:** UTC-aware timestamps everywhere; CHECK constraints and explicit delete rules on existing tables.
- **Business reason:** naive timestamps (DB3, probe PA5) and no constraints (DB1, DB2) undermine every later integrity rule.
- **Requirements:** DR-20, DR-04 (pattern), DR-24.
- **Technical:** migration converting every `DateTime` to `timestamptz` (`USING col AT TIME ZONE 'UTC'`); models use `DateTime(timezone=True)`; remove the repeated `_aware()` helpers; CHECKs for non-negative stock fields and enumerated `source` strings; `ON DELETE RESTRICT` by default, `CASCADE` only for owned children (e.g. document lines).
- **Existing code:** `app/db.py:48`, all `DateTime` columns in `app/models.py`, `_aware()` (e.g. `app/entitlements.py:75`).
- **DB:** one migration, tested over populated data. **API · UI:** none.
- **Security:** none.
- **Migration step:** DM-2 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T0.02, T0.05 (fewer tables to convert after DM-1).
- **Acceptance:** probe PA5 inverted (stored datetimes come back aware); migration upgrade/downgrade over populated data passes on Postgres.
- **Tests:** migration test; constraint violation tests.
- **Done when:** merged; PA5 retired.

### T1.02 Scoped data-access layer (C-33) · P0 · M
- **Objective:** every query is tenant-scoped (and location-scoped where relevant) by construction.
- **Business reason:** isolation by convention (S8) breaks the first time a route forgets; TR-05.
- **Requirements:** SAAS-T-04, DR-09, DR-35, C-33.
- **Technical:** `OrgContext` (tenant, allowed locations, user, permissions) built per request; repository helpers `get_scoped(Model, id)`, `select_scoped(Model)` adding tenant and location filters; refactor all product routes and services to use them; static test (AST) failing on `db.get(`, `session.get(` or unscoped `select(` in `app/web/` and `app/orchestration/` outside the access package.
- **Existing code:** `app/security.py:50-93` (`require_tenant`, `require_writable`), per-route checks such as `app/web/dashboard.py:319-321`.
- **Likely files:** `app/access/` *(proposed)*, `app/web/*.py`, `app/orchestration/*.py`.
- **DB · API · UI:** none.
- **Security:** central enforcement point; deny by default.
- **Depends on:** T1.01 (which itself waits for the snack-box removal).
- **Acceptance:** static test passes; existing cross-tenant tests pass.
- **Tests:** static test; unit tests for the helpers.
- **Done when:** no direct `get` on tenant-owned models outside the access layer.

### T1.03 Tenant-isolation test suite · P0 · M
- **Objective:** automated proof that org A can never read, write or download org B's data.
- **Business reason:** cross-tenant exposure is the highest-impact SaaS failure (NFR §9).
- **Requirements:** SAAS-T-09, AC-SAAS-01, SEC-03.
- **Technical:** parametrized test generated from the app's route table: every route with an id parameter is exercised with another tenant's id for read, write and file access; the generator fails when a new id route has no fixture.
- **Existing code:** 6 cross-tenant tests (`tests/test_web.py:70,177`, others listed in S8).
- **DB · API · UI:** none. **Security:** this is the task.
- **Depends on:** T1.02.
- **Acceptance:** 404/403 for every route × operation; suite runs on Postgres in CI.
- **Tests:** the suite.
- **Done when:** part of the required CI check.

### T1.04 Organization and location model · P0 · M
- **Objective:** locations exist independently of any POS, with the fields the blueprint needs.
- **Business reason:** today only the Square sync creates locations, so a Standalone business cannot add a single item (G1, G11, probe PA1).
- **Requirements:** ADM-FR-01, ADM-FR-02, ADM-FR-14 (field), DR-20, DR-31 (field), AC-ADM-01.
- **Technical:** tenant profile fields; `locations` gains address, time zone, business-day cutoff, open days; `provider`/`provider_location_id` nullable (mapping moves to T3.03); location create/edit/deactivate for Owner/Administrator; pure business-day helper (local date with cutoff) in `transform/`.
- **Existing code:** `app/models.py:92-121,157-169`, `app/load/upsert.py:32-58`, `app/orchestration/subscriptions.py:43-66` (location limit).
- **DB:** additive migration. **API:** location routes. **UI:** location settings screens.
- **Security:** Owner/Administrator only (permission keys arrive in T1.06; interim: tenant owner).
- **Migration step:** DM-3 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.02.
- **Acceptance:** a tenant without Square creates a location and then a stock item (probe PA1 inverted); AC-ADM-01.
- **Tests:** business-day helper unit tests (DST, cutoff); route tests.
- **Done when:** PA1 retired.

### T1.05 Memberships and org switching · P0 · M
- **Objective:** a user can belong to several organizations with a role and location scope.
- **Business reason:** one user = one tenant today (G4, probe PA3); staff and multi-business owners need memberships.
- **Requirements:** SAAS-T-03, SAAS-A-09, STF-FR-10 (model), AC-STF-07.
- **Technical:** `memberships` (user, tenant, role, location_scope, status, unique user+tenant); migrate each `users.tenant_id` to an Owner membership; session stores the active membership; org switcher; drop `users.tenant_id` in a later contract migration.
- **Existing code:** `app/models.py:123-140`, `app/security.py:30-93`, `app/web/auth.py:66-70`.
- **DB:** expand/backfill now, contract later. **API · UI:** org switcher.
- **Security:** `OrgContext` built only from an active membership.
- **Migration step:** DM-4 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.02.
- **Acceptance:** PA3 inverted; a user in two orgs sees only the selected org's data (AC-STF-07).
- **Tests:** migration test; switching test.
- **Done when:** PA3 retired.

### T1.06 Permission keys and role presets · P0 · M
- **Objective:** every operation checks a permission key; R1 presets Owner, Administrator, Manager, Employee, Read-only (PD-11).
- **Business reason:** no roles exist; staff must not see costs or change settings.
- **Requirements:** STF-FR-02, STF-FR-03, CMD-FR-14, DR-35, AC-STF-02, AC-STF-08.
- **Technical:** permission catalogue from USER_ROLES_AND_PERMISSIONS §3; `require_permission(key)` dependency; location scope enforced through `OrgContext`; helper that strips cost/value fields for roles without the cost permission.
- **Existing code:** `app/security.py:60-93`.
- **Likely files:** `app/access/permissions.py` *(proposed)*.
- **DB:** role stored on membership. **API:** every route annotated. **UI:** menus filtered by permission.
- **Security:** deny by default; tests per key.
- **Depends on:** T1.05.
- **Acceptance:** AC-STF-02 (a test per permission key and role), AC-STF-08, AC-CMD-03.
- **Tests:** generated permission matrix tests.
- **Done when:** no route without a permission declaration (static test).

### T1.07 Server-side sessions, CSRF, POST logout · P1 · M
- **Objective:** revocable sessions and CSRF protection (C-32).
- **Business reason:** signed cookies cannot be revoked (S5); no CSRF tokens (S4); revoking staff access must take effect immediately.
- **Requirements:** SAAS-A-04, SAAS-A-06, STF-FR-06, SEC-05, AC-STF-04.
- **Technical:** `sessions` table (hashed id, user, membership, created, last seen, expires, revoked); cookie carries the session id only; idle and absolute timeouts; rotate on login; revoke on password change and membership revocation; CSRF token on every state-changing form; logout becomes POST.
- **Existing code:** `app/main.py:60-64` (SessionMiddleware), `app/security.py:30-48`, `app/web/auth.py:108`.
- **DB:** sessions table. **API:** CSRF middleware. **UI:** hidden CSRF field in forms.
- **Security:** this is the task.
- **Depends on:** T1.05.
- **Acceptance:** revoked member blocked on next request (< 1 min); POST without CSRF token refused; probe R8 CSRF part inverted.
- **Tests:** session lifecycle, CSRF, revocation tests.
- **Done when:** merged.

### T1.08 Email verification, password reset and change · P1 · M
- **Objective:** C-31 must-haves (verification, reset) plus password change.
- **Business reason:** a customer who forgets a password must not need the founder (S2); unverified sign-ups must not consume AI extraction (S9).
- **Requirements:** SAAS-A-02, SAAS-A-05, AC-SAAS-02, AC-SAAS-04.
- **Technical:** `auth_tokens` (hashed token, purpose, expiry, used_at); verification email at sign-up; POS connect, invoice upload and invitations gated until verified; reset by emailed single-use token; change password; sessions revoked on change.
- **Existing code:** `app/web/auth.py`, `app/notify/email.py` (SMTP), `app/security.py:20-27` (bcrypt).
- **DB:** tokens table. **API:** verify, reset, change routes. **UI:** forms and emails.
- **Security:** tokens hashed, constant-time compare, 1 h reset expiry, rate-limited (T1.09).
- **Depends on:** T1.07.
- **Acceptance:** AC-SAAS-02, AC-SAAS-04.
- **Tests:** token lifecycle tests; gated-action tests.
- **Done when:** merged.

### T1.09 Login throttling · P1 · S
- **Objective:** throttle by account and IP (C-31).
- **Business reason:** 30 wrong passwords in a row are answered instantly today (S1, probe R8).
- **Requirements:** SAAS-A-03, SEC-12, AC-SAAS-03.
- **Technical:** DB-backed counters (works across instances); increasing delay and temporary lockout; generic error messages; same mechanism on reset, invite and verification endpoints.
- **Existing code:** `app/web/auth.py:89-106`.
- **DB:** throttle table. **API · UI:** generic messages.
- **Security:** this is the task.
- **Depends on:** T1.07.
- **Acceptance:** AC-SAAS-03; probe R8 throttle part inverted.
- **Tests:** throttle tests per account and per IP.
- **Done when:** probe R8 retired.

### T1.10 Invitations and team management · P1 · M
- **Objective:** Owners and Administrators invite, re-role and revoke members.
- **Business reason:** a business has staff; none can be added today.
- **Requirements:** STF-FR-01, STF-FR-06, STF-FR-07, SAAS-A-08, DR-27, AC-STF-01, AC-STF-05.
- **Technical:** invitation with role and location scope; 7-day single-use token bound to the invited email; accept creates the membership; change role; revoke (sessions revoked, T1.07); last active Owner cannot be removed or demoted.
- **Existing code:** none.
- **DB:** invitations table. **API · UI:** team screens.
- **Security:** invitations require verified email (T1.08) and the invite permission.
- **Depends on:** T1.06, T1.08.
- **Acceptance:** AC-STF-01, AC-STF-04, AC-STF-05.
- **Tests:** invitation lifecycle; last-owner guard.
- **Done when:** merged.

### T1.11 TOTP MFA · P1 · M
- **Objective:** optional TOTP for all, strongly prompted for Owners (SAAS-A-07; PD-20 recommendation).
- **Business reason:** owners hold financial data and POS connections.
- **Requirements:** SAAS-A-07.
- **Technical:** enrol with QR, verify, recovery codes (hashed), challenge at login; new dependency for TOTP (needs approval, ID-23).
- **Existing code:** none (JuJu has a design reference: TOTP enforced once enrolled).
- **DB:** MFA secret encrypted at rest; recovery codes hashed. **API · UI:** enrolment and challenge screens.
- **Security:** secrets encrypted with the application key; rate-limited challenge.
- **Depends on:** T1.07.
- **Acceptance:** enrolled user cannot sign in without a valid code or recovery code; recovery code single-use.
- **Tests:** TOTP flow tests with frozen time.
- **Done when:** merged.

### T1.12 Audit events · P1 · S
- **Objective:** a business audit history for non-movement changes (DR-23).
- **Business reason:** owners must see who changed settings, roles and connections (ADM-FR-11).
- **Requirements:** ADM-FR-11, SEC-14, AC-ADM-04.
- **Technical:** `audit_events` append-only; `record_audit()` called by settings, membership, role, connection, plan, mapping and recipe services; list screen with filters.
- **Existing code:** none (`inventory_events` is not an audit log).
- **DB:** table + index (tenant, at). **API · UI:** audit history screen.
- **Security:** no UPDATE/DELETE path; support actor recorded (T4.07).
- **Depends on:** T1.05.
- **Acceptance:** AC-ADM-04.
- **Tests:** each audited service writes one event.
- **Done when:** merged.

### T1.13 Item model split (C-38) · P0 · L
- **Objective:** inventory items defined once per organization, with per-location settings (ID-10).
- **Business reason:** per-location item rows duplicate definitions and block multi-location; item types are needed for prepared items and packaging.
- **Requirements:** INV-FR-01, INV-FR-02, INV-FR-06, C-38.
- **Technical:** `inventory_items` (name, slug, type, category, base unit, dimension, tracks batches/expiry, default shelf life, active) and `item_locations` (reorder point, par, min, storage area); backfill from `stock_items` (merge same slug across locations); `stock_items` kept as a compatibility layer until all readers move; balances stay in `qty_base` until T1.17.
- **Existing code:** `app/models.py:195-238`, readers across `app/web/`, `app/orchestration/`, `app/transform/`.
- **DB:** expand + backfill migrations. **API · UI:** none yet (T1.14).
- **Security:** scoped repositories only.
- **Migration step:** DM-5 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.04.
- **Acceptance:** every existing stock item maps to exactly one item + item_location; existing screens unchanged in behaviour.
- **Tests:** migration test over demo data; repository tests.
- **Done when:** split complete; sub-tasks `-a` schema/backfill, `-b` readers migrated.

### T1.14 Item management UI · P1 · M
- **Objective:** create, edit, deactivate, search and categorize items.
- **Business reason:** no item edit exists today (P3).
- **Requirements:** INV-FR-01, INV-FR-02, INV-FR-03, INV-FR-06, INV-FR-10, DR-24, AC-INV-01.
- **Technical:** item screens replacing `/app/items/new`; categories CRUD (create, rename, merge, archive); search and filters; deactivate instead of delete when referenced.
- **Existing code:** `app/web/dashboard.py:286-393`, `app/web/templates/app/item_form.html`.
- **DB:** categories table. **API · UI:** item and category screens.
- **Security:** permission keys for item management.
- **Depends on:** T1.13, T1.06.
- **Acceptance:** AC-INV-01.
- **Tests:** route and permission tests.
- **Done when:** merged.

### T1.15 Units, packages and conversions · P0 · M
- **Objective:** any recipe or receipt unit converts correctly or is refused with a clear message.
- **Business reason:** juice bars buy produce by weight and use juice by volume (G7); wrong conversions corrupt every figure.
- **Requirements:** INV-FR-04, INV-FR-05, INV-FR-11..14, DR-10, DR-11, AC-INV-02, AC-INV-03.
- **Technical:** extend `app/units.py` (tbsp, tsp); `item_packages` (e.g. case of 6 × 1 gal); `item_unit_conversions` (cross-dimension per item); pure resolver: same dimension by constants, cross-dimension only via item conversion; base unit locked once movements exist.
- **Existing code:** `app/units.py:36-130`, `receive_unit`/`pack_size_base` (`app/models.py:228-229`).
- **DB:** two tables + backfill of packages. **API · UI:** conversion editor on the item screen.
- **Security:** none.
- **Migration step:** DM-5 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.13.
- **Acceptance:** AC-INV-02, AC-INV-03; a base-unit change after movements is refused.
- **Tests:** property tests for round-trips; JuJu press-yield example (1 lb ginger → 225 ml) as a fixture.
- **Done when:** merged.

### T1.16 Ledger schema and posting service · P0 · L
- **Objective:** the append-only ledger and the single service that writes it (C-35).
- **Business reason:** stock must be explainable; today seven paths overwrite it (C6, TR-02).
- **Requirements:** DR-01..07, DR-19, INV-FR-15..18, INV-FR-21, INV-FR-22, AC-INV-04..07.
- **Technical:** `inventory_transactions`, `inventory_movements`, `stock_balances` as in [data architecture §4](TARGET_DOMAIN_AND_DATA_ARCHITECTURE.md#4-ledger-strategy); `post_transaction(ctx, type, source, idempotency_key, lines, occurred_at, actor)` idempotent by key; `SELECT … FOR UPDATE` on balance rows in item-id order; sign CHECKs per type; unit-cost snapshot; `reverse_transaction()`; pure validation in `transform/`.
- **Existing code:** design references: baseline proposal ([04](../audit/04-database-and-ledger.md#recommended-ledger-design-r1-needs-approval-d3)) and JuJu `inventory_movements`.
- **Likely files:** `app/orchestration/ledger.py`, `app/transform/ledger.py` *(proposed)*.
- **DB:** three tables, indexes per data architecture §6. **API · UI:** none yet.
- **Security:** only this service writes movements (static test); scoped context required.
- **Migration step:** DM-6 (schema) — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.01, T1.13, T1.15.
- **Acceptance:** AC-INV-04..07 on Postgres, including a real concurrent-posting test.
- **Tests:** unit (validation), integration (idempotency, concurrency, reversal), static write-path test.
- **Done when:** sub-tasks `-a` schema + service, `-b` concurrency and reversal hardening complete.

### T1.17 Opening-balance migration · P0 · M
- **Objective:** move today's balances into the ledger without loss.
- **Business reason:** the ledger must start equal to what the product shows.
- **Requirements:** DR-03; [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md) step DM-6.
- **Technical:** one `opening_balance` movement per item × location = current `qty_base`, cost snapshot = `cost_per_base_cents`, actor system, key `migration:opening:<item>:<location>`; populate `stock_balances`; legacy tables become read-only history.
- **Existing code:** `stock_items.qty_base` (`app/models.py:214`).
- **DB:** data migration. **API · UI:** none.
- **Security:** none.
- **Migration step:** DM-6 (backfill) — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.16.
- **Acceptance:** for every item × location, Σ movements = previous `qty_base`.
- **Tests:** migration test over demo data.
- **Done when:** merged.

### T1.18 Route existing writers through the ledger · P0 · M
- **Objective:** every existing stock write becomes a ledger posting; the derived engine and Square overwrites disappear (C-34, C-35).
- **Business reason:** removes C2, C3, C5 and G12 by design.
- **Requirements:** DR-01, DR-07, DR-12, DR-25, AC-RCV-05.
- **Technical:** invoice apply → `receipt` transaction with conditional status transition (`… WHERE status <> 'applied'`); scan receive → `receipt`; dashboard and scan counts → single-line count observation producing `count_variance`; delete `refresh_derived_quantities` and `/app/recalculate`; `apply_provider_counts` stops touching stock; static test forbids assignments to `qty_base`.
- **Existing code:** writers listed in [database audit §4](../audit/INVENTORY_PROJECT_DATABASE_AUDIT.md#4-inventory-update-logic).
- **DB:** none new. **API:** `/app/recalculate` removed. **UI:** counts show variance posted.
- **Security:** none.
- **Migration step:** DM-7 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.17.
- **Acceptance:** concurrent apply posts once (AC-RCV-05, probe R4 inverted); no writer outside the posting service; weight/volume items no longer change without a movement.
- **Tests:** writer tests; static test; probes R1, R2, R4, R5, R6 replaced by tests of the new behaviour (R3 was retired in T0.08).
- **Done when:** probes R1, R2, R4, R5 and R6 retired. Note: sales consumption is absent until T2.05 — acceptable because no customer uses the product before M4 (C-40).

### T1.19 Exception model · P1 · M
- **Objective:** one exception mechanism for everything that needs a person (G15).
- **Business reason:** the dashboard and reconciliation depend on deduplicated, resolvable exceptions.
- **Requirements:** REC-FR-11..16, AC-REC-06..09.
- **Technical:** `exceptions` (type, severity, subject, money impact, status, open-dedupe key, resolved_by/reason); pure evaluators in `transform/`; auto-resolve when the condition clears; resolution requires an action link or dismissal reason; role-filtered visibility; migrate the five alert kinds.
- **Existing code:** `app/models.py:58-63,289-306`, `app/transform/alerts.py`, `app/orchestration/alerts.py`.
- **DB:** table + partial unique index. **API · UI:** exception list and resolution.
- **Security:** financial exceptions hidden from Employees.
- **Migration step:** DM-8 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.06, T1.16.
- **Acceptance:** AC-REC-06..09.
- **Tests:** evaluator unit tests; dedupe and auto-resolve integration tests.
- **Done when:** merged.

### T1.20 Jobs table; scheduled evaluation for every tenant · P1 · M
- **Objective:** retryable background work off the request path, and alert/exception evaluation that does not depend on Square (G2).
- **Business reason:** a no-POS business gets no alerts today; slow work blocks requests (C8).
- **Requirements:** REL-07, SCL-03, ID-06.
- **Technical:** `jobs` table; enqueue; claim with `FOR UPDATE SKIP LOCKED`; exponential back-off; dead letter → exception; worker loop in the scheduler process; per-tenant scheduled evaluation; invoice extraction and digests become jobs.
- **Existing code:** `app/orchestration/scheduler.py:37-176`, `app/db.py:169`, `app/orchestration/sync.py:182`.
- **DB:** jobs table. **API · UI:** job health on the founder console later (T4.07).
- **Security:** jobs carry tenant id explicitly (SAAS-T-06).
- **Depends on:** T1.19.
- **Acceptance:** a tenant with no connection gets low-stock exceptions on schedule; a failing job retries then dead-letters into an exception.
- **Tests:** worker tests incl. concurrency of two workers.
- **Done when:** merged.

### T1.21 Nightly ledger verification · P1 · S
- **Objective:** detect any drift between movements and balances (DR-03).
- **Business reason:** the integrity promise must be checked, not assumed.
- **Requirements:** REL-08, AC-INV-11, OBS-06.
- **Technical:** job recomputing Σ movements per (location, item); mismatch → critical `balance_drift` exception and founder alert.
- **Existing code:** none.
- **DB · API · UI:** none new.
- **Security:** none.
- **Depends on:** T1.20.
- **Acceptance:** an injected mismatch is detected the same night (AC-INV-11).
- **Tests:** injection test.
- **Done when:** merged.

### T1.22 Adjustments and movement history · P1 · M
- **Objective:** deliberate corrections with reasons and a readable history.
- **Business reason:** today only a count can lower stock; history has no UI.
- **Requirements:** INV-FR-19, INV-FR-20, INV-FR-23, DR-08, AC-INV-08..10.
- **Technical:** adjustment form (reason required: found, correction, sample, transfer-in-from-outside, other); history per item with filters and running balance; negative-balance rule (Employees cannot take stock below zero; POS consumption may, raising `negative_balance`).
- **Existing code:** none.
- **DB:** adjustments as source records. **API · UI:** adjustment and history screens.
- **Security:** adjustment permission; post-review above threshold (STF-FR-08 R1).
- **Depends on:** T1.18, T1.06.
- **Acceptance:** AC-INV-08..10.
- **Tests:** integration tests per rule.
- **Done when:** merged.

### T1.23 Count sessions · P1 · L
- **Objective:** counts as observations with expected snapshots, variance movements and review.
- **Business reason:** counts are how operators find loss; the blueprint makes them the primary usage measure for Standalone.
- **Requirements:** REC-FR-01..03, REC-FR-05..09, DR-12, PD-12, AC-REC-01..05.
- **Technical:** `stock_counts` + lines; session types (full, category, storage area, single item); entry in any convertible unit incl. packages + loose; expected = balance at the line timestamp; variance in quantity and cost; posting creates one `count_variance` per non-zero line; `count_variance_high` exception above threshold (post-then-review).
- **Existing code:** `inventory_counts` (legacy), count routes `app/web/dashboard.py:395`, `app/web/scanning.py:221`.
- **DB:** two tables. **API · UI:** phone-friendly count screens in storage order.
- **Security:** count permission; costs hidden from Employees.
- **Depends on:** T1.22, T1.19.
- **Acceptance:** AC-REC-01..05.
- **Tests:** expected-at-timestamp tests with interleaved movements.
- **Done when:** sub-tasks `-a` model + posting, `-b` session UI complete.

### T1.24 Cost basis and valuation · P1 · M
- **Objective:** historical figures never change when today's cost changes (PD-08).
- **Business reason:** owners must trust past reports.
- **Requirements:** ANL-FR-01, ANL-FR-02, INV-FR-07, INV-FR-24, DR-19, AC-ANL-01, AC-ANL-02.
- **Technical:** `item_cost_history`; current cost; movement snapshot = current cost at posting; valuation = Σ max(recorded, 0) × current cost; as-of valuation from snapshots.
- **Existing code:** `app/transform/inventory.py:132` (latest-cost valuation), `cost_per_base_cents`.
- **DB:** history table. **API · UI:** valuation figure states its basis.
- **Security:** cost permission.
- **Depends on:** T1.16.
- **Acceptance:** AC-ANL-01, AC-ANL-02.
- **Tests:** snapshot immutability test.
- **Done when:** merged.

### T1.25 Core dashboard on ledger and exceptions · P1 · M
- **Objective:** the owner's "what needs attention" screen on trustworthy data.
- **Business reason:** first screen after sign-in; today it reads overwritten balances and Square status only.
- **Requirements:** CMD-FR-01..04, CMD-FR-10, CMD-FR-12..14, AC-CMD-01..04.
- **Technical:** rebuild `/app` on `stock_balances` and exceptions; date-range filter; completeness banner; Employee view without money; health score removed from the R1 surface (PD-26).
- **Existing code:** `app/web/dashboard.py:119-195`, `app/web/templates/app/dashboard.html`.
- **DB:** none. **API · UI:** dashboard.
- **Security:** role-filtered fields.
- **Depends on:** T1.19, T1.24.
- **Acceptance:** AC-CMD-01..04.
- **Tests:** dashboard tests per role.
- **Done when:** merged; M2 gate review.

---

## Stage 2 — Shared operations and Standalone

### T2.01 Owned product catalog (C-36) · P0 · M
- **Objective:** InventoryOS owns products, variations and modifiers; POS catalogs will map onto them.
- **Business reason:** today products are Square variations (`sold_items`), so a Standalone business cannot define what it sells (G1) and a native POS could never be added.
- **Requirements:** RCP-FR-01, RCP-FR-03 (scale, add, none in R1), C-36.
- **Technical:** `products`, `product_variations` (size, price per variation), `modifiers` + effects (scale factor, add lines, none); product screens; products and variations backfilled from existing `sold_items` (DM-9); Square-origin ones also get external-product mappings in T3.03 (DM-13).
- **Existing code:** `app/models.py:171-193` (`SoldItem`), `app/cli.py:338`.
- **DB:** new tables. **API · UI:** product and modifier screens.
- **Security:** product permission; prices hidden from roles without it.
- **Migration step:** DM-9 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.06, T1.13.
- **Acceptance:** a Manager creates "Golden State" with 12 and 16 oz variations and a "Light ice" scale modifier without any POS.
- **Tests:** model and route tests.
- **Done when:** merged.

### T2.02 Versioned recipes · P0 · L
- **Objective:** recipes per variation with immutable versions, prepared-item lines, packaging, yield and instructions.
- **Business reason:** recipes drive consumption, costing and margins; today they exist only in the CLI seed (P1, G5).
- **Requirements:** RCP-FR-02, RCP-FR-04..09, RCP-FR-14, RCP-FR-17, DR-16, DR-17, AC-RCP-01, -02, -05, -06, -09.
- **Technical:** `recipes`, `recipe_versions` (effective_from), `recipe_lines` (item, quantity, unit, packaging flag); saving creates a new version; cycle check; "start from" scaling; recipe card for staff; migrate `recipe_components` to version 1.
- **Existing code:** `app/models.py:240-253`, `app/cli.py:351`; JuJu recipe model as design reference.
- **DB:** new tables + migration. **API · UI:** recipe editor with unit pickers; read-only recipe card.
- **Security:** edit permission (Manager+); Employees view cards without costs (AC-RCP-09).
- **Migration step:** DM-9 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T2.01, T1.15.
- **Acceptance:** AC-RCP-01, -02, -05, -06, -09.
- **Tests:** versioning (a sale before an edit uses the old version), cycle detection, scaling.
- **Done when:** sub-tasks `-a` model + versioning, `-b` editor UI complete.

### T2.03 Recipe costing, margins, completeness · P1 · M
- **Objective:** recipe cost, estimated margin and the impact of price changes.
- **Business reason:** "what does a drink cost me" is a core owner question (vision §3).
- **Requirements:** RCP-FR-10..13, AC-RCP-03, -07, -08.
- **Technical:** pure costing in `transform/` (base-unit quantity × cost basis, yield-adjusted, packaging split); missing-cost lines listed; products sold without a recipe surfaced; a receipt price change shows affected recipes.
- **Existing code:** none.
- **DB:** none. **API · UI:** cost panel on recipes; affected-recipes view.
- **Security:** cost permission.
- **Depends on:** T2.02, T1.24.
- **Acceptance:** AC-RCP-03, -07, -08.
- **Tests:** costing unit tests including unknown costs.
- **Done when:** merged.

### T2.04 Sales model and sources · P0 · M
- **Objective:** every sale from any source is a SalesTransaction with lines, origin and business date.
- **Business reason:** the one engine for Standalone, Connected and a future native POS (DR-29..31).
- **Requirements:** POS-FR-17, POS-FR-18, DR-29..31, DR-34, AC-SAL-08.
- **Technical:** `sales_sources` (location, kind, effective-from business date), `sales_transactions`, `sales_transaction_lines` (variation, modifiers, quantity, line identity key, revision, consumption status), `sales_returns`; money fields (gross, discounts, net, tax) present although Standalone fills only some.
- **Existing code:** `sales_daily` (`app/models.py:270-287`) becomes legacy, read-only.
- **DB:** new tables. **API · UI:** none yet.
- **Security:** scoped; sales permission.
- **Depends on:** T2.01, T1.04.
- **Acceptance:** one source of record per location per business date is enforced (overlap raises `sales_source_overlap`).
- **Tests:** model constraints; overlap rule.
- **Done when:** merged.

### T2.05 Consumption engine · P0 · L
- **Objective:** turn sales lines into theoretical consumption movements, identically for every source (WF-12).
- **Business reason:** this is the expected usage the product sells; the old engine lost same-day sales and crossed locations (C2, C3).
- **Requirements:** POS-FR-07 (shared part), PRD-FR-11, DR-18, BAT-FR-06, AC-PRD-04, AC-RCP-04.
- **Technical:** pure resolver in `transform/`: RecipeVersion at `occurred_at`, modifiers (scale, add), made-to-order vs made-ahead, unit conversions, FEFO inference; posting `sale_consumption` per sales transaction with key `sale:<id>:r<rev>`; gaps (`unmapped_product`, `conversion_missing`, `missing_cost`) as exceptions; never blocks or drops a sale; reprocessing = reversal + re-post.
- **Existing code:** `app/transform/inventory.py:60-163` (replaced, not reused).
- **DB:** consumption status on lines. **API · UI:** none.
- **Security:** none beyond scope.
- **Depends on:** T2.02, T2.04, T1.18.
- **Acceptance:** AC-PRD-04, AC-RCP-04; a sale at location B never consumes location A's stock; sales made after a same-day count are consumed.
- **Tests:** table-driven resolver tests (JuJu examples: ice scaling, pumps as add modifiers); idempotency and reprocess integration tests.
- **Done when:** sub-tasks `-a` resolver, `-b` posting + reprocess complete.

### T2.06 Batch allocation refactor · P1 · L
- **Objective:** batch remaining comes from movement allocations; batches never disappear.
- **Business reason:** expiry losses must be traceable; batches are hard-deleted today (C9, probe R6).
- **Requirements:** BAT-FR-01..08, BAT-FR-12, BAT-FR-13, DR-13..15, AC-BAT-01..05, -08.
- **Technical:** movement-level batch with allocation mode (explicit, inferred); statuses active, depleted, expired; Σ batch remaining = balance; lot code; source reference; batch history; batch count adjustment; hard delete removed.
- **Existing code:** `StockBatch` (`app/models.py:479-501`), `allocate_fefo` (`app/transform/expiry.py`), `app/web/dashboard.py:271-284`.
- **DB:** columns on batches and movements. **API · UI:** batch history view.
- **Security:** none.
- **Migration step:** DM-10 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.18.
- **Acceptance:** AC-BAT-01..05, -08.
- **Tests:** conservation property test; FEFO/FIFO allocation tests.
- **Done when:** merged.

### T2.07 Waste module · P1 · M
- **Objective:** log waste in seconds with reason, cost and actor, without double counting.
- **Business reason:** waste is the juice bar's most visible loss; nothing exists today (C7).
- **Requirements:** WST-FR-01..12, DR-33, AC-WST-01..08.
- **Technical:** `waste_events`, `waste_lines`, `waste_reasons` (defaults + configurable); waste of a sellable product expands to its recipe version; batch explicit or FEFO-inferred; cost snapshot; 30-minute void by the logger, Manager+ afterwards; waste log, report and "found short at counts".
- **Existing code:** none.
- **DB:** three tables. **API · UI:** phone-first waste entry (≤ 3 taps after choosing the item).
- **Security:** Employees cannot take stock below zero (DR-08).
- **Depends on:** T2.06, T2.02.
- **Acceptance:** AC-WST-01..08.
- **Tests:** expansion, void window, double-count test (AC-WST-08).
- **Done when:** merged.

### T2.08 Expiry queue and auto-dispose · P1 · M
- **Objective:** the existing expiry queue on the new batch model, with an opt-in auto-dispose policy.
- **Business reason:** expiry is the second-largest waste source; the queue already works and is reused.
- **Requirements:** BAT-FR-09..11, BAT-FR-14, PD-29, AC-BAT-06, -07.
- **Technical:** reuse `/app/expiring` and date-type/confidence logic; thresholds per business and item/category; disposal posts waste (reason `expired`); auto-dispose for prepared items off by default; use-by label text.
- **Existing code:** `app/web/dashboard.py:196-270`, `app/transform/expiry.py`, `app/transform/dates.py`, `tests/test_expiry.py`.
- **DB:** settings only. **API · UI:** queue actions.
- **Security:** none.
- **Depends on:** T2.07.
- **Acceptance:** AC-BAT-06, -07.
- **Tests:** existing expiry tests adapted; auto-dispose job test.
- **Done when:** merged.

### T2.09 Production runs · P1 · L
- **Objective:** record prep batches (e.g. ginger juice) with inputs, yield, cost and use-by.
- **Business reason:** juice bars and bubble-tea shops prep daily; production is in R1 (C-29).
- **Requirements:** PRD-FR-01..08, PRD-FR-11..14, PD-15, AC-PRD-01..07.
- **Technical:** `production_orders`; production recipe = recipe of a prepared item; multiplier; required inputs and availability; posting `production_input` (FEFO from input batches) and `production_output` (new batch with code and use-by); actual yield and variance; unknown cost if any input lacks cost; 30-minute void; finished goods (bottled juice) as prepared items sold by the unit; insufficient recorded inputs allowed with negative balance + exception (PD-15).
- **Existing code:** none (JuJu `make_batch` as design reference).
- **DB:** table. **API · UI:** phone-first production screen.
- **Security:** production permission; recipe edits limited (PD-41).
- **Depends on:** T2.02, T2.06.
- **Acceptance:** AC-PRD-01..07.
- **Tests:** posting, yield variance, void and cost tests.
- **Done when:** sub-tasks `-a` posting, `-b` UI complete.

### T2.10 Suppliers and supplier items · P1 · M
- **Objective:** a real supplier entity with supplier items, pack sizes and last prices.
- **Business reason:** supplier is free text today (DB7); receiving and reorder need it.
- **Requirements:** PRC-FR-01..03, PRC-FR-08, AC-PRC-01, -02.
- **Technical:** `suppliers` (contacts, ordering method, lead time, delivery days), `supplier_items` (code, name, purchase unit, pack, last price, preferred); migrate free-text supplier values; purchase history from receipts; price-change detection.
- **Existing code:** `app/models.py:224,380`, `stock_aliases` (`app/models.py:588`).
- **DB:** two tables + backfill. **API · UI:** supplier screens.
- **Security:** purchasing permission.
- **Migration step:** DM-11 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.13.
- **Acceptance:** AC-PRC-01, -02.
- **Tests:** backfill and price-change tests.
- **Done when:** merged.

### T2.11 Receiving on the ledger · P1 · L
- **Objective:** receiving (manual and invoice) posts receipt movements exactly once, with discrepancies and price checks; extraction kept, capped and gated.
- **Business reason:** invoice receiving is the existing app's strongest feature; it must stop double-applying (C5) and stop depending on Square (C4).
- **Requirements:** RCV-FR-01..14, RCV-FR-16, DR-25, PD-19, PD-25, AC-RCV-01..10.
- **Technical:** `receivings` + lines with ordered (nullable, for R1.1 POs), invoiced, arrived, accepted; discrepancy types; price verification against supplier items; single posting with conditional status; corrections by reversal; reuse upload, SHA-256 dedupe, extraction (now a job), matching and alias learning; per-tenant daily extraction cap; verified-email gate; Employee-entered cost changes flagged; already-applied documents get receiving records for history, without new movements (DM-12).
- **Existing code:** `app/orchestration/receiving.py`, `app/transform/receiving.py`, `app/extract/invoice.py`, `app/web/receiving.py`, `tests/test_receiving_flow.py`, `tests/test_invoice_ai.py`.
- **DB:** receiving tables; documents kept. **API · UI:** review screen extended.
- **Security:** S9 cap; safe file serving from T0.06.
- **Migration step:** DM-12 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T2.10, T2.06, T1.20.
- **Acceptance:** AC-RCV-01..10.
- **Tests:** existing receiving tests adapted; concurrency test on Postgres.
- **Done when:** sub-tasks `-a` posting model, `-b` review UI and extraction job complete.

### T2.12 Standalone daily sales entry · P1 · M
- **Objective:** a no-POS business records the day's sales per variation in minutes.
- **Business reason:** half of the launch offering (STD-FR-02).
- **Requirements:** STD-FR-02, STD-FR-05, STD-FR-06, PD-37, PD-38, AC-SAL-02, -03, -05.
- **Technical:** daily entry screen (variations, optional modifier counts) creating a manual SalesTransaction per business date; correction = reversal + re-post with reason; `sales_missing` for open days without sales after the cutoff; Manager+ by default, Owner toggle for Employees.
- **Existing code:** none.
- **DB:** none new. **API · UI:** tablet-friendly sales entry screen.
- **Security:** sales permission.
- **Depends on:** T2.05.
- **Acceptance:** AC-SAL-02, -03, -05.
- **Tests:** posting, correction and missing-day tests.
- **Done when:** merged.

### T2.13 Sales file import · P1 · M
- **Objective:** import sales from a terminal, register, delivery platform or spreadsheet export.
- **Business reason:** many no-POS or unsupported-POS businesses have a file, not a person, as the record.
- **Requirements:** STD-FR-03, AC-SAL-04.
- **Technical:** CSV/XLSX upload; column mapping saved per source (`import_mappings`); product-name matching with an unmatched list; preview; posts `file_import`-origin transactions per day.
- **Existing code:** none (XLSX reading may need a new dependency, ID-23).
- **DB:** import tables. **API · UI:** import wizard.
- **Security:** file size and type limits; files stored tenant-scoped.
- **Depends on:** T2.12.
- **Acceptance:** AC-SAL-04.
- **Tests:** mapping and unmatched-name tests with fixture files.
- **Done when:** merged.

### T2.14 Direct usage records · P1 · S
- **Objective:** record ingredient use without a sale (staff meals, samples, catering by weight).
- **Business reason:** keeps count-based usage honest in Standalone.
- **Requirements:** STD-FR-04.
- **Technical:** form posting `manual_consumption` with a reason.
- **Existing code:** none.
- **DB · API · UI:** form only.
- **Security:** permission per role.
- **Depends on:** T2.05.
- **Acceptance:** a usage record changes stock and appears in the consumption report as manual usage.
- **Tests:** posting test.
- **Done when:** merged.

### T2.15 Reorder suggestions on ledger usage · P1 · M
- **Objective:** explainable reorder suggestions grouped by supplier, using ledger usage.
- **Business reason:** the existing rule engine is reusable; its input (Square velocity) is not.
- **Requirements:** ROR-FR-01..05, ROR-FR-07 (CSV), ROR-FR-08, STD-FR-09, AC-ROR-01..05.
- **Technical:** reuse `reorder_lines` and the suggestion math in `app/transform/health.py`; usage from the ledger (sales, production, manual; configurable window) with count-based fallback; "count first" for negative or stale stock; CSV export **not gated** (E-05; probe PA2).
- **Existing code:** `app/transform/health.py:665`, `app/web/health.py:50-104`, `app/web/templates/app/reorder.html`.
- **DB:** none. **API · UI:** reorder screen with "why this number".
- **Security:** purchasing permission.
- **Depends on:** T2.10, T2.05.
- **Acceptance:** AC-ROR-01..05.
- **Tests:** formula unit tests (AC-ROR-01 example).
- **Done when:** merged.

### T2.16 Reports I: consumption, waste, expiry, variance · P1 · M
- **Objective:** the core operational reports on ledger data with business days.
- **Business reason:** owners decide from these weekly (WF-24).
- **Requirements:** ANL-FR-02..05, ANL-FR-07, AC-ANL-03, AC-ANL-05.
- **Technical:** report queries on movements with location-local business days; consumption split (sales, production, waste, manual); waste cost by reason, item, product and actor; expiry loss; count variance; as-of valuation.
- **Existing code:** `app/transform/reports.py` (patterns).
- **DB:** indexes per data architecture §6. **API · UI:** report screens with ranges.
- **Security:** cost permission.
- **Depends on:** T2.07, T1.23.
- **Acceptance:** AC-ANL-03, AC-ANL-05.
- **Tests:** report math tests on fixture ledgers.
- **Done when:** merged.

### T2.17 Reports II: margin, cost of sales, receiving, count-based usage, CSV · P1 · M
- **Objective:** the remaining R1 reports, and CSV export of every report table.
- **Business reason:** margins and count-based usage are Standalone's main insights.
- **Requirements:** ANL-FR-06, ANL-FR-08, ANL-FR-12..14, STD-FR-08, AC-ANL-04, AC-ANL-06, AC-ANL-08.
- **Technical:** margin by variation; realized ingredient cost of sales with coverage %; receiving discrepancies and price changes; count-based usage per count interval; CSV for every table.
- **Existing code:** reorder CSV (`app/web/health.py:75`) as a pattern.
- **DB:** none. **API · UI:** report screens + CSV.
- **Security:** cost permission.
- **Depends on:** T2.16, T2.03, T2.11.
- **Acceptance:** AC-ANL-04, AC-ANL-06, AC-ANL-08.
- **Tests:** the AC-ANL-08 worked example as a test.
- **Done when:** merged.

### T2.18 Weekly summary email · P1 · S
- **Objective:** the weekly owner email on the new data.
- **Business reason:** owners who never open the app still see waste and money at risk.
- **Requirements:** ANL-FR-09, AC-ANL-07.
- **Technical:** reuse the report pipeline and email preferences; content per ANL-FR-09; job scheduled per tenant time zone.
- **Existing code:** `app/orchestration/reporting.py`, `app/notify/email.py`, user email preferences.
- **DB · API · UI:** template only.
- **Security:** recipients limited to Owners and opted-in members.
- **Depends on:** T2.16.
- **Acceptance:** AC-ANL-07 against the email provider on staging.
- **Tests:** rendering and scheduling tests.
- **Done when:** merged.

### T2.19 Settings module · P1 · M
- **Objective:** business configuration without database edits.
- **Business reason:** the report schedule is a database edit today (baseline onboarding step 6).
- **Requirements:** ADM-FR-01, ADM-FR-03..06, AC-ADM-01, AC-ADM-02, AC-ADM-06.
- **Technical:** settings screens: profile, display units, categories, waste and adjustment reasons, alert settings (expiry window, recipients, digest time, variance thresholds), modifier scale factors, default production mode; every change audited.
- **Existing code:** `app/web/settings.py` (email preferences).
- **DB:** settings table. **API · UI:** settings screens.
- **Security:** Owner/Administrator.
- **Depends on:** T1.04, T1.12.
- **Acceptance:** AC-ADM-01, AC-ADM-02, AC-ADM-06.
- **Tests:** settings effect tests (a scale factor applies to sales after the change).
- **Done when:** merged.

### T2.20 Onboarding flow · P1 · L
- **Objective:** a non-founder reaches first value in under 45 minutes (SAAS §5), Standalone path first.
- **Business reason:** onboarding is the commercial bottleneck; today the only path is "Connect Square" (G13).
- **Requirements:** SAAS §5 steps 0–10, CMD-FR-11, INV-FR-08, AC-INV-12, AC-SAAS-10.
- **Technical:** step 0 "How do you take payments today?" routes and records the POS name (adapter demand) and Scenario C interest; checklist; item CSV import with preview and row errors; quick-add grid; suppliers; recipes for top sellers; one-screen opening count posting `opening_balance`; invite staff. Connected steps 7–8 are added in Stage 3.
- **Existing code:** `app/web/auth.py:29-82` (sign-up).
- **DB:** onboarding state; import files. **API · UI:** onboarding screens.
- **Security:** verified email before invitations (T1.08).
- **Depends on:** T2.12, T2.13, T1.14.
- **Acceptance:** AC-INV-12; AC-SAAS-10 observed with a non-founder.
- **Tests:** import validation tests; end-to-end onboarding test (T2.22).
- **Done when:** sub-tasks `-a` import + checklist, `-b` opening count + step 0 complete.

### T2.21 Employee home and floor screens · P1 · M
- **Objective:** staff do floor tasks without admin screens (WF-23).
- **Business reason:** speed on the line decides whether waste and batches get recorded at all.
- **Requirements:** WF-23, UX-02, UX-03, PD-14, AC-WST-01.
- **Technical:** employee home showing only the tasks the role allows (receive, waste, batch, count, daily sales if permitted); every task within 2 taps; "logged by" picker on shared devices; 375 px layout; large targets; optional HTMX (ID-07).
- **Existing code:** templates and the design system (`app/web/static/`).
- **DB:** none. **API · UI:** home and task screens.
- **Security:** no costs shown to Employees.
- **Depends on:** T2.07, T2.09.
- **Acceptance:** the WF-23 success condition in PRODUCT_ACCEPTANCE_CRITERIA; AC-WST-01.
- **Tests:** browser tests at 375 px (Playwright, ID-08).
- **Done when:** merged.

### T2.22 Standalone end-to-end suite · P0 · M
- **Objective:** prove Standalone works with no POS configured and no POS credentials present.
- **Business reason:** guards the commitment that Connected never undermines Standalone (brief §3.2, MNT-08).
- **Requirements:** AC-SAL-01, AC-SAL-07, MNT-08.
- **Technical:** CI job with every POS setting unset that runs every R1 workflow end to end (onboarding, receiving, production, waste, daily sales, counts, reports); static test that provider-specific imports appear only under `app/providers/`.
- **Existing code:** none.
- **DB · API · UI · Security:** none.
- **Depends on:** T2.20.
- **Acceptance:** AC-SAL-01 and AC-SAL-07 in CI.
- **Tests:** the suite.
- **Done when:** required CI check; M4 gate review.

---

## Stage 3 — Connected: Square

### T3.01 Adapter interface and boundary check · P1 · M
- **Objective:** a provider-independent adapter contract; Square becomes its first implementation.
- **Business reason:** Square must not become the architecture (brief §3.1); a second POS later must be an adapter, not a rewrite.
- **Requirements:** POS-FR-15, DR-29, INTEGRATION_REQUIREMENTS §2, AC-POS-12, AC-SAL-07.
- **Technical:** `PosAdapter` protocol and normalized types ([target architecture §5](TARGET_ARCHITECTURE.md#5-integration-architecture)); registry by provider code; static test: no provider imports outside `app/providers/`; business-logic tests run on normalized fixtures only.
- **Existing code:** normalize/load seam `app/transform/square_normalize.py`, `app/load/upsert.py`.
- **Likely files:** `app/providers/base.py` *(proposed)*.
- **DB · API · UI:** none.
- **Security:** none.
- **Depends on:** T2.04.
- **Acceptance:** AC-POS-12; AC-SAL-07 extended to the new modules.
- **Tests:** contract tests with recorded Square payloads (existing fixtures reused).
- **Done when:** merged.

### T3.02 Square connection lifecycle · P1 · M
- **Objective:** connect, refresh, disconnect and detect revocation, with product-only scopes and one active connection per merchant + location.
- **Business reason:** no disconnect exists (SQ5); scopes are over-broad (S6); a merchant can attach to two businesses (S7, probe PA4).
- **Requirements:** POS-FR-01, SQ-02, DR-22, AC-POS-01, AC-POS-09, AC-POS-11.
- **Technical:** reuse OAuth and Fernet token encryption; scopes: merchant profile, catalog, orders read (inventory write only when R1.1 write-back is enabled); disconnect revokes at Square; revoked/expired token → connection `revoked` + critical exception; unique active connection constraint; connection changes audited.
- **Existing code:** `app/providers/square/oauth.py`, `app/web/dashboard.py:491-530`, `app/orchestration/sync.py:59-90`.
- **DB:** constraint + status values. **API · UI:** connection screen.
- **Security:** encrypted tokens, key outside DB, verified email required (T1.08).
- **Migration step:** DM-14 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T3.01, T1.12.
- **Acceptance:** AC-POS-01, AC-POS-09, AC-POS-11; probe PA4 inverted.
- **Tests:** OAuth flow with mocked Square; revocation handling.
- **Done when:** PA4 retired.

### T3.03 Location mapping and catalog sync · P1 · M
- **Objective:** map Square locations to InventoryOS locations; sync items, variations and modifiers into external catalog records.
- **Business reason:** locations are created from Square today (G11); modifiers are not synced at all.
- **Requirements:** POS-FR-03, POS-FR-04; SQ8.
- **Technical:** `external_products`, `external_modifiers` unique per (connection, external id); existing `sold_items` data migrated into them; tracked location count limited by the entitlement; fix `METRIC_MILLIGRAM` mapping.
- **Existing code:** `app/orchestration/sync.py:93-130`, `app/transform/square_normalize.py:17`.
- **DB:** new tables + migration from `sold_items`. **API · UI:** location mapping step.
- **Security:** scoped.
- **Migration step:** DM-13 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T3.02.
- **Acceptance:** a sandbox catalog with modifiers imports with correct units; re-sync is idempotent.
- **Tests:** recorded-payload tests.
- **Done when:** merged.

### T3.04 Product mapping and reprocessing · P1 · M
- **Objective:** map external variations and modifiers to InventoryOS products; fix history after a mapping change.
- **Business reason:** consumption accuracy depends on mapping (packaging matrix limitation).
- **Requirements:** POS-FR-06, POS-FR-14, PD-17, REC-FR-12 (`unmapped_product`), AC-POS-06, AC-RCP-08.
- **Technical:** mapping UI with suggestions by name; unmapped list with sales counts; `unmapped_product` exception (deduplicated); reprocess window (default since the last count, max 30 days, with preview) as reversal + re-post.
- **Existing code:** none.
- **DB:** `product_mappings`. **API · UI:** mapping screen.
- **Security:** mapping permission; audited.
- **Depends on:** T3.03, T2.05.
- **Acceptance:** AC-POS-06.
- **Tests:** reprocess idempotency tests.
- **Done when:** merged.

### T3.05 Order-line ingestion · P1 · L
- **Objective:** completed orders arrive as SalesTransactions per line, incrementally, with backfill.
- **Business reason:** replaces daily aggregates and the hourly 60-day re-read (DB10, SQ1, SQ2).
- **Requirements:** POS-FR-05, POS-FR-07, PD-16, AC-POS-02.
- **Technical:** cursor per connection (`updated_at` since last success); initial backfill window; external orders/lines stored; line uid as idempotency grain; consumption trigger per source (completed for Square); everything runs as jobs.
- **Existing code:** `SquareClient.orders` (`app/providers/square/client.py:113`).
- **DB:** external order tables, cursors. **API · UI:** import summary.
- **Security:** none beyond scope.
- **Depends on:** T3.04, T1.20.
- **Acceptance:** AC-POS-02 on the sandbox.
- **Tests:** recorded-payload ingestion tests; idempotency tests.
- **Done when:** sub-tasks `-a` cursor + backfill, `-b` consumption hand-off complete.

### T3.06 Webhooks with dedupe via jobs · P1 · M
- **Objective:** near-real-time ingestion that never double-posts.
- **Business reason:** owners expect stock to follow sales within minutes (PERF-04).
- **Requirements:** POS-FR-11, DR-21, SEC-11, PERF-07, AC-POS-03, AC-POS-10.
- **Technical:** reuse raw-body HMAC verification; store `webhook_events(provider, event_id)` before enqueueing; ack ≤ 2 s; processing in jobs; mark processed after success; duplicates no-op.
- **Existing code:** `app/providers/square/webhooks.py`, `app/web/webhooks.py:110-175`, `webhook_events` (`app/models.py:874`).
- **DB:** generalized webhook events. **API:** `/webhooks/square`. **UI:** none.
- **Security:** invalid signature rejected and logged without payload.
- **Depends on:** T3.05.
- **Acceptance:** AC-POS-03, AC-POS-10.
- **Tests:** triple-delivery test; invalid-signature test.
- **Done when:** merged.

### T3.07 Refunds, voids, discounts · P1 · M
- **Objective:** defined stock effects for every case (WF-17).
- **Business reason:** refunds are ignored today, overstating sales (integration audit).
- **Requirements:** POS-FR-08 (record), POS-FR-09, POS-FR-26, DR-26, PD-10, AC-POS-04, AC-POS-05.
- **Technical:** SalesReturn linked to the order; net sales reduced; no automatic restock; voids before completion consume nothing; completed-then-voided → reversal; discounts change value, not consumption; orphan refunds matched later.
- **Existing code:** `app/transform/square_normalize.py:138`.
- **DB:** sales returns. **API · UI:** refunds listed on the order view.
- **Security:** none.
- **Depends on:** T3.05.
- **Acceptance:** AC-POS-04, AC-POS-05.
- **Tests:** case table from WF-17 as tests.
- **Done when:** merged.

### T3.08 Reconciliation and failure recovery · P1 · M
- **Objective:** missed events are recovered and mismatches surfaced.
- **Business reason:** webhooks get lost; owners must not silently lose sales.
- **Requirements:** POS-FR-10, POS-FR-12, AC-POS-07, AC-POS-08.
- **Technical:** scheduled re-read of a short window; daily order/line count comparison per business day → `pos_reconciliation_mismatch`; sync failures → `sync_failure` with retry history.
- **Existing code:** `sync_runs` (`app/models.py:308`).
- **DB:** reconciliation results on sync runs. **API · UI:** sync history screen.
- **Security:** none.
- **Depends on:** T3.06.
- **Acceptance:** AC-POS-07, AC-POS-08.
- **Tests:** skipped-order and missed-webhook tests.
- **Done when:** merged.

### T3.09 Integration health and sales-source status · P1 · S
- **Objective:** one place to see whether sales data is flowing.
- **Business reason:** stale data must be visible (CMD-FR-08).
- **Requirements:** POS-FR-02, POS-FR-27, CMD-FR-08, AC-CMD-05, AC-CMD-06.
- **Technical:** per connection: last success, lag, error rate, unprocessed events, token status; dashboard banner for both modes.
- **Existing code:** `app/web/dashboard.py:174` (last sync/failure).
- **DB:** none. **API · UI:** health page + banner.
- **Security:** none.
- **Depends on:** T3.08.
- **Acceptance:** AC-CMD-05, AC-CMD-06.
- **Tests:** banner state tests.
- **Done when:** merged.

### T3.10 Mode switching with cutover · P1 · M
- **Objective:** a location moves between Standalone and Connected on a cutover business date without re-setup.
- **Business reason:** customers adopt or drop a POS (STD-FR-11).
- **Requirements:** WF-25, POS-FR-19, STD-FR-11, DR-31, AC-SAL-06.
- **Technical:** new sales source of record from the cutover date; POS sales before it ignored for consumption; manual entries after it refused or flagged `sales_source_overlap`; onboarding steps 7–8 for Connected; billing hook for T4.02.
- **Existing code:** none.
- **DB:** sales source rows. **API · UI:** mode switch screen.
- **Security:** Owner only; audited.
- **Depends on:** T3.05, T2.12.
- **Acceptance:** AC-SAL-06.
- **Tests:** cutover boundary tests in the location's time zone.
- **Done when:** merged.

### T3.11 Square sandbox end-to-end run · P1 · S
- **Objective:** the full Connected path verified against a real Square sandbox seller (NFR §8 gate 4).
- **Business reason:** nothing Square-related has ever run live (TR-11).
- **Requirements:** AC-POS-01..11 (sandbox parts), OA-2.
- **Technical:** on staging: seed the sandbox seller (`scripts/` seeding script), connect, map, sell, refund, void, disconnect; record the run.
- **Existing code:** sandbox seeding script; `scripts/send_test_webhook.py`.
- **DB · API · UI:** none.
- **Security:** sandbox credentials in staging env only.
- **Depends on:** T3.10, T0.09; owner Square developer account access.
- **Acceptance:** recorded run with every step passing.
- **Tests:** the run log.
- **Done when:** run recorded in `docs/EXECUTION-LOG.md`.

### T3.12 Remove the legacy Square sync path · P1 · S
- **Objective:** delete the hourly 60-day re-read, `sales_daily` writers and provider-count handling.
- **Business reason:** two sync paths would diverge; SQ2 scalability problem.
- **Requirements:** cleanup; SCL-06.
- **Technical:** remove `run_sync` sales/count parts and `sync_all_tenants` iteration of the old path; keep `sales_daily` table read-only as legacy history.
- **Existing code:** `app/orchestration/sync.py:133-206`, `app/orchestration/scheduler.py:37-52`, `app/load/upsert.py:169-240`.
- **DB:** none. **API · UI:** none.
- **Security:** none.
- **Depends on:** T3.11.
- **Acceptance:** no code path writes `sales_daily`; suite green.
- **Tests:** static check.
- **Done when:** M5 gate review.

---

## Stage 4 — Commercialization and launch readiness

### T4.01 Plan and entitlement data model (C-41) · P1 · M
- **Objective:** plans and entitlements are data; operating mode, location count and capability flags are separate dimensions.
- **Business reason:** Starter/Pro are hard-coded and gate core features (G3, probe PA2).
- **Requirements:** E-01..10, DR-32, DR-36, SAAS-B-09, -10, -16, -17, AC-SAAS-08, AC-SAAS-12.
- **Technical:** `plans` (code, billing basis, provider price reference) and `entitlements` (allowed modes, max locations, capability flags, usage limits) loaded per subscription; `allows()` (`app/entitlements.py:132`) reads them; remove `CAPABILITIES`, `PLAN_IDS`, `Plan` enum usage; core never gated; seed Complete and Standalone.
- **Existing code:** `app/entitlements.py`, `app/models.py:30-35`, `app/orchestration/subscriptions.py`.
- **DB:** tables + migration of the plan enum. **API · UI:** none (T4.08).
- **Security:** entitlement changes only from verified billing events or audited admin actions (SEC-20).
- **Migration step:** DM-15 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.06.
- **Acceptance:** AC-SAAS-08, AC-SAAS-12; probe PA2 inverted.
- **Tests:** entitlement matrix tests.
- **Done when:** PA2 retired.

### T4.02 Stripe catalogue and per-location billing · P1 · M
- **Objective:** sell Complete per location and Standalone per business, with plan change at mode cutover.
- **Business reason:** the approved pricing model cannot be sold today (checkout is one fixed unit, `app/web/billing.py:56`).
- **Requirements:** SAAS-B-01, -02, -18, -19, PD-39, AC-SAAS-11.
- **Technical:** Stripe products and prices configured outside code; quantity = active Connected locations for Complete; plan change on mode switch prorated by Stripe; marketing pricing page reads plan configuration instead of `app/web/marketing.py:16,30,43`.
- **Existing code:** `app/web/billing.py`, `app/orchestration/stripe_events.py`.
- **DB:** plan references. **API · UI:** checkout and pricing page.
- **Security:** prices chosen server-side (already true).
- **Depends on:** T4.01.
- **Acceptance:** AC-SAAS-11; changing a price in configuration changes new subscriptions only (AC-SAAS-12).
- **Tests:** recorded-payload billing tests.
- **Done when:** merged.

### T4.03 Subscription lifecycle policies · P1 · S
- **Objective:** apply the approved trial, grace and read-only policies (PD-05, PD-07).
- **Business reason:** lifecycle logic exists and is tested; policies must match the approved values.
- **Requirements:** SAAS-B-03..08, SAAS-B-11, AC-SAAS-07.
- **Technical:** trial length/card requirement, grace period, read-only behaviour (ingestion continues) as configuration; state mapping verified.
- **Existing code:** `app/entitlements.py:21-25,104-150`, `app/orchestration/subscriptions.py`.
- **DB · API · UI:** banners.
- **Security:** none.
- **Depends on:** T4.02; ID-14 approved.
- **Acceptance:** AC-SAAS-07.
- **Tests:** existing entitlement tests adapted.
- **Done when:** merged.

### T4.04 Stripe test-mode lifecycle run · P1 · S
- **Objective:** the billing lifecycle verified in Stripe test mode (NFR §8 gate 5).
- **Business reason:** billing has only run against recorded payloads (TR-11).
- **Requirements:** AC-SAAS-05, AC-SAAS-06.
- **Technical:** on staging: checkout → active; renewal; failed payment → past due → grace → read-only; cancel; resubscribe; out-of-order and duplicate webhooks.
- **Existing code:** billing code above.
- **DB · API · UI:** none.
- **Security:** test keys only.
- **Depends on:** T4.03, T0.09; owner Stripe account.
- **Acceptance:** AC-SAAS-05, AC-SAAS-06 recorded.
- **Tests:** the run log.
- **Done when:** recorded in the execution log.

### T4.05 Full data export · P1 · M
- **Objective:** an Owner downloads all business data at any time.
- **Business reason:** trust and portability; required before the first paying customer.
- **Requirements:** ADM-FR-10, SAAS-D-01, SAAS-D-02, AC-ADM-03.
- **Technical:** job producing a zip of CSVs (items, conversions, suppliers, recipes + versions, movements, batches, receivings + invoice files, waste, counts, exceptions, sales); signed short-lived download link.
- **Existing code:** reorder CSV pattern.
- **DB:** export job records. **API · UI:** export button and email.
- **Security:** Owner/Manager permission; link expiry; files tenant-scoped.
- **Depends on:** T2.17.
- **Acceptance:** AC-ADM-03.
- **Tests:** export completeness test.
- **Done when:** merged.

### T4.06 Account deletion and retention · P1 · M
- **Objective:** deletion on request and retention after cancellation per approved policy.
- **Business reason:** privacy obligations and customer trust (NFR §7).
- **Requirements:** ADM-FR-12, SAAS-D-03..05, PD-07, AC-ADM-05.
- **Technical:** deletion request with 7-day cancellable window; job hard-deletes tenant rows and files and revokes POS tokens; retention job after cancellation per PD-07; audit of the request (actor ids only).
- **Existing code:** none.
- **DB:** deletion state; delete order respecting FKs (T1.01 rules). **API · UI:** deletion screens.
- **Security:** Owner only, re-authentication required.
- **Depends on:** T4.05.
- **Acceptance:** AC-ADM-05.
- **Tests:** deletion leaves no tenant rows or files (Postgres).
- **Done when:** merged.

### T4.07 Founder console and audited support access · P1 · M
- **Objective:** the founder can find a tenant, see subscription and sync health, re-run jobs and get time-boxed support access.
- **Business reason:** a solo founder must support customers without database access.
- **Requirements:** ADM-FR-16, SAAS-T-10, SEC-19.
- **Technical:** separate admin authentication; tenant lookup; job and sync health; re-run job; impersonation with reason, time limit and audit event visible to the tenant.
- **Existing code:** `app/cli.py` (kept for scripts).
- **DB:** support sessions. **API · UI:** admin screens.
- **Security:** MFA required for admin; all actions audited.
- **Depends on:** T1.12, T1.20.
- **Acceptance:** support access appears in the tenant's audit history; expires automatically.
- **Tests:** access and audit tests.
- **Done when:** merged.

### T4.08 Plan and entitlements view · P1 · S
- **Objective:** customers see what their plan includes, limits and usage.
- **Business reason:** fewer billing questions; transparency (ADM-FR-15).
- **Requirements:** ADM-FR-09, ADM-FR-15.
- **Technical:** subscription screen with plan, location quantity, status, entitlements, usage (locations, extraction), Stripe Portal link.
- **Existing code:** `app/web/billing.py:78` (Portal).
- **DB · API · UI:** screen.
- **Security:** Owner/Administrator.
- **Depends on:** T4.01.
- **Acceptance:** values match the entitlement set.
- **Tests:** rendering tests.
- **Done when:** merged.

### T4.09 Production environment, backups, email · P1 · M
- **Objective:** production ready to receive customers (NFR §8 gates 6–7).
- **Business reason:** no backups exist (O2); email has never been delivered.
- **Requirements:** REL-05, REL-06, SEC-16, ID-02, ID-11, ID-12.
- **Technical:** production services as staging; PITR backups; **restore drill executed and logged**; object storage with versioning; email provider with SPF, DKIM, DMARC and bounce handling into `email_suppressions`; custom domain and TLS.
- **Existing code:** `email_suppressions`, deploy assets.
- **DB:** managed Postgres. **API · UI:** none.
- **Security:** production secrets separate from staging; least-privilege DB role.
- **Depends on:** T0.09; owner accounts and DNS.
- **Acceptance:** restore drill log; verification and reset emails received in real inboxes with passing SPF/DKIM/DMARC.
- **Tests:** drill and delivery evidence.
- **Done when:** evidence filed in the execution log.

### T4.10 Monitoring and alerting · P1 · M
- **Objective:** the founder's phone hears about failures before customers do (NFR §8 gate 8).
- **Business reason:** no uptime or integrity monitoring exists (O3).
- **Requirements:** OBS-02..07.
- **Technical:** Sentry alerts on new errors; uptime monitor on `/readyz`; integrity metrics (last successful sync per tenant, unprocessed events, dead letters, ledger drift, job failures, backup failures) with thresholds per OBS-06.
- **Existing code:** `app/monitoring.py`, `app/web/ops.py`.
- **DB · API · UI:** metrics endpoint or job.
- **Security:** no personal data in alerts.
- **Depends on:** T4.09.
- **Acceptance:** a test alert reaches the founder's phone for each alert type.
- **Tests:** alert tests.
- **Done when:** merged.

### T4.11 Rebrand to InventoryOS · P1 · S
- **Objective:** the product, emails and marketing say InventoryOS (C-24).
- **Business reason:** StockPulse branding remains in code (`pyproject.toml` name, config defaults, templates).
- **Requirements:** C-24.
- **Technical:** rename user-facing strings, email sender names, marketing pages, `pyproject.toml` project name; no change to Python package paths.
- **Existing code:** `app/config.py` (`smtp_from`), `app/web/marketing.py`, templates.
- **DB · API · UI:** text only.
- **Security:** none.
- **Depends on:** —
- **Acceptance:** no "StockPulse" in user-facing output (grep test on templates and emails).
- **Tests:** grep test.
- **Done when:** merged.

### T4.12 Legal and support pages · P1 · S
- **Objective:** privacy policy, terms, support contact and sub-processor list published (NFR §8 gate 10).
- **Business reason:** required before the first paying customer (SAAS-D-06).
- **Requirements:** SAAS-D-06, NFR §7.
- **Technical:** pages and links from footer and sign-up; content supplied by the owner (legal review recommended).
- **Existing code:** `/terms`, `/privacy` routes (`app/web/marketing.py:71-82`).
- **DB · API · UI:** pages.
- **Security:** none.
- **Depends on:** owner content.
- **Acceptance:** pages live on production.
- **Tests:** link checks.
- **Done when:** published.

### T4.13 Accessibility review and fixes · P1 · M
- **Objective:** WCAG 2.1 AA for R1 screens (UX-01); not audited so far (U5).
- **Business reason:** staff and owners on phones and tablets; legal exposure.
- **Requirements:** UX-01..09.
- **Technical:** automated checks (axe in Playwright) plus keyboard and screen-reader pass on employee workflows; fixes.
- **Existing code:** templates.
- **DB · API:** none. **UI:** fixes.
- **Security:** none.
- **Depends on:** Stage 2 UI tasks.
- **Acceptance:** zero serious axe violations on R1 screens; manual checklist passed for employee workflows.
- **Tests:** axe checks in the browser suite.
- **Done when:** merged.

### T4.14 Load test at design headroom · P1 · M
- **Objective:** evidence that PERF-01..07 hold at the A-03 headroom (SCL-01).
- **Business reason:** performance is unknown (U4).
- **Requirements:** PERF-01..07, SCL-01.
- **Technical:** scripted load on staging-like data volume (organizations, items, movements per day per NFR §3a); fix hotspots.
- **Existing code:** none.
- **DB:** index tuning. **API · UI:** none.
- **Security:** run on a non-production environment.
- **Depends on:** T4.09.
- **Acceptance:** p95 targets met or deviations accepted by the owner.
- **Tests:** load scripts kept in the repository.
- **Done when:** results recorded.

### T4.15 Invoice extraction trial on real invoices · P1 · S
- **Objective:** measure extraction accuracy and cost on the owner's real invoices (U3, TR-28).
- **Business reason:** decides whether extraction is promoted or kept optional at launch (PD-25).
- **Requirements:** RCV-FR-03, RCV-FR-14, PD-25.
- **Technical:** run the extraction-quality report over the owner's convenience-store and snack-box invoices on staging; record accuracy and cost per invoice; produce-supplier invoices still needed later.
- **Existing code:** `app/orchestration/extraction_quality.py`, `app/extract/invoice.py`.
- **DB · API · UI:** none.
- **Security:** documents stay in the owner's tenant on staging; sub-processor disclosed.
- **Depends on:** T2.11; OA-4 (owner provides invoices).
- **Acceptance:** report with per-field accuracy and cost.
- **Tests:** the report.
- **Done when:** results recorded; PD-25 cap value confirmed.

### T4.16 Contract migration (DM-16) · P1 · S
- **Objective:** remove the transition structures once nothing reads them: `users.tenant_id`, the `stock_items` compatibility layer, `stock_items.qty_base`, legacy writers.
- **Business reason:** two sources of truth (a balance column and the ledger) invite the overwrite bugs the ledger exists to prevent.
- **Requirements:** DR-01, DR-03, REL-10.
- **Technical:** prove no reader remains (static test + grep); forward migration dropping the columns and the compatibility layer; legacy history tables (`inventory_counts`, `inventory_events`, `sales_daily`) stay read-only and are not dropped.
- **Existing code:** `users.tenant_id` (`app/models.py:128`), `stock_items` (`app/models.py:195-238`).
- **DB:** destructive contract migration — owner approval at the time (REL-10). **API · UI:** none.
- **Security:** none.
- **Migration step:** DM-16 — [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md#3-sequence).
- **Depends on:** T1.05, T1.13, T1.18, T3.12.
- **Acceptance:** no code references the dropped columns; migration tests pass on Postgres; all DM invariants still hold.
- **Tests:** migration test; static test.
- **Done when:** merged before the launch gate review (T4.17).

### T4.17 Launch gate review · P1 · S
- **Objective:** confirm every NFR §8 production-readiness gate with evidence (G-R1).
- **Business reason:** "built" is not "production-ready" (owner's standing distinction).
- **Requirements:** NFR §8 gates 1–12, AC-SAAS-09, AC-SAAS-10.
- **Technical:** walk the [security and release checklist](SECURITY_AND_RELEASE_CHECKLIST.md); observed onboarding sessions with non-founders for both paths.
- **Existing code:** —
- **DB · API · UI · Security:** checklist.
- **Depends on:** every P1 task, including T4.16.
- **Acceptance:** every gate has evidence; owner signs off.
- **Tests:** —
- **Done when:** owner approval recorded as a C-decision.

---

## Stage 4B — First post-launch increment, R1.1 (P2, outline)

Elaborated to full task format when G-R1 passes; order adjusted by pilot feedback. Priority P2
because each item is valuable but none is needed for a single-location business to run safely at
launch (C-29).

| ID | Task | Objective and business reason | Requirements | Depends on | Outline acceptance | Size |
|---|---|---|---|---|---|---|
| T4B.01 | Purchase orders and PO-linked receiving | Order from suppliers inside the product; receiving already has a nullable PO reference | PRC-FR-04..07, CMD-FR-06 | T2.11 | AC-PRC-03..05 | L |
| T4B.02 | Outbox and optional Square write-back | Owner's original pain (sold-out items online) solved safely: local commit first, retries, dead letters → exceptions | POS-FR-13, C-34, INTEGRATION §3.4, REL-04 | T3.12 | Failed write never loses local data; `pos_correction` only for write-back items | M |
| T4B.03 | Generic sales API | First-party ordering sites (JuJu) and unsupported POSs send sales in the normalized format | POS-FR-16 | T2.04, T4.07 | API key per location; idempotent by line key; rate-limited | M |
| T4B.04 | Specialist role presets and PIN switch | Inventory manager, Purchasing, Production presets; quick person switch on shared tablets | STF-FR-02 (R1.1), STF-FR-09, PD-14 | T1.06 | Permission tests per preset; PIN attribution on records | M |
| T4B.05 | Blind counts, pre-approval, batch-level counts | Stronger count controls for growing teams | REC-FR-04, REC-FR-08 (R1.1), REC-FR-10 | T1.23 | Expected hidden from counter; lines held for approval | M |
| T4B.06 | Refund classification | Operator marks a refunded item restock or waste | POS-FR-08 (R1.1), PD-10 | T3.07 | Restock reverses consumption; waste records refund-related waste | S |
| T4B.07 | Remove/substitute modifiers, bulk recipe import, starter templates | Faster onboarding and fuller modifier semantics | RCP-FR-03 (R1.1), RCP-FR-15, INV-FR-08a | T2.02 | Import of a recipe file; templates per business type | M |
| T4B.08 | Hour-level shelf life | Bubble-tea toppings expire in hours | PRD-FR-15, PD-40 | T2.09 | Expiry date-time on prepared batches | S |
| T4B.09 | Theoretical vs actual, supplier trends, recipe cost history | The variance insight Connected customers pay for | ANL-FR-10, ANL-FR-15, STD-FR-10, RCP-FR-18 | T2.17 | Variance per item per count interval, days without sales excluded in Standalone | M |
| T4B.10 | What to make; prepared-item demand | Prep planning from expected use (JuJu precedent) | PRD-FR-09, ROR-FR-06 | T2.15 | Suggestion in batch-size steps | M |
| T4B.11 | Credit memos and supplier returns | Correct stock and cost when goods go back | RCV-FR-15 | T2.11 | Adjustment with reason `returned_to_supplier` linked to the receiving | S |
| T4B.12 | Offline queueing for floor tasks | Basements and walk-ins lose signal | WF-18 failure handling | T2.21 | Entries queued on device post with original timestamps | M |
| T4B.13 | Postgres row-level security as a second guard | Defense in depth behind the scoped layer (C-33) | SAAS-T-04, SEC-03 | T1.03 | Isolation suite passes with RLS on; a deliberately unscoped query returns nothing | M |

## Stage 5 — JuJu operational validation (P2, outline)

Plan and scenarios: [JUJU_VALIDATION_PLAN](JUJU_VALIDATION_PLAN.md). P2 because it validates the
product but does not block launch; it runs at checkpoints.

| ID | Task | Objective | Depends on | Outline acceptance |
|---|---|---|---|---|
| T5.01 | JuJu reference tenant | One-way import of JuJu's recipe, ingredient and prep-recipe data into an InventoryOS organization; missing values supplied by the owner, never invented | M3 | Tenant reproduces JuJu's recipes, modifiers (ice, pumps) and prep recipes |
| T5.02 | Scripted JuJu operating days | Same scripted inputs in both systems; compare balances, consumption and waste cost | M4, T5.01 | Discrepancies classified per the plan's tolerance |
| T5.03 | Feedback triage | Findings become bugs, proposed requirements or JuJu-specific notes | T5.02 | Every finding classified; none changes scope silently |
| T5.04 | JuJu-as-tenant decision | Owner decides whether JuJu uses InventoryOS via the sales API (C-42 revisit) | M7, JuJu live | Decision recorded; shadow mode first if yes |

## Stage 6 — Multi-location (P3, outline)

**Gate:** a paying customer with a second location asks, and PD-33 is decided (T6.01). P3 because
the R1 data model is already location-aware.

| ID | Task | Objective | Requirements | Depends on |
|---|---|---|---|---|
| T6.01 | Multi-location pricing and entitlement | Decide per-location vs package (PD-33); configure plans | PD-33, E-06 | T4.01 |
| T6.02 | Location administration and scoped roles UI | Manage many locations; assign people per location | ADM-FR-13, STF-FR-10 (UI) | T6.01 |
| T6.03 | Inter-location transfers | Send and receive stock between locations with in-transit state | INV-FR-25, WF-22 | T6.02 |
| T6.04 | Consolidated and comparative reporting | Totals and comparisons across locations | CMD-FR-17, ANL-FR-16 | T6.02 |
| T6.05 | Central purchasing | Suggestions and POs across locations | ROR, T4B.01 | T6.03 |

## Stage 7 — Advanced (P3, outline)

**Gate:** ≥ 3 paying customers ask for the same capability (PD-34 recommendation). Forecasting also
needs ≥ 60 days of ledger history.

| ID | Task | Objective | Requirements |
|---|---|---|---|
| T7.01 | Demand forecasting | Weekday-aware forecast feeding usage (JuJu's 4-week weekday average as the starting design) | ROR-FR-09, ANL-FR-17 |
| T7.02 | Automated replenishment | Draft POs from suggestions automatically; never sent without a person (ROR-FR-08) | ROR-FR-08 |
| T7.03 | Supplier analytics | Price trends, fill rate, lead-time reliability | ANL-FR-15 |
| T7.04 | Public API and API keys | Read/write API with scopes and rate limits | Advanced entitlement |
| T7.05 | Additional POS adapters | Next adapter chosen by recorded adapter demand (Toast is a known data point) | POS-FR-15 |
| T7.06 | Custom roles and extended audit history | Granular permissions; longer audit retention and exports | PD-11, PD-34 |
| T7.07 | Custom automation rules | Configurable rules (e.g. notify when X) | Advanced entitlement |

## Stage 8 — Native POS feasibility (Deferred, evaluation only)

**Gate to start:** $1,000 MRR (C-26) and PD-35 evidence. No build without explicit owner approval.

| ID | Task | Output |
|---|---|---|
| T8.01 | Demand evidence review | Signup Scenario C interest, interviews, pre-commitments vs PD-35 criteria ([CUSTOMER_VALIDATION_PLAN](CUSTOMER_VALIDATION_PLAN.md)) |
| T8.02 | Build vs partner vs white-label vs integrate study | Costed options incl. processing, hardware, support |
| T8.03 | Compliance, payments, hardware and support assessment | Risks and obligations per option |
| T8.04 | Go/no-go recommendation | Owner decision recorded as a C-entry |
