# ADR-0047: Count sessions

- **Status:** Accepted, 2026-11-12 (owner: "Yes, merge it"; PR #45, T1.23-a)
- **Decision ID:** none (implements T1.23; REC-FR-01..03, -05..08, DR-12, PD-12, WF-09, AC-REC-01..05)
- **Tasks:** T1.23-a (model and posting); T1.23-b (session screens and reconciliation history)

## Context

Counts are how an operator finds loss. Since T1.18 a quick count on the dashboard or scan page
posts one `count_variance` against the balance at that moment. A count session covers many items.
Its lines are counted minutes apart while sales and deliveries keep posting, so what each line is
compared with must be the stock at *its* instant, not at posting time (REC-FR-03, AC-REC-02).

## Decision

1. **Two tables** (migration `f38f77cefc20`):
   - **`stock_counts`:** location; scope (full, category, storage area, one item) and which
     one; `open` → `posted` (named CHECKs); who started and posted it; its transaction.
   - **`stock_count_lines`:** one per item per session (unique). Each holds:
     - the observation: counted quantity (never below zero), what was typed ("3 case + 4
       each"), when and by whom;
     - what the ledger expected then, the variance in quantity and cents, and the unit cost;
     - an optional REC-FR-06 reason, a review flag, and the posted movement.

   Only `app/counts.py` writes them (a test enforces it). Lines reach their location through
   their session (`LOCATION_PATHS`).
2. **Expected at an instant:** `balance_at()` is the sum of the movements dated at or before
   it. A movement recorded later but dated earlier (a delivery entered afterwards) counts; one
   dated later (a sale a minute after) does not.
3. **Entry** (REC-FR-02): a line is any number of (quantity, unit-or-package) parts, each through
   the ADR-0039 resolver, added up. Counting an item again replaces its line.
4. **Posting** (REC-FR-07, DR-07, DR-12):
   - **Once:** the session moves `open` → `posted` by a conditional update, so a second request
     is refused.
   - **Under the lock:** `post_count_lines()` locks every balance the session touches, then
     works out each line's expected for its own `counted_at`.
   - **Movements:** one `count` transaction keyed `count:<id>` holds one `count_variance` per
     non-zero line. Each movement is dated at its line's `counted_at`; `Line` gains an optional
     `occurred_at`.
   - **Zero and none:** a zero variance posts nothing, and a session with no difference has no
     transaction.
   - **What it leaves:** the balance ends at "counted, plus whatever moved since". Each stock
     row's last-counted time is set.
5. **Review after posting** (REC-FR-08, PD-12 R1):
   - **Threshold:** a line worth $25 or more either way, or 20% or more of a positive expected
     quantity, opens `count_variance_high` for Manager review.
   - **Visibility:** the title gives quantities only. The amount is the exception's money
     impact, withheld from roles without cost access.
   - **Setting:** the threshold becomes a business setting with ADM-FR-05.
6. **Fresh reads:** sessions keep loaded rows across commits (`expire_on_commit=False`). The
   session's status is therefore read afresh (`populate_existing`) before recording or posting.

## Alternatives considered

- **Expected snapshotted when a line is entered and never recomputed:** a delivery entered
  afterwards, but dated before the count, would be wrongly called a variance. The entry-time
  figure stays only as a preview for the counter.
- **Variance dated at posting time:** a later session counted in between would not see it, and
  history would place the correction at the wrong moment.
- **Hold large variances for approval before posting:** R1.1 option (PD-12).

## Consequences

- The dashboard and scan quick counts stay single-line counts against the balance now (T1.18).
  A session is the tool for a deliberate count.
- T1.23-b adds the screens (`app/web/counts.py`): `/app/counts` to start a count and read the
  reconciliation history with totals (REC-FR-09, AC-REC-05), and `/app/counts/{id}`. While a
  count is open, that page lists its items in storage order, one card each, with packages plus
  loose and an optional reason. Saving moves to the next item, and the page posts once. After
  posting it is the line detail. Counting takes `count.record`; reading takes `inventory.view`;
  amounts show only with cost access.
- Built in T1.23-a (migration `f38f77cefc20`) and T1.23-b (screens).
