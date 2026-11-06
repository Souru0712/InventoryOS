# ADR-0003: CI

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-03 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T0.02

## Context

Repository is on GitHub; `TEST_DATABASE_URL` already supported

## Decision

GitHub Actions with SQLite and Postgres 16 jobs, required on `main`

## Alternatives considered

Other CI services

## Consequences

Implemented in T0.02. `main` is protected by the ruleset "Protect main" with five required checks (Tests (SQLite), Tests (PostgreSQL 16), Lint and types, Dependency audit, Docker image), branches up to date, no force pushes, no deletion. The repository was made public so the ruleset is enforced on GitHub's free plan.
