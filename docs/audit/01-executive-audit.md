# 01 — Executive audit

## Summary

StockPulse is a well-structured FastAPI modular monolith (14k lines, 34 tables, 88 routes) whose
engineering hygiene is ahead of its product completeness. **The full suite passes on PostgreSQL
16 (548/548) and on SQLite (542 + 6 Postgres-only skips)** once a test-isolation bug is
neutralised. Migrations, deploy guards, webhook verification, token encryption and billing
lifecycle logic are sound.

It is **not ready for paying customers**, for three reasons:

1. **The stock numbers the product sells on are wrong for the target customer.** Weight/volume
   stock (milk, produce) loses every sale made after a count or delivery on that day (C2); a juice
   bar receiving each morning barely sees stock fall. Recipe consumption ignores location (C3). A
   failed Square write during receiving is silently erased at the next sync (C4). An invoice can
   be applied twice (C5). There is no inventory ledger to detect or explain any of it (C6).
   All five reproduced by executable probes on SQLite and Postgres.
2. **An owner cannot run it without the founder.** No recipe UI (P1) — recipes exist only in the
   demo seed — no waste logging (P2), no item edit (P3), no password reset (P4), no staff (P5).
3. **Nothing external has ever run live.** Square, Stripe, email and Claude extraction are tested
   only against recorded payloads; no email has ever been delivered.

And one risk that outranks all of them: **the code has no git remote** — this machine holds the
only copy (O1).

No rebuild is warranted. The path is: safety net → ledger and integrity fixes → five missing
self-serve features → live integrations → deploy → pilot. Estimated 8–11 weeks to a first paying
pilot at the founder's current cadence ([11](11-roadmap-and-backlog.md)).

## Current application state

| | Fact | Source |
|---|---|---|
| Built | Square catalog/count/sales sync, inventory webhooks, invoice photo receiving with Claude, barcode scanning, batches/FEFO/expiry queue, date reading, markdown suggestions, health score, reorder list + CSV, email digests and weekly report (rendered), Stripe billing, entitlements, trial, read-only lapse, scheduler with leader lock, S3 storage, Sentry | [02](02-feature-inventory.md) |
| Verified here | 548 tests on Postgres 16; 8 audit probes | [07](07-tests.md) |
| Never live | Square sandbox, Stripe test mode, SMTP, Anthropic on real documents | `NEEDS-YOU.md` |
| Deployed | Nowhere; local Docker replica only | — |
| Customers | 0 | — |
| Private module | Snack-box shop: ~2,700 lines, 35 routes, 11 tables, widens Square scopes | [03](03-architecture.md) |

## Critical blockers

