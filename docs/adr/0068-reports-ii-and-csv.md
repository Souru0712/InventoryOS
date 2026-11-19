# ADR-0068: Reports II and CSV of every report table

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.17: ANL-FR-06, -08, -12..14, STD-FR-08, AC-ANL-04, -06,
  -08)
- **Tasks:** T2.17; T2.18 (the weekly summary reuses these reads)

## Context

The remaining R1 reports are margins, realized cost of sales, receiving discrepancies and price
changes, and count-based actual usage, Standalone's main usage measure. Every report table must
export to CSV with the figures its page shows.

## Decision

1. **Count-based usage per interval** between consecutive counts of an item:
   opening count + received + produced − used in production − wasted ± adjusted − closing count
   (`count_intervals()`). Counts are count-session lines (`counted_lines()` in `app/counts.py`,
   shared with reorder) and non-batch count postings. No sales data is read.
2. **Realized cost of sales** is the sales part of the consumption report (snapshot values),
   with coverage = counted sale lines consumed in full ÷ counted sale lines that should take
   stock (status `posted` against `gap` and `pending`; history kept with `skipped` is left out).
3. **Receiving**: posted lines whose quantities did not match (short, over, rejected) in the
   period; price changes per supplier and item from posted lines' price per base unit,
   (new − previous) ÷ previous, for prices paid in the period.
4. **Margins** by product size from `size_costs()` at the chosen (or first) location, estimated
   at today's costs.
5. **One CSV route**, `/app/reports.csv?table=…` (`data.export`), builds each table from the same
   report objects as the page: consumption, cost of sales, count usage, variance, expiry, at
   risk, discrepancies, price changes, margins, waste (by reason, item, product, person) and
   valuation (now or as of a day). Quantities are in the item's display unit with a unit
   column; money is the page's figure without the currency sign. A role without cost access
   and financial reports gets no money columns, and is refused the money-only tables.
6. **`location_filter` accepts an empty value** as every location, which the dashboard's and the
   reports' "All" options send; a non-number is "not found". The isolation walker names
   `location` as an identifier so those routes stay covered.

## Alternatives considered

- **A CSV route per page:** eleven routes repeating the same range and permission handling.
- **Count-based usage between the window's first and last count only** (as reorder uses it):
  ANL-FR-14 asks for each count interval.

## Consequences

- Count-based usage has no money column yet; the metric's "snapshot (for value)" needs a rule for
  which snapshot values an interval.
