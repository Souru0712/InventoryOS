# ADR-0048: Cost basis and valuation

- **Status:** Proposed, 2026-11-13 (in the T1.24 pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T1.24 under PD-08 / C-44; ANL-FR-01, -02, INV-FR-07, -24, DR-19, AC-ANL-01, -02)
- **Tasks:** T1.24; T2.03 (recipe costing), T2.07 (waste cost), T1.25 (dashboard) read the same basis

## Context

PD-08 decided the cost basis:
- a movement keeps the current cost at the moment it posts;
- valuation uses each item's latest cost;
- FIFO actual cost may come in R2.

Movements already snapshot a cost (ADR-0040). But the current cost was a bare column that any
code could overwrite, with no history. Valuation multiplied negative stock into negative money,
and nothing could value stock as of a past date.

## Decision

1. **`item_cost_history`** (migration `2832ec7b91b7`):
   - **Contents:** one entry per change of an item's current cost at a location: cost,
     source (`opening`, `receipt`, `override`, `migration`), which receipt, who, and when it
     took effect.
   - **Append-only:** `APPEND_ONLY` in the scoped layer, so `update_scoped` refuses it.
   - **Backfill:** each stock row with a cost gets one `migration` entry, effective at the
     migration.
2. **One writer:** `app/costs.py`.
   - `set_cost()` appends an entry and mirrors it on the stock row's `cost_per_base_cents`,
     which screens and the posting service read. Nothing is written when the cost is unchanged.
     A test fails on any other assignment of that column, on `StockItem(cost_per_base_cents=…)`,
     or on constructing a history row elsewhere.
   - **Receipts** set the cost they paid (source = the document). **New items** and **stocking
     elsewhere** set the opening cost before the opening balance posts, so the opening movement
     carries it. The **demo seed** does the same.
   - **Override:** `override_cost()` lets `receiving.edit_costs` set it by hand until the next
     receipt. It is audited as `item.cost_changed`.
3. **Past figures never move** (AC-ANL-01). Every historical figure reads the movement's
   snapshot or the source record's own stored value: waste-like value, count variance cents,
   adjustment value, history values. Changing today's cost changes only the current valuation.
4. **Valuation** (AC-ANL-02, INV-FR-24) is Σ max(recorded, 0) × current cost, per item and in
   total.
   - **Negatives:** stock below zero counts as nothing; it is a count to do. `valuation_cents`
     now clamps at zero everywhere.
   - **No cost:** an item held without a cost is listed separately, never priced at zero.
   - **Basis:** the figure states its basis, "latest receipt cost, unless overridden", on the
     dashboard and on `/app/valuation` (`reports.financial`).
5. **As of a date** (ANL-FR-02): for each item at each location, what was held at the end of
   that business day (`business_day_end()`, ADR-0027) × the cost in effect then. That cost comes
   from the history, or, before the history began, from the last movement's snapshot.

## Alternatives considered

- **FIFO / batch actual cost:** PD-08 defers it to R2.
- **Value from movement snapshots alone (average of what was paid):** it contradicts PD-08's
  "valuation at latest cost".
- **Keep negative stock as negative value:** it would understate what is held whenever sales ran
  ahead of a count.

## Consequences

- Recipe costing (T2.03) and waste cost (T2.07) read the current cost through the stock row and
  snapshot it at posting.
- A future "cost basis" setting (FIFO) would change `value_stock` and the snapshot rule together.
- Built in T1.24 with migration `2832ec7b91b7`.
