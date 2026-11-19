# ADR-0063: Daily sales entry

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.12: STD-FR-02, -05, -06, PD-37, PD-38, AC-SAL-02, -03,
  -05, -06)
- **Tasks:** T2.12; T2.13 (file import records through the same sales model)

## Context

A business with no POS needs to enter each day's sales in minutes, have them take ingredients
out of stock, correct a day with a reason, and be reminded of a day it forgot. PD-37 lets the
Owner allow Employees to enter sales; PD-38 makes R1 entry daily totals per product size.

## Decision

1. **One manual daily transaction per location and day** (reference `day:<date>`), through
   `record_sale()` from the location's hand-entry source (`app/daily_sales.py`, `save_day()`):
   - the same values again record nothing (AC-SAL-02); a changed day is the next revision and
     needs a reason, which travels with the consumption job to the reversal's reason
     (STD-FR-05, AC-SAL-03);
   - consumption posts in the same request inside a savepoint, the queued job being the net;
   - a day whose source of record is not manual (a POS) is refused (AC-SAL-06); a first entry
     starts a manual source, an earlier day extends it back (`extend_source()`, audited).
2. **`sales_missing`**: the stock evaluation flags each open day of the last seven at a location
   whose source of record that day is manual and that has no counted sales; entering the day
   resolves it (STD-FR-06, AC-SAL-05).
3. **PD-37 as a permission**: `member_permissions()` gives an Employee `sales.record` when the
   business allows it (`tenants.employees_enter_sales`, migration `e557269f9eb1`), so the route
   keeps one permission key and every check (menus, routes) agrees.
4. **Screen** `/app/sales/daily`: every active product size, most sold over the last four weeks
   first, the day's numbers, a reason when changing a saved day; the Employee setting for
   `settings.manage`.

## Alternatives considered

- **A second route or a role check for Employees:** two rules for one permission, and the route
  check that every product route has one key would no longer describe the route.
- **A note column on sales transactions for the reason:** the reason belongs to the stock
  correction, which the reversal already carries.

## Consequences

- Modifier counts on daily entry (optional in PD-38) are not on the screen yet.
