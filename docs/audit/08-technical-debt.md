# 08 — Technical debt

Ranked by cost of carrying it into launch. "Interest" = what it costs each week it stays.

| Rank | Debt | Interest | Fix | Size |
|---|---|---|---|---|
| 1 | Mutable balance instead of ledger (C6) | Every new inventory feature (waste, transfers, variance, refunds) either bolts onto the wrong model or forces this refactor later at higher cost | R1 | L |
| 2 | Daily-aggregate sales as the consumption source (C2, C3, DB10) | Derived stock is wrong for every juice bar/café receiving daily; multi-location wrong | R2 | L |
| 3 | No git remote, no CI (O1) | One disk failure loses the business; every change verified by hand | Push + Actions | S |
| 4 | Tests not hermetic (T1): `Settings` reads the developer's `.env`; with the current one, **46 tests fail** | Green suite depends on whose laptop runs it; already masks real regressions | Pin env in `tests/conftest.py`; `env_file=None` under test | S |
| 5 | In-request side effects with swallowed errors (C4, C8) | Silent data loss; event loop stalls | R3, R5 | M |
| 6 | Private snack-box module inside the product (~2,700 lines, 35 routes, 11 tables, extra OAuth scopes) | Every migration, review, test run and Square review carries it | Extract or isolate (D6) | M |
| 7 | Two-tier entitlements vs one-plan brief | Pricing page, Stripe products, tests and docs disagree with the strategy | Collapse to one plan (D1) | S |
| 8 | Hand-rolled tenant checks per route (S8) | One forgotten check is a cross-tenant leak | Scoped-get helper + parametrised isolation test over every route | S |
| 9 | Naive datetimes + `_aware()` duplicated in 5 modules (DB3) | Timezone bugs; already visible in weekly report scheduling (UTC hour, tenant timezone ignored — `app/orchestration/scheduler.py:61`) | `DateTime(timezone=True)` migration | M |
| 10 | No DB constraints (DB1, DB2) | Bad data accepted silently | CHECKs with R1 | S |
| 11 | Unpinned dependencies (O5) | Non-reproducible builds; a FastAPI/Starlette major can land on deploy | Lockfile | S |
| 12 | Stale documentation: README says "60 tests"; `NEEDS-YOU.md` says Phase A "verified" while C2–C5 exist; README's onboarding asks the founder to edit tenant rows by hand | Misleads future sessions and any hire | Update after fixes | S |
| 13 | Commit `22ee0b5` mixes the redesign with snack-box work (noted in `NEEDS-YOU.md` §7b) | History only | Leave; not worth rewriting | — |
| 14 | `sync_all_tenants` sequential, 60-day full re-read every hour (SQ2) | Scales to ~50 tenants | Incremental cursor | M |
| 15 | Synchronous invoice extraction in the request | Host request timeouts (often 30–100 s) on large PDFs | Job queue (R5 later stage) | M |

Not debt (deliberate, keep): pure `transform/` layer; base-unit storage; derived-not-stored batch
quantities; read-only lapse instead of lock-out; explainable rule-based scoring.
