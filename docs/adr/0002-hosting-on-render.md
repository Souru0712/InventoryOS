# ADR-0002: Hosting

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-02 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T0.09, T4.09

## Context

Owner: no budget ceiling, choose for compatibility, plan fit and automation; Docker image and two-process model already exist

## Decision

**Render**: web service, background worker, managed Postgres 16 with point-in-time recovery, preview environments, auto-deploy from GitHub — to be confirmed against current plans at T0.09

## Alternatives considered

Fly.io (similar, more hands-on); AWS (ECS/RDS — most flexible, most operations work)

## Consequences

Not built yet. T0.09 confirms Render's current plans and creates staging once the owner has the Render account (OD-03). The image and the web and scheduler process model already exist.
