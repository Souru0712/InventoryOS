# ADR-0069: Weekly summary email on the new data

- **Status:** Accepted, 2026-11-19 (owner: "Update, then merge"; PR #60, merge commit f9184b7)
- **Decision ID:** none (implements T2.18: ANL-FR-09, AC-ANL-07)
- **Tasks:** T2.18

## Context

The weekly email predated the ledger: net sales and top sellers from POS sales, stock value from
the stock rows, and the legacy alert proposals. It went to every member who had not turned it
off, Employees included, and was due at the business's report hour in UTC.

## Decision

1. **Content per ANL-FR-09**, read as the reports read it: stock value now (`value_stock()`, with
   the items held without a cost), last week's waste cost (the consumption report's waste part,
   Monday to Sunday in each location's business days, at snapshot), money at risk (the expiry
   figures the dashboard and alerts use: stock that will not be used before its date plus stock
   past it), low stock (out of stock or at or below the reorder point, ten shown, all counted)
   and open exceptions (in needs-attention order, five shown, all counted). Sales figures are
   gone: a Standalone business may have none, and the reports carry them.
2. **Recipients**: Owners, and members whose role grants `inventory.view_costs` and
   `reports.financial`, who have not turned the email off. The email carries money, and no role
   without cost access may see it (REC-FR-16). No schema change: "opted in" is the existing
   per-person setting.
3. **Due on the business's clock**: `send_due_reports()` compares the report day and hour with
   the time in the tenant's time zone, and dedupes by that local week.
4. **The page** `/app/report` shows the same summary; the old sales inputs (`sale_rows()`,
   `sold_item_names()`, `SaleRow`) are removed.

## Alternatives considered

- **Owners only:** Managers who see costs asked for the same view; they can turn it off.
- **A new opt-in column:** a migration the task rules out; the existing setting serves.

## Consequences

- AC-ANL-07 is a manual check against the email provider on staging, at merge.
