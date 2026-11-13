# InventoryOS — Phase 3 roadmap and implementation handoff

Date 2026-11-05. **Planning only**: no application code, schema, configuration or dependency was
changed, no migration was executed, nothing was deployed, and **no task has started**. This folder
is the implementation source of truth for building InventoryOS on this repository's codebase.

## 1. Overview

InventoryOS is built on the existing Python/FastAPI codebase (C-25), keeping its shell and
replacing its inventory core (C-35), in the order **shared engine → Standalone → Square** (C-27).
The first commercial release (R1) sells **Complete** (has a POS, Square first) and **Standalone**
(no POS) as a single-location product; multi-location and Advanced follow by evidence; a native
POS is an evaluation milestone only. Stages 0–8, milestones M0–M10, gates G0–G5 and G-R1:
[MASTER_DEVELOPMENT_ROADMAP](MASTER_DEVELOPMENT_ROADMAP.md).

## 2. Source documents

| Phase | Location | Status |
|---|---|---|
| 1 — Product blueprint | [`docs/product/`](../product/README.md) — 14 documents | All present; decisions C-01..C-42 |
| 2 — Technical audit | [`docs/audit/`](../audit/README.md) — 13 documents + baseline audit `00`–`12` + probes | All present |
| 3 — Roadmap | this folder — 15 documents | This phase |

Consistency check done for this phase: Phase 2 findings and Phase 1 decisions agree; the
contradictions recorded in PRODUCT_DECISIONS §5 are resolved; one clarification was made in
Phase 2 (C-34 wording) with no requirement change.

## 3. Documents and reading order

| # | Document | Purpose |
|---|---|---|
| 1 | This README | Overview, governance, handoff |
| 2 | [IMPLEMENTATION_DECISIONS](IMPLEMENTATION_DECISIONS.md) | ID-01..ID-23, approved 2026-11-05 (C-43); open questions |
| 3 | [MASTER_DEVELOPMENT_ROADMAP](MASTER_DEVELOPMENT_ROADMAP.md) | Stages, milestones, priorities, exit criteria |
| 4 | [DEVELOPMENT_TASK_BREAKDOWN](DEVELOPMENT_TASK_BREAKDOWN.md) | 87 detailed tasks (Stages 0–4) + outline tasks (4B–8); **status index** |
| 5 | [GAP_ANALYSIS](GAP_ANALYSIS.md) | Requirement groups → class, impact, action, stage |
| 6 | [TARGET_ARCHITECTURE](TARGET_ARCHITECTURE.md) | Components, data flows, integration architecture, deployment |
| 7 | [TARGET_DOMAIN_AND_DATA_ARCHITECTURE](TARGET_DOMAIN_AND_DATA_ARCHITECTURE.md) | Tables, ledger strategy, transaction boundaries, retention |
| 8 | [OPERATING_MODE_IMPLEMENTATION_PLAN](OPERATING_MODE_IMPLEMENTATION_PLAN.md) | Standalone, Complete, native POS on one engine |
| 9 | [SAAS_AND_PRICING_IMPLEMENTATION_PLAN](SAAS_AND_PRICING_IMPLEMENTATION_PLAN.md) | Plans, entitlements, lifecycle, billing boundary |
| 10 | [DATA_MIGRATION_ROADMAP](DATA_MIGRATION_ROADMAP.md) | DM-1..DM-16, invariants, rollback, cutover |
| 11 | [TESTING_AND_QUALITY_STRATEGY](TESTING_AND_QUALITY_STRATEGY.md) | Test architecture, gates with pass conditions |
| 12 | [SECURITY_AND_RELEASE_CHECKLIST](SECURITY_AND_RELEASE_CHECKLIST.md) | Security controls, launch gate, per-release checklist |
| 13 | [RISK_AND_DEPENDENCY_REGISTER](RISK_AND_DEPENDENCY_REGISTER.md) | Risks → tasks; owner and external dependencies |
| 14 | [CUSTOMER_VALIDATION_PLAN](CUSTOMER_VALIDATION_PLAN.md) | Hypotheses, interviews, experiments, evidence log (Track V) |
| 15 | [JUJU_VALIDATION_PLAN](JUJU_VALIDATION_PLAN.md) | JuJu reference tenant and parity scenarios (Stage 5) |
| 16 | [GATE_REVIEWS](GATE_REVIEWS.md) | Evidence for each stage gate, and the owner's sign-off |

## 4. Governance

### 4.1 Sources of truth

| Question | Authoritative document |
|---|---|
| What the product must do; decisions | `docs/product/` (PRODUCT_DECISIONS wins in a conflict) |
| What exists in code today | `docs/audit/` (evidence, dated) |
| What to build next, in which order, and its status | DEVELOPMENT_TASK_BREAKDOWN index |
| Technical decisions | IMPLEMENTATION_DECISIONS until approved → [ADRs](../adr/README.md) (0001–0023 for ID-01..ID-23) + C-entries |
| What happened | `docs/EXECUTION-LOG.md` |

### 4.2 Task statuses

**Not started** → **Ready** (dependencies Completed, decisions approved, acceptance concrete) →
**In progress** → **In review** (CI green, acceptance demonstrated, awaiting owner review if
required) → **Completed**. Also **Blocked** (state the blocker) and **Deferred** (state why and
the revisit trigger). Only the index table in DEVELOPMENT_TASK_BREAKDOWN holds status. No task was
marked Completed in this phase.

