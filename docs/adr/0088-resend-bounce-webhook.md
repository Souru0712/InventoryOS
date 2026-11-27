# ADR-0088: Resend's bounces and complaints fill the suppression list

- **Status:** Proposed
- **Decision ID:** none (the bounce handling into `email_suppressions` that T4.09 asks for)
- **Tasks:** T4.09 (its email part); follow-up to AC-ANL-07

## Context

Email goes out through Resend over SMTP from `vontry.app`. `send_email()` already refuses an
address on the suppression list (`email_suppressions`, sources `bounce`, `complaint`, `manual`),
but only `python -m app.cli email --suppress` filled it. During the AC-ANL-07 test, a copy to
`support@vontry.app` bounced and Vontry never heard about it: Resend reports bounces and
complaints only to the dashboard and to a webhook. T4.09 asks for bounce handling into
`email_suppressions`. New dependencies need the owner's approval, so Resend's SDK and the `svix`
package are out.

Resend signs webhooks with Svix: the headers `svix-id`, `svix-timestamp` (seconds) and
`svix-signature` (space-separated `v1,<base64>` entries). The signature is the HMAC-SHA256 of
`{svix-id}.{svix-timestamp}.{raw body}`, keyed with the base64-decoded part of the `whsec_`
secret. `email.bounced` carries `data.to` (a list) and `data.bounce.type`, `Permanent` for a hard
bounce. `email.complained` carries `data.to`. Retries keep the same `svix-id`.

## Decision

`POST /webhooks/resend` (`app/web/email_webhooks.py`) is a public route signed like Square's: a
plain `def` route that reads the raw body through `app/web/inputs.py`, with no session, no CSRF
token (`EXEMPT_PATHS`) and no permission key (the public list in `tests/test_permissions.py`). It
refuses everything with 503 while `RESEND_WEBHOOK_SECRET` is empty or not a `whsec_` secret.
`app/notify/resend.py` handles the rest with the standard library only (`hmac`, `hashlib`,
`base64`):
- `verify()` accepts a call only when one `v1` entry matches in constant time and the timestamp
  is within 5 minutes of our clock. Anything else is a 400, and nothing is written.
- `suppressions()` reads the verified event:
  - `email.bounced` with bounce type `Permanent` suppresses each recipient as `bounce`. Any other
    bounce type (transient, temporary, undetermined) is ignored, because Resend retries those and
    the address works.
  - `email.complained` suppresses each recipient as `complaint`.
  - Every other type is answered 200 and ignored.
- `apply_event()` writes through `suppress()`, which updates an existing row, so retries and
  replays change nothing and no event store is kept. The reason stored names the bounce kind and
  Resend's email id, never the provider's message, because that often quotes the address and
  `suppress()` logs its reason. Logs carry the type, the delivery id and the email id only.

## Alternatives considered

- **The `svix` or `resend` package.** These do the same check in one call, but each is a new
  dependency needing approval, and the scheme is about twenty lines of the standard library. A
  test pins it to Svix's published example.
- **Storing events in `webhook_events` (ADR-0080) first.** That table is the POS sync's event
  store, with its jobs and tenant. A suppression belongs to no business and is safe to repeat, so
  deduplicating by `svix-id` would add nothing.
- **Suppressing soft bounces after several in a row.** A count needs history the app does not
  keep, and Resend already retries. It can be added later if soft bounces become a real cost.
- **Queuing a job.** The work is one or two rows, and the answer must be quick. Running it in the
  request keeps it simple, and a failure leaves Resend to retry.

## Consequences

- Hard bounces and complaints stop Vontry mailing an address without anyone acting. The email
  log shows `suppressed` for later sends.
- Resend keeps its own suppression list. `python -m app.cli email --allow <address>` clears only
  Vontry's, so an address that was fixed must also be removed in Resend.
- A complaint suppresses every kind of email to that address, including password resets, as a
  hand-added suppression already does.
- The owner's step: in Resend, add a webhook to `https://<APP_BASE_URL>/webhooks/resend` for
  `email.bounced` and `email.complained`, and put its signing secret in `RESEND_WEBHOOK_SECRET`
  (NEEDS-YOU §3). This needs a public URL (NEEDS-YOU §2).
- Built with tests (`tests/test_resend_webhook.py`). No migration.
