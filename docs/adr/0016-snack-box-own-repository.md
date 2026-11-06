# ADR-0016: Snack-box extraction target (C-37)

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-16 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T0.05

## Context

Clean separation; C-30

## Decision

A new private repository owned by the owner, created before T0.05; the module is copied there unchanged

## Alternatives considered

Keep it on a branch in this repository

## Consequences

Done in T0.05 (2026-11-06). The owner created the private repository `Souru0712/Snack-box`; the module was copied there unchanged from InventoryOS commit `56d6e7c` (36 files plus verbatim excerpts of its parts in five shared files, and a README giving their source lines), then removed from InventoryOS with its 11 tables (DM-1). The owner's separate local CSV tool was not touched.
