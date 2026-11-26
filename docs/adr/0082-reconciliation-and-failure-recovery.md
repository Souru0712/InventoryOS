# ADR-0082: Reconciliation and failure recovery

- **Status:** Proposed, 2026-11-20 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T3.08: POS-FR-10, POS-FR-12, SQ-12, SQ-17, AC-POS-07,
  AC-POS-08)
- **Tasks:** T3.08; T3.09 shows lag and unprocessed events from the same records

## Context

The cursor read (T3.05) sees what changed since it last looked. An order Square delivers late, with
an earlier time (an offline payment, a lost webhook), could fall behind the cursor. Nothing
compared Square's count of a day with ours. A failed read raised inside a job, which rolled back
the record of the failure itself.

## Decision

1. **An hourly wider re-read.** Once an hour (`order_cursors.reread_at`) the order read goes back
   two hours instead of the ten-minute overlap, so a late order is picked up within the hour
   (AC-POS-07). Versions keep it idempotent.
2. **A daily check of closed business days** (`reconcile_orders`, a job queued every six hours).
   It runs for each tracked location over its last three closed business days from the cursor's
   `lines_from`. It asks the adapter for the POS's completed orders by completion time (the new
   `completed_orders()` capability; Square searches by `closed_at`) and compares their orders and
   lines with the staged completed orders.
   - A difference opens `pos_reconciliation_mismatch` for the location and day, saying both
     counts (AC-POS-08). The orders that differ go through the same path as any read, so the day
     is recovered. The next check that matches resolves it: a complete check answers for its
     type (`apply_findings`), and three days are rechecked each time, so a recovered day is seen
     to match.
   - The per-day counts are kept on the run's `SyncRun` (kind `reconciliation`). A check that
     fails resolves nothing.
3. **A failed read is kept, not retried blind.** A passing failure marks its `SyncRun` failed and
   adds to a `sync_failure` exception on the connection (warning; occurrences count the retries).
   The job finishes, the cursor stays put, and the next scheduled read tries again. The next
   success clears the exception. A revoked authorization is marked as before (critical). Webhook
   events wait while their read fails.
4. **A sync history screen** (`/app/connection/history`, `pos.manage`) lists the last 50 reads,
   catalog reads and daily checks, with what each did or the error. A mismatch exception links
   to it.

## Alternatives considered

- **Re-reading 60 days every hour:** the legacy cost T3.05 removed.
- **Opening a mismatch without recovering:** the owner would have to act on something the system
  can do itself. The exception still says what was missing.
- **Retrying a failed read as the same job:** the rollback lost the failure's record, and the next
  scheduled read retries anyway.

## Consequences

- "No success for longer than a threshold" as a `sync_failure` (DOMAIN_MODEL §6) is lag, shown by
  T3.09's health page.
- Each check costs three order searches per tracked location.
