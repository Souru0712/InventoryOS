# ADR-0072: Onboarding step 0 and the opening count

- **Status:** Accepted, 2026-11-19 (owner: "Update, then merge"; PR #60, merge commit f9184b7)
- **Decision ID:** none (implements T2.20-b: SAAS §5 step 0 and step 10, WF-01)
- **Tasks:** T2.20-b; T3 connected steps 7–8 add to the same checklist

## Context

SAAS §5 starts with "How do you take payments today?", which routes a business to the connected
or Standalone path and records the POS it would want supported (adapter demand) or its interest
in an InventoryOS POS (Scenario C). Any checklist step can be skipped and resumed. Step 10 is a
guided, one-screen opening count posting `opening_balance`; its time starts reliable variance
reporting.

## Decision

1. **`onboarding`**, one row per business (made when first written): the payment answer, the POS
   named, the steps skipped, when the opening count was taken. Pages read it without creating
   it. The answer is a `settings.onboarding_answered` audit event; no plan changes (PD-36 stays
   with billing).
2. **Routing changes the checklist only**: a supported POS (Square) points "sales" at connecting
   it; a POS not yet supported at the sales file import; no POS at daily entry. The POS name is
   kept as said; nothing is sent anywhere.
3. **Skipping** is a list of step keys; a skipped step stays on the checklist with "take it back
   up", and the card goes when every step is done or skipped.
4. **The opening count** (`opening_count()`, `/app/onboarding/count`, `count.record`): every
   active item stocked at the location on one screen, in its display unit or a package. An item
   that never moved there opens with the counted quantity (`post_opening()`, `opening_balance`); one
   with a history is counted against it (`post_count()`, a count variance), so a quantity entered
   at import is corrected rather than doubled. Blank lines are left alone. Counted items get their
   counted time, and the checklist's count step is done.

## Alternatives considered

- **A count session for the opening count:** every never-moved item would post its whole
  quantity as a variance and raise count reviews; an opening is not a discrepancy.
- **Step 0 as a separate page after sign-up:** the dashboard card asks it without changing the
  sign-up flow, and stays until answered.

## Consequences

- Adapter demand is read from `onboarding.pos_name`; a report of it is for operations later.
