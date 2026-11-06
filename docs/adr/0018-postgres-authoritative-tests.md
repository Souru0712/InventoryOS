# ADR-0018: Test database policy

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-18 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T0.02

## Context

6 Postgres-only tests today; locking semantics differ

## Decision

Postgres CI is authoritative for integrity, concurrency, isolation and migration tests; SQLite for fast local unit runs

## Alternatives considered

SQLite only

## Consequences

In effect since T0.02: the PostgreSQL 16 job fails if any test is skipped there, so the Postgres-only tests always run in CI.
