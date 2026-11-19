# ADR-0067: Operational reports on the ledger

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.16: ANL-FR-02..05, -07, AC-ANL-01, -03, -05)
- **Tasks:** T2.16; T2.17 (margin, cost of sales, receiving, count-based usage, CSV of every table)

## Context

Owners decide weekly from consumption, waste, expiry and variance figures (WF-24). Waste by
reason, item, product and person (T2.07) and current and as-of valuation (T1.23) already exist.
Consumption split, count variance and expiry losses do not, and every report must use each
location's business days and the cost recorded with each movement.

## Decision

1. **One read of the ledger.** `item_moves()` gives each movement in each location's window with
   its snapshot cost, a reversal carrying the type it undid. The figures are pure
   (`app/transform/analytics.py`); `app/reports.py` builds the windows and reads waste reasons and
   batches.
2. **Windows are business days** (AC-ANL-05): the period (today, 7 days, 30 days or chosen dates,
   the dashboard's `DateRange`) resolves against each location's own today, from its first
   business day's start to its last's end, in its time zone and cutoff hour.
3. **Values per movement**, rounded to whole cents there as `movement_totals()` does, so the
   consumption totals equal the ledger's (AC-ANL-03), and a later cost change moves nothing
   (AC-ANL-01).
4. **Consumption** splits sales (`sale_consumption`), production (`production_input`), waste and
   manual usage per item. **Count variance** sums `count_variance` per item, short and over kept
   apart. **Expiry losses** are waste whose event's reason code is `expiration`, plus batches
   dated before their location's today that still hold stock, at the batch cost (else the item's
   current cost), labelled an estimate.
5. **`/app/reports`** (`reports.operational`) shows the three, and links the waste report, the
   valuation (now or as of a day) and the weekly summary; money only for a role with cost access
   and financial reports. The menu's "Report" link becomes "Reports".
6. **No new index:** the movement indexes of the data architecture (§6) exist since T1.16.

## Alternatives considered

- **A page per report:** three thin pages; one page over one period reads better weekly.
- **Values from current cost:** contradicts ANL-FR-01 and AC-ANL-01.

## Consequences

- CSV export of these tables comes with T2.17 (ANL-FR-13).
- The page reads the period's movements once per section; one shared read can come with T2.17.
