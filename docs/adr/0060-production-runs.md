# ADR-0060: Production runs

- **Status:** Accepted, 2026-11-19 (owner: "Update, then merge"; PR #60, merge commit f9184b7)
- **Decision ID:** none (implements T2.09: WF-11, PRD-FR-01..08, -12, -14, PD-15, AC-PRD-01..03,
  -05..07)
- **Tasks:** T2.09; T2.11 (receiving beside production in reports)

## Context

Juice bars prep daily (C-29). A prepared item already has a recipe of its own (ADR-0051) and
items made in house are ordinary items (PRD-FR-01); the ledger allocates batches (ADR-0056).
WF-11 asks for a run in one step with inputs taken soonest-expiry first, the output as a dated
batch, cost over actual yield (unknown, never understated, when an input has none), a 30-minute
undo, and short inputs allowed with an exception (PD-15).

## Decision

1. **`production_orders`** (migration `240830e94f98`), written only by `app/production.py`:
   item made, recipe version, multiplier, expected and actual yield, variance, unit cost (empty
   when unknown), batch code, output batch, actor, time, transaction, void details. R1 runs are
   `completed` in one step (PRD-FR-08).
2. **Posting:** one `production` transaction keyed `production_order:<id>`: each input as
   `production_input` (the ledger takes it from batches soonest-expiry first) and the actual
   yield as `production_output` into a new batch (`production` source, use-by = the location's
   day + the item's shelf life, readable code such as `GJ-1105-1` in its lot code).
3. **Cost:** Σ input quantity × cost at that moment ÷ actual yield becomes the output's
   movement cost, the batch's cost and, through `set_cost(source_type="production")`, the
   item's cost; unknown leaves the cost alone and raises `missing_cost`.
4. **Short inputs** post below zero and raise `negative_balance` (PD-15).
5. **Void:** a reversal at the run's moment, so the output batch holds nothing; the recorder
   for 30 minutes, then `production.void` with a reason.
6. **Screen** `/app/production`: choose, set the multiplier, see what it takes against what is
   on hand, confirm with the actual yield, then the label (name, use-by, code).

## Alternatives considered

- **A separate production recipe table:** the item's own recipe already holds inputs and yield.
- **Cost unknown as zero:** a zero would understate every drink that uses the item.

## Consequences

- Finished goods (bottled juice) are made the same way and sold made-ahead (AC-PRD-07).
- Not in T2.09: yield correction after completion (PRD-FR-13, via WF-26); planned orders (R2).
