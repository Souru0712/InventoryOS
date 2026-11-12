# ADR-0043: Exceptions: one record for everything that needs a person

- **Status:** Accepted, 2026-11-12 (owner: "Yes, merge it"; PR #39)
- **Decision ID:** none (implements T1.19; REC-FR-11..16, AC-REC-06..09, WF-27, DM-8)
- **Tasks:** T1.19; T1.20 (evaluates every tenant on schedule); T1.21 (`balance_drift`); T1.25 (dashboard)

## Context

Problems reach people today as `alerts`: five kinds (low stock, out of stock, stale count, waste
risk, expired), one row per stock item and kind, written only by `evaluate_and_store` and read
only by the email digest. They have no severity, no owner, no acknowledgement and no way to be
dismissed. The specification wants one exception record for every problem type (REC-FR-12),
de-duplicated by type and subject, auto-resolved when the condition clears, closed only by an
action or a dismissal reason, and hidden from Employees when it is about money (REC-FR-16).

## Decision

1. **One table, `exceptions`** (`ExceptionRecord`; the name avoids Python's `Exception`):
   - **What it is:** type, severity, subject (`subject_type`, `subject_id`), location and title.
   - **Measures:** money impact, quantity, days of cover, and occurrences.
   - **Timeline and outcome:** status (`open` → `acknowledged` → `resolved` / `dismissed`),
     detected and last-seen times, acknowledgement, resolver, resolution (`action`,
     `dismissed`, `system`) and note.
   - **Checks.** Named CHECKs cover type, severity, status and resolution. A closed exception
     must have a resolution, and a dismissed one must have a note.
   - **One open per subject.** A partial unique index on (tenant, type, subject) while open or
     acknowledged allows only one (REC-FR-13).
2. **Catalogue** (`app/transform/exceptions.py`, pure):
   - It holds every REC-FR-12 type plus the four alert kinds that have no REC-FR-12 type.
   - Each type has a label, a default severity (WF-27), whether it is financial
     (`price_variance`, `missing_cost`), and a fix.
   - `reconcile()` splits a run's findings into opened, refreshed and cleared.
3. **The five alert kinds become exceptions** (DM-8, migration). `expired` maps to
   `batch_expired_unresolved`; the other four keep their names, with the stock item as subject.
   - **Backfill.** Open alerts become open exceptions; resolved alerts become `resolved` by
     `system`.
   - **Freeze.** The `alerts` table stays as read-only history, and nothing writes it (a test
     fails on constructing `Alert`).
4. **Evaluation.**
   - `evaluate_and_store` turns the existing pure alert evaluators' proposals into findings and
     reconciles them with the open exceptions of those five types only. Other types are never
     auto-resolved by it.
   - A cleared condition is closed as resolved by the system (REC-FR-14, AC-REC-07).
   - `record_exception()` is for event-like problems: it adds to the open exception's
     occurrences instead of opening another (AC-REC-06), retrying on the unique index if two
     runs race.
5. **People act through `app/exceptions.py`.**
   - **Acknowledge** and **dismiss** are conditional status updates (DR-07). Dismissing needs a
     reason.
   - **Fix:** the exception links to the screen where it is fixed. Fixing clears the condition
     and the next evaluation closes it. Nothing here posts or edits a quantity (AC-REC-08).
   - **Audit:** each action is recorded under a new audit category, `exception`.
6. **Visibility** (AC-REC-09):
   - Every role may view, and Manager and above may resolve (`exception.view`,
     `exception.resolve`).
   - A role without `inventory.view_costs` gets no financial types and no amounts. The list
     screen and the email digest both apply this filter, and the digest also respects each
     recipient's locations.
7. **Screen:** `/app/exceptions` lists open exceptions (or closed ones), most severe first, with
   Acknowledge, Fix and Dismiss.

## Alternatives considered

- **Keep `alerts` and add `exceptions` beside them:** two mechanisms for the same thing; the
  task's objective is one (G15).
- **A unique `open_key` column cleared on close** instead of a partial index: portable, but the
  index states the rule directly, and SQLite and Postgres both support it.
- **Map every alert kind onto a REC-FR-12 type:** low stock, out of stock, stale count and
  waste risk have none; inventing near-fits would blur both.
- **A manual "resolved" button:** it would let a number be waved away without a fix or a
  reason, which AC-REC-08 forbids.

## Consequences

- Producers of the other types arrive with their tasks: `balance_drift` (T1.21), and
  `unmapped_product` and the sales types with T2.x. They call `record_exception()` or extend
  the evaluator's set.
- Standalone tenants get exceptions on a schedule from T1.20 (ADR-0044); before it, evaluation
  ran only in the Square sync and the command line, as alerts did.
- A location-limited member does not see business-wide exceptions (those with no location), by
  the access layer's deny-by-default rule.
- Built in T1.19 with migration `0dca80ee957e`.
