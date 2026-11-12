# ADR-0040: The ledger and its posting service

- **Status:** Accepted, 2026-11-12 (the owner merged the T1.16-a pull request, #35: "Yes, merge it")
- **Decision ID:** none (implements T1.16 under C-35 and ID-01; DR-01..07, DR-19, INV-FR-15..18, -21, -22; DM-6 schema)
- **Tasks:** T1.16-a (schema, service); T1.16-b (concurrency on Postgres, reversal hardening); T1.17 (opening balances); T1.18 (writers move onto it)

## Context

Seven code paths overwrite `stock_items.qty_base` (audit C6, TR-02), so no quantity can be
explained. C-35 makes stock a ledger: every change a movement, balances derived. The data
architecture (§4) fixes the shape; this ADR records how it is built.

## Decision

1. **Three tables** (migration `632386e0eb3f`):
   - `inventory_transactions`: one per business event; kind, source, revision, actor (none for
     the system), occurred and recorded times, and the transaction it reverses. The idempotency
     key is unique per business.
   - `inventory_movements`: one per item × location × batch; signed base-unit quantity, unit-cost
     snapshot, allocation mode, reason, and the movement it reverses. The **database checks the
     sign per type**: receipts and production output positive; sales, waste, production input and
     usage negative; counts, adjustments, opening balances, POS corrections and reversals any sign
     but zero.
   - `stock_balances`: the projection per (location, item).
   Quantities are exact to four places (DR-10).
2. **One writer**, `post_transaction()` in `app/ledger/posting.py` (strict typing), with the rules
   in `app/transform/ledger.py`:
   - It validates the posting: kind, movement types, signs, four places, an aware time, and that
     each item is stocked at its location within the caller's scope.
   - It returns the earlier transaction for a key already used (DR-05), including when another
     posting wins the race at the database.
   - It locks the touched balance rows with `SELECT … FOR UPDATE` in item-then-location order,
     then writes the transaction, its movements and the balances together, or nothing.
   - The unit-cost snapshot is the given cost or, by default, the location's current cost (PD-08).
3. **Reversal**: `reverse_transaction()` posts the equal and opposite movements, naming the
   originals. Asking twice returns the first reversal; a reversal is never reversed (post an
   adjustment instead).
4. **Append-only**:
   - Nothing outside `app/ledger` names the three models (a test enforces it).
   - `update_scoped` refuses movements and transactions.
   - A reversal's reference to its original uses a new `earlier()` foreign key (`NO ACTION`): a
     referenced row still cannot be deleted, yet the table can be emptied whole. A row-by-row
     RESTRICT would block that on SQLite.
5. Nothing posts yet: opening balances arrive with T1.17 and the existing writers move in T1.18,
   so `stock_items.qty_base` stays what the screens show until then.

## Alternatives considered

- Keep updating `qty_base` and log events beside it: the audit's finding, unexplainable stock.
- Lock the item or stock row instead of the balance: the balance is the row every posting to that
  item and location must touch, which makes it the natural serialization point.
- Database triggers to keep balances: two dialects to keep in step, and the posting must also
  check scope and idempotency, which belong in Python.

## Consequences

- Every future stock change posts through `post_transaction()`; the real concurrent test on
  Postgres (AC-INV-07, 50 trials) and reversal of source records (DR-06) come with T1.16-b.
- `CLAUDE.md`'s schema rule gains `earlier()` for self-references in append-only tables.
- Built in T1.16-a with migration `632386e0eb3f`. T1.16-b (migration `4166ed9858d6`) added:
  - database checks that a reversal names the transaction and movement it undoes, and that
    nothing else names one;
  - `reversed_by()`, so source records can show they were corrected (DR-06);
  - concurrency tests that run only on Postgres, where two sessions really lock rows at once
    (ADR-0018); SQLite locks the whole file.