| ID | Blocker | Evidence |
|---|---|---|
| O1 | No git remote; no CI | `git remote -v` empty |
| C2 | Same-day consumption after a count/receipt never deducted | probe R1 [RUN]; `app/transform/inventory.py:93` |
| C3 | Recipe consumption ignores location | probe R2 [RUN]; `app/transform/inventory.py:60` |
| C4 | Failed Square write on receipt/count swallowed, then overwritten | probe R3 [RUN]; `app/orchestration/receiving.py:395-402` |
| C5 | Invoice double-apply race | probe R4 [RUN]; `app/orchestration/receiving.py:288` |
| C6 | No inventory ledger; history overwritten | [04](04-database-and-ledger.md#inventory-ledger-assessment) |
| C7 | No waste module | — |
| C8 | Blocking I/O in `async def` routes stalls the worker | `app/web/receiving.py:82,182`, `app/web/scanning.py:309`, `app/web/webhooks.py:110` |
| P1 | No recipe UI | `app/cli.py:351` sole writer |
| S1–S3 | No login throttle; no reset/verify; stored XSS via upload content type | probes R7, R8 [RUN] |
| T1 | Suite not hermetic: developer `.env` fails 46 tests | [07](07-tests.md#t1--the-suite-is-not-hermetic) |

## Technical risks (beyond blockers)

S6 over-scoped Square OAuth · S7 one merchant attachable to many tenants · S8 tenant checks by
convention only · S9 AI cost abuse via unverified trials · SQ2 hourly 60-day full order re-read ·
DB1–DB3 no constraints, no delete rules, naive timestamps · O2 no backups · O5 unpinned deps ·
pricing model in code contradicts the one-plan strategy (D1).

## Recommended architecture

Keep the monolith, Jinja, SQLAlchemy/Alembic, Postgres, the pure `transform/` layer. Add, in
order: append-only movement ledger (R1), order-level sales ingestion (R2), POS outbox (R3),
conditional state transitions (R4), sync routes / job offload (R5), memberships + roles (R6),
waste on the ledger (R7). No microservices, no frontend rewrite, no new queue infrastructure.
Detail: [03](03-architecture.md#recommended-architecture).

## Required MVP gaps

Recipes · waste · item edit · auth hardening (throttle, reset, verify, CSRF, headers) · Owner/Staff
roles with invites · org settings · export + deletion · Square disconnect and scope split ·
onboarding checklist · single-plan billing · real email · AI spend cap.
Detail: [10](10-product-gaps-and-mvp.md).

## Prioritized roadmap

Phase 0 safety net (days) → 1 integrity (3–4 wk) → 2 MVP completeness (3–4 wk) → 3 commercial
(1 wk) → 4 deploy (1–2 wk) → 5 pilot of 3 → 6 toward 21. Backlog B01–B29 in
[11](11-roadmap-and-backlog.md).

## First recommended implementation task

**B02 — make the test suite hermetic** (pin settings in `tests/conftest.py`; ignore `.env`
under test). Small, no behaviour change, and every later "verified" depends on it. Your own
first action: **B01 — push to a private GitHub repo.**

## The eight known risks — answers

| # | Question | Answer |
|---|---|---|
| 1 | Receiving overwrites Square? | No — sends an adjustment. But a failed push loses the delivery (C4) |
| 2 | Counts and deliveries distinct? | At Square yes; locally no — both re-anchor derived stock (C2) |
| 3 | Duplicate events double-deduct? | No today (absolute counts, idempotent re-aggregation); no event-id record |
| 4 | Refunds restore ingredients? | No — refunds are ignored entirely; sales are overstated |
| 5 | Remakes/waste independent of sale? | Not modelled; no waste events |
| 6 | History silently edited/deleted? | Yes — balance overwrites without movements; batch hard-delete |
| 7 | Tenant isolation enforced? | Yes in every product route (app layer); webhook routing ambiguous (S7) |
| 8 | Postgres actually tested? | Yes, in this audit: 548/548 on Postgres 16 |

## Findings register

| ID | Sev | Title | Doc |
|---|---|---|---|
| C2 | Critical | Same-day consumption lost after count/receipt | 04 |
| C3 | High | Recipe consumption ignores location | 04 |
| C4 | Critical | Square write failure swallowed, then overwritten | 06 |
| C5 | High | Invoice double-apply race | 04 |
| C6 | Critical | No inventory ledger | 04 |
| C7 | High | No waste module | 10 |
| C8 | High | Blocking I/O in async routes | 03 |
| C9 | Medium | Batch removal hard-deletes | 04 |
| P1–P10 | High–Med | Product gaps (recipes, waste, edit, auth, staff, settings, export, disconnect, derived math, pricing) | 10 |
| S1–S13 | High–Info | Security findings | 05 |
| SQ1–SQ9 | Med–Low | Square integration findings | 06 |
| DB1–DB11 | Med–Low | Schema findings | 04 |
| O1 | Critical | No git remote / CI | 03 |
| O2–O5 | Medium | Backups, monitoring, logs, unpinned deps | 05 |
| T1 | High | Non-hermetic tests | 07 |
