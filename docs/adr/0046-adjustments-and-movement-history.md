# ADR-0046: Adjustments and movement history

- **Status:** Proposed, 2026-11-12 (in the T1.22 pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T1.22; INV-FR-19, -20, -23, DR-08, WF-10, STF-FR-08, AC-INV-08..10)
- **Tasks:** T1.22; T2.06 (batch filter); T2.07 (waste uses the same below-zero rule)

## Context

Until now only a count could lower stock, and the ledger had no screen. WF-10 and INV-FR-19
describe deliberate corrections that always carry a reason. DR-08 forbids a manual movement from
taking stock below zero without a Manager+ override with a reason. Movements the system observed
(a POS sale) post in full and raise `negative_balance`. Above a threshold an adjustment is
reviewed by an Owner or Administrator after it posts (R1).

## Decision

1. **`inventory_adjustments`** (migration `b8363254987e`) is the source record of one
   `adjustment` transaction, keyed `adjustment:<id>`.
   - **Contents:** item, location, signed quantity, reason, note, override reason, actor, the
     transaction, and value at cost.
   - **Checks:** the quantity is not zero; the reason is one of INV-FR-19's five (found,
     correction, sample, transfer in from outside, other); "other" needs a note.
   - **Writer:** only `app/adjustments.py` writes it (a test enforces it).
2. **Below zero** (DR-08):
   - **Guard:** `post_transaction(..., refuse_below_zero=True)` raises `BelowZero` if, with the
     balances locked, a balance would end below zero and lower than it was. Stock already
     negative may rise.
   - **Override:** an adjustment passes the guard only when the person holds
     `adjustment.override_negative` and gives a reason. Then a `negative_balance` exception
     opens.
   - **Who uses it:** waste (T2.07) will use the same guard. Receipts, counts and sales do not:
     the system observed them.
3. **`negative_balance`** joins the scheduled evaluation:
   - any stock row below zero opens one, with the title "count required", and it resolves by
     the system once stock is back at zero or above;
   - the dashboard shows such an item as "count required" (AC-INV-10).
4. **Review:**
   - **Threshold:** an adjustment worth $50 or more at cost posts, then opens an
     `adjustment_review` exception (new type, financial, so hidden from roles without cost
     access) for an Owner or Administrator.
   - **Closing:** they close it by acknowledging or dismissing it with a reason; there is no
     auto-resolve.
   - **Setting:** the threshold is a constant now; making it a business setting is ADM-FR-05.
5. **History** (`app/ledger/history.py`, read-only):
   - **Content:** every movement of one item at one location, newest first, with its kind,
     change, the balance after it, actor, reason and source (a delivery links to it).
   - **Running balance:** it is computed over all movements in posting order *before*
     filtering, so filtered rows still show true balances.
   - **Filters:** movement type, the location's business days (ADR-0027) and actor. Values
     appear only with `inventory.view_costs`.
   - **Batch:** the batch filter waits for T2.06, when movements carry batches.
6. **Screens:**
   - `/app/catalog/{item}/history` needs `inventory.view`.
   - `/app/catalog/{item}/adjust` needs `adjustment.create`: Manager and above. Employees
     cannot adjust.
   - Quantities may be entered in any unit or package (the ADR-0039 resolver).

## Alternatives considered

- **An adjustment with no source record**, just a transaction with a reason: the review,
  override and approver need a header (DOMAIN_MODEL: InventoryAdjustment).
- **Check below zero in the route:** a concurrent posting between the read and the write would
  slip through; the guard runs under the balance lock.
- **Hold large adjustments for approval before posting:** R1.1 option (USER_ROLES §4); R1
  posts and reviews.
- **WF-10's reasons** (internal use, returned to supplier): the task follows INV-FR-19, which
  the acceptance criteria cite. The two lists differ, and this is recorded for the owner.

## Consequences

- Waste, production inputs and other manual movements must pass `refuse_below_zero=True`.
- The movement history is the place to read why stock changed. T1.23's count sessions and later
  modules add their sources to its labels.
- Built in T1.22 with migration `b8363254987e`.
