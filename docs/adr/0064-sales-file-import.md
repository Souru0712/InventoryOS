# ADR-0064: Sales file import

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.13: STD-FR-03, AC-SAL-04)
- **Tasks:** T2.13; T2.20 (onboarding offers the import)

## Context

Many businesses without a supported POS keep their sales in a file exported from a card terminal,
a register, a delivery platform or a spreadsheet. They need to bring a week of it in without
retyping it, see what will be recorded, match product names once, and import the same file twice
without counting it twice.

## Decision

1. **CSV only for now.** The reader (`app/transform/imports.py`, pure) decodes UTF-8 (with or
   without a byte-order mark) or Latin-1, detects the delimiter and reads at most 20,000 rows.
   An upload's type comes from its bytes: an Excel file (ZIP signature) is refused with "save it
   as CSV", binary content is refused, size follows `max_upload_mb`. XLSX needs openpyxl
   (approved under ID-23) and is a reader hook once it is added to the lock.
2. **The file is kept**, tenant-scoped, at `{tenant}/imports/{sha256}.csv` in the storage
   backend, with a `sales_imports` row (location, file name, digest, status). Preview and post
   read the stored file, so nothing depends on the request that uploaded it.
3. **Columns once per file shape.** `import_mappings` stores the date, product and quantity
   columns and the date format per business, keyed by the file's normalized header; a known
   header reuses them, a new one is guessed from common names or asked for.
4. **Names learned once.** A product name matches a product size by its label ("Latte Large",
   or the product name alone when it has one size), else through `import_names` (a name the
   business taught, normalized). Unmatched names are listed with their quantities and are not
   imported until matched.
5. **One `file_import` transaction per day** through `record_sale()` (reference
   `import:<date>`): the same day with the same totals records nothing, different totals are the
   next revision (the reversal says which file). Consumption posts in the request inside a
   savepoint, the queued job being the net, as for daily entry (ADR-0063). A day whose source of
   record is another kind (a POS, hand entry) is shown as skipped and not imported (DR-31). The
   import source starts at the first day imported, extends back for an earlier day, and stops the
   day before the next source of record so the two never overlap.

## Alternatives considered

- **One transaction per file row:** a re-export with different row splits would double count;
  daily totals are what the file states reliably.
- **Mapping per upload:** the owner would choose the columns for every weekly export.

## Consequences

- XLSX is not read until openpyxl is added (`uv add openpyxl`, then a reader in
  `app/transform/imports.py`).
- Modifiers in files are not read; R1 imports product-size totals, as daily entry does (PD-38).
