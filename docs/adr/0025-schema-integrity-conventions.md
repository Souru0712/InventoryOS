# ADR-0025: Schema integrity conventions: UTC instants, delete rules, named CHECKs

- **Status:** Accepted, 2026-11-06 (the owner merged the T1.01 pull request, #19: "Yes, merge it")
- **Decision ID:** none (implements T1.01, migration step DM-2, and rules DR-08, DR-20, DR-24)
- **Tasks:** T1.01, and every later task that adds a table (T1.02 onwards)

## Context

The audit found no CHECK constraints (DB1), no `ON DELETE` rules (DB2) and naive timestamps whose
correctness rested on a per-connection `SET TIME ZONE 'UTC'` and an `_aware()` helper repeated in
five modules (DB3, probe PA5). DR-20 requires instants stored as UTC-aware timestamps. The suite
and local development run on SQLite, production on Postgres 16; SQLite did not enforce foreign
keys at all, so a delete that Postgres refuses passed on SQLite. Every Stage 1 table (memberships,
ledger, counts) will copy whatever convention exists when it is written.

## Decision

1. **Instants** are `app.db.UTCDateTime` columns: `timestamptz` on Postgres; written and read as
   aware UTC datetimes on both databases. A naive value is refused with an error, not assumed UTC.
2. **Every foreign key states its delete rule.** `ref()` is `ON DELETE RESTRICT`, the default:
   master data is deactivated, not deleted (DR-24), and history keeps who did what. `owned_by()` is
   `ON DELETE CASCADE`, only for a row that is part of its parent: a document's lines and
   extraction corrections, a sold item's recipe components.
3. **CHECK constraints are named** `ck_<table>_<column>[_<rule>]` and written to hold on both
   databases (`CAST(column AS NUMERIC)`, because exact quantities are text on SQLite). Alembic's
   batch mode keeps only named CHECKs when SQLite rebuilds a table.
4. **SQLite enforces foreign keys** (`PRAGMA foreign_keys=ON` on every app connection), so the
   suite fails on SQLite where Postgres would.

Left unconstrained on purpose: recorded balances (`stock_items.qty_base`, `inventory_counts.qty_base`,
`inventory_events.qty_after_base`) may go negative from system-observed events (DR-08);
`inventory_events.source` holds rows written by the removed snack-box module, and the ledger's
transactions replace that table (T1.16); `document_lines` quantities hold what the invoice says
until a reviewer decides what was received.

## Alternatives considered

- Read a naive value as UTC (the old convention): silent, and hides a `datetime.now()` local-time
  bug until the numbers are wrong.
- `SET NULL` for the "who did it" columns (`uploaded_by`, `decided_by`, `corrected_by`, `user_id`):
  loses attribution; users are deactivated, not deleted.
- Postgres's default `NO ACTION`: the same refusal, checked at the end of the statement; the task
  asks for an explicit rule, and `RESTRICT` says it.
- A CHECK on balances: contradicts DR-08.

## Consequences

- New tables use `UTCDateTime`, `ref()` or `owned_by()`, and named CHECKs; a test asserts every
  timestamp column is `UTCDateTime`, and the migration tests compare CHECK names and delete rules
  between the models and a migrated database on SQLite and Postgres.
- Deleting a tenant (account deletion, T4) must delete in foreign-key order; nothing deletes one today.
- Built in T1.01: migration `68c6fdbc31a7` (DM-2) converts the 31 timestamp columns, sets the rules
  on 44 foreign keys and adds ten CHECKs; probe PA5 is retired.
