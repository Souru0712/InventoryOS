# ADR-0005: Dependency locking

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-05 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T0.03

## Context

Unpinned ranges today (O5)

## Decision

uv lockfile; Docker installs from it; Dependabot weekly

## Alternatives considered

pip-tools

## Consequences

Implemented in T0.03: `uv.lock`; CI installs with `uv sync --frozen`; the image installs the exported lock with hashes and a CI job checks its versions against the lock; pip-audit audits the lock. Dependabot updates the lock only (`versioning-strategy: lockfile-only`), because its default raised minimums in `pyproject.toml` and proposed downgrades.
