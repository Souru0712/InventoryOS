# ADR-0021: Task tracking

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-21 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** all

## Context

One source of truth in the repository the agent reads

## Decision

Status column in [DEVELOPMENT_TASK_BREAKDOWN](../roadmap/DEVELOPMENT_TASK_BREAKDOWN.md) index + one `EXECUTION-LOG.md` entry per task; GitHub Issues optional mirror

## Alternatives considered

GitHub Projects as source of truth

## Consequences

In effect since T0.01: status in the task index, one execution-log entry per task, owner merges each task's pull request.
