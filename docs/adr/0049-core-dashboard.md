# ADR-0049: Core dashboard on the ledger and exceptions

- **Status:** Accepted, 2026-11-13 (owner: "Yes, merge it"; PR #48, T1.25)
- **Decision ID:** none (implements T1.25: CMD-FR-01..04, -10, -12..14, AC-CMD-01..04, PD-26)
- **Tasks:** T1.25; T2.05 and T3.04 bring the producers and the screen for `unmapped_product`

## Context

`/app` was the first screen after sign-in. It had grown out of the old app:
- **Attention:** its "Needs attention" count came from alert proposals computed on the page,
  not from the exceptions people act on (ADR-0043).
- **Money:** it showed no money over a period.
- **Completeness:** it never said when its figures were missing inputs.
- **Health score:** it led with the health score, which PD-26 (C-44) keeps off R1 unless the
  owner asks for it.

## Decision

1. **Needs attention is the open exceptions** (CMD-FR-01), from `listing()`.
   - **Order:** most severe first, then most money at stake, then latest seen
     (`attention_key()`). The exceptions screen uses the same order.
   - **Links:** each exception names its subject and links to that subject's record
     (`ExceptionView.link`):
     - an item goes to its item page;
     - dated stock goes to its row on `/app/expiring`;
     - an adjustment goes to the item's history;
     - a count line goes to its count.
     - A subject with no screen yet, such as a POS product, links to the exceptions screen
       until T3.04 adds one.
   - **Next action:** each one states its recommended action as a button (CMD-FR-12).
     Severities look different from one another.
   - **Location filter:** a location's view keeps business-wide exceptions.
2. **Money over a period** (CMD-FR-10) shows waste, receipts and count variances for today,
   7 days, 30 days, or a custom range of up to 366 days.
   - **Days:** each location counts in its own business days (`business_day_start()` /
     `business_day_end()`, ADR-0027).
   - **Source:** figures come from the ledger alone: movements valued at the cost each was
     posted with (`movement_totals()`, DR-33, AC-ANL-01).
   - **Reversals:** a reversal counts against the type it reversed.
   - **Refused ranges:** a range the rules refuse falls back to 7 days and says why.
3. **Stock value** keeps T1.24's figure and its basis (CMD-FR-04, ADR-0048).
4. **Completeness** (CMD-FR-13): when products sold in the period have no recipe and are not a
   POS-counted item themselves, a banner gives the count. "Cover" and "Uses / day" are then
   labelled incomplete.
5. **No money for a role without cost access** (CMD-FR-14, AC-CMD-03). The page's data passes
   through `without_costs()` before it reaches the template; the period money and stock value
   are never computed for that role. Financial exceptions and every amount were already
   withheld by `listing()`.
6. **On hand is the balance's projection** (ADR-0042). Only posting writes it, and the nightly
   check proves it equals `stock_balances` (ADR-0045). The page does not query the balances a
   second time.
7. **The health score leaves the dashboard** (PD-26). Its page still answers at its URL and its
   job still runs. Removing them is a separate change for the owner to ask for.

## Alternatives considered

- **Keep computing attention from alert proposals:** two lists of problems would disagree with
  each other. The exceptions are the ones people acknowledge and dismiss.
- **Count a period in UTC days:** a café in New York would see its evening waste on the next
  day. ADR-0027 already decides that today is the location's own day.
- **A separate JSON endpoint to prove AC-CMD-03:** the page is rendered on the server, so the
  response is the page. Stripping costs before rendering guarantees it without a second
  surface.

## Consequences

- **T2.05 and T3.04:** when they raise `unmapped_product` and add the mapping screen, they
  give `sold_item` subjects a link.
- **T2.07 waste logging:** it posts `waste` movements, which the waste figure already sums.
- **Built in T1.25, with no schema change.** It completes the M2 tasks. The G2 gate review is
  the owner's.
