# Master development roadmap

Phase 3, 2026-11-05. Planning only; **no task has started**. Stages follow dependencies proven in
the Phase 2 audit and the build order the owner approved: **shared engine → Standalone → Square**
(C-27), release plan C-29, foundation decisions C-33..C-42. Tasks:
[DEVELOPMENT_TASK_BREAKDOWN](DEVELOPMENT_TASK_BREAKDOWN.md). Gates:
[TESTING_AND_QUALITY_STRATEGY §5](TESTING_AND_QUALITY_STRATEGY.md#5-stage-exit-gates).

## 1. Priority scale

| Priority | Meaning | Examples |
|---|---|---|
| **P0** | Must be resolved before dependent work or launch; integrity, security or blocking foundation | Hermetic tests, CI, ledger, scoped data access, item model |
| **P1** | Required for the initial commercial offering (R1) | Waste, production, Square adapter, billing catalogue, export |
| **P2** | Important post-launch (R1.1) | Purchase orders, write-back outbox, sales API, PIN switch |
| **P3** | Future expansion | Multi-location, Advanced |
| **Deferred** | Not justified yet | Native POS build |

Priority reflects customer value, revenue impact, operational importance, technical risk, data
integrity, security, dependency count, complexity, reuse and time to commercial readiness. Every
task's reason is stated in its entry in the task breakdown.

## 2. Stages at a glance

```
Stage 0  Stabilize & foundation ─────────────── M0 Safe to build
Stage 1  Core foundation ───── M1 Tenancy & access ── M2 Ledger engine
Stage 2  Shared operations + Standalone ─── M3 Operations engine ── M4 Standalone end-to-end
                                              │  (Stage 5: T5.01 reference tenant at M3, T5.02 at M4)
                                              │  free Standalone pilots may start at M4 (ID-13)
Stage 3  Connected: Square ─────────────────── M5 Square connected (Square pilots may start)
Stage 4  Commercialization & launch ────────── M6 Launch-ready ── G-R1 launch gate ── first paid customers
Stage 4B First post-launch increment (R1.1) ── M7
Stage 5  JuJu validation (checkpoints at M3, M4, M7) ── T5.04 owner decision after JuJu is live
Stage 6  Multi-location ───────────── M8   (gate: paying customers asking + PD-33)
Stage 7  Advanced ─────────────────── M9   (gate: ≥ 3 paying customers asking, PD-34)
Stage 8  Native POS feasibility ───── M10 go/no-go   (gate: $1,000 MRR C-26 + PD-35 evidence)
Track V  Customer validation, in parallel from now (TR-31; CUSTOMER_VALIDATION_PLAN)
```

The task index records each task's milestone ([DEVELOPMENT_TASK_BREAKDOWN](DEVELOPMENT_TASK_BREAKDOWN.md#index)).
Stages 3 and 4 can overlap: T4.01–T4.08 depend only on Stage 1 and parts of Stage 2, so billing,
export and admin can be built while the Square adapter is under way. The launch gate G-R1 needs
both.

## 3. Stages

### Stage 0 — Stabilize and foundation (P0/P1) → M0 "Safe to build"

| | |
|---|---|
| Why first | The audit found a non-hermetic suite (T1), no CI, unpinned dependencies (O5), a stored-XSS hole (S3), blocking I/O (C8), a write-back that loses deliveries (C4) and tenant-specific snack-box code (C-37). Every later "verified" depends on fixing these |
| Tasks | T0.01–T0.11 |
| Reuse | Entire existing suite and probes; Dockerfile and compose; Sentry wiring |
| Exit criteria (gate G0) | CI green on SQLite and Postgres 16 for every push; tests pass with a developer `.env` present; dependencies locked and audited (no critical CVEs); probes R3 and R7 retired and C8 fixed; Square write-back disabled with no swallowed failures; snack-box code and scopes gone from InventoryOS; staging deployed from `main` (deferred by the owner until before T3.11, C-49) |

### Stage 1 — Core foundation (P0/P1) → M1, M2

| | |
|---|---|
| Why | The blueprint's integrity rules (DR-01..09, 19..24, 27, 35) and access model must exist before any operational module, or each module would be built on a balance-overwrite model and then rewritten (C-35) |
| Milestone M1 "Tenancy & access" | T1.01–T1.12: timestamptz + constraints, scoped data access (C-33), isolation suite, organization/location model, memberships, permissions, sessions + CSRF, verification + reset, throttling, invitations, MFA, audit events |
| Milestone M2 "Ledger engine" | T1.13–T1.25: item model split (C-38), item UI, units and conversions, ledger + posting service, opening-balance migration, writers routed through the ledger, exceptions, jobs table + scheduled evaluation for every tenant, nightly verification, adjustments + history, count sessions, cost basis, core dashboard |
| Exit criteria (gates G1, G2) | Isolation suite passes for every route and file; permission matrix tests pass; AC-INV-01..11 pass on Postgres; no code path writes stock outside the posting service (static test); probes R1, R2, R4, R5, R6, R8, PA1, PA3 and PA5 replaced by passing tests of the corrected behaviour |

### Stage 2 — Shared operations + Standalone (P0/P1) → M3, M4

| | |
|---|---|
| Why | Juice bars and cafés need recipes, production, waste, batches and receiving every day; Standalone is half the launch offering and has no external dependency (C-27) |
| Milestone M3 "Operations engine" | T2.01–T2.11, T2.19, T2.21: owned catalog, versioned recipes, costing, sales model, consumption engine, batch allocation, waste, expiry, production, suppliers, receiving on the ledger, settings, employee home |
| Milestone M4 "Standalone end-to-end" | T2.12–T2.18, T2.20, T2.22: daily sales, file import, usage records, reorder, reports, weekly summary, onboarding, no-POS end-to-end suite |
| Validation | T5.01 JuJu reference tenant at M3; T5.02 scripted JuJu days at M4; free Standalone pilots may start at M4 (ID-13, after C-40) |
| Exit criteria (gates G3, G4) | AC-RCP, AC-PRD, AC-WST, AC-BAT, AC-RCV, AC-REC, AC-ROR, AC-ANL R1 criteria and AC-SAL-01..05, -07, -08 pass; AC-SAAS-10 observed with a non-founder |

### Stage 3 — Connected: Square (P1) → M5

| | |
|---|---|
| Why | Complete is the other half of the launch offering; Square is first because OAuth, webhook verification and the client already exist and are tested (integration audit), and the owner chose it |
| Tasks | T3.01–T3.12 |
| Exit criteria (gate G5) | AC-POS-01..12 pass on the sandbox; AC-SAL-06 (mode switch) passes; Standalone suite still green (Connected must not undermine Standalone) |

### Stage 4 — Commercialization and launch readiness (P1) → M6, G-R1

| | |
|---|---|
| Why | Paying customers need the approved plan catalogue (C-41), lifecycle, export, deletion, support tooling, production environment, monitoring, legal pages, rebrand, accessibility and load evidence (NFR §8), and the contract migration that removes transition structures (DM-16) |
| Tasks | T4.01–T4.17 |
| Before any paid customer | All of Stage 4 (gate G-R1 = NFR §8 production readiness standard, every gate) |
| Exit criteria | NFR §8 gates 1–12 evidenced; AC-SAAS-01..12 pass; restore drill done; onboarding observed for both paths (AC-SAAS-09, -10) |

### Stage 4B — First post-launch increment, R1.1 (P2) → M7

T4B.01–T4B.13: purchase orders, outbox + optional Square write-back, generic sales API, specialist
roles + PIN switch, blind/batch counts and pre-approval, refund classification, remove/substitute
modifiers + bulk recipe import + starter templates, hour-level shelf life, theoretical vs actual +
supplier trends + recipe cost history, what-to-make, credit memos, offline queueing, Postgres RLS
as a second guard. Order inside the stage follows pilot feedback.

### Stage 5 — JuJu operational validation (P2, checkpoints)

T5.01 (at M3), T5.02 (at M4), T5.03 (continuous), T5.04 (after M7 and JuJu's own go-live).
JuJu stays its own business and product (C-13, C-42). Plan: [JUJU_VALIDATION_PLAN](JUJU_VALIDATION_PLAN.md).

### Stage 6 — Multi-location (P3) → M8

T6.01–T6.05. **Gate:** at least one paying customer with a second location asking, and PD-33
decided. Foundations already in R1: location on every location-scoped record (SCL-07),
location-scoped memberships (STF-FR-10 model), per-location balances (C-38).

### Stage 7 — Advanced (P3) → M9

T7.01–T7.07. **Gate:** ≥ 3 paying customers asking for the same capability (PD-34
recommendation); ≥ 60 days of ledger history for forecasting.

### Stage 8 — Native POS feasibility (Deferred) → M10

T8.01–T8.04: evaluation only, ending in a build / partner / integrate / defer recommendation.
**Gate to even start:** $1,000 MRR (C-26) and PD-35 evidence. No implementation without the owner's
explicit approval.

## 4. Critical path

```
T0.01 → T0.02 → T0.05 → T1.01 → T1.02 → T1.04 → T1.13 → T1.15 → T1.16 → T1.17 → T1.18 → T2.05
T1.02 → T1.05 → T1.06 → T2.01 → T2.02 → T2.05
T2.05 → T2.12 → T2.13 → T2.20 → T2.22                          (M4 Standalone end-to-end)
T2.05 → T3.04 → T3.05 → T3.10 → T3.11 → T3.12 → T4.16          (M5 Square connected, then contract migration)
T1.06 → T4.01 → T4.02 → T4.03 → T4.04                          (billing, in parallel with Stage 3)
T0.09 → T4.09 → T4.10                                           (production environment; needs the owner's accounts)
every P1 task → T4.17 launch gate review → G-R1
```

## 5. What is deliberately deferred

| Item | Why | Revisit when |
|---|---|---|
| Native POS build | No validation; $1,000 gate (C-26); PD-35 | Stage 8 |
| Second POS adapter (Toast, Clover…) | Square first (owner); demand unmeasured | Adapter demand recorded at signup ≥ 3 paying requests |
| Square write-back | Unsafe without outbox (C4, C-34) | R1.1 (T4B.02) |
| Purchase orders | Receiving works without them; PO reference is nullable from R1 | R1.1 (T4B.01) |
| Markdowns, packaging-photo date reading, health score in R1 UI | Ahead of plan (PD-26) | R2, or if the owner asks |
| Postgres RLS | Scoped layer first (C-33) | R1.1 (T4B.13) |
| Partitioning of movements | Not needed at launch volume | 100+ organizations |
| Message broker / microservices | One worker suffices (ID-06) | Job latency threshold exceeded |

## 6. Milestone exit summary

| Milestone | Proves | Who can use the product |
|---|---|---|
| M0 | The codebase can be changed safely | Nobody new |
| M2 | Stock is explainable, isolated, permissioned | Internal demo |
| M4 | Standalone runs a real operating week | Free Standalone pilots; JuJu scripted days |
| M5 | Square sales post consumption reliably | Free Square pilots |
| G-R1 | Production readiness standard met | Paying customers (Complete, Standalone) |
| M7 | R1.1 increment | Paying customers; JuJu tenant decision possible |
