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
- Only the ledger posting service writes stock movements and balances (DR-01..07):
  `post_transaction()` / `reverse_transaction()` in `app/ledger/posting.py` (ADR-0040); nothing
  else names the ledger models (a test enforces it). A nightly job (`app/ledger/verify.py`,
  ADR-0045) checks balance = Σ movements = stock row and raises `balance_drift`; it never
  corrects either figure. A manual movement (an adjustment, waste) posts with
  `refuse_below_zero=True`: below zero only with `adjustment.override_negative` and a reason
  (DR-08). Adjustments go through `app/adjustments.py` (ADR-0046); history is read through
  `app/ledger/history.py`. A count session (`app/counts.py`, ADR-0047) compares each line
  with `balance_at()` its own `counted_at` and posts through `post_count_lines()` under the
  lock. It is also the only writer of
  `stock_items.qty_base`, the balance's projection that screens read (ADR-0042): a count posts
  through `post_count()`, a new item's quantity through `post_opening()`, a receipt as a
  `receipt`; a test fails on any other assignment. Square's counts never change stock (C-34).
  `inventory_counts` and `inventory_events` are frozen history (DM-7): nothing creates them.
- An item's current cost changes only through `app/costs.py` (ADR-0048): `set_cost()` appends
  to `item_cost_history` (append-only) and mirrors `stock_items.cost_per_base_cents`; a hand
  change is `override_cost()` (audited). A test fails on any other assignment. Past figures
  read the movement's snapshot, never today's cost (AC-ANL-01); stock value comes from
  `value_stock()` (Σ max(held, 0) × cost, its basis stated, items without a cost listed).
- The dashboard (`/app`, ADR-0049) reads only exceptions and the ledger: needs attention is
  `needs_attention()` (open exceptions, severity then money at stake, each linking to its
  record); money over a period is `period_money()` in each location's business days. Its
  data goes through `without_costs()` for a role without `inventory.view_costs`.
- Every query goes through the scoped data-access layer (tenant + location) (C-33, ADR-0026):
  routes take `org: OrgContext = Depends(require_org)`, services take `org`, queries use
  `select_scoped`/`get_scoped`/`update_scoped`; a lookup across organizations is a named function
  in `app/access/system.py`. A test fails on raw queries in `app/web` and `app/orchestration`.
  A new route that takes an identifier needs a case in `tests/test_isolation.py` (a test fails
  until it has one); an optional `?location=` goes through `location_filter`.
- A person reaches a business only through an active membership (ADR-0028): the request's
  business is the session's membership, never `users.tenant_id`. Users are global; a business
  reaches its people through `members(db, org)`.
- Team changes go through `app/team.py` (rules in `app/transform/team.py`, ADR-0033): a
  business always keeps one active Owner (DR-27), only `owner.manage` touches an Owner, and
  removing a member revokes the membership and its sessions.
- Anything that needs a person is an exception (`app/exceptions.py`, ADR-0043), the only writer
  of `exceptions`: `record_exception()` for a problem seen as an event (it adds to the open
  one), `apply_findings()` for an evaluation (opens, refreshes, auto-resolves its own types).
  People close one only by fixing its cause or dismissing it with a reason; financial types
  and every amount stay hidden from a role without `inventory.view_costs`. `alerts` is frozen
  history (DM-8): nothing creates an `Alert` (a test enforces both).
- A change to settings, locations, the team, a connection, the plan, a mapping, a product or a
  recipe
  calls `record_audit()` (`app/audit.py`, ADR-0035) in the same change. Audit events are
  append-only: nothing else names `AuditEvent` (a test enforces it).
- Every product route takes `org: OrgContext = Depends(require_permission("key"))` with a key
  from `app/access/permissions.py` (ADR-0029; a test fails for a route without one). Money in a
  page sits behind `can(request, "inventory.view_costs")`; JSON for a role without it goes
  through `without_costs`.
- Sessions are rows (ADR-0030): the cookie holds only a token. Every POST form carries
  `{{ csrf_field(request) }}` and JavaScript posts send `X-CSRF-Token` (a test checks the
  templates); logout is a POST. Tests post like a browser through `tests/browser.py`.
