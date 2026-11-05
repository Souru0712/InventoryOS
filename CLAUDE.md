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

## How to work
- One task at a time, only if its dependencies are Completed. Small, reviewable commits.
- Read the requirement before coding; no unrelated refactoring; preserve working behaviour.
- Add or update tests with every change; run the full suite; CI must be green on SQLite and Postgres.
- Record completion: task status in the index + an entry in `docs/EXECUTION-LOG.md`.
- Update any affected doc in the same change. Technical decisions → ADR in `docs/adr/`.
- Never change product scope silently: stop, record the finding, propose a decision.

## Architecture rules (do not break)
- Only the ledger posting service writes stock movements and balances (DR-01..07).
- Every query goes through the scoped data-access layer (tenant + location) (C-33).
- Provider-specific (Square) code only in `app/providers/`; core works with no POS (DR-29).
- Pure business rules in `app/transform/` (no ORM, no I/O).
- No prices in domain code (DR-36); no per-customer or per-segment code (C-30).
- JuJu (`C:\Users\oscar\.vscode\JuJu`) and the snack-box project are separate repositories: never
  modify them from here.

## Commands
- Tests: `python -m pytest -q` from the repository root with the project venv
  (Windows: `.venv/Scripts/python.exe -m pytest -q`). Until T0.01 lands, prefix
  `APP_BASE_URL=http://localhost:8000` or a developer `.env` breaks ~46 tests.
- Postgres run: set `TEST_DATABASE_URL` to an empty Postgres 16 database.
- Audit probes in `docs/audit/probes/` assert known defects; never add them to CI.

## Documentation contract
- The master roadmap, task breakdown and data migration roadmap must agree. Before committing any
  change under `docs/`, run `python scripts/check_docs.py` (also in the suite as
  `tests/test_docs_contract.py`). It proves references and ordering, not that code is correct.

## Requires the owner's explicit approval
Anything marked OPEN or proposed; new dependencies; destructive migrations; pricing or plan
changes; security configuration; production deploys; stage-gate sign-off; changing owner
decisions (C-entries). **Never build native POS functionality.**
