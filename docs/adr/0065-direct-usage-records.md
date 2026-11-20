# ADR-0065: Direct usage records

- **Status:** Accepted, 2026-11-19 (owner: "Update, then merge"; PR #60, merge commit f9184b7)
- **Decision ID:** none (implements T2.14: STD-FR-04, WF-16)
- **Tasks:** T2.14; T2.16 (reports read the consumption split)

## Context

A business records ingredients used without a sale (staff meals, samples, catering by weight) so
that usage worked out from counts stays honest. WF-16 asks for `manual_consumption` entries on the
ingredient, keyed `usage:<id>`, with a reason; the task asks for a form only.

## Decision

1. **The ledger transaction is the record.** `record_usage()` (`app/usage.py`) posts one `usage`
   transaction of `manual_consumption` movements, source type `usage`, keyed `usage:<token>`.
   No new table: the transaction already holds the location, the person, the time and the
   reason, and the movements hold each ingredient and its cost snapshot.
2. **The token comes from the form** (32 hex characters, issued when the page is drawn), so the
   same form sent twice posts once and the second answer says so.
3. **Reasons are fixed**: staff meal, sample or tasting, catering by weight, other (a note
   required). Quantities convert through `entered_base` (DR-11); several ingredients may go in
   one record (at most eight).
4. **Below zero** is refused without `adjustment.override_negative` (DR-08); with it, the record
   posts and `negative_balance` opens. An ingredient with no cost opens `missing_cost`.
5. **Permission `sales.record`** ("Enter or correct daily sales and usage"), so Employees record
   usage when the business allows them to enter sales (PD-37).
6. **Reading it**: `consumption()` gives, per location and item over business days, what left
   stock through sales and as manual usage, kept apart; `records()` lists usage records, newest
   first. Both read `movements_in()`, which now also carries each movement's time, person and
   reason.

## Alternatives considered

- **A `usage_records` table like adjustments:** a second copy of what the transaction holds, with
  nothing of its own to add.
- **Usage through a product's recipe:** WF-16 records ingredients; a product used without a sale
  is waste or a sale.

## Consequences

- A mistaken usage record is corrected by an adjustment or a count; there is no void yet.
- The consumption report screen arrives with T2.16; until then the split shows on `/app/usage`.
