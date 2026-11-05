# Testing and quality strategy

Phase 3, 2026-11-05. Planning only. Builds on the existing suite (542 passed + 6 Postgres-only
skipped on SQLite, 548/548 on Postgres 16 — [audit](../audit/INVENTORY_PROJECT_TECHNICAL_AUDIT.md#1-verification-done-this-session))
and the NFR rule that every acceptance criterion maps to a test or a documented manual check
(MNT-05).

## 1. Test architecture

| Layer | What | Where | Runs on | Speed |
|---|---|---|---|---|
| Unit (pure rules) | `transform/`: units, conversions, recipe resolution, consumption, costing, FEFO, reorder math, report math, business days | `tests/` (existing pattern, ~200 today) | SQLite job (no DB needed) | seconds |
| Integration (DB) | Services + posting service: idempotency, concurrency, reversals, constraints | `tests/` | SQLite locally; **Postgres 16 in CI (authoritative)** | minutes |
| Workflow | One test module per WF-01..28 in R1 scope, driving services end to end | `tests/workflows/` *(proposed)* | Postgres CI | minutes |
| Route / API | HTTP via `TestClient`: forms, CSRF, redirects, webhooks | `tests/` | both | seconds |
| Permission matrix | Generated: every permission key × role → allowed/denied (AC-STF-02) | `tests/test_permissions.py` *(proposed)* | both | seconds |
| Tenant isolation | Generated from the route table: every id route × operation with another tenant's id → 404/403 (AC-SAAS-01) | `tests/test_isolation.py` *(proposed)* | Postgres CI | minutes |
| Migration | Model/migration diff, single head, downgrade, upgrade over populated data + per-step invariants ([migration roadmap §5](DATA_MIGRATION_ROADMAP.md#5-validation-per-step-migration-test-invariants)) | `tests/test_migrations.py` (existing) | both | minutes |
| Contract (adapters) | Square adapter against recorded payloads; business logic only against normalized fixtures (AC-POS-12) | `tests/` (existing Square fixtures) | both | seconds |
| Static architecture | Only the posting service writes movements/balances; no unscoped queries; provider imports only in `app/providers/`; no price literals in domain code; every route declares a permission | `tests/test_architecture.py` *(proposed)* | both | seconds |
| Standalone end-to-end | All R1 workflows with no POS settings (AC-SAL-01) | CI job (T2.22) | Postgres CI | minutes |
| Browser end-to-end | Playwright (ID-08): onboarding both paths, waste at 375 px, production, receiving review, counts, daily sales, reports; axe accessibility checks | `e2e/` *(proposed)* | CI against a built app | minutes |
| Live integration runs | Square sandbox (T3.11), Stripe test mode (T4.04), real email delivery (T4.09), real invoices (T4.15) | recorded runs | staging | manual, logged |
| Load | PERF-01..07 at design headroom (T4.14) | `load/` *(proposed)* | staging-like | manual, logged |

## 2. Coverage priorities

1. **Ledger integrity** (DR-01..08): idempotency, concurrency (real concurrent transactions on
   Postgres), reversal, sign rules, projection = Σ movements.
2. **Tenant isolation and permissions**: generated suites must cover 100 % of id routes and
   permission keys; adding a route without coverage fails CI.
3. **Consumption engine**: table-driven cases including JuJu's real modifier shapes (ice scale
   factors, pumps as add modifiers, press yields).
4. **Money figures**: cost snapshots never change history (AC-ANL-01); waste counted once
   (AC-WST-08).
5. **Integration failure paths**: duplicate webhooks, missed webhooks, revoked tokens, dead
   letters.
6. **Billing lifecycle**: recorded payloads + one live test-mode run.
7. UI rendering and accessibility for R1 screens.

Coverage is measured (line + branch) for `transform/` and services; **target ≥ 90 % for
`transform/` and the posting service**, reported not gated elsewhere. Test count is never used as a
quality claim on its own.

## 3. Test data

- Fixtures built through services (never raw inserts that bypass the ledger), seeded per test.
- A **juice-bar fixture** ("Golden State", ginger-juice prep) mirrors BUSINESS_WORKFLOWS examples.
- JuJu-derived cases use only quantities present in JuJu's files (owner rule: never invent recipe
  quantities); missing values are marked as synthetic test data.
- No real secrets; hermetic configuration (T0.01).
- The existing demo seed (`app/cli.py`) is updated to go through services once the ledger exists.

## 4. Traceability and probes

- Test names or docstrings cite the AC/DR IDs they prove; a script (proposed, in CI) lists every R1
  `AC-*` ID without a referencing test or a documented manual check. The list must be empty at G-R1.
- Audit probes (`docs/audit/probes/`) assert *current defects*. Each fixing task replaces its probe
  with a test of the corrected behaviour and deletes the probe (R1–R8, PA1–PA5). Probes are never
  run in CI.

## 5. Stage exit gates

Each gate has a testable pass condition; a stage is complete only when its gate passes in CI on
Postgres (plus recorded runs where named). The [security and release checklist](SECURITY_AND_RELEASE_CHECKLIST.md)
holds the security and commercial items.

| Gate | When | Pass condition |
|---|---|---|
| **G0 Safe to build** | End of Stage 0 | CI green on SQLite and Postgres for every push; suite passes with a developer `.env` present; lockfile + `pip-audit` with no unaddressed critical CVE; probes R3 and R7 retired; zero Square write calls on receipt/count (test); no snack-box symbol in the repo (grep test); staging `/readyz` 200 |
| **G1 Tenancy & access** | M1 | Isolation suite passes for 100 % of id routes and file routes; permission matrix passes for every key × role; static test finds no unscoped query; probes PA1, PA3, PA5 and R8 retired |
| **G2 Ledger engine** | M2 | AC-INV-01..11 pass on Postgres including a real concurrent-posting test; static test: only the posting service writes stock; Σ movements = balance for every fixture; nightly verification detects an injected drift; probes R1, R2, R4, R5 and R6 retired |
| **G3 Operations engine** | M3 | AC-RCP, AC-PRD, AC-WST, AC-BAT, AC-RCV R1 criteria pass; waste counted once (AC-WST-08); batch conservation property test passes (DR-13) |
| **G4 Standalone end-to-end** | M4 | AC-SAL-01..05, -07, -08, AC-REC, AC-ROR, AC-ANL R1 criteria pass; Standalone end-to-end job green with no POS settings; AC-SAAS-10 observed with a non-founder |
| **G5 Square connected** | M5 | AC-POS-01..12 pass (sandbox run recorded, T3.11); AC-SAL-06 passes; Standalone job still green; duplicate delivery ×3 posts once; probe PA4 retired |
| **G-R1 Launch** | M6 | Every R1 AC has a passing test or documented manual check (traceability script empty); every item in the security and release checklist evidenced; NFR §8 gates 1–12 evidenced; probe PA2 retired; DM-16 applied |

### Data-integrity gate conditions (brief §10)

| Condition | Testable pass condition |
|---|---|
| Inventory movements are traceable | Every movement has transaction, source type/id, actor and timestamp (schema NOT NULL + test) |
| Stock counts reconcile correctly | AC-REC-02, AC-REC-03 pass with interleaved movements |
| Duplicate events cannot cause duplicate deductions | AC-INV-06, AC-POS-03 pass |
| Units and conversions are consistent | AC-INV-02, AC-INV-03; round-trip property tests |
| Historical records are preserved | AC-INV-05 (no update/delete path); AC-ANL-01; AC-RCP-02 |
| Refunds and voids have defined effects | AC-POS-04, AC-POS-05 |
| Waste is not double-counted | AC-WST-08 |
| Batch and expiration records remain traceable | AC-BAT-04, AC-BAT-05, AC-BAT-08 |

### Reliability gate conditions

| Condition | Testable pass condition |
|---|---|
| Error handling is defined | No `except` that swallows without logging and recording (ruff rule + review); user-facing errors actionable (UX-05) |
| Integration retries are safe | Retried job re-runs produce no duplicate movements (idempotency test) |
| Background tasks are observable | Every job type reports status; dead letters raise exceptions; founder console lists failures |
| Failed synchronization is recoverable | AC-POS-07 (missed webhook recovered ≤ 60 min) |
| Database operations preserve consistency | A business event posts all movements or none (failure-injection test, REL-02) |

## 6. CI pipeline (target)

```
push / PR ─► lint (ruff) ─► types (mypy) ─► unit (SQLite) ─► full suite + isolation + migrations (Postgres 16)
          ─► architecture static tests ─► Standalone e2e job ─► Playwright smoke ─► pip-audit
main green ─► deploy staging ─► smoke /readyz
```

## 7. Release criteria

A change merges only with CI green and the task's acceptance criteria demonstrated. A stage closes
only at its gate. Production releases after G-R1 follow the [release checklist](SECURITY_AND_RELEASE_CHECKLIST.md#3-per-release-checklist-after-g-r1).
