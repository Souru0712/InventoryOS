# Implementation decisions

Phase 3, 2026-11-05. Planning only. **Recommended** decisions needed to implement the roadmap.
Product decisions already made by the owner (C-01..C-42) are not repeated; see
[PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md). Once the owner approves an ID it is recorded as
a `C-` entry there and as an ADR in `docs/adr/` (T0.11). Status: **all 23 approved by the owner on
2026-11-05 ("Approve all 23") and recorded as C-43**, and as [ADR-0001..0023](../adr/README.md) (T0.11); ID-14's product recommendations are C-44.

## 1. Decisions (approved 2026-11-05)

| ID | Decision | Recommendation | Alternatives | Evidence / reason | Blocks |
|---|---|---|---|---|---|
| ID-01 | Application architecture and code layout | Keep the modular monolith (web + worker) and the existing layer-first layout; add a scoped data-access package and a single ledger posting service | Microservices; module-first package reorganization | Audit: layering is the codebase's strongest asset; scale 21–500 tenants; solo founder | T1.02, T1.16 |
| ID-02 | Hosting | **Render**: web service, background worker, managed Postgres 16 with point-in-time recovery, preview environments, auto-deploy from GitHub — to be confirmed against current plans at T0.09 | Fly.io (similar, more hands-on); AWS (ECS/RDS — most flexible, most operations work) | Owner: no budget ceiling, choose for compatibility, plan fit and automation; Docker image and two-process model already exist | T0.09, T4.09 |
| ID-03 | CI | GitHub Actions with SQLite and Postgres 16 jobs, required on `main` | Other CI services | Repository is on GitHub; `TEST_DATABASE_URL` already supported | T0.02 |
| ID-04 | Coding standards | ruff (lint + format) and mypy (strict on new modules, gradual elsewhere) | flake8 + black; no type checking | No linter or type config exists; many sessions will edit the code | T0.04 |
| ID-05 | Dependency locking | uv lockfile; Docker installs from it; Dependabot weekly | pip-tools | Unpinned ranges today (O5) | T0.03 |
| ID-06 | Background jobs | Postgres `jobs` table drained by the existing leader-locked worker; revisit a broker only if p95 job wait > 5 min or > 10k jobs/day | Redis + RQ/Celery now | One worker suffices at launch scale; fewer moving parts | T1.20 |
| ID-07 | Frontend interactivity | Keep server-rendered Jinja; allow **HTMX** (vendored, no build step) for floor screens only | Vanilla JS only; SPA framework | Floor tasks need partial updates on phones; baseline audit advised against a CSS/SPA swap | T2.21 |
| ID-08 | Browser end-to-end tests | Playwright (Python) with axe accessibility checks | No browser tests | 0 browser tests today; JuJu proves the approach | T2.21, T4.13 |
| ID-09 | Physical naming | Keep `tenants`/`tenant_id` in code and schema; "Organization" is the domain and UI term | Rename everything to `organizations` | Renaming touches every table for no customer value | Stage 1 |
| ID-10 | Item model tables (C-38) | `inventory_items` (org) + `item_locations` (per-location settings) + `stock_balances` (projection); `stock_items` kept as a compatibility layer until DM-16 | Keep `stock_items` per location | Multi-location without duplicate definitions | T1.13 |
| ID-11 | Email provider | Resend (PD-24) | Postmark (strong deliverability focus); SES | SMTP interface exists; free of lock-in; owner criteria favour fit — either works | T4.09 |
| ID-12 | Object storage | AWS S3 (superseded 2026-11-06 by C-48: photos kept briefly in Postgres, [ADR-0024](../adr/0024-invoice-photos-in-postgres.md)) | Cloudflare R2 (no egress fees) | App already supports S3-compatible storage; S3 matches the owner's existing stack | T0.09 |
| ID-13 | Pilot policy (refines C-40) | Free Standalone pilots from M4; Square pilots from M5; paid customers only after G-R1 | Wait for G-R1 for any pilot | Earliest real feedback without running the old engine; addresses TR-31 | Track V |
| ID-14 | Approve pending product recommendations | Approve as recommended: PD-05, PD-07, PD-08, PD-10, PD-11, PD-12, PD-15, PD-16, PD-19, PD-25, PD-36, PD-38 (P1) and PD-14, PD-17, PD-20, PD-22, PD-23, PD-24, PD-26, PD-27, PD-29, PD-30, PD-32, PD-37, PD-39, PD-40, PD-41 (P2) | Decide individually | Tasks were written to these recommendations; changing one changes the named tasks | T1.06, T1.23, T2.05, T2.09, T2.11, T2.12, T4.03, T4.06 |
| ID-15 | Multi-location pricing (PD-33) | Defer to Stage 6; R1 data model stays location-aware | Decide now | No multi-location customer yet; nothing in R1 depends on it | T6.01 |
| ID-16 | Snack-box extraction target (C-37) | A new private repository owned by the owner, created before T0.05; the module is copied there unchanged | Keep it on a branch in this repository | Clean separation; C-30 | T0.05 |
| ID-17 | JuJu validation approach (C-42) | Reference tenant by one-way import + scripted parity days ([JUJU_VALIDATION_PLAN](JUJU_VALIDATION_PLAN.md)) | Connect JuJu live before R1 | C-42; no coupling before the sales API exists | T5.01, T5.02 |
| ID-18 | Test database policy | Postgres CI is authoritative for integrity, concurrency, isolation and migration tests; SQLite for fast local unit runs | SQLite only | 6 Postgres-only tests today; locking semantics differ | T0.02 |
| ID-19 | Project `CLAUDE.md` | Keep the repository-root `CLAUDE.md` created in Phase 3 as the implementation agent's entry point | No project file | Handoff requirement (brief §14) | — |
| ID-20 | Architecture decision records | `docs/adr/NNNN-title.md`, one per approved ID and per later architecture change | Decisions only in PRODUCT_DECISIONS | Keeps product decisions and technical decisions apart | T0.11 |
| ID-21 | Task tracking | Status column in [DEVELOPMENT_TASK_BREAKDOWN](DEVELOPMENT_TASK_BREAKDOWN.md) index + one `EXECUTION-LOG.md` entry per task; GitHub Issues optional mirror | GitHub Projects as source of truth | One source of truth in the repository the agent reads | all |
| ID-22 | Postgres RLS timing | R1.1 (T4B.13), after the scoped layer and Postgres CI are stable (C-33 says "later") | In R1 | Scoped layer covers R1; RLS complicates SQLite tests | T4B.13 |
| ID-23 | New runtime dependencies | Approve: a TOTP library (e.g. pyotp), an XLSX reader (e.g. openpyxl), HTMX (vendored); dev-only: Playwright, axe, ruff, mypy, pip-audit. Any other new dependency needs approval | Build TOTP/XLSX in-house | Small, maintained libraries; fewer security bugs than custom code | T1.11, T2.13, T2.21 |

