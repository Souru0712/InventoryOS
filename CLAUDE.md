# InventoryOS — project instructions for Claude Code

InventoryOS is a multi-tenant inventory and operations SaaS for independent food-service
businesses, built on this Python/FastAPI codebase (formerly StockPulse). These instructions govern
implementation sessions; the detail lives in `docs/`.

## Read before working
1. `docs/roadmap/README.md` — governance and handoff.
2. The task's entry in `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md` (status index at the top).
3. For any domain change: its module in `docs/product/PRODUCT_SPECIFICATION.md`, workflows in
   `BUSINESS_WORKFLOWS.md`, rules DR-01..36 in `DOMAIN_MODEL.md`, criteria in
   `PRODUCT_ACCEPTANCE_CRITERIA.md`. `docs/product/PRODUCT_DECISIONS.md` wins any conflict.
4. For any technical choice: `docs/adr/README.md` (index of architecture decision records
   ADR-0001..0023, one per approved implementation decision ID-01..ID-23).

## How to work
- One task at a time, only if its dependencies are Completed. Small, reviewable commits.
- `main` is protected (ruleset "Protect main": required CI checks, branch up to date, no force
  push, no deletion, no bypass). Never push to `main` directly: push a branch, open a pull request,
  and merge only when every check is green and the owner approves. The repository is public.
- Read the requirement before coding; no unrelated refactoring; preserve working behaviour.
- Add or update tests with every change; run the full suite; CI must be green on SQLite and Postgres.
- Record completion: task status in the index + an entry in `docs/EXECUTION-LOG.md`.
- Update any affected doc in the same change. Technical decisions → a new ADR in `docs/adr/` from
  `0000-template.md`, added to its index (the docs checker enforces the index and one ADR per ID).
- Never change product scope silently: stop, record the finding, propose a decision.

## Architecture rules (do not break)
- Only the ledger posting service writes stock movements and balances (DR-01..07).
- Every query goes through the scoped data-access layer (tenant + location) (C-33, ADR-0026):
  routes take `org: OrgContext = Depends(require_org)`, services take `org`, queries use
  `select_scoped`/`get_scoped`/`update_scoped`; a lookup across organizations is a named function
  in `app/access/system.py`. A test fails on raw queries in `app/web` and `app/orchestration`.
  A new route that takes an identifier needs a case in `tests/test_isolation.py` (a test fails
  until it has one); an optional `?location=` goes through `location_filter`.
- A person reaches a business only through an active membership (ADR-0028): the request's
  business is the session's membership, never `users.tenant_id`. Users are global; a business
  reaches its people through `members(db, org)`.
- Every product route takes `org: OrgContext = Depends(require_permission("key"))` with a key
  from `app/access/permissions.py` (ADR-0029; a test fails for a route without one). Money in a
  page sits behind `can(request, "inventory.view_costs")`; JSON for a role without it goes
  through `without_costs`.
- Sessions are rows (ADR-0030): the cookie holds only a token. Every POST form carries
  `{{ csrf_field(request) }}` and JavaScript posts send `X-CSRF-Token` (a test checks the
  templates); logout is a POST. Tests post like a browser through `tests/browser.py`.
- An action that costs AI or connects outside (POS, invoice and photo reading, invitations)
  also takes `Depends(require_verified)` (ADR-0031). Emailed links open a page whose button posts.
- A form that guesses at a secret or sends email on request is throttled through
  `app/throttle.py` (`locked_for`, `hit`, `clear`; ADR-0032); a refusal reveals nothing more
  than a success would.
- Provider-specific (Square) code only in `app/providers/`; core works with no POS (DR-29).
- Pure business rules in `app/transform/` (no ORM, no I/O).
- "Today" at a location is `business_date(now, location.timezone, location.business_day_cutoff_hour)`
  (`app/transform/business_days.py`, ADR-0027), never UTC's date.
- Schema (ADR-0025): instants are `UTCDateTime` (aware UTC; naive values refused); every foreign
  key is `ref()` (RESTRICT) or `owned_by()` (CASCADE, parts of a parent only); CHECKs are named.
- No prices in domain code (DR-36); no per-customer or per-segment code (C-30).
- Every response carries CSP and the other security headers (`app/web/headers.py`). Inline
  `<script>` tags need `nonce="{{ request.state.csp_nonce }}"`; no inline event handlers or
  `javascript:` URLs (a test enforces both). Templates see only `PublicSettings`, never secrets.
- Routes are plain `def` (FastAPI runs them in its threadpool); a route that needs the raw
  body or a dynamic form takes it from `app/web/inputs.py`. `async def` routes would run the
  synchronous database and API calls on the event loop and stall the worker (C8; a test enforces it).
- Logs are JSON with `request_id`, `tenant_id`, `user_id` (`app/observability.py`). Never log
  bodies, cookies, query strings, tokens, passwords or email addresses (log ids instead); emails
  are masked as a last resort. A new scheduled job is registered through `scheduler._job`, and a
  per-tenant loop wraps each tenant in `log_context(tenant_id=...)`.
- An upload's type comes from its bytes (`detect_media_type`), never its name or Content-Type;
  uploads are served with an allow-listed type, `nosniff` and `Content-Disposition`.
- JuJu (`C:\Users\oscar\.vscode\JuJu`) and the snack-box project are separate repositories: never
  modify them from here.

## Commands
- Dependencies: `uv sync` installs exactly `uv.lock` into `.venv`. Change dependencies only in
  `pyproject.toml` and re-run `uv lock`; commit both. The project is never packaged (`[tool.uv]
  package = false`).
- Tests: `uv run pytest -q` from the repository root (or `.venv/Scripts/python.exe -m pytest -q`). The suite is hermetic: under
  `APP_ENV=test` settings ignore `.env`, and `tests/conftest.py` clears inherited settings variables.
- Postgres run: set `TEST_DATABASE_URL` to an empty Postgres 16 database.
- Lint and format: `uv run ruff check .` and `uv run ruff format .` (line length 100, bandit
  security rules on). Types: `uv run mypy`, strict for `app/transform/`, `app/access/` and
  the coming `app/ledger/`, gradual elsewhere. Settings in `pyproject.toml`. Keep formatting-only changes in their own
  commit and list it in `.git-blame-ignore-revs`.
- CI: `.github/workflows/ci.yml` runs the docs checker and the suite on SQLite, and the suite on
  PostgreSQL 16 (no skips allowed), plus lint and types (ruff, mypy), a dependency audit
  (pip-audit on the lock) and a Docker build whose installed versions must match `uv.lock`, on
  every push to `main` and every PR.
  Dependabot proposes dependency, action and image updates weekly.
- Audit probes in `docs/audit/probes/` assert known defects; never add them to CI.

## Documentation contract
- The master roadmap, task breakdown and data migration roadmap must agree. Before committing any
  change under `docs/`, run `python scripts/check_docs.py` (also in the suite as
  `tests/test_docs_contract.py`). It proves references and ordering, not that code is correct.

## Requires the owner's explicit approval
Anything marked OPEN or proposed; new dependencies; destructive migrations; pricing or plan
changes; security configuration; production deploys; stage-gate sign-off; changing owner
decisions (C-entries). **Never build native POS functionality.**
