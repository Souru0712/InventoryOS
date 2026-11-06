# ADR-0023: New runtime dependencies

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-23 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T1.11, T2.13, T2.21

## Context

Small, maintained libraries; fewer security bugs than custom code

## Decision

Approve: a TOTP library (e.g. pyotp), an XLSX reader (e.g. openpyxl), HTMX (vendored); dev-only: Playwright, axe, ruff, mypy, pip-audit. Any other new dependency needs approval

## Alternatives considered

Build TOTP/XLSX in-house

## Consequences

Partly in effect: ruff and mypy are dev dependencies (T0.04); pip-audit runs from a pinned version in CI without being a project dependency (T0.03). The TOTP library, XLSX reader, HTMX, Playwright and axe arrive with their tasks. Any other new dependency still needs the owner's approval.
