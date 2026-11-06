# ADR-0006: Background jobs

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-06 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T1.20

## Context

One worker suffices at launch scale; fewer moving parts

## Decision

Postgres `jobs` table drained by the existing leader-locked worker; revisit a broker only if p95 job wait > 5 min or > 10k jobs/day

## Alternatives considered

Redis + RQ/Celery now

## Consequences

Not built yet (T1.20). Until then extraction runs inside the upload request in a worker thread (T0.07).
