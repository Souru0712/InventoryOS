# ADR-0024: Invoice photos kept briefly, in Postgres

- **Status:** Accepted, 2026-11-06 (owner chose option A: "Option A sounds more optimal and less expensive"; product decision C-48 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md)). Supersedes [ADR-0012](0012-s3-object-storage.md).
- **Decision ID:** none (follows product decision C-48)
- **Tasks:** T0.09

## Context

The spec kept every uploaded invoice photo as the receiving record (RCV-FR-02), which is why an
S3 bucket was planned (ADR-0012). The owner asked why the founder should hold the invoice
documents of many customers, each with several distributors. What the product needs from a photo
is short-lived: the model reads it on the server, the review screen shows it beside the extracted
lines (often later, or from another device), and a failed reading can be retried. The extracted
lines (supplier, items, quantities, costs) are the lasting record and are kept either way.
Suppliers' claim windows on the owner's invoices are 24 hours to 5 days. With more than one web
instance, a photo on one instance's disk is invisible to the others, so it needs shared storage.

## Decision

Keep an invoice photo until its delivery is received plus 7 days, then delete it; keep the
extracted lines. Store the photos in the application's Postgres database, so no separate storage
account is needed.

## Alternatives considered

- Keep every photo permanently in S3 (ADR-0012): the most data held for the least use, and a
  second account to run.
- Never keep the photo: the review screen could not show it and a reading could not be retried.

## Consequences

- No AWS account for staging or production; Render and Sentry are the only accounts T0.09 needs.
- The database holds at most about a week of photos per active tenant (15 MB cap per upload), so
  its size stays bounded; backups include them while they exist.
- Not built yet: a database storage backend, the deletion job, and RCV-FR-02's new wording in the
  code. Until then staging runs one web instance with photos on its own disk (`render.yaml`).
- To confirm when it is built: what happens to an upload that is never received. Proposal: delete
  it 30 days after upload.
