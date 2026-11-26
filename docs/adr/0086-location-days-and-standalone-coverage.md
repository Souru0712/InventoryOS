# ADR-0086: Location days: confirmed no-sales days and one-off closures

- **Status:** Accepted, 2026-11-26 (owner: "merge PR #65"; merge commit 3c5755d)
- **Decision ID:** none (implements C-54, PD-54; AC-SAL-08, STD-FR-07)
- **Tasks:** none (owner decision C-54, a follow-up to T2.12 and AC-SAL-08)

## Context

AC-SAL-08 asks Standalone reports for coverage, "N of M open days". The first build counted the
location's open weekdays while hand entry or file import was its source of record, and the days
among them with sales. The owner then decided how coverage works (C-54): a scheduled day is
recorded sales, confirmed zero sales, missing, or closed. Two of those could not be expressed:
- saving an all-zero day wrote nothing, so a day the location was open and sold nothing showed as
  missing and raised `sales_missing`;
- a one-off closure, such as a holiday, could not be marked, so it was expected.

A closure must not delete or change sales or ledger entries.

## Decision

A new table, `location_days`, holds one row per location and business day that a person
accounted for without sales: `no_sales` (open, nothing sold) or `closed` (with a reason). It is
written only by `app/location_days.py`:
- `confirm_no_sales()`: saving a day with nothing sold (`save_day()`) calls it after starting the
  hand-entry source, as an entry would; a day corrected to nothing sold gets it too; recording
  sales for the day removes it;
- `close_day()` (reason required, audited as `location.day_closed`) and `reopen_day()` (audited
  as `location.day_reopened`). A day with sales recorded, or one a POS reports, cannot be closed;
  a closed day takes no hand entry until it is reopened.

Coverage is worked out in `app/transform/analytics.py` (`day_status()`, `sales_coverage()`):
- a day is expected when it is a scheduled weekday (the location's `open_days`) and hand entry or
  file import is its source of record, and it is over (today once something is recorded);
- an expected day is covered with sales or a `no_sales` row, missing without either; a `closed`
  day leaves the expected days;
- coverage = covered expected days ÷ expected days × 100.

`missing_days()` skips marked days, so a confirmed or closed day raises no `sales_missing`. The
reports page, the `sales_coverage` CSV and the weekly summary show covered of expected, with the
days with sales, with no sales, closed and missing.

## Alternatives considered

- **A sales transaction with no lines for a zero-sales day.** It would reuse the sales tables,
  but a file-import day and a closure have no transaction to hold, and a closure is not a sale.
- **Columns on `sales_transactions` or `locations`.** A day's status belongs to the day, not to a
  sale or to the weekly schedule.
- **Allowing a closure over recorded sales.** The owner asked that a closure never change sales;
  refusing keeps the day's sales and its status from contradicting each other.

## Consequences

- Built on branch `after-stage3` with tests (`tests/test_sales_coverage.py`).
- Additive migration `f1b4c7e2a9d3`.
- Holiday detection and closure workflows beyond one day with a reason are out of scope (C-54).
