# Execution log

One entry per task, newest first. Every entry: implemented · verified · files changed · open
issues · decisions · next task.

---

## 2026-11-06 — T1.02 Scoped data-access layer (C-33)

**Implemented**
- `app/access/` (strict mypy): `OrgContext` (frozen; organization, user, locations; built only by
  `for_user` through the new `require_org` dependency, or `system(tenant_id)` for scheduled jobs,
  signed webhooks and the command line) and `select_scoped`, `get_scoped`, `update_scoped`, which
  add the organization filter and, for a location-limited context, the location filter. They
  refuse instead of guessing: tables owned by no organization, tables without `tenant_id`, and
  (under a location limit) tables whose location is known only through a parent.
- Every service that took `tenant_id: int` now takes `org: OrgContext` (57 functions: 44 public, 13 private helpers); the
  document, promotion and scanning services that loaded rows by id take it too. Rewritten with an
  AST script, then reviewed by hand.
- Every route that reads tenant rows takes `org`. The per-route `if row.tenant_id != tenant.id`
  checks are gone: a row from another organization is simply not found (404).
- `app/access/system.py` holds the only cross-organization queries, each named for its job:
  sign-in by email, the connection a Square webhook names, the scheduler's tenant lists, the
  Stripe account lookups. Webhooks and scheduled jobs then work through `OrgContext.system`.
- The readiness check's `SELECT 1` moved to `app.db.ping()`. `parse-doc` on the command line now
  takes `--tenant`, like the other per-tenant commands.
- ADR-0026 (Proposed until this pull request is merged); `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_scoped_access.py` (10): the scan finds no raw query in `app/web` or
  `app/orchestration`, and a second test proves it catches the old pattern (six findings in a
  pre-T1.02 snippet). Scoped select, get and update stay inside one organization; a location
  limit narrows location rows and not organization-wide ones, and refuses counts (location known
  only through the item); global tables are refused; a context is frozen; sign-in by email and
  webhook merchant lookups behave.
- Existing cross-tenant tests pass unchanged; the suite's callers now pass a context.
- Full suite 583 passed, 9 skipped (573 before; the skips are Postgres-only migration runs);
  ruff, mypy (74 files) and the docs checker clean. Demo seeder, `health` and `report` commands
  ran on a scratch database.

**Deviations from the task text**
- `OrgContext` has no permissions field: the permission catalogue arrives in T1.06, and a field
  nothing checks would be dead weight (ADR-0026).
- The static test also bans `.query()` and SQLAlchemy's `text`, `insert` and `delete`, not only
  `db.get`, `session.get` and `select`.

**Files changed**
- `app/access/{__init__,context,scoped,system}.py` (new), `app/security.py` (`require_org`),
  `app/db.py` (`ping`), every module in `app/orchestration/` that queries, `app/web/{auth,
  dashboard,health,ops,promotions,receiving,scanning,webhooks}.py`, `app/cli.py`; tests: the new
  `test_scoped_access.py` and the callers in eleven test modules; ADR-0026 (new), ADR-0025
  (Accepted), ADR-0001, the ADR index, `CLAUDE.md`, `pyproject.toml` (comment),
  `TARGET_ARCHITECTURE.md`, task index (T1.01 → Completed, T1.02 → In review), this log.

**Open issues**
- Outside the scan, as the task scopes it: `app/load` (Square writers, replaced through the
  ledger in T1.16–T1.18), `app/notify`, `app/security`, `app/entitlements` and the command line
  keep their own tenant filters.

**Next task**
- T1.03 Tenant-isolation test suite.

---

## 2026-11-06 — T1.01 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #19 was merged with a merge commit (`38dbcf8`). CI
  on its head `7700b14`, all five required jobs green: SQLite 573 passed, 9 skipped; PostgreSQL 16
  582 passed, 0 skipped, so DM-2 converted, round-tripped and was checked on Postgres; lint and
  types; dependency audit; Docker image. Same five green on `main` after the merge. Acceptance
  met: probe PA5 inverted and retired; the migration upgrades and downgrades over populated data
  on Postgres.

**Decisions**
- ADR-0025 accepted with the merge.

**Files changed**
- Task index (T1.01 → Completed), ADR-0025 status and index, this log.

---

## 2026-11-06 — T1.01 Schema hygiene: timestamptz, constraints, delete rules (DM-2)

**Implemented**
- `app.db.UTCDateTime`: an instant is written and read as an aware UTC datetime on both databases
  (`timestamptz` on Postgres); a naive value is refused rather than guessed. All 31 timestamp
  columns use it. The `_aware()` helpers (entitlements, alerts, views, Square webhooks) and the
  zone patches in Stripe events, subscriptions and sync are gone. The two places that parse
  Square's text stamps (webhook counts, OAuth token expiry) read a stamp without an offset as UTC.
- Delete rules on all 44 foreign keys: `ON DELETE RESTRICT` by default (`ref()`); `CASCADE`
  (`owned_by()`) only for a document's lines and extraction corrections and a sold item's recipe
  components.
- Ten named CHECK constraints: an item's reorder point, par level and lead time are not negative
  and its pack size is above zero; a batch and a recipe component hold more than nothing; the
  `source` of counts, product codes, batches and email suppressions is one the code writes. Not
  checked, on purpose (ADR-0025): balances, which DR-08 lets go negative, and
  `inventory_events.source`, which holds legacy values and is replaced by the ledger (T1.16).
- SQLite connections enforce foreign keys, so the suite fails there as it would on Postgres.
- Migration `68c6fdbc31a7` (DM-2): converts the columns `AT TIME ZONE 'UTC'` on Postgres, rebuilds
  the foreign keys with their rules and adds the CHECKs (table rebuilds on SQLite); its downgrade
  restores the previous schema.
- The add-item and thresholds forms answer 400 with a reason to a negative threshold or an empty
  pack instead of hitting the database; the thresholds form now also answers 400, not 500, to a
  malformed number. The demo seeder skips a lot that its random sales emptied.
- Probe PA5 retired. ADR-0025 (schema conventions, Proposed until this pull request is merged);
  `CLAUDE.md` states the rule for new tables.

**Verified (locally, SQLite)**
- `tests/test_schema_hygiene.py` (27): instants come back aware in UTC, also when written from
  another zone, and compare correctly in queries; a naive value is refused; every timestamp column
  is `UTCDateTime`; each of the eleven impossible rows is refused; negative balances, minus zero
  and nulls are accepted; a reference to a missing row is refused; deleting an item with history
  or a tenant is refused; deleting a document takes its lines and corrections, a sold item its
  recipe; the forms refuse negative thresholds and an empty pack; Square expiries are aware.
- `tests/test_migrations.py`: a migrated database has every CHECK and delete rule the models
  declare (and `timestamptz` on Postgres); with rows present, DM-2 reads a stored naive
  `2026-11-04 12:00:00` as 12:00 UTC, the downgrade gives back the same wall clock, and no row is
  lost either way. Existing chain, model-drift and rows-survive tests pass.
