# ADR-0012: Object storage

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-12 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T0.09

## Context

App already supports S3-compatible storage; S3 matches the owner's existing stack

## Decision

AWS S3

## Alternatives considered

Cloudflare R2 (no egress fees)

## Consequences

Not deployed yet (T0.09). The app already stores invoice photos in S3 when configured (`app/storage.py`).
