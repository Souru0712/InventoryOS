# ADR-0092: Full data export: a job writes a zip of every table and invoice file

- **Status:** Proposed
- **Decision ID:** none (implements ADM-FR-10, SAAS-D-01, SAAS-D-02, AC-ADM-03)
- **Tasks:** T4.05

## Context

SAAS-D-01 lets an Owner or Manager export all business data as CSV in a zip: items, conversions,
suppliers, recipes and their versions, movements, batches, receivings with their invoice files,
waste, counts and exceptions; SAAS-D-02 keeps it available in every state except deleted. Only the
reorder list and report tables had CSV exports. An export holds every record of the business, so
it must reach only the person who asked and must not outlive its use.

## Decision

- **Asking** (`app/exports.py`, `POST /app/exports`, `data.export`): one `data_exports` row and an
  `export_data` job; asking again while one is being prepared returns it. It works read-only
  (listed in `tests/test_lifecycle.py`). It needs access to every location (PD-58). Audited as
  `data.export_requested`.
- **Writing** (the job): one CSV per table of the business's records, from locations and items to
  the ledger, receivings and documents, waste, counts, production, exceptions, sales and the audit
  trail, read through the scoped layer and ordered by primary key; every invoice file under
  `invoices/`; a README with row counts and any file storage could not return. Tables are listed
  by name and resolved through the mapper registry: the export reads and never writes, and the
  ledger and audit models stay named only by their writers. Columns whose names
  mark secrets (`_enc`, token, secret, password, hash) are never written; money columns are left
  out when the asker does not see costs. Cells that would run as spreadsheet formulas are quoted.
  The zip goes to the upload storage under `exports/<business>/`; the asker is emailed a link to
  the exports page.
- **Downloading** (`GET /app/exports/<id>/download`): only the person who asked, while it is ready
  and for three days; served as an attachment.
- **Expiry**: an hourly sweep (`expire_exports`) marks finished exports past their time expired
  and deletes their file; storage gains `delete()`.

## Alternatives considered

- **Exporting in the request.** Simple, but a business with years of movements and photos would
  time the request out; the job queue is the pattern for heavy work (ADR-0044).
- **A signed URL anyone holding it can use.** It would work from email alone, but a forwarded link
  would hand over the whole business; requiring the asker's own session is safer.
- **Exports scoped to a member's locations.** Lines, recipes and other rows without a location of
  their own would need joins to decide; the full export needs every location for now (PD-58).

## Consequences

- Built on branch `stage4` with tests (`tests/test_exports.py`, isolation and permission cases).
- Additive migration `d4f7a2c9e6b1`: `data_exports` and the `export_data` job kind.
- A new table of business records belongs in `TABLES` when it should be exported; the export test
  checks every listed table and the SAAS-D-01 entities.