- Full suite 573 passed, 9 skipped (544 and 7 before; the two new skips are the new migration tests' Postgres runs); ruff and mypy clean; docs checker 0 problems. The demo seeder ran through the
  whole migration chain on a scratch database (7 lots, 9 counts, aware timestamps).
- Postgres 16 runs in CI only: Docker is not running on this machine.

**Files changed**
- `app/db.py`, `app/models.py`, `migrations/versions/68c6fdbc31a7_schema_hygiene.py` (new),
  `app/entitlements.py`, `app/orchestration/{alerts,stripe_events,subscriptions,sync,views}.py`,
  `app/web/{dashboard,webhooks}.py`, `app/providers/square/oauth.py`, `app/cli.py`; tests
  `test_schema_hygiene.py` (new), `test_migrations.py`; probe file (PA5 retired);
  `docs/adr/0025-schema-integrity-conventions.md` (new) and the ADR index, `CLAUDE.md`, task index
  (T1.01 → In review), this log.

**Open issues**
- A local database holding a value the new CHECKs refuse (a negative threshold typed before this
  change) stops the upgrade with the constraint's name; fix the row and rerun. Only development
  databases exist.

**Next task**
- T1.02 Scoped data-access layer (C-33).

---

## 2026-11-06 — T0.05 and T0.11 completed; staging deferred (C-49); gate G0

**Verified**
- T0.05: the owner merged PR #18 with a merge commit (`ee755a2`). CI on its head `f050811`, all
  five required jobs green: SQLite 544 passed, 7 skipped, docs checker 0 problems; PostgreSQL 16
  551 passed, 0 skipped; lint and types; dependency audit; Docker image. The same five are green
  on `main` after the merge. Acceptance met: no snack-box symbol remains (test), 53 product
  routes, five Square scopes, migration tests pass on both databases.
- T0.11: PR #16 was merged with a merge commit (`56d6e7c`); CI on its head green on all five jobs
  (SQLite 614 passed, 6 skipped; PostgreSQL 16 620 passed, 0 skipped). Acceptance met: an ADR
  for every approved ID, indexed and enforced by the docs checker; `CLAUDE.md` points to them.

**Decisions**
- C-49 (owner, 2026-11-06, "defer staging"): staging is not created until a task needs a public
  HTTPS endpoint, first T3.11 (Square sandbox run). T0.09 is Deferred: `render.yaml` stays on
  `main`; the Blueprint, its secrets, the C-48 photo storage and T0.09's acceptance checks are
  done before T3.11. TR-26 moves from G0 to G5.
- Gate G0: every exit criterion is met except "staging deployed from `main`", which C-49 defers:
  CI green on SQLite and Postgres 16 for every push (T0.02); suite hermetic with a developer
  `.env` present (T0.01); dependencies locked and audited (T0.03); probes R3 and R7 retired and C8
  fixed (T0.08, T0.06, T0.07); write-back off with no swallowed failures (T0.08); snack-box code
  and scopes gone (T0.05). The owner started Stage 1 on 2026-11-06 ("Let's start on Stage 1").

**Files changed**
- Task index (T0.05, T0.11 → Completed; T0.09 → Deferred; T1.01 → In progress), T0.09 entry,
  `PRODUCT_DECISIONS.md` (C-49), master roadmap and testing strategy (G0), OD-03, TR-26,
  `docs/roadmap/README.md`, ADR-0002, this log.

**Next task**
- T1.01 Schema hygiene (DM-2).

---

## 2026-11-06 — T0.05 Extract the snack-box module (C-37)

**Implemented**
- **Snack-box repository** (`Souru0712/Snack-box`, private, created by the owner): first commit
  `ae99561` on `main` (the repository was empty, so there was no branch to open a pull request
  against). It holds the module copied unchanged from InventoryOS commit `56d6e7c`: 36 files that
  belonged only to it (orchestration `boxes`/`customers`/`referrals`, web `boxes`/`shop`, pure rules
  `allocation`/`boxes`/`credits`/`taste`, templates, the customer sign-in email, the migration that
  created its tables, seven test files), verbatim excerpts of its parts in five shared files
  (models, entitlements, security, scheduler, Square client) headed with their source lines, and a
  README with permalinks to the smaller references. The owner's local CSV tool was not touched.
- **InventoryOS:** the 35 files removed; routers unmounted; the 11 models and five enums, the
  `inventory_events.box_order_id` column, `SNACK_BOX_TENANT_IDS` and `CUSTOMER_LOGIN_TTL_MINUTES`,
  `owns_snack_box`, shopper sign-in, the box housekeeping job, the Square payment and refund webhook
  branch, `create_payment_link`, the shop's navigation, its email test, and the mypy override for
  its modules. Square now asks merchants for five scopes (merchant profile, items read, inventory
  read and write, orders read) instead of eight.
- **DM-1:** migration `5c1d0f2a9b31` drops the 11 tables, their five Postgres enum types and
  `inventory_events.box_order_id`; it is the reverse of `e7b19fdcb1ce`, generated from it, and keeps
  `webhook_events`, which that migration also created and the product uses. Its downgrade recreates
  the tables empty, so the chain still rolls back to base. Destructive, approved by C-37.
- README, `.env.example`, `CLAUDE.md`, ADR-0004 and ADR-0016 updated; OD-02 done.

**Verified (locally)**
- Routes: 88 before, 35 of them the shop's; 53 after, as T0.05 specifies.
- `tests/test_snack_box_removed.py`: 53 routes and none under `/shop` or `/app/boxes`; Square
  scopes are exactly the five; no trace of the module in `app/` or `scripts/` (names, paths,
  scopes). `tests/test_migrations.py`: the 11 tables and the column are gone after migrating,
  `webhook_events` stays; the existing chain, downgrade-to-base and rows-survive tests pass on
  SQLite (their Postgres runs are in CI).
- ruff, mypy (70 files) clean; docs checker 0 problems. Full suite 544 passed, 7 skipped: 614
  before, less the shop's 74 tests (its own files and one email test), plus the new tests; the
  extra skip is the new migration test's Postgres run.

**Files changed**
- 35 files deleted; `migrations/versions/5c1d0f2a9b31_drop_snack_box_tables.py` (new);
  `app/main.py`, `models.py`, `config.py`, `entitlements.py`, `security.py`,
  `orchestration/scheduler.py`, `web/webhooks.py`, `web/templating.py`, `templates/base.html`,
  `providers/square/oauth.py`, `client.py`, `webhooks.py`; tests `conftest.py`, `test_email.py`,
  `test_migrations.py`, `test_snack_box_removed.py` (new); `pyproject.toml`, `.env.example`,
  `README.md`, `CLAUDE.md`, ADR-0004, ADR-0016, task index (T0.05 → In review), OD-02, this log.

**Open issues**
- Existing Square connections were granted the three removed scopes; they keep them until the
  merchant reconnects. No merchant is connected today.

**Next task**
- Stage 1 starts with T1.01 (schema hygiene), now unblocked.

---

## 2026-11-06 — T0.09 Staging on Render, part 1: Blueprint and C-48

**Implemented**
- `render.yaml` Blueprint: web service (`/readyz` health check), scheduler worker
  (`python -m app.cli scheduler`), Postgres 16 reachable only on Render's private network, region
  Virginia, deploy only when `main`'s required checks pass. Secrets are not in the file:
  `SECRET_KEY` is generated by Render; `ENCRYPTION_KEY`, `SENTRY_DSN` and `APP_BASE_URL` are asked
  for when the Blueprint is created. Plans are Render's defaults, shown with their cost before
  creation; the database must be a paid plan for point-in-time recovery (3 days on Render's Hobby
  workspace, 7 on Pro; none on the free database).
- Web runs one instance, not T0.09's two, until photos move into Postgres: on an instance's own
  disk a photo is invisible to other instances and lost at each deploy.
- An encryption key was generated into `.claude/staging-secrets.txt` (git-ignored, never shown in
  chat) for the owner to paste into Render and then delete.
- **C-48** (owner, 2026-11-06, option A): an invoice photo is kept until its delivery is received
  plus 7 days, then deleted; stored in Postgres; no AWS account. Recorded in PRODUCT_DECISIONS,
  RCV-FR-02, ADR-0024 (supersedes ADR-0012), the ID-12 row, T0.09's technical line and OD-03.
- Sentry advice to the owner: error monitoring only. The app sends errors with personal data
  scrubbed; tracing stays off (`SENTRY_TRACES_SAMPLE_RATE` 0); logs stay in Render, not Sentry.

**Verified**
- `render.yaml` parses; the field names and values follow Render's Blueprint reference as read on
  2026-11-06 (its code examples did not load, so the `envVars` layout follows Render's usual
  form); Render validates the file when the Blueprint is created. Migrations at startup take a
  Postgres advisory lock, so web and scheduler starting together is safe. Docs checker 0 problems.
- **Not verified:** a deploy. That needs the owner to create the Blueprint.

**Files changed**
- `render.yaml` (new), `docs/adr/0024-invoice-photos-in-postgres.md` (new), `docs/adr/0012…`,
  `docs/adr/README.md`, `PRODUCT_DECISIONS.md`, `PRODUCT_SPECIFICATION.md`,
  `IMPLEMENTATION_DECISIONS.md`, `DEVELOPMENT_TASK_BREAKDOWN.md` (T0.09 → In progress),
  `RISK_AND_DEPENDENCY_REGISTER.md`, this log.

**Next**
- Owner: create the Blueprint once this is on `main`. Then part 2: database photo storage and the
  7-day deletion (C-48), two web instances, staging kept out of search engines, and T0.09's
  acceptance (`/readyz`, one active scheduler, a test error in Sentry, the restore procedure).
- T0.05 (the owner created the private `Snack-box` repository on 2026-11-06).

---

## 2026-11-06 — T0.11 Governance scaffolding (ADRs, status tracking)

**Implemented**
- `docs/adr/`: `README.md` (when to write an ADR, numbering, statuses Proposed / Accepted /
  Superseded, the index, and a pointer to the roadmap's governance for task status and the
  execution log), `0000-template.md`, and ADR-0001..0023, one per approved implementation
  decision ID-01..ID-23. Each states status (Accepted 2026-11-05, C-43), the ID with a link to its
  row, the tasks it shapes, context, decision, alternatives, and consequences, including where
  Stage 0 already stands (for example ADR-0005 records Dependabot's lockfile-only setting, ADR-0007
  that `hx-on` cannot run under the CSP from T0.06, ADR-0016 that the snack-box repository does not
  exist yet).
- `scripts/check_docs.py` checks links under `docs/adr/` and fails when an approved ID has no ADR
  or more than one, when two ADRs share a number, or when an ADR is missing from the index.
- `CLAUDE.md` points to the ADR index (reading list item 4; new ADRs from the template, indexed);
  the roadmap README and IMPLEMENTATION_DECISIONS link to it.
- Task status workflow: already defined in the roadmap README §4.2–4.3 and in use since T0.01
  (index status, one log entry per task, owner merges each task's pull request); the ADR README
  points to it rather than repeating it.
- `RISK_AND_DEPENDENCY_REGISTER.md` OD-02: the owner's local `Snack-box` folder is a separate CSV
  command-line tool, not a git repository; the private repository for T0.05 is still to be created.

**Verified (locally)**
- Docs checker 0 problems. Negative runs: with ADR-0017 removed it reports "ID-17 is recorded in
  0 ADRs" (and the index's broken link); with a stray `0024` copy of ADR-0009 carrying a bad link
  it reports the broken link, "ID-09 is recorded in 2 ADRs" and "0024-extra.md is not in the
  index".
- ruff, ruff format, mypy clean; `tests/test_docs_contract.py` passes. Only documents and the
  checker changed, so the full suite was left to CI.

**Files changed**
- `docs/adr/` (25 new files), `scripts/check_docs.py`, `CLAUDE.md`, `docs/roadmap/README.md`,
  `docs/roadmap/IMPLEMENTATION_DECISIONS.md`, `docs/roadmap/RISK_AND_DEPENDENCY_REGISTER.md`, task
  index (T0.10 → Completed, T0.11 → In review), this log.

**Next task**
- Stage 0's remaining tasks wait for the owner: T0.05 (snack-box repository, OD-02) and T0.09
  (Render, AWS and Sentry accounts, OD-03).

---

## 2026-11-06 — T0.10 completed

**Verified**
- At the owner's request PR #15 was merged with a merge commit (`497db30`). CI on its head
  `fe5023c`, all five required jobs green: SQLite 614 passed, 6 skipped; PostgreSQL 16 620 passed,
  0 skipped; lint and types; dependency audit; Docker image. Same five green on `main` after the
  merge. Acceptance met: every log line is JSON with `request_id`; a request with a password field
  leaves no trace of it.

**Files changed**
- Task index (T0.10 → Completed), this log.

---

## 2026-11-05 — T0.10 Structured logs and request ids (OBS-01)

**Implemented** (`app/observability.py`)
- `JsonFormatter`: one JSON object per line with `ts`, `level`, `logger`, `message`,
  `request_id`, and `job`, `tenant_id`, `user_id` when known; the exception text for errors.
  Anything shaped like an email address is masked (`[email]`) in messages and exceptions, as a
  last resort behind the rule below.
- `RequestContext`, the outermost middleware: a request id per request (a caller's
  `X-Request-ID` is kept if it is 8–64 plain characters, otherwise replaced), returned as
  `X-Request-ID`, and one access line per request with method, path without the query string,
  status and duration. The context is one dict shared with FastAPI's threadpool copies, so
  `current_user` attaches `tenant_id` and `user_id` and every later line of the request,
  including the access line, carries them.
- Scheduled jobs: each run gets `request_id` `job-…` and `job` (`scheduler._job`); the per-tenant
  loops (sync, weekly reports, health snapshots) wrap each tenant in `log_context(tenant_id=…)`.
  Lines outside any request or job carry the process's own id (`process-…`).
- `configure_logging()` in the web process and the CLI (which also runs the scheduler). uvicorn's
  own lines go through the JSON handler; its access log is off (also `--no-access-log` in the
  Dockerfile) because it prints query strings, and customer sign-in links carry their token in
  one. `httpx`/`httpcore` drop to WARNING: they log every outgoing URL at INFO.
- Five log calls printed email addresses (email suppression, SMTP skipped, send failure, alert
  preferences, email settings); they now name the user id or nothing. The snack-box modules rely
  on the masking until T0.05 removes them.
- Sentry scrubbing (`app/monitoring.py`) unchanged. `CLAUDE.md`: the logging rules.

**Verified (locally)**
- `tests/test_observability.py` (9): sign-up, a wrong and a right sign-in leave neither the
  password nor the email anywhere in the captured JSON, and every line has a `request_id`; the
  response's `X-Request-ID` matches its access line; a signed-in request's line names its tenant
  and user; a caller's id is kept and a hostile one replaced; a token in a query string never
  reaches the log; an email in a message is masked; a job run has its own id and names the
  tenant; uvicorn's access log is off and `httpx` is at WARNING; a start-up line has the process
  id. The first run of these tests caught two real leaks (an `httpx` line with the full URL and
  lines without any id), fixed above.
- Full suite 614 passed, 6 skipped (605 + 9); ruff, ruff format, mypy (79 files) clean.

**Files changed**
- `app/observability.py` (new), `app/main.py`, `app/cli.py`, `app/security.py`,
  `app/orchestration/scheduler.py`, `app/notify/email.py`, `app/orchestration/alerts.py`,
  `app/web/settings.py`, `Dockerfile`; `tests/test_observability.py` (new); `CLAUDE.md`; task
  index (T0.08 → Completed, T0.10 → In review); this log.

**Open issues**
- Not checked on a deployed host (T0.09): what the platform's log viewer does with JSON lines.

**Next task**
- T0.11 governance scaffolding. T0.05 and T0.09 wait for the owner (OD-02, OD-03).

---

## 2026-11-05 — T0.08 completed

**Verified**
- The owner accepted T0.08 as write-back only, leaving the Square count overwrite to T1.18
  ("Accept, merge it"), and asked for the merge. PR #14 merged with a merge commit (`6918ee5`).
  CI on its head `4f0c777`, all five required jobs green: SQLite 605 passed, 6 skipped;
  PostgreSQL 16 611 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five
  green on `main` after the merge.

**Files changed**
- Task index (T0.08 → Completed), this log.

---

## 2026-11-05 — T0.08 Disable Square write-back; no swallowed failures (C-34, C4)

**Implemented**
- `SQUARE_WRITE_BACK` setting, default off (`app/config.py`, `.env.example`).
- The four flows that wrote to Square no longer call it: invoice apply
  (`push_adjustments_to_square`), receiving by barcode scan (`adjust_stock_in_square`), the scan
  count and the dashboard count (`push_counts_to_square`). Each changes InventoryOS only. With
  the calls went the three `except Exception` blocks that logged a failed push and carried on
  (C4).
- `push_counts_to_square` and `push_adjustments_to_square` are kept for R1.1's outbox and raise
  `WriteBackDisabled` while the setting is off, so a stray call fails loudly instead of skipping
  a write its caller believes happened.
- Copy no longer promises Square updates: the receiving page's "count(s) pushed back to Square"
  notice and its "pushed back so your storefront matches" line are gone; after a scan of an item
  Square tracks, the page says the number is saved here only and Square's next sync replaces it.
  `pushed_to_pos` is gone from the scan JSON and the receiving redirect (the database column
  stays; it is now always false).
- Probe R3 retired (comment in `docs/audit/probes/test_audit_probes.py`). `NEEDS-YOU.md` item 1
  says write-back is off until R1.1.
- Remaining Square calls are reads. The syncs (catalog, counts, sales) already record a failure
  on `sync_runs` with the exception and log it. The one read outside a sync, the scan lookup's
  refresh of a single item, only logged a failure and showed the local number as if fresh: it now
  records a failed `sync_runs` row (kind `item`, `record_square_failure`), which the dashboard's
  sync status shows, and the scan page says Square could not be reached and the number is
  InventoryOS's last count.
- Also in this PR: C-45, C-46 in the extraction prompt (entry below).

**Verified (locally)**
- `tests/test_square_writeback.py` rewritten: invoice apply, scan receive, scan count and
  dashboard count make zero Square calls and change InventoryOS stock (24 → 48, 30, 37, 40); the
  setting is off by default; both push functions raise while it is off. With the old flows
  restored all six fail. The R1.1 semantics (adjustment not count, idempotent receipt, derived
  items never pushed) are kept as tests with write-back switched on. A failed Square read during
  a scan lookup leaves a failed `item` sync run and `square_unreachable` in the response.
- Full suite 605 passed, 6 skipped (598 + 7); ruff, ruff format, mypy (78 files) clean.

**Acceptance point for the owner**
- The acceptance reads "probe R3 scenario can no longer lose a delivery". R3's second half is the
  hourly sync: `apply_provider_counts` sets a Square-tracked item to Square's count. With
  write-back off, a delivery or count recorded in InventoryOS for such an item is now replaced at
  the next sync every time, not only after a failed push. Stopping that is the inbound half of
  C-34, which the roadmap gives to T1.18 ("`apply_provider_counts` stops touching stock").
  Nothing is silent any more (no push is attempted or reported, and the scan page says Square's
  sync will replace the number), but the delivery is not kept for those items until T1.18. No
  tenant uses Square tracking today (the owner's store runs Toast; C-40 no pilots yet).
  Recommendation: accept T0.08 as write-back only and leave the overwrite to T1.18.

**Files changed**
- `app/config.py`, `.env.example`, `app/orchestration/sync.py`, `receiving.py`, `scanning.py`,
  `app/web/receiving.py`, `scanning.py`, `dashboard.py`, templates `receiving_review.html`,
  `scan.html`; `tests/test_square_writeback.py`; `docs/audit/probes/test_audit_probes.py`;
  `NEEDS-YOU.md`; task index (T0.08 → In review); this log.

**Open issues**
- The snack-box module (`app/orchestration/boxes.py`) still adjusts Square stock when a box is
  packed, through its own client call. It leaves this repository in T0.05; left unchanged here.

**Next task**
- T0.10 structured logs and request ids; T0.11 governance scaffolding. T0.05 waits for the
  snack-box repository (OD-02).

---

## 2026-11-05 — Owner decisions on the invoice gaps (C-45, C-46, C-47)

- The owner's answers on the gaps recorded in the entry below: lines circled or marked "Return"
  by hand are left out of a scan (C-45); shipped beats ordered, including shipped weight (C-46);
  supplier item codes are not captured from invoices (C-47). The owner offered an invoice longer
  than one page (OD-09).
- `app/extract/invoice.py`: the system prompt carries C-45 and C-46;
  `tests/test_invoice_ai.py` checks both rules reach the model. Whether the model follows them is
  for the real-invoice trial (T4.15).
- The pack-notation fix below was merged by the owner as PR #13 (`2cf167b`); CI on its head
  `7b6fb4b` was green on all five jobs (SQLite 598 passed, 6 skipped; PostgreSQL 16 604 passed,
  0 skipped), and the same five were green on `main` after the merge.

---

## 2026-11-05 — Receiving: supplier pack notations from the owner's invoices (unplanned fix)

Not a roadmap task. The owner shared the front pages of four suppliers' invoices (Central
Boeki, USA Seafood, JFC, Hadson (Toko)). Running their printed pack wording through the
receiving conversion found live quantity bugs; the owner approved fixing them before T0.08.
The photos are not in this repository (it is public; they carry the store's address, phone and
supplier accounts).

**Found (each reproduced against `main` at `9fda108`)**
- `3.9OZ x 14 x 2 #` (Hadson; `#` means pieces) read as 14 packs of 2 lb (28 lb a bundle).
  For a counted item that sent the line to review as a weight; for an item tracked by weight it
  would have booked pounds of chocolate.
- Size-first notations (`2.4 oz x 10 #`), packs within cases (`10/5PK/2.70 FZ`, `3/6/6.76 FZ`)
  and `FZ` for fluid ounces were not read at all.
- Unit abbreviations `cs`, `BX`, `BUNDL`, `PK` (and plurals such as `boxes`) were not taken as
  packs.
- A quantity in pieces was multiplied by the whole case: JFC prints 30 EACH of
  `3/30/1.93 OZ` at $1.05 = $31.50, which became 30 × 90 pieces.

**Implemented** (`app/transform/receiving.py`)
- `parse_pack` reads a chain of numbers joined by `x`, `*` or `/` with exactly one weight or
  volume measure, in either order; the other numbers multiply into pieces per pack (`PK`, `PC`,
  `EA`, `#` allowed as piece markers). `#` is pounds only when it is the notation's only measure
  (`10/1.1#`, JFC). A chain with no measure, such as an expiry date `3/31/2027`, is skipped.
- `pack_word()` with `PACK_ABBREVIATIONS`; `fz` → fluid ounces.
- Conversion: a quantity in pieces counts pieces (one piece = the stated size); a counted item
  received by the pack gets its pieces (`0.5 BUNDL` of `3.9OZ x 14 x 2 #` → 14). Before, a
  counted item with a weight notation always went to review.
- `normalize_name` strips the same notations from names (alias keys are re-normalized on both
  sides when compared, so learned aliases keep matching).

**Verified**
- 27 new cases in `tests/test_receiving.py`, every notation copied from the four invoices; on
  `main` the file does not even collect (the new helpers do not exist) and the KitKat notation
  parses as (14, 2, lb). The existing receiving tests still pass (`6 x 1L`, `12x500g`, unit
  mismatch flagged).
- Full suite 598 passed, 6 skipped (571 + 27 new); ruff, ruff format, mypy (78 files) clean.

**Gaps recorded, not fixed (extractor and product; for T2.11 and the T4.15 trial)**
- **Barcodes:** printed per line on three of the four. They already help: matching looks for a
  stock item's UPC in the line's `raw_text`, which the model is told to copy as printed. There
  is no separate barcode field, so a barcode in its own column depends on the model including
  it in `raw_text`.
- **Returned and hand-corrected lines:** JFC's tempura line is circled "Return"; Hadson's
  KitKat price is hand-corrected 112 → 126. The prompt says to transcribe only print, so a
  returned line would be received. Needs a product decision on handwritten marks.
- **Ordered vs shipped:** USA Seafood's salmon is 1 case ordered, 46.96 lb shipped at a price per
  lb. The prompt asks for the quantity delivered; not checked against the real model.
- **Multi-page invoices:** JFC (1 of 3) and Hadson (1 of 4); a document holds one photo.
- **Supplier item codes** (JFC `05218`, Hadson `C019`) are not captured; they fit
  `SupplierItem` (T2.10).
- **Central Boeki** prints pieces per box (PC 40) beside a quantity already in pieces (12 EA);
  if the model put 40 into `pack_size` the line would be multiplied. Not checked against the
  real model.

**Files changed**
- `app/transform/receiving.py`, `tests/test_receiving.py`; task index (T0.07 → Completed);
  `RISK_AND_DEPENDENCY_REGISTER.md` (OD-09 partly met); this log.

**Next task**
- T0.08 disable Square write-back.

---

## 2026-11-05 — T0.07 completed

**Verified**
- PR #12 merged by the owner (`9fda108`, merge commit). CI on the PR head `5724f17`, all five
  required jobs green: SQLite 571 passed, 6 skipped; PostgreSQL 16 577 passed, 0 skipped; lint and
  types; dependency audit; Docker image. Same five green on `main` after the merge. Acceptance
  met: `/healthz` answers in under 200 ms while an extraction is held open.
- Still not done: a real invoice through streaming extraction.

**Files changed**
- Task index (T0.07 → Completed), this log.

---

## 2026-11-05 — T0.07 Async correctness (C8)

**Implemented**
- Every route is now plain `def`, so FastAPI runs it in its threadpool and a slow call no longer
  stops the event loop. The audit named four (`upload_invoice`, `apply_receipt`, `scan_expiry`,
  `square_webhook`); the same fault was in six more and they are fixed too: the Stripe webhook,
  the email-settings form, and the snack-box `pack`, `save_snack`, `save_settings`,
  `save_review`, `save_preferences` (all ran synchronous database calls on the event loop).
- `app/web/inputs.py`: `posted_form` and `raw_body`, async dependencies that read the body on the
  event loop before the route starts. Uploads read `photo.file` synchronously in the thread.
- Anthropic clients built with a 60 s timeout (`app/extract/__init__.py`; SDK default 600 s).
  Invoice extraction now streams (`messages.stream` → `get_final_message()`), so the 60 s limits
  a silence, not the whole reading: a non-streamed 16,000-token answer from a model that thinks by
  default can take longer than a minute, and a whole-request limit would have failed long
  invoices. The expiry reader (512 tokens) stays a single request.
- `CLAUDE.md`: routes are plain `def`; bodies come from `app/web/inputs.py`.

**Verified (locally)**
- `tests/test_async_correctness.py`: (1) while an extraction is held open in a worker thread,
  `/healthz` on the same event loop (httpx `ASGITransport`) answers before the extraction
  finishes and in under 200 ms; the acceptance's "sleeping 5 s" is replaced by an extractor held
  until the test releases it, which proves the same without a fixed wait. (2) No route endpoint
  is a coroutine function; the test walks the included routers (FastAPI 0.162 keeps them nested)
  and requires more than 50 routes, so it cannot pass by seeing none. With the old
  `app/web/receiving.py` both tests fail ("/healthz waited for the extraction to finish").
  The first version of these tests passed against the old code too; both flaws found that way
  are fixed above.
- `tests/test_invoice_ai.py`: the fake client supports streaming and records how it was built;
  new test asserts the timeout is at most 60 s.
- Full suite 571 passed, 6 skipped (568 + 3 new); ruff, ruff format, mypy (78 files) clean.

**Files changed**
- `app/web/inputs.py` (new), `app/web/receiving.py`, `scanning.py`, `webhooks.py`, `billing.py`,
  `settings.py`, `boxes.py`, `shop.py`, `app/extract/__init__.py`, `invoice.py`, `expiry.py`;
  `tests/test_async_correctness.py` (new), `tests/test_invoice_ai.py`; `CLAUDE.md`; task index
  (T0.06 → Completed, T0.07 → In review); this log.

**Open issues**
- Extraction still runs inside the upload request (a worker thread for its duration; the default
  threadpool has 40). Moving it to a job is T1.20.
- Streaming extraction has not been run against the real API in this task (tests use a fake;
  a real call would spend the owner's Anthropic credit).

**Decisions made**
- None new (T0.07 spec; streaming follows from its 60 s limit, reasons above).

**Next task**
- T0.08 disable Square write-back; T0.10 structured logs; T0.11 governance scaffolding.

---

## 2026-11-05 — T0.06 completed

**Verified**
- PR #11 merged by the owner (`9ae3707`, merge commit). CI on the PR head `3ad124a`, all five
  required jobs green: SQLite 568 passed, 6 skipped; PostgreSQL 16 574 passed, 0 skipped; lint and
  types; dependency audit; Docker image. Same five green on `main` after the merge. Acceptance
  met: an HTML file sent as a `.jpg` is refused, and a real image is served with its own type and
  `nosniff`; every response carries the headers (tested on representative routes); templates
  render. Probe R7 retired.
- Still not done: a real-browser click-through of the pages the CSP touches (recorded in the
  T0.06 entry).

**Files changed**
- Task index (T0.06 → Completed), this log.

---

## 2026-11-05 — T0.06 Security quick fixes (S3, S10, S11, S12)

**Implemented**
- **S3 stored XSS / S12 content sniffing.** `detect_media_type()` (`app/extract/images.py`) reads
  the type from the bytes: `%PDF-` in the first kilobyte, otherwise Pillow's header read (JPEG,
  phone multi-picture JPEG "MPO", PNG, WebP, GIF; decompression bombs rejected). `save_upload`
  refuses bytes that are none of these, stores them under the suffix for what they are
  (`STORED_SUFFIX`) with that type, and only logs the browser's Content-Type when it disagrees.
  The image route serves the allow-listed type for the stored suffix (never the stored
  `content_type`, which pre-T0.06 rows took from the uploader), `X-Content-Type-Options:
  nosniff`, and `Content-Disposition: inline` (`attachment` + `application/octet-stream` for a
  suffix outside the list).
- **SEC-13 size:** `MAX_UPLOAD_MB` default 20 → 15 (`app/config.py`, `.env.example`); it also
  bounds expiry photos, which share the setting.
- **S10 headers:** `app/web/headers.py`, an ASGI middleware outside everything (static files
  included): CSP with a per-request script nonce (`default-src 'self'`, `object-src 'none'`,
  `base-uri 'self'`, `frame-ancestors 'none'`, Google Fonts allowed, inline styles allowed),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: strict-origin-when-cross-origin`, and HSTS (one year, no subdomains) when the
  request is https. A header a route sets itself is kept.
- Templates: the three inline scripts carry the nonce; the three inline handlers moved into
  script (`data-confirm` + one listener in `base.html`; item form `change` listener; the
  snack-box referral form's action).
- **S11:** Jinja sees `PublicSettings` (base URL, support email, customer-link lifetime) instead
  of the full `Settings` object.
- Probe R7 retired (comment left in `docs/audit/probes/test_audit_probes.py`).
- Upload tests use real sample files (`tests/sample_files.py`); `CLAUDE.md` gains the CSP and
  upload rules.

**Verified (locally)**
- `tests/test_receiving_web.py` (8): HTML named `.jpg` and sent as `text/html` refused (replaces
  R7); a real JPEG sent as `text/html` stored and served as `image/jpeg` with `nosniff` and
  `inline`; PNG named `.jpg` stored and served as PNG; PDF by header; over 15 MB refused; a
  pre-fix row with `text/html` served as `image/jpeg`; an unknown suffix downloads. With the two
  receiving modules stashed, 6 of the 8 fail (the other two cover the size setting and T0.04's
  form fix).
- `tests/test_security_headers.py` (14): headers on `/`, `/login`, `/pricing`, a static file, a
  404 and four signed-in pages; HSTS only over https; every inline script carries that response's
  nonce; a fresh nonce per response; no template has an inline handler, `javascript:` URL or
  un-nonced inline script; templates see no secret.
- Full suite: 568 passed, 6 skipped (547 before + 21 new). ruff, ruff format, mypy (77 files)
  clean; docs checker 0 problems.
- **Not verified:** a real browser. The preview tool in this session reads another project's
  launch configuration, so the CSP was checked by tests and by reading every template and the
  vendored barcode library (no `eval`, no WebAssembly), not by loading pages.

**Files changed**
- `app/extract/images.py`, `app/extract/invoice.py`, `app/orchestration/receiving.py`,
  `app/web/receiving.py`, `app/web/headers.py` (new), `app/main.py`, `app/web/templating.py`,
  `app/config.py`, `.env.example`, 6 templates, `docs/audit/probes/test_audit_probes.py`;
  tests: `sample_files.py`, `test_security_headers.py` (new), `test_receiving_web.py`,
  `test_storage.py`, `test_invoice_ai.py`, `test_receiving_flow.py`, `test_square_writeback.py`,
  `test_deployment.py`; `CLAUDE.md`; task index (T0.04 → Completed, T0.06 → In review); this log.

**Open issues**
- SEC-13 lists JPEG, PNG, HEIC and PDF. WebP and GIF are still accepted (existing behaviour,
  not removed silently); HEIC needs a new dependency (`pillow-heif`), the owner's call.
- No `form-action` in the CSP: checkout and Square connect post to this app, which redirects to
  Stripe and Square, and browsers apply `form-action` to that redirect. Adding it needs those
  hosts listed and a browser check.
- Responses from an unhandled exception (500) are produced outside the middleware and carry no
  headers.
- A real-browser pass over the item form, expiring "Remove", the scan page and checkout.

**Decisions made**
- None new (SEC-08, SEC-09, SEC-13 and the T0.06 spec). Referrer policy
  `strict-origin-when-cross-origin` keeps the customer sign-in token in a query string from
  reaching other sites.

**Next task**
- T0.07 async correctness, T0.08 disable Square write-back, T0.10 structured logs, T0.11
  governance scaffolding (all Ready).

---

## 2026-11-05 — T0.04 completed

**Verified**
- PR #10 merged by the owner (`db03e94`, merge commit, so `.git-blame-ignore-revs` holds). All
  five jobs green on `main`. The owner approved making "Lint and types" required; the "Protect
  main" ruleset now requires all five checks (saved, confirmed after a reload). Acceptance met:
  CI runs ruff and mypy and passes on `main`; the commands are in `CLAUDE.md`.
- The CI-evidence commit below (`5042a2b`) was pushed after the merge started; it is carried
  into this branch.

**Files changed**
- Task index (T0.04 → Completed), this log.

---

## 2026-11-05 — T0.04 Coding standards: ruff + mypy

**Implemented** (ID-04; one commit per kind of change)
- `af1ec6b` config: ruff 0.17.4 and mypy 2.4.1 in the dev group (`uv.lock` gains only them and
  their 4 helper packages). ruff: line length 100; rules pycodestyle, pyflakes, isort, bugbear,
  pyupgrade and bandit (`S`). Ignored, each with its reason in `pyproject.toml`: E501 (the
  formatter owns code width; 85 long strings/comments remain) and UP042 (StrEnum changes `str()`
  output). Tests may assert, import fixtures across modules and use throwaway passwords;
  FastAPI's `Depends`/`Form`/... are allowed as defaults; `docs/` (frozen audit probes) excluded.
  mypy: gradual by default; strict flags for `app.transform.*` and for `app.access.*` and
  `app.ledger.*` before they exist; the five snack-box modules leaving in T0.05 skipped;
  `apscheduler`, `boto3`, `botocore` have no type information and are not checked.
- `7f3cb61` `ruff format .` only (118 files); `f47bbb8` ruff's safe autofixes only (sorted and
  unused imports, `datetime.UTC`). Both listed in the new `.git-blame-ignore-revs`.
- `4c5d47a` remaining lint by hand: sha1 box-rules fingerprint marked `usedforsecurity=False`
  (same digest), `l` → `bl`, `itertools.pairwise`, an f-string; four bandit false positives get
  `noqa` with the reason on the line above (dev secret placeholder that production refuses, a
  migration's constant SQL, the docs test's fixed subprocess, the migration test's seed SQL).
- `9c432bb` the 61 mypy errors left after scoping. Typing only, apart from two paths that
  crashed:
  - **Bug found by mypy:** receiving apply called `.strip()`/`.isdigit()` on form values; a file
    part posted under a text field's name raised AttributeError (500). New `_field()` treats it
    as empty; `tests/test_receiving_web.py` covers it and fails without the fix.
  - The alert digest now raises `LookupError` naming the tenant when alerts exist without a
    tenant row (was an AttributeError inside `allows`).
  - The two Anthropic calls use the SDK's request types; the media type is known only at runtime,
    so the content block is `cast` with that reason in a comment. Sentry's `Event`/`Hint` types
    for the scrubber.
- CI job **Lint and types**: `ruff check`, `ruff format --check`, `mypy`, on the locked tools.
- `CLAUDE.md` (commands, CI, "required CI checks"), `README.md` (lint/type commands; the stale
  "60 tests" note dropped).

**Verified (locally)**
- `ruff check .` all passed; `ruff format --check .` 131 files formatted; `mypy` no issues in 76
  files; strict flags pass on all 15 `app/transform` modules.
- Full suite after all changes: 546 passed, 6 skipped (unchanged); the new test passes, and fails
  with `AttributeError: 'UploadFile' object has no attribute 'strip'` when the fix is stashed.
- Workflow YAML parses (five jobs); docs checker 0 problems.
- **CI on PR #10 (commit `6fe3edb`), all five jobs green:** Lint and types ("131 files already
  formatted", mypy "no issues found in 76 source files"); SQLite 547 passed, 6 skipped, docs 0
  problems; PostgreSQL 16 553 passed, 0 skipped; dependency audit; Docker image.

**Files changed**
- `pyproject.toml`, `uv.lock`, `.git-blame-ignore-revs` (new), `.github/workflows/ci.yml`,
  `CLAUDE.md`, `README.md`, `tests/test_receiving_web.py` (new); 119 Python files changed in all
  (118 by the formatter, typing fixes in 19); `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md`
  (T0.04 → In review), this log.

**Open issues**
- "Lint and types" is not a required check until the owner adds it to "Protect main".
- `.git-blame-ignore-revs` lists the two mechanical commits by SHA: merge this PR with a merge
  commit (as before), not squash or rebase, or the SHAs change.
- The expiry reader forces `tool_choice` to its tool. That works on the configured
  `claude-opus-5`; Claude Opus 5.5, Sonnet 5.5 and Fable 5.1 reject forced tool choice, so a
  future model change there must move it to `auto` or structured output.
- 85 lines over 100 columns remain (strings and comments); not enforced.

**Decisions made**
- None new (ID-04). Scoping choices recorded above with their reasons.

**Next task**
- Stage 0 tasks now Ready: T0.06 security quick fixes, T0.07 async correctness, T0.08 disable
  Square write-back, T0.10 structured logs, T0.11 governance scaffolding. T0.05 still waits for
  the owner's snack-box repository.

---

## 2026-11-05 — T0.03 completed; Dependabot set to lockfile-only

**Implemented**
- `.github/dependabot.yml` (uv): `versioning-strategy: lockfile-only`, and major updates of
  `python-dateutil` ignored, each with a comment giving the reason.

**Verified**
- PR #4 merged by the owner (`d2957b8`); PR #3's commit came with it and GitHub marked PR #3
  merged. All four required checks green on `main` at `d2957b8`. Acceptance of T0.03 met: the
  image installs exactly the locked versions (Docker image job), pip-audit runs in CI with no
  finding.
- Dependabot's first run on `main` (config valid; 4 of 6 update jobs succeeded) opened PRs #5–#8.
  Each only raised a minimum in `pyproject.toml`, the default "increase" strategy for
  applications: #5 setuptools (build-system table, unused with `package = false`), #6 uvicorn (lock
  unchanged), #7 psycopg and #8 sentry-sdk, which would have **downgraded** the lock (3.3.8 →
  3.3.6, 2.74.0 → 2.73.0).
- The 2 failed jobs: both "dependency_file_not_resolvable" for `python-dateutil` 3.x, because
  botocore requires `python-dateutil>=2.1,<3.0.0`.
- GitHub's Dependabot options reference lists `uv` among the ecosystems that support
  `versioning-strategy`, and `lockfile-only` means pull requests only for lockfile updates.
- Dependabot YAML parses; docs checker 0 problems.
- **Not verified yet:** Dependabot's next run under the new configuration (it runs after this
  merges).

**Files changed**
- `.github/dependabot.yml`, `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md` (T0.03 → Completed), this
  log.

**Open issues**
- The owner closes PRs #5–#8 after this merges.
- Merged branches `docs/t0.02-protection`, `docs/record-t0.02` and `t0.03-lock-deps` still on
  GitHub.

**Decisions made**
- None new: lockfile-only keeps ID-05's intent (the lock is the source of exact versions; ranges
  in `pyproject.toml` change only by hand).

**Next task**
- T0.04 coding standards: ruff + mypy.

---

## 2026-11-05 — T0.03 Lock dependencies, audit, Dependabot

**Implemented**
- `uv.lock` (uv 0.13.7, ID-05): 68 packages resolved from `pyproject.toml`; `.python-version` = 3.12.
- `pyproject.toml`: dev tools moved from `[project.optional-dependencies]` to `[dependency-groups]`
  so they never reach the image; `[tool.uv] package = false` — the repository is an application
  and setuptools' flat-layout discovery refuses `app/` + `migrations/` (CI run #1's failure).
- `Dockerfile`: copies uv from the pinned `ghcr.io/astral-sh/uv:0.13.7` image, exports the runtime
  lock (no dev group) and installs it with `pip install --require-hashes`; uv is removed after.
- `.github/workflows/ci.yml`: both test jobs install with `uv sync --frozen` (fails if the lock is
  stale against `pyproject.toml`). New job **Dependency audit**: `uv export` of every locked
  package, `pip-audit==2.10.1 --disable-pip` (no install). New job **Docker image**: builds the
  image, lists its installed distributions, `scripts/check_lock_matches.py` fails on any version
  that differs from the lock or any package not in it.
- `.github/dependabot.yml`: weekly for uv (minor + patch grouped), GitHub Actions and Docker.
- `README.md` quickstart and `CLAUDE.md` (dependencies, tests, CI) switched to uv.

**Verified (locally)**
- `uv lock --check` consistent; runtime export: 60 hashed pins, no pytest.
- pip-audit on the full export: "No known vulnerabilities found".
- `check_lock_matches.py`: passes on an environment installed from the export; fails (exit 1) on
  a planted version mismatch.
- Full suite on the locked versions (fresh venv from the export): 546 passed, 6 skipped (the
  Postgres-only tests; the CI Postgres job runs them).
- Workflow and Dependabot YAML parse; docs checker 0 problems.
- **CI on PR #4 (commit `7202e41`), all four jobs green:** SQLite 546 passed, 6 skipped, docs
  0 problems; PostgreSQL 16 552 passed, 0 skipped; audit "No known vulnerabilities found"; Docker
  image built, "checked 58 installed packages against 68 locked" with no mismatch (58 = the 60
  runtime pins minus `tzdata`, Windows only, and `httpx2-jsfetch`, browser only).

**Files changed**
- `uv.lock`, `.python-version`, `scripts/check_lock_matches.py`, `.github/dependabot.yml` (new);
  `pyproject.toml`, `Dockerfile`, `.github/workflows/ci.yml`, `README.md`, `CLAUDE.md`,
  `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md` (T0.03 → In review), this log.

**Open issues**
- Resolved: the owner approved making "Dependency audit" and "Docker image" required; the
  "Protect main" ruleset now requires all four checks (saved and confirmed after a reload). PR #3
  (built before these jobs existed) can no longer merge on its own; PR #4 contains its commit and
  replaces it.
- The audit fails on any known vulnerability, not only critical ones (pip-audit has no severity
  filter); an accepted finding needs `--ignore-vuln` plus an ADR with a fix date.
- Existing local `.venv` folders were made with pip; `uv sync` replaces them.

**Decisions made**
- None new (ID-05 chose uv; the "uv" Dependabot ecosystem replaces "pip" because the lock is uv's).

**Next task**
- T0.04 coding standards: ruff + mypy (after this PR merges).

---

## 2026-11-05 — T0.02 completed: protection of `main` proven

**Implemented**
- Nothing new in code; this entry records the acceptance evidence for T0.02.

**Verified**
- **Direct push rejected:** pushing a commit straight to `main` returned "GH013: Repository rule
  violations found for refs/heads/main … 2 of 2 required status checks are expected"; `main` was
  unchanged (`c3dcaa9`).
- **Failing pull request blocked:** throwaway PR #2 (branch `ci/blocked-merge-check`, one test
  that asserts False) — both required checks "Failing after 2m · Required", and the "Merge pull
  request" button disabled. The owner closed PR #2 and deleted the branch.
- **Normal flow works:** PR #1 (documentation) passed both checks and was merged by the owner
  (`84a98a1`).
- Acceptance of T0.02 met: both jobs green on `main` (run #3), Postgres job with 0 skipped tests,
  required checks enforced, a failing test blocks merging.

**Files changed**
- `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md` (T0.02 → Completed), this log.

**Open issues**
- Merged branch `docs/t0.02-protection` still exists on GitHub (harmless; GitHub's
  "Automatically delete head branches" setting would remove such branches after merge).

**Decisions made**
- None.

**Next task**
- T0.03 lockfile, dependency audit, Dependabot (now Ready: T0.02 completed).

---

## 2026-11-05 — T0.02 CI on GitHub Actions

**Implemented**
- `.github/workflows/ci.yml`: runs on push to `main`, pull requests and manual dispatch; read-only
  permissions; superseded runs cancelled. Job **Tests (SQLite)**: install, `scripts/check_docs.py`,
  full suite. Job **Tests (PostgreSQL 16)**: `postgres:16-alpine` service, `TEST_DATABASE_URL` set
  (the existing conftest switch), full suite, and the step fails if any test is skipped so the
  Postgres-only tests must run. Python 3.12 (as the Dockerfile). Actions pinned to commit SHAs:
  `actions/checkout` v7.0.1 `3d3c42e…`, `actions/setup-python` v7.1.0 `6d4fd22…` (resolved with
  `git ls-remote`; both are lightweight tags, so the SHAs are commits).
- Audit probes stay out of CI: pytest collects only `tests/` (`pyproject.toml` testpaths).
- `CLAUDE.md`: CI line added.

**Verified**
- Workflow parses (PyYAML): two jobs, three triggers, steps as listed; Postgres URL matches the
  `postgresql` prefix the migration tests require.
- Locally: docs checker 0 problems; suite on SQLite passed in T0.01 (545 + the docs contract test).
- **Not verified yet:** an actual GitHub run (needs the push) and the PostgreSQL job (Docker is not
  running on this machine; the earlier audit run on Postgres 16 was 548/548).
- **Correction after the first run (run #1, commit `5db2399`): both jobs failed at Install.**
  setuptools refused the editable install ("Multiple top-level packages discovered in a
  flat-layout: ['app', 'migrations']") — the earlier local `top_level.txt` was not evidence for a
  fresh build. Fixed by installing dependencies only from `pyproject.toml` (as the Dockerfile
  does); the generator was run locally and lists all 21 requirements.
- **Run #2 (commit `fd1e3f0`):** Tests (SQLite) **passed** on GitHub — docs checker "0 problems",
  suite **546 passed, 6 skipped** in 1 m 43 s. Tests (PostgreSQL 16) failed before any test:
  Docker could not publish the database port ("failed to start userland proxy for port mapping
  0.0.0.0:5432 … timed out starting the userland proxy").
- **Fixes after run #2:** the Postgres job now runs in a `python:3.12-slim` container on the
  database's network and connects to host `postgres` (no published port); a workflow-level
  `shell: bash` default turns on `pipefail`, because GitHub's implicit shell does not, which would
  have let `pytest | tee` hide a failing run (demonstrated locally: exit 3 propagates).
- **Run #3 (commit `c3dcaa9`): both jobs green** (3 m 47 s). Tests (SQLite): docs checker "0
  problems", **546 passed, 6 skipped**. Tests (PostgreSQL 16, server 16.15, service healthy):
  **552 passed, 0 skipped** — the 6 Postgres-only tests ran. Read from the raw job logs.
- **Required check (OD-04), checked in GitHub settings after the owner enabled it:** the
  Branches page lists no branch protection rule; the Rulesets page shows none and states "Your
  rulesets won't be enforced on this private repository until you move to GitHub Team
  organization account." Not in effect; owner decision recorded as open in the risk register.
- Owner declined a plan upgrade and asked for the protections to be added. A ruleset "Protect
  main" was filled in through the settings page (Active; default branch; required checks
  Tests (SQLite) and Tests (PostgreSQL 16); block force pushes; restrict deletions) and submitted;
  GitHub did not save it — the Rulesets page still reports none. The classic branch-protection
  form carries the same notice ("won't be enforced on this private repository until you move to a
  GitHub Team or Enterprise organization account"). Server-side protection of `main` is not
  available on this plan.
- **Owner made the repository public** so the ruleset is enforced. The owner's saved ruleset
  had "Require status checks to pass" ticked but **no checks listed**; both checks were added and
  saved (verified after reload): Tests (PostgreSQL 16), Tests (SQLite). Final ruleset: Active ·
  default branch · restrict deletions · required checks with "up to date before merging" and "not
  required on creation" · block force pushes · empty bypass list. OD-04 done.
- **Public-exposure checks:** all 25 commits scanned — the only env file ever committed is
  `.env.example`; no secret-shaped strings; secret settings only ever had placeholder or test
  values; no uploads, images or databases ever committed. **Commit metadata carries the owner's
  personal email** on every commit (now public); rewriting history would need a force push, which
  the ruleset blocks and which is not recommended. The planning documents (pricing hypotheses,
  validation plans, JuJu context) are now public too.
- `CLAUDE.md`: pull-request workflow — `main` is never pushed to directly.
- **T0.01 → Completed** (its done-definition: merged and run by CI). **T0.02 stays In review**
  until the owner requires the check on `main` (OD-04) and a deliberately failing branch shows a
  blocked merge.

**Files changed**
- `.github/workflows/ci.yml` (new), `CLAUDE.md`, `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md`
  (status), this log.

**Open issues**
- Acceptance still needs: both jobs green on `main`; Postgres job with 0 skips; the owner enabling
  "require status checks" on `main` (OD-04) and one failing-test branch to prove a merge is blocked.
- T0.01 moves to Completed after the first green run.

**Decisions made**
- None (pinned versions are the latest releases of the two official actions).

**Next task**
- Owner: approve commit + push to trigger the first run. Then T0.03 (lockfile, audit, Dependabot).

---

## 2026-11-05 — Documentation contract and checker

**Implemented**
- `scripts/check_docs.py`: one checker for links/anchors (product, audit, roadmap), product ID
  definitions, traceability coverage of all spec requirements, roadmap task/decision/requirement
  references, index dependency order and entry/index agreement, and alignment of the master
  roadmap, task index and migration roadmap (milestone ranges, critical-path arrows, DM step
  ownership, naming and ordering). Exit code 1 on any problem.
- `tests/test_docs_contract.py`: runs the checker inside the test suite, so CI (T0.02) enforces it.
- Governance: roadmap README §4.4 "The development contract" (master roadmap authoritative;
  distinct task responsibilities such as T4.16 vs T4.17; explicit migration ownership; the checker
  proves references and ordering, not correctness; critical-path blockers resolved deliberately);
  `CLAUDE.md` documentation-contract rule; T0.05 marked as on the critical path (task entry, index,
  OD-02, handoff).

**Verified**
- Checker on the working tree: 87 tasks, 231 requirements, 0 problems.
- Negative test on a scratch copy with six planted errors (broken link, dropped requirement, forward
  dependency, entry/index mismatch, false critical-path arrow, migration step not named by its
  owner): all six reported, exit code 1.
- Checker and contract test run on a clean export of exactly the committed content (see commit).

**Files changed**
- `scripts/check_docs.py`, `tests/test_docs_contract.py` (new); `CLAUDE.md`; `docs/roadmap/` README,
  DEVELOPMENT_TASK_BREAKDOWN, RISK_AND_DEPENDENCY_REGISTER; this log.

**Open issues**
- T0.01 code remains uncommitted at the owner's request; T0.05 blocked on the owner's repository.

**Decisions made**
- None.

**Next task**
- First unfinished Stage 0 tasks: T0.06, T0.07, T0.08, T0.10, T0.11 (Ready); T0.02 after T0.01 is
  committed.

---

## 2026-11-05 — Roadmap, task breakdown and migration roadmap aligned (docs only)

**Implemented**
- Milestones stay as defined in MASTER_DEVELOPMENT_ROADMAP (M0 safe to build · M1 tenancy & access ·
  M2 ledger engine · M3 operations engine · M4 Standalone end-to-end · M5 Square connected · M6
  launch-ready → G-R1 · M7 R1.1 · M8–M10 expansion). The task index gained a **Milestone** column
  and the breakdown a milestone summary pointing to the master roadmap as authoritative.
- New **T4.16 Contract migration** owns DM-16, which had no task; the launch gate review is now
  **T4.17** (references renumbered in the roadmap, task breakdown, migration roadmap and security
  checklist).
- Dependencies: T1.01 now follows T0.05 (DM-1 before DM-2); T1.02 follows T1.01 (DM-2 precedes
  every new table); T4.17 names T4.16.
- Every task that owns a migration step names it (DM-1..DM-16); T2.01 backfills products from
  `sold_items` (DM-9); T2.11 creates history receivings for applied documents (DM-12).
- Master roadmap critical path rewritten so every arrow is a real dependency; Stage 0/1 exit
  criteria and gates G0, G1, G2, G5, G-R1 now retire each probe in the milestone whose task retires
  it (R3 in G0; PA1, PA5 in G1; PA4 in G5; PA2 in G-R1).

**Verified**
- Script: every master-roadmap milestone range matches the index; no task depends on a later
  milestone; every critical-path arrow is a dependency; every DM step has an owning task that
  precedes what it must precede and names the step. Task, decision and requirement IDs defined;
  index dependencies point backwards and match each task entry; all links and anchors resolve.

**Files changed**
- `docs/roadmap/` MASTER_DEVELOPMENT_ROADMAP, DEVELOPMENT_TASK_BREAKDOWN, DATA_MIGRATION_ROADMAP,
  TESTING_AND_QUALITY_STRATEGY, SECURITY_AND_RELEASE_CHECKLIST, README; this log.

**Open issues**
- Uncommitted, together with T0.01.

**Decisions made**
- None: milestone definitions unchanged; the proposed alternative numbering (launch at M7) was not
  adopted because six documents already use the existing definitions.

**Next task**
- Owner: commit when ready. Then T0.02.

---

## 2026-11-05 — T0.01 Hermetic test configuration

**Implemented**
- `app/config.py`: `Settings.settings_customise_sources` drops the `.env` source when
  `APP_ENV=test`; development and deployed environments still read `.env` as before.
- `tests/conftest.py`: before pinning test values, removes every inherited environment variable
  that names a `Settings` field, so neither a developer's `.env` nor their shell can change results;
  `APP_BASE_URL` pinned to `http://localhost:8000` (the value the audit's passing runs used — the
  task text suggested `http://testserver`; the existing default keeps results identical).
- `tests/test_settings.py`: 3 tests — `.env` ignored under test, still read with `APP_ENV=dev`,
  base URL not inherited.
- `CLAUDE.md`: test command note updated (no pin needed any more).

**Verified**
- Full suite, SQLite, developer `.env` present (with an https `APP_BASE_URL`), no variables pinned:
  **545 passed, 6 skipped** (the 6 are the Postgres-only tests). Before this change the same run
  failed 46 tests (audit T1).
- Same run with a hostile shell environment (`APP_BASE_URL=https://…`, `APP_ENV=production`, Stripe,
  Square webhook key, SMTP host set): **545 passed, 6 skipped**.
- Audit probes without the pin: 13 passed (they still confirm defects later tasks fix).
- Not run: PostgreSQL (needs Docker; T0.02 adds it to CI).

**Files changed**
- `app/config.py`, `tests/conftest.py`, `tests/test_settings.py` (new), `CLAUDE.md`,
  `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md` (status), this log.

**Open issues**
- Status In review until merged and run by CI (T0.02 done-definition).

**Decisions made**
- None beyond the base-URL value noted above.

**Next task**
- T0.02 CI on GitHub Actions (depends on T0.01); T0.06, T0.07, T0.08, T0.10, T0.11 are Ready.

---

## 2026-11-05 — Owner approved implementation decisions ID-01..ID-23 (docs only)

**Implemented**
- `PRODUCT_DECISIONS.md`: C-43 (ID-01..ID-23 approved as recommended) and C-44 (27 pending product
  recommendations approved via ID-14: PD-05, 07, 08, 10, 11, 12, 14, 15, 16, 17, 19, 20, 22, 23, 24,
  25, 26, 27, 29, 30, 32, 36, 37, 38, 39, 40, 41); those PD rows marked decided; PD-33 marked
  deferred to Stage 6. Still open: PD-06, PD-33, PD-34, PD-35.
- `SAAS_REQUIREMENTS.md` (trial, sales tax, refunds, retention) and `PRODUCT_SPECIFICATION.md`
  (PD-15) no longer show those items as open.
- Roadmap: decisions marked approved; T0.09 now blocked only on owner accounts (OD-03); OD-01 done;
  README handoff updated. Product README §6–7 updated.

**Verified**
- Scripts: links and anchors resolve across `docs/`; product IDs all defined; roadmap task,
  decision and requirement references consistent; index dependencies valid.

**Files changed**
- `docs/product/` PRODUCT_DECISIONS, SAAS_REQUIREMENTS, PRODUCT_SPECIFICATION, README;
  `docs/roadmap/` IMPLEMENTATION_DECISIONS, DEVELOPMENT_TASK_BREAKDOWN, RISK_AND_DEPENDENCY_REGISTER,
  README; this log.

**Open issues**
- Owner dependencies OD-02..OD-11 (snack-box repository, accounts, branch protection, content,
  invoices, JuJu values); validation decisions O-1..O-7; questions Q-1..Q-5. Nothing committed yet.

**Decisions made**
- None by the agent.

**Next task**
- T0.01 hermetic test configuration (Ready), on the owner's go-ahead.

---

## 2026-11-05 — Phase 3 roadmap and implementation handoff (docs only)

**Implemented**
- `docs/roadmap/`: 15 documents — README (governance, Claude Code handoff), gap analysis (56
  capability rows), target architecture, target domain and data architecture, operating-mode plan,
  SaaS and pricing plan, master roadmap (Stages 0–8, M0–M10, gates G0–G5 and G-R1), task
  breakdown (86 detailed tasks for Stages 0–4 + outline tasks for 4B–8, status index), data
  migration roadmap (DM-1..DM-16), testing and quality strategy, security and release checklist,
  risk and dependency register, customer validation plan, JuJu validation plan, implementation
  decisions (ID-01..ID-23).
- Repository-root `CLAUDE.md`: project instructions for implementation sessions (ID-19).
- `docs/product/README.md` §7: Phase 3 marked complete.

**Verified**
- Script checks: all task IDs, decision IDs and requirement IDs referenced in the roadmap are
  defined; index dependencies only point to earlier tasks and match each task's own entry;
  269 relative links and anchors across `docs/roadmap/`, `docs/audit/` and `docs/product/` resolve.
- The customer and JuJu validation plans were drafted by delegated agents and reviewed: no
  fabricated evidence; owner inputs marked.

**Files changed**
- `docs/roadmap/*` (new), `CLAUDE.md` (new), `docs/product/README.md`, this log. No application code,
  schema, configuration or dependency changed; no migration run; nothing deployed.

**Open issues**
- ID-01..ID-23 await owner approval; owner dependencies OD-01..OD-11; validation-plan decisions
  O-1..O-7; questions Q-1..Q-5.

**Decisions made**
- None by the agent.

**Next task**
- Owner: approve the IDs. Agent, on go-ahead: T0.01 (hermetic tests), the first Ready task.

---

## 2026-11-05 — Repository on GitHub (docs only)

**Implemented**
- Audit: TR-01 (only copy on one laptop) and OA-1 (push to GitHub) marked resolved; confirmed
  finding 10 updated.

**Verified**
- `origin` = `github.com/Souru0712/InventoryOS`; `main` in sync with `origin/main` at `601540b`
  (includes the owner's `scripts/send_test_webhook.py` commit). Unauthenticated GitHub API returns
  404, consistent with a private repository. Only `.env.example` is tracked among env/db files.

**Files changed**
- `docs/audit/TECHNICAL_RISK_REGISTER.md`, `docs/audit/AUDIT_DECISIONS_AND_OPEN_QUESTIONS.md`,
  this log.

**Open issues**
- No CI yet (TR-14). Local branch `docs/inventoryos-phase-1` (at `3fb4afa`, already in `main`'s
  history) exists only locally.

**Decisions made**
- None.

**Next task**
- Agent, on go-ahead: Phase 3 plan.

---

## 2026-11-04 — Owner answered the four audit questions (docs only)

**Implemented**
- `PRODUCT_DECISIONS.md`: C-37 resolved — the snack-box module moves to its own project, not
  deleted (owner's side project for a Japanese-snack convenience store); A-01 gains a data point
  (owner's store uses Toast; Square stays first); §7 D8 host: no budget ceiling, choose for
  compatibility, plan fit and automation, decided in Phase 3.
- `PRODUCT_GLOSSARY.md`: "Snack-box project" defined as separate from InventoryOS.
- Audit: questions §5 answered; U3 points to the owner's real invoices; OA-4 added; risk TR-31
  (no pilot businesses, requirements unvalidated) added; README range TR-01..31.

**Verified**
- Scripts: 192 links and anchors resolve; 659 referenced IDs defined in `docs/product/`.

**Files changed**
- `docs/product/PRODUCT_DECISIONS.md`, `docs/product/PRODUCT_GLOSSARY.md`,
  `docs/audit/AUDIT_DECISIONS_AND_OPEN_QUESTIONS.md`, `docs/audit/TECHNICAL_RISK_REGISTER.md`,
  `docs/audit/README.md`, this log.

**Open issues**
- OA-1 push to GitHub; nothing committed yet. Hosting choice deferred to Phase 3.

**Decisions made**
- None by the agent. Read "we want Square integration first" as Square remaining the first POS
  adapter; the build order C-27 (engine → Standalone → Square) is unchanged.

**Next task**
- Agent, on go-ahead: Phase 3 plan.

---

## 2026-11-04 — Owner approved the Phase 2 decisions AD-01..10 (docs only)

**Implemented**
- `docs/product/PRODUCT_DECISIONS.md`: C-33 scoped data access for tenant isolation (was PD-18);
  C-34 the ledger owns all stock, Square write-back off until the R1.1 outbox (was PD-09); C-35
  replace the inventory engine; C-36 InventoryOS owns the product catalog; C-37 snack-box module
  leaves before the Square pilot (was PD-28); C-38 items per organization; C-39 keep migration
  history; C-40 no pilots on the old engine; C-41 retire Starter/Pro; C-42 JuJu parity by
  specification until R1 (was PD-03). Contradiction 10 resolved; §7 audit mapping updated.
- Propagated to SAAS_REQUIREMENTS (SAAS-T-04), DOMAIN_MODEL (DR-09, `pos_correction`),
  INTEGRATION_REQUIREMENTS (§3.4 write-back off in R1), PRODUCT_SPECIFICATION (POS-FR-13),
  JUJU_REFERENCE_AND_PARITY, product README (decided list, Phase 2 complete); audit decisions doc,
  baseline `12-decisions.md`, risk register, SaaS, integration, migration, reuse and JuJu documents.

**Verified**
- Scripts: 192 relative links and anchors resolve; 659 referenced IDs defined in `docs/product/`;
  traceability still covers all 231 FR IDs exactly once.

**Files changed**
- `docs/product/*` (8 files), `docs/audit/*` (9 files), this log. No application code.

**Open issues**
- No P0 decision remains open. Owner actions: push this repository to GitHub (OA-1); four audit
  questions remain (snack-box plans, real invoices, pilots, hosting); the fifth was answered by C-38.
- Nothing committed yet.

**Decisions made**
- None by the agent. Clarified, not changed: under C-34 Square counts never change stock in R1;
  the blueprint's existing R1.1 write-back design (`pos_correction`) is unchanged.

**Next task**
- Owner: push to GitHub; answer the audit questions. Agent, on go-ahead: Phase 3 plan.

---

## 2026-11-04 — Phase 2 audit of existing projects (read-only)

**Implemented**
- `docs/audit/`: 13 Phase 2 documents (README, inventory project technical/database/integration
  audits, JuJu technical audit, JuJu ↔ InventoryOS comparison, requirements traceability, SaaS
  readiness, operating-mode readiness, reuse/adapt/rebuild matrix, risk register, data migration
  assessment, decisions and open questions). Baseline audit files 00–12 kept as evidence.
- `docs/audit/probes/test_phase2_probes.py`: 5 probes (PA1–PA5) asserting current behaviour; outside
  `tests/`, never collected by the suite.

**Verified**
- Existing app: 542 passed, 6 skipped (SQLite, `APP_BASE_URL` pinned); baseline probes 8/8; Phase 2
  probes 5/5. PostgreSQL run not repeated (548/548 earlier today).
- Traceability covers all 231 spec FR IDs exactly once (script). 188 relative links and anchors
  across `docs/audit/` and `docs/product/` resolve (script). No secret-shaped strings in the docs.
- JuJu: code read at `ae1e702`; tests not run (Node 24.15.0 < 24.22.2); post-CI diff is docs-only.
  JuJu repository unchanged.

**Files changed**
- `docs/audit/*` (new), this log. No application code, schema, config or dependency changed.
  Phase 1 documents not edited; `docs/product/README.md` §7 still shows Phase 2 "Not started" until
  the owner reviews this audit.

**Open issues**
- Owner decisions AD-01..10 (AD-01 = PD-18, P0); owner action OA-1 (push to GitHub).
- Not done: dependency vulnerability scan, load test, accessibility review, any live integration run.

**Decisions made**
- None by the agent.

**Next task**
- Owner: review the audit; answer AD-01..10 and the five questions. Phase 3 only on go-ahead.

---

## 2026-11-04 — Owner decision recorded: PD-21 login approach (docs only)

**Implemented**
- `PRODUCT_DECISIONS.md`: C-32 finish the existing in-app authentication (option 1): verification,
  reset, throttling (C-31), revocable server-side sessions and TOTP MFA; PD-21 marked decided.
- `SAAS_REQUIREMENTS.md`: options table relabelled as options considered, with the decision.
- README: PD-21 removed from open decisions; Phase 2 now sizes the login work.

**Verified**
- Script check: 0 broken links or anchors; all 649 referenced IDs defined.

**Files changed**
- `docs/product/` PRODUCT_DECISIONS, SAAS_REQUIREMENTS, README; this log.

**Open issues**
- Only P0 left: PD-18 (how the code enforces tenant isolation), decided in Phase 2.
- 16 P1 decisions remain open; they block only their own areas of the R1 build.

**Decisions made**
- None by the agent.

**Next task**
- Agent, on the owner's go-ahead: Phase 2 audit against `docs/product/`.

---

## 2026-11-04 — Owner decisions recorded: PD-13, PD-18 (product side), PD-21 must-haves (docs only)

**Implemented**
- `PRODUCT_DECISIONS.md`: C-29 release plan approved (was PD-13); C-30 one configurable product for
  every target segment with each business's data separated (owner's answer to PD-18); C-31 login
  must include verification, reset and throttling. PD-18 narrowed to the technical enforcement
  mechanism (Phase 2); PD-21 points to the options and recommends option 1.
- `SAAS_REQUIREMENTS.md`: new SAAS-T-11 (one codebase and schema for every tenant, differences as
  configuration); SAAS-A-02/03/05 marked confirmed; the PD-21 options table now compares in-app
  auth, a managed provider and a self-hosted identity server, with a recommendation.
- `PRODUCT_SPECIFICATION.md` release plan marked confirmed; README open-decision table updated.

**Verified**
- Script check: 0 broken links or anchors; all 648 referenced IDs defined.
- Facts behind the PD-21 options checked in code: bcrypt hashing (`app/security.py`), Starlette
  signed-cookie sessions (`app/main.py`), SMTP email (`app/config.py`, `app/notify/email.py`).

**Files changed**
- `docs/product/` PRODUCT_DECISIONS, SAAS_REQUIREMENTS, PRODUCT_SPECIFICATION, README; this log.

**Open issues**
- PD-21 needs the owner's choice of option. PD-18 mechanism is decided in Phase 2.

**Decisions made**
- None by the agent.

**Next task**
- Owner: choose the PD-21 option. Agent, on go-ahead: Phase 2 audit against `docs/product/`.

---

## 2026-11-04 — Phase 1 package checked against the re-issued brief (docs only)

**Implemented**
- Checked all 14 `docs/product/` files against every section of the re-issued Phase 1 brief instead
  of regenerating them, so the owner decisions C-24..C-28 are kept.
- Repository rename: references to the `POS-Inventory-Automation` folder updated to `InventoryOS`
  (README, vision, glossary, decisions); audit citations became relative links. The glossary no
  longer says InventoryOS "has no codebase", which contradicted C-25.
- `BUSINESS_WORKFLOWS.md`: WF-23 Employee operations gained steps and the full field table (it was
  the only one of the brief's 24 workflows without them); WF-17 gained explicit steps.
- `NON_FUNCTIONAL_REQUIREMENTS.md`: new §3a Scalability (SCL-01..07, with a ledger-growth sizing
  note); header points to SaaS for multi-tenancy, subscription lifecycle and retention.
- `PRODUCT_SPECIFICATION.md`: tap-count principle aligned with WF-23 and AC (open a task within 2
  taps; waste saved within 3 taps of choosing the item).
- `PRODUCT_DECISIONS.md`: contradictions 12 (brief's "don't assume the codebase is InventoryOS" vs
  C-24/C-25 — resolved: owner confirmed "Keep C-24/C-25") and 13 (existing `docs/audit/` vs "don't
  do Phase 2", resolved).

**Verified**
- Script check: 14/14 deliverables present; 0 broken links or anchors; all 644 referenced IDs
  defined. A planted bad ID and bad anchor were both caught.

**Files changed**
- `docs/product/` README, PRODUCT_VISION, PRODUCT_GLOSSARY, PRODUCT_DECISIONS,
  PRODUCT_SPECIFICATION, BUSINESS_WORKFLOWS, NON_FUNCTIONAL_REQUIREMENTS, INTEGRATION_REQUIREMENTS,
  JUJU_REFERENCE_AND_PARITY; this log. No application code changed.

**Open issues**
- P0 still open: PD-13 (R1 scope), PD-18 (tenant isolation), PD-21 (auth).
- StockPulse branding remains in code (`pyproject.toml` name `stockpulse`, `app/config.py`, etc.).

**Decisions made**
- None by the agent. Owner confirmed C-24/C-25 stand (contradiction 12).

**Next task**
- Owner: answer PD-13. Agent, on go-ahead: Phase 2 audit against `docs/product/`.

---

## 2026-11-04 — Owner decisions recorded: PD-01, PD-02, PD-31, PD-42 (docs only)

**Implemented**
- `docs/product/PRODUCT_DECISIONS.md`: C-24 InventoryOS is the new name for StockPulse; C-25 build on
  this codebase (repo rename pending, by the owner); C-26 target 21 paying customers, $1,000 MRR gates
  new revenue products; C-27 build order engine → Standalone → Square; C-28 `docs/product/` is the
  permanent docs home (follows from C-25). PD-03 and PD-21 narrowed by C-25.
- Propagated to README, vision, pricing, spec, glossary, JuJu parity and SaaS docs.

**Verified**
- Script check: 0 broken links; all PD/DR/AC/A/C/E/WF/FR references defined.

**Files changed**
- `docs/product/*`, this log.

**Open issues**
- StockPulse branding remains in code (`pyproject.toml` name, templates, `app/config.py`, marketing
  pages) — rebrand is code work, not done here.
- P0 still open: PD-13 (R1 scope), PD-18 (tenant isolation), PD-21 (auth).

**Decisions made**
- None by the agent; C-28 recorded as a direct consequence of C-25.

**Next task**
- Owner: rename the repository; answer PD-13. Agent, on go-ahead: Phase 2 audit against `docs/product/`.

---

## 2026-11-04 — InventoryOS Phase 1 revised: pricing by POS situation, operating modes (docs only)

**Implemented**
- Revised `docs/product/` to the owner's revised Phase 1 brief: files renamed to the brief's names
  (no numeric prefixes); new `PRICING_AND_PACKAGING.md`; vision rewritten (8 segments, journeys,
  competitive positioning, outcomes); workflows rewritten (24 + 4 supporting, full fields); roles
  rewritten (8 roles, location scope); spec gained operating modes, Module 15 Standalone
  Operations, UX structure, roadmap boundaries, prioritization matrix; domain gained
  InventoryTransaction, SalesTransaction/SalesSource, Plan/Entitlement, Transfer, DR-29..36;
  decisions rewritten with rationale/alternatives (C-01..23, PD-01..42, A-01..14, contradictions).
- The $49 single plan is superseded by the pricing hypotheses (Complete $99/location, Standalone
  $99, InventoryOS POS $149–199 future, Multi-location ~$149–199+, Advanced ~$199–299+).

**Verified**
- Script check: 0 broken links/anchors; every referenced PD, DR, AC, A, C, E, WF and FR ID is defined.
- JuJu `git status` clean; this repository changed only under `docs/` (untracked).

**Files changed**
- `docs/product/*` (13 renamed/updated + `PRICING_AND_PACKAGING.md` added).

**Open issues**
- P0: PD-01, PD-02, PD-13, PD-18, PD-21, PD-31, PD-42.

**Decisions made**
- None by the agent beyond recommendations recorded in `PRODUCT_DECISIONS.md` §2.

**Next task**
- Owner reviews Phase 1. Phase 2 (existing-project audit) only on the owner's go-ahead.

---

## 2026-11-04 — InventoryOS Phase 1: product blueprint (docs only)

**Implemented**
- `docs/product/README.md` + documents 01–12 defining InventoryOS (new SaaS, no codebase):
  vision, 14-module specification with release tags, workflows WF-01..13, domain model and
  integrity rules DR-01..28, roles/permissions, SaaS requirements, JuJu reference + capability
  matrix, integration requirements, NFRs + production readiness standard, decisions
  (C-01..19, PD-01..30, A-01..12), glossary, acceptance criteria (118 `AC-` IDs).
- No application code, schema, tests or existing documentation changed in either repository.

**Verified**
- Internal links/anchors and cross-referenced IDs (PD, DR, AC, A, WF) resolve: script check, 0 broken.
- JuJu repository `git status` clean; this repository only gained files under `docs/product/`.
- Not verified: anything requiring running either application (out of Phase 1 scope).

**Files changed**
- Added: `docs/product/README.md`, `docs/product/01-…` through `12-…`.

**Open issues**
- P0 owner decisions: PD-01 (InventoryOS vs StockPulse identity), PD-02 (foundation), PD-03
  (JuJu relationship), PD-13 (release plan), PD-18 (tenant isolation), PD-21 (auth).

**Decisions made**
- None by the agent beyond recommendations recorded in `docs/product/10-PRODUCT_DECISIONS.md` §2.

**Next task**
- Owner: answer P0 decisions. Then Phase 2 technical audit against `docs/product/`.

---

## 2026-11-04 — Master project audit (audit only)

**Implemented**
- Audit deliverables 1–17 and the founder decision list in `docs/audit/`.
- 8 audit probes in `docs/audit/probes/test_audit_probes.py` (outside `tests/`, not collected by
  the suite) that reproduce the critical findings.
- No application code, migrations, tests or configuration changed.

**Verified**
- Full suite, SQLite, with developer `.env`: 496 passed, 46 failed, 6 skipped.
- Full suite, SQLite, `APP_BASE_URL=http://localhost:8000`: 542 passed, 6 skipped.
- Full suite, PostgreSQL 16 in the compose network (empty `stockpulse_audit` DB): 548 passed.
- Probes: 8/8 pass on SQLite and on PostgreSQL 16.
- Not performed: dependency vulnerability scan (needs `pip-audit` install); any live call to
  Square, Stripe, SMTP or Anthropic; load testing of the async-route stall (C8).

**Files changed**
- Added: `docs/audit/00-INDEX.md` … `12-decisions.md`, `docs/audit/probes/test_audit_probes.py`,
  `docs/EXECUTION-LOG.md`.

**Open issues** (see `docs/audit/01-executive-audit.md#findings-register`)
- Critical: O1 no git remote; C2 same-day consumption loss; C4 swallowed Square writes; C6 no ledger.
- High: C3, C5, C7, C8, P1–P5, S1–S3, T1.

**Decisions made**
- None. D1–D13 await the founder (`docs/audit/12-decisions.md`).

**Next task**
- Founder: B01 — create a private GitHub repo and push.
- Agent, on approval: B02 — hermetic test configuration.
