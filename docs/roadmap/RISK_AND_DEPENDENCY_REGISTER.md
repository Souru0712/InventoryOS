# Risk and dependency register

Phase 3, 2026-11-05. Planning only. Technical risks carry over from the Phase 2
[technical risk register](../audit/TECHNICAL_RISK_REGISTER.md) (TR-01..31) with the roadmap task that
mitigates each; product and delivery risks are new (RR-xx). Responsible roles: **Owner** (business
decisions, accounts, content), **Implementation agent** (Claude Code sessions doing the tasks),
**Founder-operator** (the owner running production), **External** (Square, Stripe, host, email
provider).

## 1. Technical risks → roadmap mitigation

| Risk | Summary | Severity | Mitigating tasks | Gate | Responsible |
|---|---|---|---|---|---|
| TR-01 | Code only on one laptop | — | **Resolved 2026-11-05** (private GitHub) | — | Owner |
| TR-02, TR-03 | No ledger; consumption wrong for juice bars | Critical | T1.16–T1.18, T2.05 | G2, G3 | Implementation agent |
| TR-04 | Standalone impossible today | Critical | T1.04, T2.12–T2.14, T2.20, T2.22 | G4 | Implementation agent |
| TR-05 | Isolation by convention | High | T1.02, T1.03, T4B.13 | G1 | Implementation agent |
| TR-06 | Square owns catalog and counts | High | T2.01, T1.18, T3.03 | G2, G5 | Implementation agent |
| TR-07 | Invoice double apply | High | T1.18, T2.11 | G2 | Implementation agent |
| TR-08 | Write-back failures erase deliveries | High | T0.08 now; T4B.02 outbox | G0 | Implementation agent |
| TR-09, TR-10, TR-17 | Auth gaps; stored XSS; sessions/CSRF/headers | High | T0.06, T1.07–T1.11 | G0, G1 | Implementation agent |
| TR-11 | Nothing external ever run live | High | T3.11, T4.04, T4.09, T4.15 | G5, G-R1 | Owner + agent |
| TR-12 | No backups | High | T0.09, T4.09 | G-R1 | Founder-operator |
| TR-13 | Approved pricing cannot be sold | High | T4.01, T4.02 | G-R1 | Implementation agent |
| TR-14 | No CI, non-hermetic tests, unpinned deps | High | T0.01–T0.04 | G0 | Implementation agent |
| TR-15 | Recipes cannot express juice-bar reality | High | T1.15, T2.02 | G3 | Implementation agent |
| TR-16 | R1 large for a solo founder | High | Stage gates; C-29 boundaries; RR-03 below | every gate | Owner |
| TR-18, TR-19 | Over-scoped OAuth; merchant on two tenants | Medium | T0.05, T3.02 | G0, G5 | Implementation agent |
| TR-20 | AI cost abuse | Medium | T1.08, T2.11 | G-R1 | Implementation agent |
| TR-21 | Blocking I/O in async routes | Medium | T0.07, T1.20 | G0 | Implementation agent |
| TR-22 | Daily-aggregate sales | Medium | T2.04, T3.05 | G5 | Implementation agent |
| TR-23 | Naive timestamps, no constraints, hard deletes | Medium | T1.01, T2.06 | G1, G3 | Implementation agent |
| TR-24 | Snack-box module inside the product | Medium | T0.05 | G0 | Owner (repo) + agent |
| TR-25 | 60-day order re-read | Medium | T3.05, T3.12 | G5 | Implementation agent |
| TR-26 | Never deployed | Medium | T0.09, T4.09 | G0, G-R1 | Owner + agent |
| TR-27 | No structured logs or integrity metrics | Medium | T0.10, T4.10 | G-R1 | Implementation agent |
| TR-28 | Extraction accuracy unknown | Medium | T4.15 | G-R1 | Owner (invoices) + agent |
| TR-29 | Milligram unit mapping | Low | T3.03 | G5 | Implementation agent |
| TR-30 | Pressure to merge with JuJu | Low | C-42; T5.01–T5.04 | — | Owner |
| TR-31 | No pilot businesses | High | Track V ([CUSTOMER_VALIDATION_PLAN](CUSTOMER_VALIDATION_PLAN.md)) | before M4 pilots | Owner |

## 2. Product and delivery risks

