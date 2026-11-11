# ADR-0032: Throttling sign-in and emailed links

- **Status:** Accepted, 2026-11-11 (the owner merged the T1.09 pull request, #27, approving the limits below: "Yes, merge it")
- **Decision ID:** none (implements T1.09 under C-31 and C-32; SAAS-A-03, SEC-12, AC-SAAS-03)
- **Tasks:** T1.09; T1.10 (invitations take the same mechanism)

## Context

Thirty wrong passwords in a row were all answered at once (audit S1, probe R8). C-32 asks for
throttling state in shared storage, so the limits hold however many web servers run. The
forgot-password and resend forms send email on request and can be used to flood an inbox.

## Decision

1. **Counters are rows** (`throttles`: key, attempts, window start, locked until, updated). The
   key is a rule name and the SHA-256 of the subject (an email or an IP address), so neither is
   stored. A nightly job removes counters untouched for a day.
2. **A rule** has a limit within a window. Reaching the limit locks the subject out for the rule's
   first lock; each further attempt doubles it, up to the rule's maximum. Attempts refused while
   locked are not counted, so a lockout cannot be stretched by hammering it.
3. **The limits** (security configuration; merging approves them):

   | Rule | Counts | Limit | Lock |
   |---|---|---|---|
   | Sign-in, per account | wrong passwords | 5 in 15 minutes | 1 minute, doubling to 1 hour |
   | Sign-in, per address | wrong passwords | 20 in 15 minutes | 1 minute, doubling to 1 hour |
   | Reset email, per account | requests | 3 an hour | 1 hour |
   | Reset email, per address | requests | 10 an hour | 1 hour |
   | Confirmation email, per account | resend requests | 3 an hour | 1 hour |
   | Links, per address | links that did not work | 10 in 15 minutes | 1 minute, doubling to 1 hour |

4. **Replies reveal nothing**: a locked sign-in answers 429 with a Retry-After header and the same
   sentence whether or not the account exists (unknown emails are counted too); a throttled reset
   request shows the usual "check your email" page and sends nothing.
5. A good sign-in clears the account's counter but not the address's, so one success from an
   attacker's own account does not wipe the guesses made at others.
6. The client's address is what uvicorn reports; deployed it trusts the platform proxy's
   X-Forwarded-For (`--proxy-headers`), which the image already sets.

## Alternatives considered

- Counters in memory: each server would count separately and a restart would forget them.
- Permanent account lockout until an operator unlocks it: anyone could lock any customer out.
- CAPTCHA after a few failures: a third-party dependency and friction for shop staff on shared
  tablets; time-based locks meet SAAS-A-03.

## Consequences

- A locked person can still reset their password; the reset page and email are throttled
  separately.
- Shops whose staff share one network address share the per-address count; 20 wrong passwords in
  15 minutes is far above normal use.
- Invitations (T1.10) count with the same mechanism.
- Built in T1.09 with migration `60bee21fefbd`.
