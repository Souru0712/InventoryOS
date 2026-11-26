# ADR-0083: Integration health and sales-source status

- **Status:** Accepted, 2026-11-26 (owner: "Merge it"; PR #63, merge commit bc67035)
- **Decision ID:** none (implements T3.09: POS-FR-02, POS-FR-27, CMD-FR-08, AC-CMD-05, AC-CMD-06)
- **Tasks:** T3.09

## Context

The dashboard said "Square connected. Last synced …" from whichever sync ran last, for the whole
business. It said nothing about a location entering sales by hand. Silence looked the same as
zero sales.

## Decision

1. **Health is judged by pure rules** (`app/transform/integration_health.py`). A connection is
   rated from:
   - its order reads: the last success, the last failure, and the 24-hour tally and error rate;
   - its webhook events still waiting more than 15 minutes;
   - its status, and how many locations it feeds.

   The states are `ok`; `setup` (no location tracks it); `warning` (no successful read for over
   two hours, the last read failed, or events waiting); `critical` (revoked); and `off`.
2. **Sales-source status is per location** (`app/sales_status.py`). A location that tracks a POS
   location of an active or revoked connection is Connected, and shows that connection's
   health. Any other location is Standalone: it shows the last business day with sales recorded
   and the open days still missing sales (the `missing_days()` rule behind `sales_missing`).
3. **The dashboard shows it for each location.** A stale connection shows the last successful
   time and says that sales since then may be missing, not zero (AC-CMD-05). A Standalone
   location shows its last day entered and its missing days, with no POS status (AC-CMD-06). A
   revoked connection asks for a reconnect. The location filter narrows the status too.
4. **The connection page's health table** lists the last successful read, failed reads out of the
   last 24 hours, waiting webhook events, the token's status, and products sold but not mapped.
5. **Alerts:** a failed read already raises `sync_failure` (T3.08), and a revoked one emails the
   Owners (T3.02). A quiet but unfailing connection shows as a dashboard warning. Whether that
   also emails is PD-49.

## Alternatives considered

- **Business-wide status:** a business with one POS location and one entering sales by hand would
  see only half its truth.
- **An exception for staleness:** the reads that make a connection stale already raise
  `sync_failure`. Staleness with no failing read means the jobs are not running, which the
  founder's monitoring (OBS-03) catches.

## Consequences

- `sync_health()` remains only on the connection page's summary. The dashboard reads
  `sales_status()`.