| ID | Risk | Likelihood | Impact | Severity | Mitigation | Responsible |
|---|---|---|---|---|---|---|
| RR-01 | Pricing hypotheses wrong ($99 too high for Standalone, per-location unacceptable) | Medium | High | High | Validation experiments before G-R1; prices are configuration (T4.01) so changing them costs no code | Owner |
| RR-02 | Standalone demand smaller than assumed (A-01) | Medium | Medium | Medium | Measure POS situation at sign-up and in interviews; Standalone also serves "unsupported POS" via file import | Owner |
| RR-03 | Scope creep into R1 (POs, write-back, native POS, multi-location) | High | High | High | C-29 boundaries; new requirements enter only through the governance process ([README §4](README.md#4-governance)); deferred list in the master roadmap | Owner + agent |
| RR-04 | Long time to first revenue with a solo founder | High | High | High | Free Standalone pilots at M4 (ID-13); stages overlap where dependencies allow (Stage 4 billing alongside Stage 3) | Owner |
| RR-05 | JuJu-specific needs leak into the product | Medium | Medium | Medium | JuJu validation classifies findings; C-30 forbids per-customer code | Agent + Owner |
| RR-06 | Rewrite temptation on the engine spreads to reusable parts | Medium | Medium | Medium | Reuse matrix is binding; replacing a reusable component needs an ADR with evidence | Agent |
| RR-07 | Square app review or API changes delay Connected | Medium | Medium | Medium | Product-only scopes; sandbox early (T3.11); Standalone launch path does not depend on it | External + agent |
| RR-08 | Founder operational load after launch (support, incidents) | Medium | High | High | Founder console, runbooks, alerts (T4.07, T4.10); small pilot cohort first | Founder-operator |
| RR-09 | Regulatory or tax surprises on subscriptions | Low | Medium | Low | Stripe Tax + accountant (PD-22) | Owner |

## 3. Dependencies

### 3.1 Owner dependencies (block named tasks)

| ID | Dependency | Blocks | Status |
|---|---|---|---|
| OD-01 | Approve implementation decisions ID-01..ID-23 ([IMPLEMENTATION_DECISIONS](IMPLEMENTATION_DECISIONS.md)) | T0.09 (ID-02), T0.11, T1.11 (ID-23), T2.13 (ID-23), T2.21 (ID-07), T4.03 (ID-14), Stage 1 tasks touching ID-09/ID-10 | Done 2026-11-05 (C-43, C-44) |
| OD-02 | Create the snack-box project repository | T0.05, and through it T1.01 and all of Stage 1 (critical path) | Open. 2026-11-06: the owner shared a local `Snack-box` folder, a separate CSV command-line tool that is not a git repository; the private repository for the extracted module (ADR-0016) is still to be created |
| OD-03 | Host, object storage and Sentry accounts | T0.09 | Open |
| OD-04 | Enable branch protection (required CI check) on GitHub | T0.02 done-definition | Open — checked 2026-11-05: no classic rule and no ruleset exist; GitHub says rulesets are not enforced on this private repository without a paid plan. Plan upgrade declined by the owner (2026-11-05). A ruleset "Protect main" (required checks Tests (SQLite) and Tests (PostgreSQL 16), block force pushes, restrict deletions) was submitted through the settings page and not saved. **Done 2026-11-05:** the owner made the repository public; ruleset "Protect main" is Active on the default branch with required checks Tests (SQLite) and Tests (PostgreSQL 16), branch up to date, no force pushes, no deletion, empty bypass list |
| OD-05 | Square developer account with sandbox seller (OA-2) | T3.11 | Open |
| OD-06 | Stripe account in test mode | T4.04 | Open |
| OD-07 | Email provider account and domain DNS | T4.09 | Open |
| OD-08 | Privacy policy, terms, refund policy content | T4.12 | Open |
| OD-09 | Real invoices for the extraction trial (OA-4) | T4.15 | Partly met 2026-11-05: front pages of four suppliers' invoices (Central Boeki, USA Seafood, JFC, Hadson) shared in a session and kept out of this public repository; their pack wording fixed receiving bugs (execution log). The owner offered an invoice longer than one page (2026-11-05). The trial still needs complete multi-page invoices, kept privately |
| OD-10 | Customer validation time and decisions O-1..O-7 in the validation plan | Track V, pilots | Open |
| OD-11 | JuJu values not in JuJu's files (press yields, opening stock, costs) | T5.01 | Open |

### 3.2 External dependencies

| Dependency | Used by | Risk handling |
|---|---|---|
| Square API, OAuth, webhooks | Stage 3 | Adapter boundary; recorded-payload tests; reconciliation |
| Stripe Billing | Stage 4 | Verified webhooks; event ordering; test mode run |
| Host + managed Postgres | T0.09, T4.09 | Dockerized app; standard Postgres; PITR |
| Email provider | T1.08, T2.18, T4.09 | SMTP interface already abstracted |
| Anthropic API (invoice extraction) | T2.11, T4.15 | Manual entry always available; per-tenant cap |

### 3.3 Critical internal dependencies

```
T0.01 → T0.02 → (everything)
T0.05 → T1.02 → T1.05 → T1.06 → most Stage 1–4 UI tasks
T1.13 → T1.15 → T1.16 → T1.17 → T1.18 → all stock-changing modules
T2.01 → T2.02 → T2.04 → T2.05 → Standalone capture and the Square adapter
T1.19 → T1.20 → scheduled evaluation, extraction jobs, webhook processing
T4.01 → T4.02 → T4.03 → T4.04 → G-R1
```
