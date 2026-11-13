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

Built in T1.20 (ADR-0044, migration `cb043c5cd27a`): the scheduler process drains the queue, and
invoice reading, alert digests, weekly reports and every business's stock and expiry checks
run as jobs. Before it, extraction ran inside the upload request in a worker thread (T0.07).
