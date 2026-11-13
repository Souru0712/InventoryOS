# ADR-0045: Nightly ledger verification

- **Status:** Accepted, 2026-11-12 (owner: "Yes, merge it"; PR #43)
- **Decision ID:** none (implements T1.21; DR-03, REL-08, AC-INV-11, OBS-06)
- **Tasks:** T1.21; T4.07 and T4.10 (founder console and incident thresholds read the same signal)

## Context

DR-03 says a recorded balance equals the sum of its movements, and that a nightly job checks
it and raises `balance_drift` on a mismatch, never overwriting. Since T1.18 there are two
cached figures per item at a location (ADR-0042):
- the balance row;
- the stock row every screen reads, kept equal to the balance by the posting service.

## Decision

1. **What it checks** (`app/ledger/verify.py`, read-only). For each organization it computes:
   - each item's movements at each location, summed in Python as exact decimals (SQLite keeps
     quantities as text and would add them as floats);
   - the recorded balance;
   - the stock row's quantity.

   The pure rule `find_drift()` (`app/transform/ledger.py`) reports every pair where they
   differ: the balance is not the sum, there are movements but no balance row, or the stock row
   is not the balance.
2. **When.** A `verify_ledger` job (new kind; its CHECK by migration `6bedfbcf985f`) is queued
   for every subscribed business at 02:45 UTC, once per date (dedupe key `night:<date>`), and
   run by the T1.20 worker.
3. **What a drift does:**
   - **Exception:** a critical `balance_drift` opens per item and location. Its title gives the
     three figures in the item's base unit and says nothing was changed.
   - **Lifecycle:** it is refreshed while the drift lasts, and resolved by the system the first
     night the figures agree again (DOMAIN_MODEL §6).
   - **Founder alert** (OBS-06: drift > 0): one ERROR log line per business per night, with ids
     only. With `SENTRY_DSN` set, error reporting turns it into an alert. T4.10 adds the
     thresholds and the channel.
4. **It never corrects.** Neither the balance nor the movements nor the stock row is written. A
   drift is a defect to understand; support rebuilds the projection deliberately.

## Alternatives considered

- **Sum in SQL:** exact on Postgres, but floating point on SQLite, which the suite runs on.
  Summing in Python is exact on both, and one pass over a business's movements is cheap at
  launch scale. Revisit with a per-pair checkpoint if a night's run grows long.
- **Auto-correct the projection from the movements:** DR-03 forbids it; it would hide the bug
  that caused the drift.
- **Check only balance against movements:** the stock row is what people see, and a writer that
  bypasses the posting service would show up only there.

## Consequences

- Any future cached figure (batch remaining in T2.06, for instance) should join this check.
- Built in T1.21 with migration `6bedfbcf985f`.