## 2. Recommendations already settled by owner decisions (for reference)

| Topic | Decision |
|---|---|
| Foundation | C-25 build on this codebase; C-35 replace the inventory engine; C-38 items per organization |
| Build order and release | C-27 engine → Standalone → Square; C-29 release plan; C-40 no pilots on the old engine |
| Tenancy and auth | C-30, C-33 scoped data access; C-31, C-32 finish in-app auth |
| Integrations | C-34 ledger owns stock, write-back off until R1.1; C-36 InventoryOS owns the catalog; Square first |
| Commercial | C-26 $1,000 MRR gate; C-41 retire Starter/Pro |
| Other projects | C-37 snack-box moves to its own project; C-42 JuJu parity by specification until R1 |

## 3. Unresolved questions

| # | Question | Needed by | Owner of the answer |
|---|---|---|---|
| Q-1 | Dates or a weekly time budget for the milestones (the roadmap is dependency-ordered, not dated) | Planning pilots and validation | Owner |
| Q-2 | Validation-plan decisions O-1..O-7 (founder hours, landing page, definition of "qualified" for PD-35, deposits, JuJu contacts, concierge pilot) | Track V | Owner |
| Q-3 | JuJu parity tolerances and BYO/walk-in modelling marked [OWNER] in the JuJu plan | T5.02 | Owner |
| Q-4 | Default business-day cutoff hour for new locations (proposed 04:00) | T1.04 | Owner (or accept proposal) |
| Q-5 | Whether to keep the existing marketing site content for InventoryOS or rewrite it at rebrand | T4.11 | Owner |

## 4. Evidence gaps to investigate before deciding

| Gap | How it will be closed | Task |
|---|---|---|
| Real-invoice extraction accuracy and cost | Trial on the owner's invoices | T4.15 |
| Behaviour against a live Square seller | Sandbox run, then one merchant pilot | T3.11 |
| Performance at design headroom | Load test | T4.14 |
| Accessibility of current templates | Review with axe and manual checks | T4.13 |
| Dependency vulnerabilities | `pip-audit` in CI | T0.03 |
| Customer demand and willingness to pay | Validation experiments | Track V |
