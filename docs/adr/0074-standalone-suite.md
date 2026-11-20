# ADR-0074: The Standalone end-to-end suite and the adapter boundary

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.22: AC-SAL-01, AC-SAL-07, MNT-08, DR-29)
- **Tasks:** T2.22; the M4 gate review reads its result

## Context

Standalone is a whole product, not Connected with the POS missing (brief §3.2). Nothing proved
that every R1 workflow runs with no POS configured, and Square-specific code sat outside the
adapter (`app/transform/square_normalize.py`).

## Decision

1. **One end-to-end test drives the pages as a browser would** (`tests/test_standalone.py`):
   sign up, step 0 "no POS", a location, items from a CSV, a supplier, a delivery received, a
   production recipe and run, the default "made ahead", a product with a recipe, a day's sales by
   hand, waste, a staff meal, the opening count, the reports and their CSV, reorder, valuation,
   the weekly summary and the floor screen, then a paid subscription through the Stripe event. It
   ends by reconciling every item's ledger balance with what the workflows did, and asserts no
   POS connection exists. Every POS setting is cleared and constructing the Square client fails
   the test.
2. **Provider code lives in `app/providers/`.** The normaliser moved to
   `app/providers/square/normalize.py`. A static test allows imports of `app.providers` only in
   the three seams where the product meets a POS (`app/orchestration/sync.py`, the connect routes
   in `app/web/dashboard.py`, `app/web/webhooks.py`), allows no `*square*` module elsewhere, and
   keeps the rules, the ledger, scoped access and the domain services from importing those seams.
3. **A CI job "Standalone (no POS)"** runs the suite with every Square variable empty. Making it a
   required check is a change to the `main` ruleset, which is the owner's.

## Alternatives considered

- **Service-level calls instead of the pages:** quicker, but would not show that the screens work
  with no POS.
- **Forbidding the word "square" outside the adapter:** labels, comments and the step-0 answer
  name Square legitimately; imports are what couple code to the provider.

## Consequences

- The suite is part of the normal run too, so a change that makes any R1 workflow need a POS
  fails locally as well as in CI.
