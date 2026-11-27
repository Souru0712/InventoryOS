# ADR-0091: Subscription lifecycle policies as configuration; read-only refuses every write

- **Status:** Proposed
- **Decision ID:** none (applies C-44, the PD-05 and PD-07 values approved under ID-14, ADR-0014)
- **Tasks:** T4.03

## Context

C-44 set the lifecycle: a 30-day trial without a card, 7 days of full access after a failed
payment, then read-only; read-only keeps views, reports, export and billing, and POS ingestion
continues (SAAS §3, PD-07). The values were constants in `app/entitlements.py`. Three gaps
remained: paying during a trial started billing at once, although the checkout page promised the
30 days either way; Stripe's `unpaid` (retries exhausted) was given a fresh grace period, while
SAAS §3 makes it read-only; and six write routes did not depend on `require_writable`, so a
read-only business could still switch a location's sales source, map POS products and reprocess
sales, track a POS location, or have a date photo read.

## Decision

- **Configuration.** `TRIAL_DAYS` and `GRACE_DAYS` settings (defaults 30 and 7, C-44), read
  through `trial_days()` and `grace_days()`.
- **Trial days are kept.** A checkout during a trial passes the trial's end to Stripe as the
  subscription's `trial_end`, so billing starts when the trial ends; with less than 48 hours left
  (Stripe's minimum) it is billed at once.
- **`unpaid` is read-only at once**: the grace is treated as spent, and the banner asks for a
  card update.
- **Every write is refused while read-only** unless it is on a short list with its reason
  (signing in and account security, removing people, revoking a POS connection, Sync now and the
  POS webhook, billing). `tests/test_lifecycle.py` fails for any new write route that neither
  depends on `require_writable` nor is listed. The six routes above now depend on it.

## Alternatives considered

- **Policies as per-business settings.** The lifecycle is the product's terms, the same for every
  business; a deployment-level setting is enough.
- **A new `unpaid` subscription status.** It would mean altering the stored enum type on
  Postgres for a state that behaves exactly like an expired grace period.

## Consequences

- Built on branch `stage4` with tests (`tests/test_lifecycle.py`).
- No schema change.
- Retention after cancellation (90 days, then deletion after notice, PD-07) is T4.06's.
