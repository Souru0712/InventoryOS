# ADR-0011: Email provider

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-11 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T4.09

## Context

SMTP interface exists; free of lock-in; owner criteria favour fit — either works

## Decision

Resend (PD-24)

## Alternatives considered

Postmark (strong deliverability focus); SES

## Consequences

Not built yet (T4.09); email goes through the existing SMTP interface.
