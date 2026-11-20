# ADR-0071: Item import, quick-add and the onboarding checklist

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.20-a: INV-FR-08, AC-INV-12, CMD-FR-11, SAAS §5 steps 2–10)
- **Tasks:** T2.20-a; T2.20-b (step 0, the one-screen opening count, skipping steps)

## Context

A new business could only add items one form at a time, and nothing told it what to set up next
(G13: the only path was "Connect Square"). AC-INV-12 asks for a CSV of 150 items with 3 bad rows
to preview, import 147 and report the 3 with reasons.

## Decision

1. **Rows are tried by the item rules themselves.** The preview runs every row through
   `create_item()` inside a savepoint that is rolled back, so the reported errors are exactly
   what importing meets (units, levels, names already taken), plus what only the file can say
   (a unit the app does not know, a name in the file twice). Importing does the same and keeps
   the rows that pass; nothing is all-or-nothing.
2. **The file is kept** in the business's storage under its digest
   (`{tenant}/imports/items-{sha256}.csv`); preview and import read it there, so no table is
   needed and another business's digest names nothing in this business's storage.
3. **Columns go by their common names** (Name or Item, Unit or UOM, On hand, Par, Cost or Price,
   Vendor...); only a name is required, the unit defaults to each, and a template CSV is
   offered. Spreadsheets are refused with "save it as CSV", as for sales files (ADR-0064).
4. **Quick-add** is a twelve-row grid run through the same path.
5. **The checklist is computed** from the business's own records (a location, an item, a
   supplier, a recipe, a sales source or POS connection, alert settings or a reorder point, a
   posted count, a posted receiving, a second member or an invitation), so it never disagrees
   with the product and a step done any other way counts. It shows on the dashboard to Owners and
   Administrators until every step is done.
6. **Screens** at `/app/items/import` and `/app/items/quick-add` (`item.edit`): the catalog's
   `/app/catalog/{id}` routes would catch any path beneath it.

## Alternatives considered

- **Validating rows with a separate rule set:** two copies of the item rules that would drift.
- **An onboarding state table now:** the steps' state is the data; skipping a step (which needs
  state) comes with step 0 in T2.20-b.

## Consequences

- XLSX item files wait for openpyxl, as sales files do.
