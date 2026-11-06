# ADR-0001: Application architecture and code layout

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-01 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T1.02, T1.16

## Context

Audit: layering is the codebase's strongest asset; scale 21–500 tenants; solo founder

## Decision

Keep the modular monolith (web + worker) and the existing layer-first layout; add a scoped data-access package and a single ledger posting service

## Alternatives considered

Microservices; module-first package reorganization

## Consequences

Not built yet: the scoped data-access package arrives in T1.02 and the single ledger posting service in T1.16. Until then new code keeps the existing layers (`extract`, `transform`, `load`, `orchestration`, `web`).
