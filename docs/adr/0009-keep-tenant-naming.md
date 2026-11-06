# ADR-0009: Physical naming

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-09 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** Stage 1

## Context

Renaming touches every table for no customer value

## Decision

Keep `tenants`/`tenant_id` in code and schema; "Organization" is the domain and UI term

## Alternatives considered

Rename everything to `organizations`

## Consequences

In effect: code and schema keep `tenants` / `tenant_id`; screens and product documents say Organization.
