# Execution log

One entry per task, newest first. Every entry: implemented · verified · files changed · open
issues · decisions · next task.

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
