# ADR-0059: Expiry alert window and auto-dispose

- **Status:** Accepted, 2026-11-19 (owner: "Update, then merge"; PR #60, merge commit f9184b7)
- **Decision ID:** none (implements T2.08: BAT-FR-09..11, BAT-FR-14, PD-29, AC-BAT-06, -07)
- **Tasks:** T2.08

## Context

The expiry page already projected waste risk; BAT-FR-09 adds an alert window (a business default
with item and category overrides), BAT-FR-10 a dispose action on the queue, and BAT-FR-11 an
opt-in write-off of expired prepared batches at day close that must wait for pending sales
consumption (AC-BAT-07). AC-BAT-06 wants one alert when a lot enters the window, at its
location's day start.

## Decision

1. **`expiring_soon`**, a new exception type (expiry family, info): opened per item by the
   stock evaluation when a lot holding stock is within its window, counted in the location's
   business days; it stays one exception until the lot leaves the window (`tenants`,
   `inventory_items`, `inventory_categories` gain `expiry_alert_days`; default 2).
2. **Dispose** (`dispose_batch()` in `app/waste.py`): what the lot holds is logged as waste from
   that batch with the "past its date" reason; the queue shows a Dispose button.
3. **Auto-dispose** (`dispose_expired()`), off by default (`tenants.auto_dispose_prepared`,
   PD-29): runs at the start of each business's evaluation job, writes off prepared batches
   whose date has passed at their location as waste by the system, and skips a location while
   any of its sales lines is still pending consumption. A disposed batch holds nothing, so a
   repeat does nothing.
4. **Use-by label** (BAT-FR-14): a prepared batch's page shows its label text.

## Alternatives considered

- **A separate `auto_dispose` job kind on its own clock:** a new CHECK value and scheduler entry
  for work the evaluation job already runs per business on a clock.
- **Reuse `waste_risk` for the window:** it projects unsold stock over 30 days; the window is a
  plain date reminder with its own setting.

## Consequences

- Category overrides are read but set on no screen yet (items and the business are).
- Built with migration `0662af2faff6`.
