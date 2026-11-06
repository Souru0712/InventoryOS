# ADR-0020: Architecture decision records

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-20 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T0.11

## Context

Keeps product decisions and technical decisions apart

## Decision

`docs/adr/NNNN-title.md`, one per approved ID and per later architecture change

## Alternatives considered

Decisions only in PRODUCT_DECISIONS

## Consequences

This folder (T0.11). The docs checker fails when an approved ID has no ADR or the index below misses a file.
