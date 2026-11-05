# Execution log

One entry per task, newest first. Every entry: implemented · verified · files changed · open
issues · decisions · next task.

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