### 4.3 Recording work

- **Completing a task:** update its status in the index; add an `EXECUTION-LOG.md` entry
  (implemented · verified · files changed · open issues · decisions · next task); retire any audit
  probe the task fixed.
- **Decisions:** product decisions → PRODUCT_DECISIONS as `C-nn` with the owner's words;
  technical decisions → ADR in `docs/adr/` referencing the ID.
- **New requirements:** proposed in PRODUCT_DECISIONS §3 as a `PD-nn` with context and
  recommendation; enter the spec and acceptance criteria only after owner approval; then a task.
- **Scope changes:** never silent. A task that discovers it needs more than its acceptance criteria
  stops, records the finding, and proposes a new or changed task for approval.
- **Architecture changes:** ADR with alternatives and evidence; replacing a component marked
  reusable in the [reuse matrix](../audit/REUSE_ADAPT_REBUILD_MATRIX.md) needs evidence and owner
  approval.
- **Implementation discoveries:** recorded in the execution log; if they change a plan, update the
  affected roadmap document in the same change.
- **Customer feedback:** goes to the evidence log ([validation plan §8](CUSTOMER_VALIDATION_PLAN.md));
  it changes priorities only through a proposed decision the owner approves.

### 4.4 The development contract

The master roadmap, the task breakdown, the data migration roadmap and `scripts/check_docs.py`
together form a maintained contract, not planning notes to be rewritten casually.

1. **The master roadmap is authoritative** for stages, milestones (M0–M10) and gates; the task
   index mirrors it (Milestone column) and must not drift.
2. **Tasks own distinct responsibilities.** Example: T4.16 (contract migration) owns DM-16; T4.17
   (launch gate review) only verifies that all required work is complete.
3. **Migration ownership is explicit.** Every DM step has an owning task that names it, and the
   task dependencies enforce the step order.
4. **Run `python scripts/check_docs.py` before committing any change under `docs/`** (it also runs
   in the test suite as `tests/test_docs_contract.py`). Passing it proves references and
   sequencing constraints only — it does not prove that the implementation is correct or safe;
   tests, reviews and gates do that.
5. **Blockers on the critical path are resolved deliberately.** T0.05 waits for the owner's
   snack-box repository and T1.01 depends on it; it is not bypassed without a documented and
   approved alternative.

## 5. Claude Code implementation handoff

1. **Read first:** repository-root `CLAUDE.md`, then this README, then the task's entry in
   DEVELOPMENT_TASK_BREAKDOWN.
2. **First task:** **T0.01 Hermetic test configuration** (Ready). In parallel-safe order after it:
   T0.06, T0.07, T0.08, T0.10, T0.11 (Ready); then T0.02 → T0.03/T0.04. T0.05 waits for the owner's
   snack-box repository and is on the critical path (§4.4); T0.09 is deferred by the owner until a task needs a public URL, first T3.11 (C-49).
3. **Before changing a domain, read:** its module in PRODUCT_SPECIFICATION, its workflows in
   BUSINESS_WORKFLOWS, DOMAIN_MODEL (entities + DR rules), the matching acceptance criteria, and
   TARGET_DOMAIN_AND_DATA_ARCHITECTURE.
4. **Boundaries to respect:** only the ledger posting service writes stock (DR-01); all queries go
   through the scoped data-access layer (C-33); provider-specific code only in `app/providers/`
   (DR-29); pure rules in `transform/`; no price in domain code (DR-36); no per-customer code (C-30);
   JuJu and the snack-box project are separate repositories.
5. **Dependencies:** a task is started only when every task in its "Depends on" is Completed.
6. **Tests:** full suite locally, then CI on SQLite and Postgres must be green; add the task's tests;
   integrity, isolation and migration tests are authoritative on Postgres (ID-18).
7. **Documenting changes:** update the task status and the execution log; update any roadmap or
   product document the change affects in the same commit; ADR for technical decisions.
8. **Recording completion:** status → In review when CI is green and acceptance is demonstrated;
   → Completed after owner review where the task or gate requires it.
9. **Human approval required for:** anything marked OPEN or proposed; new dependencies (ID-23);
   destructive migrations; changes to pricing or plans; security-relevant configuration; deploying
   to production; pushing to `main` if the owner has not delegated it; any stage gate sign-off.
10. **Never change without explicit approval:** product scope or acceptance criteria; owner
    decisions (C-entries); the ledger's append-only rules; tenant-isolation mechanism; secrets or
    credentials; anything in the JuJu repository; **and never build any native POS functionality**.

## 6. Planning assumptions

| # | Assumption | Effect if wrong |
|---|---|---|
| PA-1 | Scale per A-03: 21 organizations at launch, headroom 500 | Revisit jobs (ID-06) and partitioning |
| PA-2 | A solo founder with Claude Code sessions implements the roadmap; tasks are sized for focused sessions | Re-sequence or parallelize |
| PA-3 | The roadmap is dependency-ordered, not dated (Q-1) | Add dates once a weekly time budget is known |
| PA-4 | Pending product recommendations (ID-14) are approved as written | Named tasks change |
| PA-5 | No customer data exists before M4 | Migration steps need a cutover count |