- A person with two-step sign-in is signed in only after a code (`/login/code`, `app/mfa.py`,
  ADR-0034): no other path may call `login_user` for them on a password alone. Secrets kept at
  rest are encrypted through `app/crypto.py` (`ENCRYPTION_KEY`).
- An action that costs AI or connects outside (POS, invoice and photo reading, invitations)
  also takes `Depends(require_verified)` (ADR-0031). Emailed links open a page whose button posts.
- A form that guesses at a secret or sends email on request is throttled through
  `app/throttle.py` (`locked_for`, `hit`, `clear`; ADR-0032); a refusal reveals nothing more
  than a success would.
- Provider-specific (Square) code only in `app/providers/`; core works with no POS (DR-29).
- Pure business rules in `app/transform/` (no ORM, no I/O).
- Items are defined once per business (`inventory_items`) with per-location settings
  (`item_locations`) (C-38, ADR-0036, ADR-0037). A stock item's name, category, dimension, unit,
  expiry tracking, reorder point, par and active flag read from those rows and write to both;
  queries on them go through `app/catalog.py` (`active_stock`, `by_item_name`), never
  `StockItem`'s legacy columns (a test enforces it).
  Item and category changes go through `app/items.py` (screens at `/app/catalog`, ADR-0038):
  items are deactivated and categories archived or merged, never deleted (DR-24).
- A quantity someone enters in a unit or package goes through the resolver
  (`app/transform/conversions.py` via `entered_base` in `app/items.py`, ADR-0039): across
  weight, volume and count only an item's own conversion converts; never assume a density.
- "Today" at a location is `business_date(now, location.timezone, location.business_day_cutoff_hour)`
  (`app/transform/business_days.py`, ADR-0027), never UTC's date.
- Schema (ADR-0025): instants are `UTCDateTime` (aware UTC; naive values refused); every foreign
  key is `ref()` (RESTRICT) or `owned_by()` (CASCADE, parts of a parent only), or `earlier()`
  for a self-reference in an append-only table (ADR-0040); CHECKs are named.
- No prices in domain code (DR-36); no per-customer or per-segment code (C-30).
- Every response carries CSP and the other security headers (`app/web/headers.py`). Inline
  `<script>` tags need `nonce="{{ request.state.csp_nonce }}"`; no inline event handlers or
  `javascript:` URLs (a test enforces both). Templates see only `PublicSettings`, never secrets.
- Routes are plain `def` (FastAPI runs them in its threadpool); a route that needs the raw
  body or a dynamic form takes it from `app/web/inputs.py`. `async def` routes would run the
  synchronous database and API calls on the event loop and stall the worker (C8; a test enforces it).
- Work that should not hold up a request, or that runs per business on a clock, is a job
  (`app/jobs.py`, ADR-0044): `enqueue(db, org, kind, payload, dedupe_key=...)`, drained by the
  scheduler process. Jobs run at least once, so a handler must be safe to repeat. A new kind
  goes in `app/transform/jobs.py` (its CHECK by migration) with a handler in `app/jobs.py`.
  Tests run the queue with `drain()`.
- Logs are JSON with `request_id`, `tenant_id`, `user_id` (`app/observability.py`). Never log
  bodies, cookies, query strings, tokens, passwords or email addresses (log ids instead); emails
  are masked as a last resort. A new scheduled job is registered through `scheduler._job`, and a
  per-tenant loop wraps each tenant in `log_context(tenant_id=...)`.
- An upload's type comes from its bytes (`detect_media_type`), never its name or Content-Type;
  uploads are served with an allow-listed type, `nosniff` and `Content-Disposition`.
- What a business sells is its own catalog (C-36, ADR-0050): `products`, `product_variations`
  (sizes, informational price) and `modifiers` (none, scale, add), changed only through
  `app/products.py` and never deleted. A POS catalog is mapped onto it (T3.03, T3.04); the
  Square sync writes `sold_items` only. A price is money: behind `inventory.view_costs`.
- Recipes are versioned (ADR-0051): `recipes`, `recipe_versions`, `recipe_lines`, written only
  by `app/recipes.py` (`save_recipe()`). Versions and lines are append-only: an edit is the
  next version, effective now or later, never earlier, and anything that happened reads the
  version in effect at its own time (`effective_version()`, DR-16). Lines convert through
  `entered_base` (DR-11); prepared items never form a circle (DR-17).
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
