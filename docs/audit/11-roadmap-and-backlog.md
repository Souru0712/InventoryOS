# 11 — Roadmap and prioritized backlog

Principle: integrity before features, features before polish. Each backlog item is sized to be
one independently verifiable AI task (S ≤ ½ day, M ≈ 1–2 days, L ≈ 3–5 days of agent work plus
your review). ⚠ = stop for your approval before starting (per your stop list).

Durations below assume your ~1 hour/day of review and asynchronous agent work. They are
estimates, not commitments.

## Roadmap

| Phase | Goal | Exit criterion | Est. |
|---|---|---|---|
| 0 Safety net | Code is backed up; every change verified the same way on any machine | Private remote; CI green on SQLite + Postgres | 2–3 days |
| 1 Integrity | Stock numbers are correct and explainable | Ledger invariant holds after every flow; C2–C5, C8 closed; probes R1–R6 fail (fixed) | 3–4 weeks |
| 2 MVP completeness | An owner and staff run the product without you | Recipes, waste, item edit, staff, settings, export, auth hardening shipped | 3–4 weeks |
| 3 Commercial | One plan is purchasable and email is real | Stripe test-mode lifecycle passes; real alert + report land in Gmail and Outlook | 1 week (+ your account setup) |
| 4 Deploy | Staging and production on a host with backups | Square sandbox Phase D script passes on staging; restore drill done | 1–2 weeks |
| 5 Pilot | 3 paying juice bars/cafés | Each onboards in < 45 min; no founder DB edits for 2 weeks | 4 weeks |
| 6 Toward 21 | Repeatable sales + onboarding | 21 paying (~$1,000 MRR at $49) | ongoing |
| Post-launch (E) | Depth | Driven by pilot feedback | — |
| Long-term (F) | Expansion | After $1k MRR | — |

## Prioritized engineering backlog

### Phase 0 — Safety net

| ID | Task | Fixes | Size | Accept when |
|---|---|---|---|---|
| B01 | Create a private GitHub repo and push (**your action**, 5 min — or approve me running `gh repo create --private`) | O1 | S ⚠ | Remote has all branches |
| B02 | **Hermetic tests**: pin every setting in `tests/conftest.py`, ignore `.env` under `APP_ENV=test` | T1 | S | Suite passes with the current `.env` present and with it absent |
| B03 | CI: GitHub Actions, SQLite job + Postgres 16 service job, `pip-audit` | T1, deps | S | Both jobs green on push |
| B04 | Lock dependencies; Dockerfile installs from the lock | O5 | S | Image build reproducible |

### Phase 1 — Integrity

| ID | Task | Fixes | Size | Accept when |
|---|---|---|---|---|
| B05 | Invoice apply: conditional status transition (`UPDATE … WHERE status <> 'applied'`) | C5 | S | Probe R4 fails; new Postgres concurrency test passes |
| B06 | Blocking routes → plain `def`; Anthropic client timeout ≤ 60 s | C8 | S | No sync I/O inside `async def` (grep test) |
| B07 | Invoice image: serve `SUPPORTED_MEDIA[suffix]`, `nosniff`, `Content-Disposition: inline`; sniff upload with Pillow | S3, S12 | S | Probe R7 fails |
| B08 | **Ledger design** written up for approval | C6 | S ⚠ | You approve D3 |
| B09 | Ledger table + projection + backfill `opening` movements (additive migration) | C6 | L ⚠ | Invariant check passes on demo data and on a copy of staging |
| B10 | Route every quantity write through one ledger service; counts → `count_variance`; Square → `pos_correction`; batch removal → void, not delete | C6, C9, risk 6 | L | Probes R3 (event part), R5, R6 fail; no `qty_base =` outside the service |
| B11 | POS outbox + retry + "not yet in Square" banner | C4 | M | Probe R3 fails; outage test lands after retry |
| B12 | Order-level sales (`pos_sale_lines` with `closed_at`, location, return link); timestamped consumption; incremental cursor | C2, C3, SQ2 | L ⚠ | Probes R1, R2 fail; derived milk matches hand calculation over a test week |

### Phase 2 — MVP completeness

| ID | Task | Fixes | Size |
|---|---|---|---|
| B13 | Recipe builder (Square item → ingredients, unit pickers, cost per drink) | P1 | M |
| B14 | Item edit / deactivate | P3 | S |
| B15 | Waste module: reasons, cost snapshot, actor, phone-friendly entry, weekly waste cost in report and dashboard | P2, C7 | M |
| B16 | Auth hardening: login throttle, password reset + change, email verification, session rotation + server-side revocation, security headers, CSRF tokens | S1, S2, S4, S5, S10 | M |
| B17 | Memberships + Owner/Staff roles + email invites + scoped-get helper + parametrised cross-tenant test | P5, S8, DB5 | M ⚠ |
| B18 | Org settings: name, timezone, report day/hour (use tenant timezone) | P6 | S |
| B19 | CSV export (items, movements, waste, receipts); deletion request → scheduled purge with FK rules | P7, DB2 | M ⚠ |
| B20 | Square: disconnect/reconnect UI, one active tenant per merchant, product-only OAuth scopes | S6, S7, SQ5 | M ⚠ |
| B21 | Onboarding: checklist, bulk add/CSV import, one-screen opening count | onboarding | M |

### Phase 3 — Commercial

| ID | Task | Fixes | Size |
|---|---|---|---|
| B22 | One plan: collapse `CAPABILITIES`, pricing page, trial on the single plan | D1 | S ⚠ |
| B23 | Stripe test-mode lifecycle run (needs your Stripe account) | B-live | S |
| B24 | Email provider + bounce webhook into `email_suppressions` (needs your provider choice) | O | S |
| B25 | AI cost guard: verified email before extraction; per-tenant daily cap; `/app/scan/expiry` behind `require_writable` | S9 | S |

### Phase 4 — Deploy

| ID | Task | Size |
|---|---|---|
| B26 | Staging: host, managed Postgres, R2, Sentry, uptime check, JSON logs | M ⚠ |
| B27 | Backups: verify automated backups + PITR; documented restore drill executed once | S ⚠ |
| B28 | Square sandbox Phase D script on staging (`NEEDS-YOU.md`) | M |
| B29 | Production deploy | S ⚠ |

### Post-launch (E), in likely order

Refund ingestion (record returns; operator classifies restock vs waste; never auto-restore) ·
supplier entity + price history · recipe costing and gross margin · count variance report ·
recipe versions · prep/production items (pressed juice) · purchase orders · transfers ·
multi-location UI · extraction as a background job · HTMX on receiving/counts.

### Long-term (F)

Forecasting and reorder optimisation · Clover / Toast adapters · SMS · supplier catalogs ·
approval workflows.

## First recommended implementation task

**B02 — hermetic tests.** Smallest change with the widest payoff: today the same commit passes
or fails 46 tests depending on whose `.env` is on disk, so no later task can honestly be called
"verified". No schema change, no behaviour change, ~1 file. Follow with B05, B06, B07 (each small,
independent, no approval gate), then B08 for your decision on the ledger.

You should do **B01** yourself today: it is five minutes and it is the only copy of the code.
