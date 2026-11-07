# ADR-0030: Server-side sessions, CSRF tokens, POST logout

- **Status:** Accepted, 2026-11-07 (the owner merged the T1.07 pull request, #25: "Yes, merge it", approving the two timeouts below)
- **Decision ID:** none (implements T1.07 under C-32; SAAS-A-04, SAAS-A-06, STF-FR-06, SEC-05, AC-STF-04)
- **Tasks:** T1.07; T1.08 (password change revokes sessions), T1.10 (revoking a member)

## Context

The session was a signed cookie holding the user id (audit S5): it could not be revoked, was not
rotated at sign-in, and lasted 14 days whatever happened. Forms carried no CSRF token and logout
was a GET (S4). Revoking a member must end their access on their next request (AC-STF-04).

## Decision

1. **A session is a row** (`sessions`: token hash, user, chosen membership, created, last seen,
   absolute end, revoked). The signed cookie carries only a random token (and the form token);
   the row is found by the token's SHA-256, so the database never holds a usable token.
2. **Timeouts**: a session ends after **12 hours without a request** (`SESSION_IDLE_MINUTES`,
   720) and **14 days after sign-in** in any case (`SESSION_MAX_DAYS`, 14, the old cookie's
   lifetime). Last-seen is written at most every 5 minutes. The cookie's own lifetime matches.
3. **Rotation and revocation**: every sign-in issues a new token and ends the session the browser
   held; logout ends the row; "Log out all devices" (settings) ends every row of the person;
   `revoke_sessions` serves a password change (T1.08); `revoke_membership` revokes a membership
   and ends the sessions acting for it. A revoked membership is refused on the next request
   whatever the session (T1.05).
4. **CSRF**: every state-changing request carries the session's form token, as a hidden
   `csrf_token` field (every POST form; a test checks the templates) or an `X-CSRF-Token` header
   (the scanner's fetch calls). An ASGI middleware inside the session middleware compares it in
   constant time and answers 403 otherwise. Square and Stripe webhooks are exempt: they carry no
   cookie and are verified by signature. Signing in issues a new form token.
5. **Logout is a POST** (a link or image elsewhere cannot sign anyone out); `GET /logout` is gone.

## Alternatives considered

- Keep the whole session server-side, form token included: every anonymous visitor to the sign-in
  page would create a database row; the signed cookie holds only the token and the form token.
- SameSite=Lax plus an Origin check instead of tokens (SEC-05 allows it): weaker for older
  browsers and embedded webviews on shop tablets, and T1.07 asks for tokens.
- Shorter idle timeouts (30-60 minutes): shared counter tablets would sign out mid-shift; per-person
  PIN switching is R1.1 (STF-FR-09).

## Consequences

- Everyone signs in again once after the deploy: cookies from before carry no session token.
- New forms need `{{ csrf_field(request) }}`, new JavaScript posts the `X-CSRF-Token` header, and a
  new webhook from a provider that signs its calls is added to the exempt list.
- Tests post like a browser (`tests/browser.py` adds the session's token unless a test sends its own).
- Built in T1.07 with migration `8fadf35f1453`.
