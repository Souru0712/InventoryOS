# ADR-0031: Email verification, password reset and change

- **Status:** Proposed, 2026-11-07 (in the T1.08 pull request; accepted when the owner merges it)
- **Decision ID:** none (implements T1.08 under C-31 and C-32; SAAS-A-02, SAAS-A-05, AC-SAAS-02, AC-SAAS-04)
- **Tasks:** T1.08; T1.09 (rate limits on these forms), T1.10 (invitations join the gate)

## Context

A customer who forgot a password needed the founder (audit S2), and an unverified sign-up could
spend AI extraction (S9). C-31 makes verification and reset must-haves; C-32 keeps them in-app.

## Decision

1. **Emailed links are single-use tokens** (`auth_tokens`: user, purpose, hash, created,
   expires, used). Only the SHA-256 is stored; a token is found by its hash. Issuing a new link
   for a purpose ends the earlier unused ones. A confirmation link lasts **48 hours**, a reset
   link **one hour**.
2. **A link opens a page; its button posts the token.** A mail scanner that fetches every link
   cannot use one up; the post carries the CSRF token like any form.
3. **Verification gates the costly and the connecting**: connecting Square, uploading or re-reading
   an invoice, and reading an expiry photo (`require_verified`) answer 403 until the address is
   confirmed (`users.email_verified_at`). Inviting staff joins the gate in T1.10. Everything else
   works unconfirmed, with a banner and a "send the link again" button.
4. **Reset** (`/password/forgot`, `/password/reset`): the request answers the same whether or not
   the account exists; setting the new password ends every session, and confirms the address (the
   email reached them). A password shorter than 10 characters is refused without using up the link.
5. **Change** (settings): the current password is required; every other session ends and this
   browser continues on a new one.
6. Existing accounts start unconfirmed; `python -m app.cli verify-email --email` confirms one by
   hand (support, or a machine without email configured). The demo owner is created confirmed.

## Alternatives considered

- Confirm on GET: one click less, but link scanners in mail services would consume the link.
- Signed, stateless tokens (no table): they cannot be made single-use or revoked by a newer link.
- Block everything until confirmed: a new owner could not even look around while the email
  arrives; only the actions with a cost or an outside connection wait.

## Consequences

- Account emails go through `app/notify/email.py` like the others and are always sent (they are
  not a category a person can turn off); without SMTP they are logged as skipped, so a developer
  confirms with the command line.
- Throttling these forms is T1.09's (SAAS-A-03).
- Built in T1.08 with migration `6fbdcbb20640`.
