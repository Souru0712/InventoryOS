# ADR-0093: Account deletion and retention: a cancellable window, then a full erase

- **Status:** Proposed
- **Decision ID:** none (implements ADM-FR-12, SAAS-D-03..05, C-44 (PD-07), AC-ADM-05)
- **Tasks:** T4.06

## Context

SAAS-D-04: an Owner may ask for the business to be deleted, with confirmation and a 7-day
cancellable window, then hard deletion of its data and files and a written confirmation.
SAAS-D-03 (C-44): a business that stops paying stays read-only for 90 days, its POS ingestion
running, then is deleted after notice. SAAS-D-05: staff personal data goes with the
organization. Nothing deleted a business, and `users.tenant_id` (a legacy column until DM-16)
still points every user at a business, so a business row cannot simply be removed.

## Decision

- **Asking** (`app/deletion.py`, `/app/settings/deletion`, `account.delete`, Owners only): the
  Owner re-enters their password (throttled, `DELETE_ACCOUNT`); the deletion falls due after
  `DELETION_WINDOW_DAYS` (7); every Owner is emailed a link to the page that cancels it; a banner
  shows on every page meanwhile. Asking and cancelling work in every subscription state.
  Audited with actor ids only.
- **Retention**: a nightly sweep finds businesses read-only past `RETENTION_DAYS` (90), counted
  from the trial's end, the paid period's end, or the end of the grace (`app/transform/
  retention.py`), and schedules their deletion the same way, telling the Owners (PD-59).
  Subscribing again cancels a retention deletion; an Owner's own request stands.
- **When due**, the `delete_business` job: cancels the Stripe subscription; revokes the POS
  connection; deletes the business's invoice, import and export files; deletes every row
  carrying its `tenant_id`, children before parents (`erase_business()` in
  `app/access/system.py`), with the sessions of its memberships and the accounts of people whose
  only business it was (SAAS-D-05); empties the business's own row as a tombstone (name
  "Deleted business", no contact, no Stripe ids, `deleted_at`); emails the Owners a
  confirmation, then removes that email's own log rows. A repeat, or a deletion cancelled or no
  longer due, does nothing.
- This is the one sanctioned removal of ledger, audit and other append-only rows: it removes the
  whole business, not a correction inside it.

## Alternatives considered

- **Deleting the business row too.** `users.tenant_id` and other people's legacy pointers would
  block it until DM-16; a tombstone keeps references valid and records that it happened.
- **Soft deletion (a flag).** SAAS-D-04 asks for hard deletion; a flag would keep personal data.
- **A longer notice before a retention deletion.** The same 7-day window as an Owner's request
  keeps one rule; the owner may want longer (PD-59).

## Consequences

- Built on branch `stage4` with tests (`tests/test_deletion.py`, `tests/test_retention_rules.py`),
  which check that no row of the business is left, on SQLite and on PostgreSQL in CI.
- Additive migration `e5a8b3d1f7c2`: deletion state on `tenants`, the `delete_business` job kind.
- Backups age out on their own schedule (SAAS-D-04); that is T4.09's backup retention.
