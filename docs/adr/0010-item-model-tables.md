# ADR-0010: Item model tables (C-38)

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-10 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T1.13

## Context

Multi-location without duplicate definitions

## Decision

`inventory_items` (org) + `item_locations` (per-location settings) + `stock_balances` (projection); `stock_items` kept as a compatibility layer until DM-16

## Alternatives considered

Keep `stock_items` per location

## Consequences

Partly built: the tables, the backfill and the mirror that keeps them in step with `stock_items` arrive in T1.13-a (ADR-0036); readers move in T1.13-b; `stock_balances` comes with the ledger (T1.16). `stock_items` stays until the contract migration DM-16 (T4.16).
