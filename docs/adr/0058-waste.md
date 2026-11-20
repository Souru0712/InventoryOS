# ADR-0058: Waste

- **Status:** Proposed, 2026-11-19 (in the T2.07 pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T2.07: WST-FR-01..12, DR-33, AC-WST-01..08)
- **Tasks:** T2.07; T2.08 (expiry disposal posts waste), T2.09 (production loss), T2.11
  (counts and receiving beside waste in reports)

## Context

Waste is the juice bar's most visible loss and nothing recorded it (C7). WST-FR asks for phone
entry in three taps, reasons a business can rename, the actor and a cost snapshot, waste of a
sellable product expanded to its recipe, a 30-minute undo, and a log and report that never count
a loss twice (DR-33: the event says why; its ledger entries are the only quantities and costs).

## Decision

1. **Three tables** (migration `2f829ccb897e`), written only by `app/waste.py`:
   - `waste_reasons`: the twelve defaults (WST-FR-02), written the first time a business opens
     waste, plus its own. Renamed or archived, never deleted; changes are audited as settings.
   - `waste_events`: an item or a product size, the quantity as entered, the reason's name as it
     was then (so a rename never rewrites history), the actor, the time, an optional note and
     related record, the ledger transaction, and void details.
   - `waste_lines`: what the event takes, item by item, with the batch when one was chosen. A
     product's event has a line per ingredient, by the recipe in effect then.
2. **Posting:** one `waste` transaction keyed `waste:<event id>`, costed at the event's moment
   (`cost_on()`); a chosen batch is explicit, otherwise the ledger takes the soonest-expiring
   (ADR-0056). It refuses to go below zero unless the person may override (DR-08); a Manager's
   waste below zero raises `negative_balance`. An item with no cost is posted at zero, its cost
   shown as unknown, and raises `missing_cost`.
3. **Product waste** goes through the consumption resolver (`resolve_variation()`), so it
   expands exactly as a sale would, made-to-order or made-ahead; a size with no recipe in effect
   is refused, with the advice to log the ingredients.
4. **Void:** a reversal at the waste's own moment, so its period nets to nothing. The logger may
   void their own entry for 30 minutes; after that, or for someone else's, `waste.void` and a
   reason (WST-FR-07).
5. **Reading, from the ledger only:** `logged()` nets each event's movements (a voided one
   disappears), `report()` totals by reason, item, product and person with the top items by
   cost, and `found_short()` lists what counts found missing in the same period, shown apart
   (WST-FR-10). Quantities read `waste` and `count_variance` movements; nothing sums events or
   lines.
6. **Screens:** `/app/waste` (anyone with `waste.record`): choose an item or product, then
   quantity, reason, save; recent entries with Void. `/app/waste/log` (`reports.operational`):
   filters, entries, the report and found short; costs only with cost access and financial
   reports; reasons for `settings.manage`.

## Alternatives considered

- **Report from the events:** a voided or partly reversed entry, or a product expanded at a
  later recipe, would disagree with stock; DR-33 forbids it.
- **Waste of a product as one movement of the product:** sellable products hold no stock; the
  ingredients do.
- **Store the reason by id only:** a rename would rewrite last month's report.

## Consequences

- Waste is never consumption: sales and production totals read their own movement types.
- **Not in T2.07** (recorded in the execution log): a photo on an entry; choosing the person on
  a shared device (WST-FR-03, PD-14); picking a related order on the form (the fields exist).
- **Built in T2.07** with migration `2f829ccb897e`.
