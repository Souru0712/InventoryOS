# ADR-0004: Coding standards

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-04 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T0.04

## Context

No linter or type config exists; many sessions will edit the code

## Decision

ruff (lint + format) and mypy (strict on new modules, gradual elsewhere)

## Alternatives considered

flake8 + black; no type checking

## Consequences

Implemented in T0.04: ruff with line length 100 and bandit's security rules; mypy strict for `app/transform/` and the future `app/access/` and `app/ledger/`, gradual elsewhere, snack-box modules skipped until T0.05. "Lint and types" is a required check. Formatting-only commits are listed in `.git-blame-ignore-revs`.
