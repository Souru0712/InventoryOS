# Execution log

One entry per task, newest first. Every entry: implemented · verified · files changed · open
issues · decisions · next task.

---

## 2026-11-12 — T1.17 Opening-balance migration

**Implemented**
- **Data migration** `a6eec9216078` (DM-6 backfill): for every stock row with a non-zero quantity,
  one `opening` transaction with one `opening_balance` movement of exactly that quantity
  (negative stays negative). It is costed at the row's current cost, posted by the system at
  migration time, and keyed `migration:opening:<item>:<location>`. Every pair gets its
  `stock_balances` row, zeros included.
- Re-runnable (existing keys and balances are skipped); written in SQL, not through
  `app/ledger`; downgrade clears the opening postings and balances and leaves stock rows alone.
- Until T1.18 the screens and old writers still use `stock_items.qty_base`; ADR-0041 records that
  T1.18 must catch the ledger up before readers switch.
- ADR-0041 (Proposed).

**Verified (locally, SQLite)**
- `tests/test_opening_balances.py` (3), over the demo business with one item below zero and one
  at zero:
  - **acceptance**: for every item × location, the balance and the sum of movements equal the
    previous quantity;
  - one opening per non-zero pair, each with its key, the system as actor and the row's cost;
  - zeros get a balance row and no movement;
  - running the backfill again posts nothing;
  - the ledger carries on from the opening balance.
- `tests/test_migrations.py`: through Alembic from pre-ledger rows (a shared item at two
  locations, one negative, and a zero), the expected movements and balances; the downgrade
  empties the ledger and keeps the stock rows. The upgrade over populated data still passes.
- Full suite 867 passed, 19 skipped (863 passed, 18 skipped before; the new skip is the Postgres half of the DM-6 migration test, run in CI); ruff, mypy and the docs checker clean.

**Files changed**
- Migration `a6eec9216078`, `tests/test_opening_balances.py`, ADR-0041 (new);
  `tests/test_migrations.py`; the ADR index, task index (T1.16 → Completed, T1.17 → In review),
  this log.

**Next task**
- T1.18 Route existing writers through the ledger.

---

## 2026-11-12 — T1.16 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #36 (T1.16-b) was merged with a merge commit
  (`264c203`). CI on its head `4c06f3d`, all five required jobs green: SQLite 863 passed, 18
  skipped; PostgreSQL 16 881 passed, 0 skipped. The concurrency tests ran and passed there,
  AC-INV-07 included. Lint and types, dependency audit and Docker image also passed. Same five
  green on `main` after the merge.
- With T1.16-a (#35): AC-INV-04, -05, -06 and -07 met.

**Files changed**
- Task index (T1.16 → Completed), this log.

---

## 2026-11-12 — T1.16-b Ledger concurrency and reversal hardening

**Implemented**
- **Reversal links**: a reversal names the transaction and movements it undoes, and nothing else
  may name one. This is enforced by the rules (`check_transaction`), the service, and two
  database checks (migration `4166ed9858d6`).
- **`reversed_by()`** finds a posting's reversal, so source records can show they were corrected
  (DR-06).
- No change to the posting path itself: the locking and idempotency built in T1.16-a are what
  the new tests prove under real concurrency.

**Verified**
- `tests/test_ledger_concurrency.py` (10). On Postgres only, as ADR-0018 requires for row-lock
  behaviour; locally they skip, CI's Postgres 16 job runs them with no skips allowed:
  - **AC-INV-07**: 50 trials of a receipt and a waste released at the same moment on one item
    and location, with both movements kept and the balance right after each;
  - the same key posted twice at once posts once (20 trials);
  - two postings naming two items in opposite orders do not deadlock (20 trials);
  - two first postings to a new item both land while racing to create its balance row (20
    trials);
  - a posting reversed twice at once is reversed once (10 trials).
  On both databases: only a reversal names what it undoes (service), and the database refuses a
  reversal transaction or movement that names nothing. A corrected posting finds its reversal,
  which keeps the source and the next revision; another business cannot reverse a posting.
- Locally the threaded tests were also run on SQLite outside the suite: they reach the database,
  which answers "database is locked", as the skip says.
- Full suite 863 passed, 18 skipped (858 passed, 13 skipped before; the 5 new skips are the Postgres-only concurrency tests, run in CI); ruff, mypy and the docs checker clean.

**Files changed**
- Migration `4166ed9858d6`, `tests/test_ledger_concurrency.py` (new); `app/ledger/posting.py`,
  `app/transform/ledger.py`, `app/models.py`; ADR-0040 (Accepted, updated), the index, task index
  (T1.16 → In review), this log.

**Next task**
- T1.17 Opening-balance migration (DM-6 backfill).

---

## 2026-11-12 — T1.16-a merged

**Verified**
- At the owner's request ("Yes, merge it") PR #35 was merged with a merge commit (`e167c9f`). CI
  on its head `f86b5d8`, all five required jobs green: SQLite 858 passed, 13 skipped; PostgreSQL 16
  871 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge.

**Decisions**
- ADR-0040 accepted with the merge.

**Files changed**
- ADR-0040 status and index, this log.

---

## 2026-11-12 — T1.16-a Ledger schema and posting service

**Implemented**
- **Tables** (migration `632386e0eb3f`, DM-6 schema):
  - `inventory_transactions`: one per business event; the idempotency key is unique per business.
  - `inventory_movements`: signed base-unit quantities with a unit-cost snapshot; a database
    CHECK enforces the sign per movement type.
  - `stock_balances`: the projection per location and item.
- **Posting service** (`app/ledger/posting.py`, rules in `app/transform/ledger.py`):
  - `post_transaction()` validates the posting, returns the earlier transaction for a used key
    (also when another posting wins the race), and locks the balance rows in item order.
  - It writes the transaction, movements and balances together.
  - `reverse_transaction()` posts the opposite movements naming the originals, once; a reversal
    is never reversed.
- **Append-only**: only `app/ledger` names the ledger models (a test enforces it);
  `update_scoped` refuses movements and transactions. Self-references use a new `earlier()`
  foreign key (NO ACTION), so a referenced row still cannot be deleted while the table can be
  emptied whole (a row-by-row RESTRICT blocked that on SQLite).
- Nothing posts yet: opening balances come in T1.17, writers move in T1.18.
- ADR-0040 (Proposed); ADR-0039 Accepted; `CLAUDE.md` states the rules.

**Verified (locally, SQLite)**
- `tests/test_ledger.py` (21):
  - the rules: zero, sign by type, four places, kinds and their movement types;
  - the database refuses a movement against its sign;
  - a posting writes movements and moves the balance together, with the cost snapshot kept
    through a later price change;
  - **AC-INV-06**: the same key twice posts once and answers the same;
  - the actor is the member unless the system posts;
  - nothing is posted for an item not stocked there, another business's item, or a time without
    a zone; a location-limited member posts only at their locations;
  - reversal undoes, names and happens once, and a reversal is not reversed;
  - **AC-INV-04**: 1,000 random sequences of receipts, sales, waste, production, counts,
    adjustments and reversals across two locations and two items, with balance = sum of
    movements = the pure model after every sequence;
  - **AC-INV-05**: no other module names the ledger models, and the scoped layer refuses updates.
- Full suite 858 passed, 13 skipped (837 before); ruff, mypy (strict for `app/ledger`) and the docs checker clean.

**Files changed**
- `app/ledger/__init__.py`, `app/ledger/posting.py`, `app/transform/ledger.py`, migration
  `632386e0eb3f`, `tests/test_ledger.py`, ADR-0040 (new); `app/models.py`, `app/access/scoped.py`;
  ADR-0039 (Accepted), the index, `CLAUDE.md`, task index (T1.15 → Completed, T1.16 → In progress),
  this log.

**Next task**
- T1.16-b: concurrent postings on Postgres (AC-INV-07) and reversal hardening.

---

## 2026-11-12 — T1.15 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #34 was merged with a merge commit (`64073b4`). CI
  on its head `6e32c36`, all five required jobs green: SQLite 837 passed, 13 skipped; PostgreSQL 16
  850 passed, 0 skipped (the pack-size backfill test included); lint and types; dependency audit;
  Docker image. Same five green on `main` after the merge. Acceptance met: AC-INV-02, AC-INV-03.

**Decisions**
- ADR-0039 accepted with the merge.

**Files changed**
- Task index (T1.15 → Completed), ADR-0039 status and index, this log.

---

## 2026-11-12 — T1.15 Units, packages and conversions

**Implemented**
- **Resolver** (`app/transform/conversions.py`, pure): any built-in unit or item package into the
  item's base unit, exact to four places. Within a dimension by constants (tbsp and tsp added);
  across dimensions only by the item's own conversion. Otherwise it refuses, naming the missing
  conversion (DR-11). `entered_base()` in `app/items.py` applies it to an item.
- **Packages** (`item_packages`): "case" = 6 × gal, or 24 × another package; nesting loops and
  names that clash with units are refused; one package says how deliveries arrive.
  **Conversions** (`item_unit_conversions`): "1 lb = 340 ml", one per pair of measures, both ways.
  Migration `6d82b619c66f`.
- **The delivery package sets every stock row's pack size**, which receiving and scanning read,
  so there is one source of truth until T2.11. The add-item form's pack size becomes the "pack"
  package. **Backfill**: existing pack sizes became packages ("pack", "pack 2"); the first is the
  delivery package.
- **Entry and editor**: counts accept any unit or package (`unit`); an unconvertible one answers
  400 with the reason. The item page lists packages with what each is in the base unit (exact),
  and edits packages and conversions. Every change is audited.
- ADR-0039 (Proposed); ADR-0038 Accepted; `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_units_packages.py` (20): **AC-INV-02** (2 lb = 907.1847 g; 1 case of 6 × 1 gal =
  22,712.4707 ml) in the resolver and through the count form; **AC-INV-03** (120 ml for a
  weight item refused, naming the missing conversion; after 1 lb = 340 ml it records 160.0914 g)
  in the resolver and through the count form, with a second conversion for the same measures
  refused; the JuJu press-yield fixture (1 lb ginger = 225 ml, both ways); nested packages, loops,
  unit-name clashes and unknown units; tbsp and tsp; seeded round-trip property tests within and
  across dimensions; the delivery package drives the stock rows' pack size (create, switch,
  remove, a package still in use refused); a package in another measure resolves once its
  conversion exists; a base-unit change after movement is refused.
- `tests/test_migrations.py`: pack sizes became packages (exact display unit or base unit, first
  one delivers). Isolation suite: the five new routes refuse another business's items, packages
  and conversions.
- In the browser (local server, test data): added a "case" of 6 L as the delivery package (shown
  as 6000 ml) and a conversion of 1 L = 1030 g on the item page.
- Full suite 837 passed, 13 skipped (811 passed, 12 skipped before; the new skip is the Postgres half of the pack-size backfill test, run in CI); ruff, mypy and the docs checker clean.

**Files changed**
- `app/transform/conversions.py`, migration `6d82b619c66f`, `tests/test_units_packages.py`,
  ADR-0039 (new); `app/units.py`, `app/models.py`, `app/items.py`, `app/access/scoped.py`,
  `app/web/catalog.py`, `app/web/dashboard.py` (count unit), `app/web/templating.py` (`num`, `unit`
  filters), `app/web/templates/app/catalog_item.html`, `tests/test_isolation.py`,
  `tests/test_migrations.py`; ADR-0038 (Accepted), the index, `CLAUDE.md`, task index (T1.14 →
  Completed, T1.15 → In review), this log.

**Next task**
- T1.16 Ledger schema and posting service (depends on T1.01, T1.13, T1.15).

---

## 2026-11-12 — T1.14 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #33 was merged with a merge commit (`b0f42c2`). CI
  on its head `a94573a`, all five required jobs green: SQLite 811 passed, 12 skipped; PostgreSQL 16
  823 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: AC-INV-01.

**Decisions**
- ADR-0038 accepted with the merge.

**Files changed**
- Task index (T1.14 → Completed), ADR-0038 status and index, this log.

---

## 2026-11-12 — T1.14 Item management UI

**Implemented**
- **Screens at `/app/catalog`** ("Items" in the menu; `/app/items/new` redirects there):
  - a list with search and filters (category, supplier, status, low stock, expiring within 7
    days of each location's business date, location);
  - an item page: details, per-location levels (reorder point, par, minimum, storage area,
    stocked), stocking it at another location, barcodes, deactivate or bring back;
  - a categories page: add, rename, merge, archive, restore.
  Looking takes `inventory.view`; changing takes `item.edit` and the subscription's write rule.
- **Service** (`app/items.py`, rules in `app/transform/items.py`): items created at several
  locations at once with opening quantities (stock rows linked by the T1.13 hook); definitions
  and levels written to the split tables; names unique in any case; the unit picked decides the
  dimension, locked once stock has moved (INV-FR-14); the old form's pack size, delivery unit,
  lead time and codes stay on the new form. Every change is audited.
- **Categories** (`inventory_categories`, migration `9c903ba85a8f`): the T1.13-a free-text
  category becomes a link; typing a name reuses a category in any case; merging moves items and
  archives the source. Items gain notes.
- **Deactivated items leave every pick list**: `active_stock()` also requires the item active.
- ADR-0038 (Proposed); ADR-0037 Accepted; ADR-0035 updated; `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_catalog.py` (13), **AC-INV-01**: an item is created once and stocked at the chosen
  locations with opening counts; names are unique in any case; editing changes it everywhere it
  is shown; the dimension locks once stock has moved; levels are set per location in the item's
  unit and validated; a deactivated item keeps its stock and counts, leaves the stock list and
  pickers, shows under inactive, and comes back; stocking at another location adds an empty row
  once; the old address redirects; the old form's pack size, delivery unit, lead time and codes
  are still set. **INV-FR-10**: search by name, category, supplier, low stock,
  expiring, location; a foreign category is 404. **INV-FR-03**: categories created, renamed,
  merged, archived (out of the picker, still on its items) and restored; typed names reuse a
  category. Employees can look but not change. Audit events for each change.
- Isolation suite: the eleven new routes that take an id refuse another business's items,
  settings, categories and locations. Permission suite: `inventory.view` or `item.edit`.
- In the browser (local server, test data): added an item at two locations, saw both on its page,
  saved a par level and storage area, and browsed the list and categories.
- Full suite 811 passed, 12 skipped (787 before); ruff, mypy and the docs checker clean.

**Files changed**
- `app/items.py`, `app/web/catalog.py`, templates (`app/catalog.html`, `app/catalog_item.html`,
  `app/categories.html`, `app/item_form.html` rewritten), migration `9c903ba85a8f`,
  `tests/test_catalog.py`, ADR-0038 (new); `app/models.py`, `app/catalog.py`,
  `app/transform/items.py`, `app/access/scoped.py`, `app/audit.py`, `app/web/dashboard.py` (old
  form removed), `app/main.py`, templates (`base.html`, `app/dashboard.html`,
  `app/receiving_review.html`), `tests/test_isolation.py`, and three tests that used the old form
  (`test_locations.py`, `test_memberships.py`, `test_schema_hygiene.py`); ADR-0037 (Accepted), ADR-0035, the
  index, `CLAUDE.md`, task index (T1.13 → Completed, T1.14 → In review), this log.

**Next task**
- T1.15 Units, packages and conversions.

---

## 2026-11-12 — T1.13 completed

**Verified**
- The owner merged PR #32 (T1.13-b) with a merge commit (`0d1d70f`). CI on its head `a37d599`,
  all five required jobs green: SQLite 787 passed, 12 skipped; PostgreSQL 16 799 passed, 0
  skipped; lint and types; dependency audit; Docker image. Same five green on `main` after the
  merge. With T1.13-a (#31): every stock item maps to exactly one item and item location, and
  existing screens are unchanged.

**Decisions**
- ADR-0037 accepted with the merge.

**Files changed**
- Task index (T1.13 → Completed), ADR-0037 status and index, this log.

---

## 2026-11-12 — T1.13-b Item model split: readers moved

**Implemented**
- **Reads come from the split rows**: a stock item's `name`, `category`, `dimension`,
  `stock_unit` and `tracks_expiry` read from its inventory item, and `reorder_point_base`,
  `par_level_base` and `is_active` from its item location (`SplitField`, `app/models.py`). The
  table columns keep their names; the mapped legacy attributes are `_name` and so on (DM-16).
- **Writes reach both**: setting one of those fields updates the split row (with `base_unit` and
  `tracks_batches`) and the legacy column; the flush hook only links new stock items now.
- **Queries moved**: the active filter and the name order use the split tables
  (`active_stock()`, `by_item_name()`) in the stock list, derived-quantity refresh and both
  receiving lists. Both relationships load with the stock item, so no extra queries.
- ADR-0037 (Proposed); ADR-0036 Accepted and updated; ADR-0010 updated; `CLAUDE.md` states the
  rule.

**Verified (locally, SQLite)**
- `tests/test_item_split.py` (12): what the item screens will change directly (the item's name
  and unit, the location's reorder point and active flag) is what the stock list shows, sorted by
  the item's name, without consulting the legacy column; writing a stock item fills the split row
  and the legacy column, derived fields included; no product query filters or sorts on a legacy
  column. The T1.13-a tests still pass.
- Acceptance (T1.13): every stock item maps to exactly one item and item location (DM-5 tests);
  existing screens unchanged: the full suite passes untouched. Full suite 787 passed, 12 skipped (784 before); ruff, mypy and
  the docs checker clean.

**Files changed**
- ADR-0037 (new); `app/models.py`, `app/catalog.py`, `app/orchestration/views.py`,
  `app/orchestration/receiving.py`, `app/web/receiving.py`, `tests/test_item_split.py`;
  ADR-0036 (Accepted), ADR-0010, the index, `CLAUDE.md`, task index (T1.13 → In review), this log.

**Next task**
- T1.14 Item management UI (its dependencies T1.13 and T1.06 complete once this merges).

---

## 2026-11-12 — T1.13-a merged

**Verified**
- The owner merged PR #31 with a merge commit (`1e37e69`). CI on its head `4f14bc2`, all five
  required jobs green: SQLite 784 passed, 12 skipped; PostgreSQL 16 796 passed, 0 skipped (the
  DM-5 backfill test included); lint and types; dependency audit; Docker image. Same five green
  on `main` after the merge.

**Decisions**
- ADR-0036 accepted with the merge.

**Files changed**
- ADR-0036 status and index, this log.

---

## 2026-11-12 — T1.13-a Item model split: schema, backfill and mirror

**Implemented**
- **Tables** (migration `e3f7a1c9d5b2`, DM-5): `inventory_items` (business, slug, name, type,
  category, dimension, base and display unit, batch and expiry tracking, shelf life, active;
  unique per business and slug) and `item_locations` (location, item, reorder point, par, min,
  storage area, active; unique per location and item). `stock_items` gains links to both.
- **Backfill**: every stock item becomes one item location; the same slug across locations is
  one item when measured the same way, otherwise its own item with the dimension in the slug;
  anything with a shelf price is `resale`, the rest `raw`.
- **Mirror** (`app/catalog.py`): a `before_flush` listener links each new stock item to its item
  (found or created) and a new item location, and carries changed definition fields to the item
  and changed settings to the location; quantity changes write nothing there. Scoped reads:
  `inventory_items()` (business-wide) and `item_locations()` (location scope). Rules in
  `app/transform/items.py`.
- No screen changes: every reader still uses `stock_items` (T1.13-b moves them). ADR-0036
  (Proposed); ADR-0010 updated; ADR-0035 Accepted; `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_item_split.py` (9): a new stock item gets its item and settings; the same item at
  two locations is one item with two settings, also when added in one flush (as the Square sync
  does); a slug measured differently becomes its own item; definition and setting changes reach
  the split tables; a quantity change writes nothing there; the type and slug rules; **DM-5 over
  the demo business**: every stock item maps to exactly one item and location with matching
  settings, and every item has a location; items are business-wide while settings follow the
  location scope and another business's rows stay out.
- `tests/test_migrations.py`: DM-5 backfill over pre-split rows (two locations, a shared slug, a
  dimension clash, a resale item) gives the expected items and links, and the downgrade keeps
  every stock item; the model/migration diff stays empty.
- Acceptance: existing screens unchanged: the full suite passes untouched. Full suite 784 passed, 12 skipped (774 passed, 11 skipped before; the new skip is the Postgres half of the DM-5 migration test, run in CI);
  ruff, mypy and the docs checker clean.

**Files changed**
- `app/catalog.py`, `app/transform/items.py`, migration `e3f7a1c9d5b2`,
  `tests/test_item_split.py`, ADR-0036 (new); `app/models.py`, `app/access/scoped.py`,
  `tests/test_migrations.py`; ADR-0010, ADR-0035 (Accepted), the index, `CLAUDE.md`, task index
  (T1.12 → Completed, T1.13 → In progress), this log.

**Next task**
- T1.13-b: move the readers to the split tables.

---

## 2026-11-12 — T1.12 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #30 was merged with a merge commit (`0530973`). CI
  on its head `cee0737`, all five required jobs green: SQLite 774 passed, 11 skipped; PostgreSQL 16
  785 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: AC-ADM-04. Every M1 task (T1.01–T1.12) is complete; the
  Stage 1 exit criteria (gates G1, G2) also need M2.

**Decisions**
- ADR-0035 accepted with the merge.

**Files changed**
- Task index (T1.12 → Completed), ADR-0035 status and index, this log.

---

## 2026-11-11 — T1.12 Audit events

**Implemented**
- **`audit_events`** (migration `d8a2b6e4f1c3`): business, location (empty for business-wide
  changes), time, actor kind (user, system, support) and actor, action, subject, a one-line
  summary, the changed fields before and after, client address; indexed by (business, time).
- **`record_audit()`** (`app/audit.py`), called for: the business profile; locations added,
  changed, archived, restored; invitations sent and withdrawn, people joining (recorded as
  themselves), role or scope changes, removals; Square connected or reconnected; plan or status
  changes from Stripe (recorded as the product). A save that changes nothing records nothing.
- **Append-only**: only `record_audit()` inserts; `update_scoped` refuses the table; a test fails
  if any other module names `AuditEvent`. Nothing deletes events (SEC-14: kept at least a year).
- **History screen** (`/app/audit`, `audit.view`, linked from Settings): newest first, filter by
  kind of change and person, 100 at a time with older pages. Owners and Administrators see
  everything; Managers only their locations' changes, never business-wide ones.
- ADR-0035 (Proposed); ADR-0034 Accepted; `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_audit.py` (10): each audited service writes one event: the business profile
  (with before and after, actor and address; an unchanged save writes nothing), each location
  change against its location, each team change by who made it (the person joining for a join),
  Square connecting, a Stripe plan change as the product (a repeat writes nothing).
  **AC-ADM-04**: settings, role and connection changes appear in the history, with filters by
  kind and person; Managers see only their locations (a location-limited one only theirs) and
  no business-wide changes; Employees get 403; older pages work. Append-only: the scoped layer
  refuses updates; no other module names the table.
- Isolation suite: `?person=` from another business answers 404; permission suite:
  `audit.view`.
- In the browser (local server, test data): changed the business name in Settings, opened the
  history from Settings, and saw the change with its old and new name.
- Full suite 774 passed, 11 skipped (763 before); ruff, mypy and the docs checker clean.

**Files changed**
- `app/audit.py`, `app/web/audit.py`, `app/web/templates/app/audit.html`, migration
  `d8a2b6e4f1c3`, `tests/test_audit.py`, ADR-0035 (new); `app/models.py`, `app/access/scoped.py`,
  `app/web/locations.py`, `app/team.py`, `app/web/team.py`, `app/web/dashboard.py`,
  `app/orchestration/stripe_events.py`, `app/main.py`, `app/web/templates/app/settings.html`,
  `tests/test_isolation.py`, `tests/test_permissions.py`; ADR-0034 (Accepted), the index,
  `CLAUDE.md`, task index (T1.11 → Completed, T1.12 → In review), this log.

**Next task**
- With this merged, every M1 task (T1.01–T1.12) is complete; gate G1's sign-off is the owner's.
  Next: T1.13 Item model split (M2).

---

## 2026-11-11 — T1.11 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #29 was merged with a merge commit (`f0c5cd6`). CI
  on its head `1701244`, all five required jobs green: SQLite 763 passed, 11 skipped; PostgreSQL 16
  774 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: an enrolled person cannot sign in without a code or recovery
  code; a recovery code works once.
- The PR's first run was lost in a GitHub Actions incident (queued, could not be cancelled). A
  manual run passed on the same commit; with the owner's approval the PR was closed and reopened
  to start a fresh PR run, which passed and satisfied the required checks.

**Decisions**
- ADR-0034 accepted with the merge, approving the code limit.

**Files changed**
- Task index (T1.11 → Completed), ADR-0034 status and index, this log.

---

## 2026-11-11 — T1.11 TOTP MFA

**Implemented**
- **Enrolling** (`/app/settings/mfa`, linked from Settings): a QR code (`segno`, inline SVG) and
  setup key for an authenticator app; one matching code turns it on, shows ten recovery codes once,
  and ends every other session. The secret is encrypted with the application key (`app/crypto.py`);
  recovery codes are stored as SHA-256 hashes (migration `c5e1f0a94b2d`).
- **Signing in**: after a correct password, an enrolled person gets only a five-minute signed mark
  and the code page (`/login/code`); the session starts when an authenticator or recovery code
  matches. Codes are good for their 30-second step and one either side, never twice; a recovery
  code works once. 5 wrong codes in 15 minutes lock the step (1 minute, doubling to an hour).
- **Managing**: new recovery codes need a code; turning it off needs the password and a code;
  `python -m app.cli reset-mfa --email` turns it off for a locked-out person and ends their
  sessions. Owners and Administrators without it see a prompt on every page (PD-20); it is not
  required. An enrolled account can join an invitation only while signed in.
- Dependencies `pyotp` 2.10.0 (ID-23) and `segno` 1.6.6 (owner approved 2026-11-11), locked in
  `uv.lock`. ADR-0034 (Proposed); ADR-0033 Accepted; ADR-0023 notes the arrival; `CLAUDE.md`
  states the rule.

**Verified (locally, SQLite)**
- `tests/test_mfa.py` (16), acceptance: an enrolled person cannot sign in without a code (no
  session row after the password alone); a recovery code works once. Also: enrolling takes a code
  and shows ten codes once, with the secret encrypted and only hashes stored; the setup key stays
  the same across reloads until confirmed; turning it on signs out other devices; no encryption
  key, no enrolment; a code works only once (replay refused); guessing is stopped (429); the code
  step needs a fresh password step; turning it off needs password and code; new recovery codes
  replace the old; the Owner prompt shows until it is on and never on its own pages; support can
  reset it; an invitation never signs in an enrolled account by password; the step window and
  replay rules at fixed times; recovery code format and hashing.
- In the browser (local server, test data): the Owner prompt, the QR code and setup key,
  enrolling with a computed code, the recovery codes, then signing out and in: the password led
  to the code page with `/app` still closed, and a code finished the sign-in.
- Full suite 763 passed, 11 skipped (747 before); ruff, mypy and the docs checker clean.

**Decision for the owner**
- The code limit (5 wrong codes in 15 minutes, 1 minute doubling to an hour) is security
  configuration: merging this pull request approves it.

**Files changed**
- `app/crypto.py`, `app/mfa.py`, `app/transform/mfa.py`, `app/web/mfa.py`, templates
  (`app/mfa.html`, `auth/mfa_challenge.html`), migration `c5e1f0a94b2d`, `tests/test_mfa.py`,
  ADR-0034 (new); `app/models.py`, `app/web/auth.py`, `app/web/team.py`, `app/security.py`,
  `app/throttle.py`, `app/config.py`, `app/cli.py`, `app/main.py`, templates (`base.html`,
  `app/settings.html`, `auth/accept_invitation.html`), `pyproject.toml`, `uv.lock`,
  `tests/test_permissions.py`; ADR-0033 (Accepted), ADR-0023, the index, `CLAUDE.md`, task index
  (T1.10 → Completed, T1.11 → In review), this log.

**Next task**
- T1.12 Audit events.

---

## 2026-11-11 — T1.10 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #28 was merged with a merge commit (`f954294`). CI
  on its head `427f8da`, all five required jobs green: SQLite 747 passed, 11 skipped; PostgreSQL 16
  758 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: AC-STF-01, AC-STF-04, AC-STF-05.

**Decisions**
- ADR-0033 accepted with the merge, approving the invitation limit.

**Files changed**
- Task index (T1.10 → Completed), ADR-0033 status and index, this log.

---

## 2026-11-11 — T1.10 Invitations and team management

**Implemented**
- **Invitations** (`invitations`, `invitation_locations`, migration `a3c7d133e37d`; `app/team.py`):
  email, role, all locations or a list, inviter, the SHA-256 of a random token, 7-day expiry,
  accepted or withdrawn. Inviting an address again withdraws its earlier link. The email links
  to a page whose button posts the token.
- **Accepting** (`/invitations/accept`): only as the invited address. Signed in as it: one click.
  Signed in as someone else: refused. Signed out with an account: its password (throttled like
  sign-in). No account: a new password creates one, already confirmed. Joining switches the
  browser to that business; a removed member invited back gets the new role.
- **Team page** (`/app/team`, `team.manage`, linked from Settings): members with role and
  locations, change role and scope, remove; pending and lapsed invitations with send again and
  withdraw. Rules in `app/transform/team.py`: only `owner.manage` touches an Owner; the last active
  Owner cannot be demoted or removed; nobody changes their own access there; Owners and
  Administrators always cover every location.
- **Removal** revokes the membership and ends that business's sessions at once. Inviting needs a
  confirmed address and is throttled at 20 an hour per person (proposed; merging approves it).
  Removing and withdrawing work even when the subscription is read-only.
- ADR-0033 (Proposed); ADR-0032 Accepted; `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_team.py` (21): **AC-STF-01**: an invitation creates the membership with its role,
  confirms the address, stores only the hash, and works once; it lasts 7 days and is dead after;
  only the invited address can use it; an existing account joins with its password and keeps its
  other business; a signed-in invitee joins in one click; a location-limited invitation gives
  exactly that location; inviting again or withdrawing kills the earlier link; an existing member
  is not invited; inviting waits for a confirmed address; 20 invitations an hour then a wait;
  guessed links are stopped. **STF-FR-06**: a new role applies on the member's next request.
  **AC-STF-04**: removal ends access and every session at once; a removed member invited back
  gets the new role. **AC-STF-05**: the last Owner cannot be demoted or removed, whoever asks; an
  Administrator cannot add, change or remove an Owner; an Owner can add a second Owner who can
  then step the first down; nobody changes their own access; the pure rules.
- Isolation suite: the four team routes refuse another business's members, invitations and
  locations (404) and change nothing; permission suite: each route needs `team.manage`.
- In the browser (local server, test data): invited a Manager limited to one location, accepted
  as a new account, landed in the business with the Manager menu; the team page answered 403.
- Full suite 747 passed, 11 skipped (722 before); ruff, mypy and the docs checker clean.

**Decision for the owner**
- The invitation limit (20 an hour per person) is security configuration: merging this pull
  request approves it.

**Files changed**
- `app/team.py`, `app/transform/team.py`, `app/web/team.py`, migration `a3c7d133e37d`, templates
  (`app/team.html`, `auth/accept_invitation.html`, `email/invitation.*`), `tests/test_team.py`,
  ADR-0033 (new); `app/models.py`, `app/access/` (scoped, system), `app/security.py` (`login_user`
  returns the session), `app/throttle.py`, `app/main.py`, `app/web/templates/app/settings.html`,
  `tests/test_isolation.py`, `tests/test_permissions.py`; ADR-0032 (Accepted), the index,
  `CLAUDE.md`, task index (T1.09 → Completed, T1.10 → In review), this log.

**Next task**
- T1.11 TOTP MFA (needs a new dependency: owner approval, ID-23).

---

## 2026-11-11 — T1.09 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #27 was merged with a merge commit (`4394192`). CI
  on its head `96f2faa`, all five required jobs green: SQLite 722 passed, 11 skipped; PostgreSQL 16
  733 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: AC-SAAS-03; probe R8 retired.

**Decisions**
- ADR-0032 accepted with the merge, approving its limits.

**Files changed**
- Task index (T1.09 → Completed), ADR-0032 status and index, this log.

---

## 2026-11-11 — T1.09 Login throttling

**Implemented**
- **Counters in the database** (`throttles`, migration `60bee21fefbd`; `app/throttle.py`): one row
  per rule and subject, keyed by the rule and the SHA-256 of the email or IP address, so every
  server counts the same attempts and neither is stored. A nightly job prunes counters untouched
  for a day.
- **Rules** (proposed; merging approves them): sign-in 5 wrong passwords per account and 20 per
  address in 15 minutes; reset emails 3 an hour per account and 10 per address; confirmation
  resends 3 an hour per account; links that did not work 10 per address in 15 minutes. Past the
  limit the lock starts at the rule's first lock and doubles with every further attempt (sign-in
  and links: 1 minute up to an hour). Refused attempts are not counted.
- **Replies reveal nothing**: a locked sign-in answers 429 with Retry-After and one sentence for
  every account, real or not; a throttled reset request shows the usual page and sends nothing; a
  throttled resend says to check the inbox. A good sign-in clears the account's count, not the
  address's.
- Probe R8 retired (its CSRF half went in T1.07). ADR-0032 (Proposed); ADR-0031 Accepted;
  `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_throttling.py` (14): **AC-SAAS-03**: five wrong passwords lock the account even
  against the right one (429, Retry-After); counters hold no email or address; the lock ends and a
  good sign-in starts the count again; each guess past the limit doubles the lock; one address
  guessing at many accounts is stopped while another address is not; guesses from many addresses
  add up for one account (the count is shared, not per server); a locked unknown account reads the
  same as a real one; a good sign-in does not wipe the address's count; reset emails stop after
  three an hour without the page changing; one address cannot send resets to everyone;
  confirmation resends stop after three; guessing at links is stopped, while a real link still
  works from another address; old counters are pruned and live ones kept.
- Full suite 722 passed, 11 skipped (708 before); ruff, mypy and the docs checker clean.

**Decision for the owner**
- The limits are security configuration: merging this pull request approves them; say if any
  should differ.

**Files changed**
- `app/throttle.py`, migration `60bee21fefbd`, `tests/test_throttling.py`, ADR-0032 (new);
  `app/models.py`, `app/web/auth.py`, `app/web/accounts.py`, `app/orchestration/scheduler.py`,
  `app/web/templates/base.html`; probe file (R8 retired); ADR-0031 (Accepted), the index,
  `CLAUDE.md`, task index (T1.08 → Completed, T1.09 → In review), this log.

**Next task**
- T1.10 Invitations and team management.

---

## 2026-11-07 — T1.08 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #26 was merged with a merge commit (`664fb5d`). CI
  on its head `ba0bab7`, all five required jobs green: SQLite 708 passed, 11 skipped; PostgreSQL 16
  719 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: AC-SAAS-02, AC-SAAS-04.

**Decisions**
- ADR-0031 accepted with the merge.

**Files changed**
- Task index (T1.08 → Completed), ADR-0031 status and index, this log.

---

## 2026-11-07 — T1.08 Email verification, password reset and change

**Implemented**
- **Single-use emailed links** (`auth_tokens`, migration `6fbdcbb20640`): only the token's hash
  is stored; a new link ends the earlier ones for the same purpose; confirmation links last 48
  hours, reset links one hour. A link opens a page whose button posts the token, so a mail
  scanner fetching links cannot use one up.
- **Confirming the address** (SAAS-A-02): sign-up sends the link (`users.email_verified_at` is
  set when it is used). Until then connecting Square, uploading or re-reading an invoice and
  reading an expiry photo answer 403 (`require_verified`); a banner explains and offers to send
  the link again. Inviting staff joins the gate in T1.10.
- **Forgotten password** (SAAS-A-05): the request answers the same whether or not the account
  exists; the new password ends every session and confirms the address; a password under 10
  characters is refused without using the link up.
- **Changing the password** in settings: the current one is required; every other device is
  signed out and this one continues on a new session.
- Login links to "Forgot your password?"; `python -m app.cli verify-email --email` confirms an
  address by hand; the demo owner is created confirmed. Existing accounts start unconfirmed.
- ADR-0031 (Proposed); ADR-0030 Accepted (the owner's merge approved its timeouts); `CLAUDE.md`
  and README updated.

**Verified (locally, SQLite)**
- `tests/test_accounts.py` (13): sign-up sends a hashed, 48-hour link and leaves the address
  unconfirmed; **AC-SAAS-02**: an unconfirmed account gets 403 for an invoice upload, an expiry
  photo and connecting Square; the link page does not use the link, the post confirms once, a
  second use fails; a new link replaces the old; an expired link does nothing; the reset request
  reads the same for known and unknown addresses and mails only the known one; **AC-SAAS-04**: a
  reset link works once within the hour, ends every session, and the old password stops working;
  a short password keeps the link usable; an expired reset link points to a new one; a
  confirmation link cannot reset a password; changing the password signs out every other device
  and keeps this one; wrong or short changes are refused and change nothing.
- Fixtures that sign up and then upload confirm the address first (`tests/accounts.py`); the
  route declaration test lists the new public and personal routes.
- Full suite 708 passed, 11 skipped (695 before); ruff, mypy and the docs checker clean. The demo
  seeder and `verify-email` ran on a scratch database.

**Open issues**
- These forms are not rate-limited yet (T1.09).

**Files changed**
- `app/web/accounts.py`, migration `6fbdcbb20640`, templates `auth/verify_email.html`,
  `auth/forgot_password.html`, `auth/reset_password.html`, `email/verify_email.{html,txt}`,
  `email/reset_password.{html,txt}`, `tests/accounts.py`, `tests/test_accounts.py`, ADR-0031 (new);
  `app/models.py`, `app/security.py`, `app/main.py`, `app/cli.py`, `app/web/{auth,dashboard,
  receiving,scanning}.py`, templates `base.html`, `auth/login.html`, `app/settings.html`; seven
  test files' fixtures; ADR-0030 (Accepted), the index, `CLAUDE.md`, `README.md`, task index (T1.07
  → Completed, T1.08 → In review), this log.

**Next task**
- T1.09 Login throttling.

---

## 2026-11-07 — T1.07 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #25 was merged with a merge commit (`5805170`),
  approving the 12-hour idle and 14-day session limits. CI on its head `199c78e`, all five
  required jobs green: SQLite 695 passed, 11 skipped; PostgreSQL 16 706 passed, 0 skipped; lint
  and types; dependency audit; Docker image. Same five green on `main` after the merge.
  Acceptance met: a revoked member is blocked on the next request (AC-STF-04); a POST without the
  token is refused; probe R8's CSRF half retired.

**Decisions**
- ADR-0030 accepted with the merge, timeouts included.

**Files changed**
- Task index (T1.07 → Completed), ADR-0030 status and index, this log.

---

## 2026-11-07 — T1.07 Server-side sessions, CSRF, POST logout

**Implemented**
- **Sessions are rows** (`sessions`, migration `8fadf35f1453`): token hash, user, the business
  chosen, created, last seen, absolute end, revoked. The signed cookie holds only the random
  token and the form token; the chosen business moved from the cookie to the row.
- **Timeouts** (proposed; merging approves them): 12 hours without a request, 14 days after
  sign-in; both are settings (`SESSION_IDLE_MINUTES`, `SESSION_MAX_DAYS`). Last-seen is written at
  most every 5 minutes. The cookie's lifetime matches the absolute end.
- **Rotation and revocation**: a new token at every sign-in, ending the browser's previous
  session; logout ends the row; "Log out all devices" on the settings page ends every row of the
  person; `revoke_sessions` (for password changes, T1.08) and `revoke_membership` (revokes the
  membership and ends the sessions acting for it).
- **CSRF**: an ASGI middleware refuses a state-changing request without this session's token
  (403, "reload the page"); all 25 POST forms carry it as a hidden field and the scanner's four
  fetch calls as a header; Square and Stripe webhooks are exempt (signed). A new form token at
  every sign-in.
- **Logout is a POST** (a button in the header); `GET /logout` answers 405.
- Tests post like a browser: `tests/browser.py` adds the session's token unless a test sends its
  own; the async concurrency test reads the token from the page.
- Probe R8's CSRF half retired (its throttling half stays for T1.09). ADR-0030 (Proposed);
  ADR-0029 Accepted; `CLAUDE.md`, README and `.env.example` updated.

**Verified (locally, SQLite)**
- `tests/test_sessions.py` (19): the cookie holds only the token, the row only its hash; sign-in
  rotates and ends the previous session; logout ends it for good (a copied cookie no longer
  works); logout needs a POST; idle and absolute timeouts end a session, use keeps it alive;
  logging out everywhere ends every device; the chosen business is kept on the server;
  **AC-STF-04**: revoking a member blocks their very next request and ends their session, others
  unaffected; every POST form in the templates carries the token; the login page carries one;
  posts with no token, a forged one or another session's are refused and change nothing; the
  token works as a form field in a multipart upload; sign-in issues a new token; webhooks need
  none; the refusal says what to do.
- Full suite 695 passed, 11 skipped (676 before); ruff, mypy and the docs checker clean. Existing suites run unchanged through
  the browser shim, so every earlier POST now passes the CSRF check.

**Decision for the owner**
- The two timeouts (12 hours idle, 14 days absolute) are security configuration: merging this
  pull request approves them; say if they should differ.

**Open issues**
- Everyone signs in again once after this deploys (older cookies carry no session token).

**Files changed**
- `app/web/csrf.py`, `migrations/versions/8fadf35f1453_server_side_sessions.py`,
  `tests/browser.py`, `tests/test_sessions.py`, ADR-0030 (new); `app/models.py`, `app/config.py`,
  `app/security.py`, `app/main.py`, `app/web/auth.py`, `app/web/templating.py`, 14 templates (the
  form token, the logout button, the sessions section, the scanner's header); tests `conftest.py`,
  `test_async_correctness.py`, `test_permissions.py`, five tests moved to POST logout; probe file
  (R8's CSRF half); ADR-0029 (Accepted), the index, `CLAUDE.md`, `README.md`, `.env.example`, task
  index (T1.06 → Completed, T1.07 → In review), this log.

**Next task**
- T1.08 Email verification, password reset and change.

---

## 2026-11-07 — T1.06 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #24 was merged with a merge commit (`e34146d`). CI
  on its head `d1837cd`, all five required jobs green: SQLite 676 passed, 11 skipped; PostgreSQL 16
  687 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: AC-STF-02 (every role against every declared route),
  AC-STF-08, AC-CMD-03; no route without a permission declaration.

**Decisions**
- ADR-0029 accepted with the merge.

**Files changed**
- Task index (T1.06 → Completed), ADR-0029 status and index, this log.

---

## 2026-11-06 — T1.06 Permission keys and role presets

**Implemented**
- **Catalogue** (`app/access/permissions.py`, strict mypy): every R1 key from
  USER_ROLES_AND_PERMISSIONS §3 and the five R1 presets. Owner holds every key; Employees post
  receivings (PD-19) but do not enter sales (PD-37) and have no void rights until the undo window
  exists; Manager, Administrator and Owner nest. An unknown key fails when the module loads.
- **`require_permission(key)`** on every product route (46 route and method pairs): 403 for a role
  without the key; it also gives the route its `OrgContext`, which now carries the role's
  permissions (`org.can`). The public site, sign-in, signed webhooks and three personal routes are
  listed as such. ruff is told that `require_permission(...)` in a parameter default is safe, as
  for FastAPI's own markers.
- **Costs** (CMD-FR-14, §5): the dashboard, expiring and receiving pages hide money for a role
  without `inventory.view_costs` (the dashboard's health block, whose findings are stated in money,
  with it); the scanner's JSON drops cost fields; a role without `receiving.edit_costs` cannot change
  a line's cost when posting a delivery. Menus and buttons follow the role.
- **Location scope everywhere** (DR-35): counts, lots, alerts, codes, aliases, events, markdowns,
  recipe lines, invoice lines and corrections are filtered through their item's or document's
  location for a location-limited member, replacing T1.02's refusal; every page works for one.
- ADR-0029 (Proposed until this pull request is merged); ADR-0026 and ADR-0028 updated; ADR-0028
  Accepted; `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_permissions.py` (13): the presets follow the matrix (owner holds all; nesting;
  Employee and Read-only sets); a mistyped key fails; cost fields are removed at any depth; every
  route declares a key or is listed as public or personal; **AC-STF-02**: each of the five roles
  against every declared route, 403 exactly where the role lacks the key and never a 403 or 500
  where it holds it; **AC-CMD-03**: an Employee's dashboard, expiring and receiving pages carry no
  amount and the scanner's JSON no cost (the owner's do); the menu follows the role; an Employee's
  cost override is ignored; **AC-STF-08**: a Manager of one location gets every page without the
  other location's stock, and 404 for its items, codes, reorder page and CSV, while an owner sees
  both.
- `tests/test_scoped_access.py`: counts follow their item's location under a limit; every tenant
  table resolves under a limit.
- Full suite 676 passed, 11 skipped (662 before); ruff, mypy and the docs checker clean.

**Open issues**
- Keys without a route yet (production, waste, sales, exceptions, team, audit) are exercised when
  their routes arrive; the declaration and matrix tests pick new routes up automatically.
- The health score and its findings are hidden from roles without costs rather than restated
  without money.

**Files changed**
- `app/access/permissions.py`, `tests/test_permissions.py`, ADR-0029 (new); `app/access/context.py`,
  `scoped.py`, `app/security.py`, every module in `app/web/` with product routes,
  `app/web/templating.py`, templates `base.html`, `dashboard.html`, `expiring.html`,
  `receiving.html`, `receiving_review.html`, `settings.html`; `pyproject.toml` (ruff); tests
  `test_scoped_access.py`; ADR-0026, ADR-0028 (Accepted), the index, `CLAUDE.md`, task index (T1.05
  → Completed, T1.06 → In review), this log.

**Next task**
- T1.07 Server-side sessions, CSRF, POST logout.

---

## 2026-11-06 — T1.05 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #23 was merged with a merge commit (`35db5ee`). CI
  on its head `7d6cea5`, all five required jobs green: SQLite 662 passed, 11 skipped; PostgreSQL 16
  673 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: PA3 inverted and retired; a user in two businesses sees only the
  selected one (AC-STF-07).

**Decisions**
- ADR-0028 accepted with the merge.

**Files changed**
- Task index (T1.05 → Completed), ADR-0028 status and index, this log.

---

## 2026-11-06 — T1.05 Memberships and org switching (DM-4)

**Implemented**
- **Memberships** (`memberships`, `membership_locations`): a person's access to a business, with
  a role (the R1 presets owner, administrator, manager, employee, read-only), active or revoked,
  and all locations or a list. One per person and business; role and status are checked by the
  database. Their permissions are T1.06's.
- **The business a request acts for comes from an active membership only.** The session keeps
  the chosen membership; without a valid choice the person's oldest active membership is used;
  with none, 403. `OrgContext.for_membership` replaces `for_user` and carries the location scope.
  Signing in clears the previous person's choice. `require_tenant` now follows the membership.
- **Switching**: a business picker in the header when a person has more than one;
  `/app/organizations/switch` accepts only a membership the person holds (404 otherwise).
- **Users are global identities**: scoped selects refuse `User`; alerts and the weekly report go
  to `members(db, org)` (active members with active accounts), so a manager of a second business
  gets that business's emails. Square's connect callback files the connection under the active
  business, not `users.tenant_id`.
- Signup and the demo seeder make the creator the Owner.
- **DM-4**, migration `545dd2eaa736`: the two tables, and every existing user as the active Owner
  of the business it signed up with. `users.tenant_id` stays until DM-16; nothing decides access by
  it any more.
- Probe PA3 retired. ADR-0028 (Proposed until this pull request is merged); ADR-0026 updated;
  `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_memberships.py` (12): PA3 inverted (one person in two businesses, once each); role
  and status refused outside the presets; signing up makes the owner; after switching only the
  selected business's stock shows, and back again (AC-STF-07); a new item lands in the selected
  business; another person's business cannot be chosen; a revoked membership loses access on the
  next request, and with none left the answer is 403; signing in does not inherit the last
  choice; a location-scoped membership carries its locations into the context; members() returns
  active members only.
- Isolation suite: the coverage test required a case for the switch route; switching to another
  business's membership answers 404 and changes nothing (34 cases pass). Users are now refused by
  scoped selects (test).
- DM-4 migration test: two existing users become the active owners of their own businesses.
- Full suite 662 passed, 11 skipped (647 and 10 before; the new skip is the DM-4 migration test's Postgres run); ruff, mypy and the docs checker clean.

**Open issues**
- A location-limited membership is stored and carries its locations, but pages reading counts,
  lots or alerts refuse it until T1.06 gives those rows a location path (fails closed). No screen
  creates one before invitations (T1.10).
- Roles do not yet limit anything: every member can do everything until T1.06.

**Files changed**
- `migrations/versions/545dd2eaa736_memberships.py`, `tests/test_memberships.py`, ADR-0028 (new);
  `app/models.py`, `app/access/{__init__,context,scoped,system}.py`, `app/security.py`,
  `app/web/auth.py`, `app/web/dashboard.py`, `app/web/templates/base.html`, `app/cli.py`,
  `app/orchestration/{alerts,reporting}.py`; tests `conftest.py`, `test_email.py`,
  `test_webhooks.py`, `test_isolation.py`, `test_scoped_access.py`, `test_migrations.py`; probe
  file (PA3 retired); ADR-0026, ADR-0027 (Accepted), the index, `CLAUDE.md`, task index (T1.04 →
  Completed, T1.05 → In review), this log.

**Next task**
- T1.06 Permission keys and role presets.

---

## 2026-11-06 — T1.04 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #22 was merged with a merge commit (`2f7055c`). CI
  on its head `30ca62c`, all five required jobs green: SQLite 647 passed, 10 skipped; PostgreSQL 16
  657 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: a business without Square creates a location and then a stock
  item (PA1 retired); AC-ADM-01.

**Decisions**
- ADR-0027 accepted with the merge.

**Files changed**
- Task index (T1.04 → Completed), ADR-0027 status and index, this log.

---

## 2026-11-06 — T1.04 Organization and location model (DM-3)

**Implemented**
- **Locations without a POS.** Owners add, edit, archive and restore locations at
  `/app/locations`: name, address, IANA time zone, currency, the hour the business day starts,
  open days. Provider ids are optional; an owner-added location has none and is never sent to
  Square (the sync's Square calls now take only locations with a Square id, which mypy flagged).
- **Archive is its own state** (`archived_at`): the plan limit skips archived locations and the
  Square sync leaves them off, so an upgrade or Square's status cannot bring one back. Adding and
  restoring respect the plan's allowance (Starter covers one).
- **Square keeps what it supplies** for a location it created: name, time zone and currency are
  shown read-only and not saved from the form; address, cutoff and open days are the owner's.
- **Business profile** (ADM-FR-01): name, address and account contact email on the settings
  page. The optional logo is left for later.
- **Business days** (`app/transform/business_days.py`, strict mypy): `business_date(instant,
  zone, cutoff)` and the open-days mask helpers. Expiry is now judged on each item's location
  business day unless a caller fixes the date: dashboard, expiring page, health, markdown
  suggestions and the alert job (AC-ADM-01).
- **DM-3**, migration `912d17673f03` (additive): `tenants.address`, `contact_email`;
  `locations.address`, `business_day_cutoff_hour` (default 0, CHECK 0–23), `open_days` (default
  127, CHECK 0–127), `archived_at`; provider ids nullable. Its downgrade names owner-added
  locations `manual-<id>` so the older schema's constraints hold.
- Empty states point a business without Square to "Add a location". Probe PA1 retired.
- ADR-0027 (Proposed until this pull request is merged); `CLAUDE.md` states the business-day rule.

**Verified (locally, SQLite)**
- `tests/test_business_days.py` (11): local date versus UTC, cutoff, both daylight-saving
  changes in Los Angeles, offsets, refusals, time-zone names, open-day masks.
- `tests/test_locations.py` (19): PA1 inverted (a business without Square adds a location, then
  an item); five refusals with reasons; editing; Square-owned fields kept; archive and restore;
  the Starter allowance for adding and restoring; an archived location stays off through a Square
  sync and an upgrade; the business profile and its refusals; AC-ADM-01 (a lot dated the 5th is
  expired at 03:00 UTC on the 6th in a UTC location and not in Los Angeles, in positions and in
  the alert job; moving the zone moves the day); the cutoff; a fixed date still applies; the new
  pages render.
- Isolation suite: the coverage test flagged the four new location routes until they had cases,
  and the integer cutoff field until it was listed as a quantity; 33 cases pass.
- DM-3 migration test: a location with no provider ids gets the server defaults (midnight, every
  day); the downgrade names it `manual-1`. Two older tests that pinned exact counts (53 routes, 31
  timestamp columns) now check what they were for without the number.
- Full suite 647 passed, 10 skipped (612 before; the new skip is the DM-3 migration test's Postgres run); ruff, mypy and the docs checker clean. No browser check: the preview tool
  reads another project's launch configuration in this session; the pages are covered by route
  and rendering tests.

**Open issues**
- Until roles exist (T1.06) every member of a business can manage its locations; the requirement
  is Owner and Administrator (`location.manage`).

**Files changed**
- `app/transform/business_days.py`, `app/web/locations.py`, templates `app/locations.html` and
  `app/location_form.html`, migration `912d17673f03` (all new); `app/models.py`, `app/main.py`,
  `app/load/upsert.py`, `app/orchestration/{alerts,health,promotions,subscriptions,sync,views}.py`,
  `app/web/promotions.py`, templates `dashboard.html`, `item_form.html`, `settings.html`; tests
  `test_business_days.py`, `test_locations.py` (new), `test_isolation.py`, `test_migrations.py`,
  `test_schema_hygiene.py`, `test_snack_box_removed.py`; probe file (PA1 retired); ADR-0027 and the
  index, `CLAUDE.md`, task index (T1.03 → Completed, T1.04 → In review), this log.

**Next task**
- T1.05 Memberships and org switching.

---

## 2026-11-06 — T1.03 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #21 was merged with a merge commit (`68f4e89`). CI
  on its head `0d543e4`, all five required jobs green: SQLite 612 passed, 9 skipped; PostgreSQL 16
  621 passed, 0 skipped (the isolation suite runs in both); lint and types; dependency audit;
  Docker image. Same five green on `main` after the merge. Acceptance met: 404/403 for every route
  and operation; the suite runs on Postgres in a required check.

**Files changed**
- Task index (T1.03 → Completed), this log.

---

## 2026-11-06 — T1.03 Tenant-isolation test suite

**Implemented**
- `tests/test_isolation.py`, generated from the app's route table. A route takes an identifier
  when it has a path parameter, a query or form field named `id` or `*_id`, or any integer
  parameter not listed as a quantity (only `lead_time_days` is). That finds 27 routes: reads, writes,
  the invoice photo download and the six pages narrowed by `?location=`.
- Two businesses, each with a location, an item with a lot about to expire, a running markdown, a
  product code and a stored delivery photo with a parsed line. Signed in as A, each route is
  called with B's identifiers: it must answer 404 or 403, the answer must not mention B, and every
  row B owns (every table, every column) must be unchanged. The same call with A's identifiers
  must succeed (200 or 303), so a route that refuses everything cannot pass.
- A coverage test fails when a route takes an identifier and has no case (or a case outlives its
  route). Two cases the route table cannot generate: B's barcode looked up by A's scanner finds
  nothing, and A's own invoice with a line pointed at B's item receives nothing into it. A guard
  test proves the snapshot notices a change.
- **Defect found and fixed:** the six pages narrowed by `?location=` (inventory, expiring,
  health, reorder, reorder CSV, promotions) answered 200 with an empty page for another business's
  location. Nothing leaked, but AC-SAAS-01 asks for 404/403. A shared `location_filter` dependency
  now answers 404 for a location outside the organization.

**Verified (locally, SQLite)**
- The suite (29 tests) passes. Without the `location_filter` fix the six location cases fail; with
  an unscoped lookup planted in the count route its case fails (303 where 404 is required), and
  the planted lookup was removed again.
- Full suite 612 passed, 9 skipped (583 before); ruff, mypy and the docs checker clean.
- Postgres 16 runs in CI (Docker is not running on this machine); the suite is in both required
  test jobs.

**Files changed**
- `tests/test_isolation.py` (new); `app/security.py` (`location_filter`), `app/web/dashboard.py`,
  `health.py`, `promotions.py`; ADR-0026 (Accepted; the location rule), the ADR index,
  `CLAUDE.md`, `TESTING_AND_QUALITY_STRATEGY.md`, task index (T1.02 → Completed, T1.03 → In
  review), this log.

**Next task**
- T1.04 Organization and location model (and T1.05 memberships, both unblocked by T1.02).

---

## 2026-11-06 — T1.02 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #20 was merged with a merge commit (`039ad45`). CI
  on its head `d929de3`, all five required jobs green: SQLite 583 passed, 9 skipped; PostgreSQL 16
  592 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five green on `main`
  after the merge. Acceptance met: the static test passes and the existing cross-tenant tests pass.

**Decisions**
- ADR-0026 accepted with the merge.

**Files changed**
- Task index (T1.02 → Completed), ADR-0026 status and index, this log.

---

## 2026-11-06 — T1.02 Scoped data-access layer (C-33)

**Implemented**
- `app/access/` (strict mypy): `OrgContext` (frozen; organization, user, locations; built only by
  `for_user` through the new `require_org` dependency, or `system(tenant_id)` for scheduled jobs,
  signed webhooks and the command line) and `select_scoped`, `get_scoped`, `update_scoped`, which
  add the organization filter and, for a location-limited context, the location filter. They
  refuse instead of guessing: tables owned by no organization, tables without `tenant_id`, and
  (under a location limit) tables whose location is known only through a parent.
- Every service that took `tenant_id: int` now takes `org: OrgContext` (57 functions: 44 public, 13 private helpers); the
  document, promotion and scanning services that loaded rows by id take it too. Rewritten with an
  AST script, then reviewed by hand.
- Every route that reads tenant rows takes `org`. The per-route `if row.tenant_id != tenant.id`
  checks are gone: a row from another organization is simply not found (404).
- `app/access/system.py` holds the only cross-organization queries, each named for its job:
  sign-in by email, the connection a Square webhook names, the scheduler's tenant lists, the
  Stripe account lookups. Webhooks and scheduled jobs then work through `OrgContext.system`.
- The readiness check's `SELECT 1` moved to `app.db.ping()`. `parse-doc` on the command line now
  takes `--tenant`, like the other per-tenant commands.
- ADR-0026 (Proposed until this pull request is merged); `CLAUDE.md` states the rule.

**Verified (locally, SQLite)**
- `tests/test_scoped_access.py` (10): the scan finds no raw query in `app/web` or
  `app/orchestration`, and a second test proves it catches the old pattern (six findings in a
  pre-T1.02 snippet). Scoped select, get and update stay inside one organization; a location
  limit narrows location rows and not organization-wide ones, and refuses counts (location known
  only through the item); global tables are refused; a context is frozen; sign-in by email and
  webhook merchant lookups behave.
- Existing cross-tenant tests pass unchanged; the suite's callers now pass a context.
- Full suite 583 passed, 9 skipped (573 before; the skips are Postgres-only migration runs);
  ruff, mypy (74 files) and the docs checker clean. Demo seeder, `health` and `report` commands
  ran on a scratch database.

**Deviations from the task text**
- `OrgContext` has no permissions field: the permission catalogue arrives in T1.06, and a field
  nothing checks would be dead weight (ADR-0026).
- The static test also bans `.query()` and SQLAlchemy's `text`, `insert` and `delete`, not only
  `db.get`, `session.get` and `select`.

**Files changed**
- `app/access/{__init__,context,scoped,system}.py` (new), `app/security.py` (`require_org`),
  `app/db.py` (`ping`), every module in `app/orchestration/` that queries, `app/web/{auth,
  dashboard,health,ops,promotions,receiving,scanning,webhooks}.py`, `app/cli.py`; tests: the new
  `test_scoped_access.py` and the callers in eleven test modules; ADR-0026 (new), ADR-0025
  (Accepted), ADR-0001, the ADR index, `CLAUDE.md`, `pyproject.toml` (comment),
  `TARGET_ARCHITECTURE.md`, task index (T1.01 → Completed, T1.02 → In review), this log.

**Open issues**
- Outside the scan, as the task scopes it: `app/load` (Square writers, replaced through the
  ledger in T1.16–T1.18), `app/notify`, `app/security`, `app/entitlements` and the command line
  keep their own tenant filters.

**Next task**
- T1.03 Tenant-isolation test suite.

---

## 2026-11-06 — T1.01 completed

**Verified**
- At the owner's request ("Yes, merge it") PR #19 was merged with a merge commit (`38dbcf8`). CI
  on its head `7700b14`, all five required jobs green: SQLite 573 passed, 9 skipped; PostgreSQL 16
  582 passed, 0 skipped, so DM-2 converted, round-tripped and was checked on Postgres; lint and
  types; dependency audit; Docker image. Same five green on `main` after the merge. Acceptance
  met: probe PA5 inverted and retired; the migration upgrades and downgrades over populated data
  on Postgres.

**Decisions**
- ADR-0025 accepted with the merge.

**Files changed**
- Task index (T1.01 → Completed), ADR-0025 status and index, this log.

---

## 2026-11-06 — T1.01 Schema hygiene: timestamptz, constraints, delete rules (DM-2)

**Implemented**
- `app.db.UTCDateTime`: an instant is written and read as an aware UTC datetime on both databases
  (`timestamptz` on Postgres); a naive value is refused rather than guessed. All 31 timestamp
  columns use it. The `_aware()` helpers (entitlements, alerts, views, Square webhooks) and the
  zone patches in Stripe events, subscriptions and sync are gone. The two places that parse
  Square's text stamps (webhook counts, OAuth token expiry) read a stamp without an offset as UTC.
- Delete rules on all 44 foreign keys: `ON DELETE RESTRICT` by default (`ref()`); `CASCADE`
  (`owned_by()`) only for a document's lines and extraction corrections and a sold item's recipe
  components.
- Ten named CHECK constraints: an item's reorder point, par level and lead time are not negative
  and its pack size is above zero; a batch and a recipe component hold more than nothing; the
  `source` of counts, product codes, batches and email suppressions is one the code writes. Not
  checked, on purpose (ADR-0025): balances, which DR-08 lets go negative, and
  `inventory_events.source`, which holds legacy values and is replaced by the ledger (T1.16).
- SQLite connections enforce foreign keys, so the suite fails there as it would on Postgres.
- Migration `68c6fdbc31a7` (DM-2): converts the columns `AT TIME ZONE 'UTC'` on Postgres, rebuilds
  the foreign keys with their rules and adds the CHECKs (table rebuilds on SQLite); its downgrade
  restores the previous schema.
- The add-item and thresholds forms answer 400 with a reason to a negative threshold or an empty
  pack instead of hitting the database; the thresholds form now also answers 400, not 500, to a
  malformed number. The demo seeder skips a lot that its random sales emptied.
- Probe PA5 retired. ADR-0025 (schema conventions, Proposed until this pull request is merged);
  `CLAUDE.md` states the rule for new tables.

**Verified (locally, SQLite)**
- `tests/test_schema_hygiene.py` (27): instants come back aware in UTC, also when written from
  another zone, and compare correctly in queries; a naive value is refused; every timestamp column
  is `UTCDateTime`; each of the eleven impossible rows is refused; negative balances, minus zero
  and nulls are accepted; a reference to a missing row is refused; deleting an item with history
  or a tenant is refused; deleting a document takes its lines and corrections, a sold item its
  recipe; the forms refuse negative thresholds and an empty pack; Square expiries are aware.
- `tests/test_migrations.py`: a migrated database has every CHECK and delete rule the models
  declare (and `timestamptz` on Postgres); with rows present, DM-2 reads a stored naive
  `2026-11-04 12:00:00` as 12:00 UTC, the downgrade gives back the same wall clock, and no row is
  lost either way. Existing chain, model-drift and rows-survive tests pass.
- Full suite 573 passed, 9 skipped (544 and 7 before; the two new skips are the new migration tests' Postgres runs); ruff and mypy clean; docs checker 0 problems. The demo seeder ran through the
  whole migration chain on a scratch database (7 lots, 9 counts, aware timestamps).
- Postgres 16 runs in CI only: Docker is not running on this machine.

**Files changed**
- `app/db.py`, `app/models.py`, `migrations/versions/68c6fdbc31a7_schema_hygiene.py` (new),
  `app/entitlements.py`, `app/orchestration/{alerts,stripe_events,subscriptions,sync,views}.py`,
  `app/web/{dashboard,webhooks}.py`, `app/providers/square/oauth.py`, `app/cli.py`; tests
  `test_schema_hygiene.py` (new), `test_migrations.py`; probe file (PA5 retired);
  `docs/adr/0025-schema-integrity-conventions.md` (new) and the ADR index, `CLAUDE.md`, task index
  (T1.01 → In review), this log.

**Open issues**
- A local database holding a value the new CHECKs refuse (a negative threshold typed before this
  change) stops the upgrade with the constraint's name; fix the row and rerun. Only development
  databases exist.

**Next task**
- T1.02 Scoped data-access layer (C-33).

---

## 2026-11-06 — T0.05 and T0.11 completed; staging deferred (C-49); gate G0

**Verified**
- T0.05: the owner merged PR #18 with a merge commit (`ee755a2`). CI on its head `f050811`, all
  five required jobs green: SQLite 544 passed, 7 skipped, docs checker 0 problems; PostgreSQL 16
  551 passed, 0 skipped; lint and types; dependency audit; Docker image. The same five are green
  on `main` after the merge. Acceptance met: no snack-box symbol remains (test), 53 product
  routes, five Square scopes, migration tests pass on both databases.
- T0.11: PR #16 was merged with a merge commit (`56d6e7c`); CI on its head green on all five jobs
  (SQLite 614 passed, 6 skipped; PostgreSQL 16 620 passed, 0 skipped). Acceptance met: an ADR
  for every approved ID, indexed and enforced by the docs checker; `CLAUDE.md` points to them.

**Decisions**
- C-49 (owner, 2026-11-06, "defer staging"): staging is not created until a task needs a public
  HTTPS endpoint, first T3.11 (Square sandbox run). T0.09 is Deferred: `render.yaml` stays on
  `main`; the Blueprint, its secrets, the C-48 photo storage and T0.09's acceptance checks are
  done before T3.11. TR-26 moves from G0 to G5.
- Gate G0: every exit criterion is met except "staging deployed from `main`", which C-49 defers:
  CI green on SQLite and Postgres 16 for every push (T0.02); suite hermetic with a developer
  `.env` present (T0.01); dependencies locked and audited (T0.03); probes R3 and R7 retired and C8
  fixed (T0.08, T0.06, T0.07); write-back off with no swallowed failures (T0.08); snack-box code
  and scopes gone (T0.05). The owner started Stage 1 on 2026-11-06 ("Let's start on Stage 1").

**Files changed**
- Task index (T0.05, T0.11 → Completed; T0.09 → Deferred; T1.01 → In progress), T0.09 entry,
  `PRODUCT_DECISIONS.md` (C-49), master roadmap and testing strategy (G0), OD-03, TR-26,
  `docs/roadmap/README.md`, ADR-0002, this log.

**Next task**
- T1.01 Schema hygiene (DM-2).

---

## 2026-11-06 — T0.05 Extract the snack-box module (C-37)

**Implemented**
- **Snack-box repository** (`Souru0712/Snack-box`, private, created by the owner): first commit
  `ae99561` on `main` (the repository was empty, so there was no branch to open a pull request
  against). It holds the module copied unchanged from InventoryOS commit `56d6e7c`: 36 files that
  belonged only to it (orchestration `boxes`/`customers`/`referrals`, web `boxes`/`shop`, pure rules
  `allocation`/`boxes`/`credits`/`taste`, templates, the customer sign-in email, the migration that
  created its tables, seven test files), verbatim excerpts of its parts in five shared files
  (models, entitlements, security, scheduler, Square client) headed with their source lines, and a
  README with permalinks to the smaller references. The owner's local CSV tool was not touched.
- **InventoryOS:** the 35 files removed; routers unmounted; the 11 models and five enums, the
  `inventory_events.box_order_id` column, `SNACK_BOX_TENANT_IDS` and `CUSTOMER_LOGIN_TTL_MINUTES`,
  `owns_snack_box`, shopper sign-in, the box housekeeping job, the Square payment and refund webhook
  branch, `create_payment_link`, the shop's navigation, its email test, and the mypy override for
  its modules. Square now asks merchants for five scopes (merchant profile, items read, inventory
  read and write, orders read) instead of eight.
- **DM-1:** migration `5c1d0f2a9b31` drops the 11 tables, their five Postgres enum types and
  `inventory_events.box_order_id`; it is the reverse of `e7b19fdcb1ce`, generated from it, and keeps
  `webhook_events`, which that migration also created and the product uses. Its downgrade recreates
  the tables empty, so the chain still rolls back to base. Destructive, approved by C-37.
- README, `.env.example`, `CLAUDE.md`, ADR-0004 and ADR-0016 updated; OD-02 done.

**Verified (locally)**
- Routes: 88 before, 35 of them the shop's; 53 after, as T0.05 specifies.
- `tests/test_snack_box_removed.py`: 53 routes and none under `/shop` or `/app/boxes`; Square
  scopes are exactly the five; no trace of the module in `app/` or `scripts/` (names, paths,
  scopes). `tests/test_migrations.py`: the 11 tables and the column are gone after migrating,
  `webhook_events` stays; the existing chain, downgrade-to-base and rows-survive tests pass on
  SQLite (their Postgres runs are in CI).
- ruff, mypy (70 files) clean; docs checker 0 problems. Full suite 544 passed, 7 skipped: 614
  before, less the shop's 74 tests (its own files and one email test), plus the new tests; the
  extra skip is the new migration test's Postgres run.

**Files changed**
- 35 files deleted; `migrations/versions/5c1d0f2a9b31_drop_snack_box_tables.py` (new);
  `app/main.py`, `models.py`, `config.py`, `entitlements.py`, `security.py`,
  `orchestration/scheduler.py`, `web/webhooks.py`, `web/templating.py`, `templates/base.html`,
  `providers/square/oauth.py`, `client.py`, `webhooks.py`; tests `conftest.py`, `test_email.py`,
  `test_migrations.py`, `test_snack_box_removed.py` (new); `pyproject.toml`, `.env.example`,
  `README.md`, `CLAUDE.md`, ADR-0004, ADR-0016, task index (T0.05 → In review), OD-02, this log.

**Open issues**
- Existing Square connections were granted the three removed scopes; they keep them until the
  merchant reconnects. No merchant is connected today.

**Next task**
- Stage 1 starts with T1.01 (schema hygiene), now unblocked.

---

## 2026-11-06 — T0.09 Staging on Render, part 1: Blueprint and C-48

**Implemented**
- `render.yaml` Blueprint: web service (`/readyz` health check), scheduler worker
  (`python -m app.cli scheduler`), Postgres 16 reachable only on Render's private network, region
  Virginia, deploy only when `main`'s required checks pass. Secrets are not in the file:
  `SECRET_KEY` is generated by Render; `ENCRYPTION_KEY`, `SENTRY_DSN` and `APP_BASE_URL` are asked
  for when the Blueprint is created. Plans are Render's defaults, shown with their cost before
  creation; the database must be a paid plan for point-in-time recovery (3 days on Render's Hobby
  workspace, 7 on Pro; none on the free database).
- Web runs one instance, not T0.09's two, until photos move into Postgres: on an instance's own
  disk a photo is invisible to other instances and lost at each deploy.
- An encryption key was generated into `.claude/staging-secrets.txt` (git-ignored, never shown in
  chat) for the owner to paste into Render and then delete.
- **C-48** (owner, 2026-11-06, option A): an invoice photo is kept until its delivery is received
  plus 7 days, then deleted; stored in Postgres; no AWS account. Recorded in PRODUCT_DECISIONS,
  RCV-FR-02, ADR-0024 (supersedes ADR-0012), the ID-12 row, T0.09's technical line and OD-03.
- Sentry advice to the owner: error monitoring only. The app sends errors with personal data
  scrubbed; tracing stays off (`SENTRY_TRACES_SAMPLE_RATE` 0); logs stay in Render, not Sentry.

**Verified**
- `render.yaml` parses; the field names and values follow Render's Blueprint reference as read on
  2026-11-06 (its code examples did not load, so the `envVars` layout follows Render's usual
  form); Render validates the file when the Blueprint is created. Migrations at startup take a
  Postgres advisory lock, so web and scheduler starting together is safe. Docs checker 0 problems.
- **Not verified:** a deploy. That needs the owner to create the Blueprint.

**Files changed**
- `render.yaml` (new), `docs/adr/0024-invoice-photos-in-postgres.md` (new), `docs/adr/0012…`,
  `docs/adr/README.md`, `PRODUCT_DECISIONS.md`, `PRODUCT_SPECIFICATION.md`,
  `IMPLEMENTATION_DECISIONS.md`, `DEVELOPMENT_TASK_BREAKDOWN.md` (T0.09 → In progress),
  `RISK_AND_DEPENDENCY_REGISTER.md`, this log.

**Next**
- Owner: create the Blueprint once this is on `main`. Then part 2: database photo storage and the
  7-day deletion (C-48), two web instances, staging kept out of search engines, and T0.09's
  acceptance (`/readyz`, one active scheduler, a test error in Sentry, the restore procedure).
- T0.05 (the owner created the private `Snack-box` repository on 2026-11-06).

---

## 2026-11-06 — T0.11 Governance scaffolding (ADRs, status tracking)

**Implemented**
- `docs/adr/`: `README.md` (when to write an ADR, numbering, statuses Proposed / Accepted /
  Superseded, the index, and a pointer to the roadmap's governance for task status and the
  execution log), `0000-template.md`, and ADR-0001..0023, one per approved implementation
  decision ID-01..ID-23. Each states status (Accepted 2026-11-05, C-43), the ID with a link to its
  row, the tasks it shapes, context, decision, alternatives, and consequences, including where
  Stage 0 already stands (for example ADR-0005 records Dependabot's lockfile-only setting, ADR-0007
  that `hx-on` cannot run under the CSP from T0.06, ADR-0016 that the snack-box repository does not
  exist yet).
- `scripts/check_docs.py` checks links under `docs/adr/` and fails when an approved ID has no ADR
  or more than one, when two ADRs share a number, or when an ADR is missing from the index.
- `CLAUDE.md` points to the ADR index (reading list item 4; new ADRs from the template, indexed);
  the roadmap README and IMPLEMENTATION_DECISIONS link to it.
- Task status workflow: already defined in the roadmap README §4.2–4.3 and in use since T0.01
  (index status, one log entry per task, owner merges each task's pull request); the ADR README
  points to it rather than repeating it.
- `RISK_AND_DEPENDENCY_REGISTER.md` OD-02: the owner's local `Snack-box` folder is a separate CSV
  command-line tool, not a git repository; the private repository for T0.05 is still to be created.

**Verified (locally)**
- Docs checker 0 problems. Negative runs: with ADR-0017 removed it reports "ID-17 is recorded in
  0 ADRs" (and the index's broken link); with a stray `0024` copy of ADR-0009 carrying a bad link
  it reports the broken link, "ID-09 is recorded in 2 ADRs" and "0024-extra.md is not in the
  index".
- ruff, ruff format, mypy clean; `tests/test_docs_contract.py` passes. Only documents and the
  checker changed, so the full suite was left to CI.

**Files changed**
- `docs/adr/` (25 new files), `scripts/check_docs.py`, `CLAUDE.md`, `docs/roadmap/README.md`,
  `docs/roadmap/IMPLEMENTATION_DECISIONS.md`, `docs/roadmap/RISK_AND_DEPENDENCY_REGISTER.md`, task
  index (T0.10 → Completed, T0.11 → In review), this log.

**Next task**
- Stage 0's remaining tasks wait for the owner: T0.05 (snack-box repository, OD-02) and T0.09
  (Render, AWS and Sentry accounts, OD-03).

---

## 2026-11-06 — T0.10 completed

**Verified**
- At the owner's request PR #15 was merged with a merge commit (`497db30`). CI on its head
  `fe5023c`, all five required jobs green: SQLite 614 passed, 6 skipped; PostgreSQL 16 620 passed,
  0 skipped; lint and types; dependency audit; Docker image. Same five green on `main` after the
  merge. Acceptance met: every log line is JSON with `request_id`; a request with a password field
  leaves no trace of it.

**Files changed**
- Task index (T0.10 → Completed), this log.

---

## 2026-11-05 — T0.10 Structured logs and request ids (OBS-01)

**Implemented** (`app/observability.py`)
- `JsonFormatter`: one JSON object per line with `ts`, `level`, `logger`, `message`,
  `request_id`, and `job`, `tenant_id`, `user_id` when known; the exception text for errors.
  Anything shaped like an email address is masked (`[email]`) in messages and exceptions, as a
  last resort behind the rule below.
- `RequestContext`, the outermost middleware: a request id per request (a caller's
  `X-Request-ID` is kept if it is 8–64 plain characters, otherwise replaced), returned as
  `X-Request-ID`, and one access line per request with method, path without the query string,
  status and duration. The context is one dict shared with FastAPI's threadpool copies, so
  `current_user` attaches `tenant_id` and `user_id` and every later line of the request,
  including the access line, carries them.
- Scheduled jobs: each run gets `request_id` `job-…` and `job` (`scheduler._job`); the per-tenant
  loops (sync, weekly reports, health snapshots) wrap each tenant in `log_context(tenant_id=…)`.
  Lines outside any request or job carry the process's own id (`process-…`).
- `configure_logging()` in the web process and the CLI (which also runs the scheduler). uvicorn's
  own lines go through the JSON handler; its access log is off (also `--no-access-log` in the
  Dockerfile) because it prints query strings, and customer sign-in links carry their token in
  one. `httpx`/`httpcore` drop to WARNING: they log every outgoing URL at INFO.
- Five log calls printed email addresses (email suppression, SMTP skipped, send failure, alert
  preferences, email settings); they now name the user id or nothing. The snack-box modules rely
  on the masking until T0.05 removes them.
- Sentry scrubbing (`app/monitoring.py`) unchanged. `CLAUDE.md`: the logging rules.

**Verified (locally)**
- `tests/test_observability.py` (9): sign-up, a wrong and a right sign-in leave neither the
  password nor the email anywhere in the captured JSON, and every line has a `request_id`; the
  response's `X-Request-ID` matches its access line; a signed-in request's line names its tenant
  and user; a caller's id is kept and a hostile one replaced; a token in a query string never
  reaches the log; an email in a message is masked; a job run has its own id and names the
  tenant; uvicorn's access log is off and `httpx` is at WARNING; a start-up line has the process
  id. The first run of these tests caught two real leaks (an `httpx` line with the full URL and
  lines without any id), fixed above.
- Full suite 614 passed, 6 skipped (605 + 9); ruff, ruff format, mypy (79 files) clean.

**Files changed**
- `app/observability.py` (new), `app/main.py`, `app/cli.py`, `app/security.py`,
  `app/orchestration/scheduler.py`, `app/notify/email.py`, `app/orchestration/alerts.py`,
  `app/web/settings.py`, `Dockerfile`; `tests/test_observability.py` (new); `CLAUDE.md`; task
  index (T0.08 → Completed, T0.10 → In review); this log.

**Open issues**
- Not checked on a deployed host (T0.09): what the platform's log viewer does with JSON lines.

**Next task**
- T0.11 governance scaffolding. T0.05 and T0.09 wait for the owner (OD-02, OD-03).

---

## 2026-11-05 — T0.08 completed

**Verified**
- The owner accepted T0.08 as write-back only, leaving the Square count overwrite to T1.18
  ("Accept, merge it"), and asked for the merge. PR #14 merged with a merge commit (`6918ee5`).
  CI on its head `4f0c777`, all five required jobs green: SQLite 605 passed, 6 skipped;
  PostgreSQL 16 611 passed, 0 skipped; lint and types; dependency audit; Docker image. Same five
  green on `main` after the merge.

**Files changed**
- Task index (T0.08 → Completed), this log.

---

## 2026-11-05 — T0.08 Disable Square write-back; no swallowed failures (C-34, C4)

**Implemented**
- `SQUARE_WRITE_BACK` setting, default off (`app/config.py`, `.env.example`).
- The four flows that wrote to Square no longer call it: invoice apply
  (`push_adjustments_to_square`), receiving by barcode scan (`adjust_stock_in_square`), the scan
  count and the dashboard count (`push_counts_to_square`). Each changes InventoryOS only. With
  the calls went the three `except Exception` blocks that logged a failed push and carried on
  (C4).
- `push_counts_to_square` and `push_adjustments_to_square` are kept for R1.1's outbox and raise
  `WriteBackDisabled` while the setting is off, so a stray call fails loudly instead of skipping
  a write its caller believes happened.
- Copy no longer promises Square updates: the receiving page's "count(s) pushed back to Square"
  notice and its "pushed back so your storefront matches" line are gone; after a scan of an item
  Square tracks, the page says the number is saved here only and Square's next sync replaces it.
  `pushed_to_pos` is gone from the scan JSON and the receiving redirect (the database column
  stays; it is now always false).
- Probe R3 retired (comment in `docs/audit/probes/test_audit_probes.py`). `NEEDS-YOU.md` item 1
  says write-back is off until R1.1.
- Remaining Square calls are reads. The syncs (catalog, counts, sales) already record a failure
  on `sync_runs` with the exception and log it. The one read outside a sync, the scan lookup's
  refresh of a single item, only logged a failure and showed the local number as if fresh: it now
  records a failed `sync_runs` row (kind `item`, `record_square_failure`), which the dashboard's
  sync status shows, and the scan page says Square could not be reached and the number is
  InventoryOS's last count.
- Also in this PR: C-45, C-46 in the extraction prompt (entry below).

**Verified (locally)**
- `tests/test_square_writeback.py` rewritten: invoice apply, scan receive, scan count and
  dashboard count make zero Square calls and change InventoryOS stock (24 → 48, 30, 37, 40); the
  setting is off by default; both push functions raise while it is off. With the old flows
  restored all six fail. The R1.1 semantics (adjustment not count, idempotent receipt, derived
  items never pushed) are kept as tests with write-back switched on. A failed Square read during
  a scan lookup leaves a failed `item` sync run and `square_unreachable` in the response.
- Full suite 605 passed, 6 skipped (598 + 7); ruff, ruff format, mypy (78 files) clean.

**Acceptance point for the owner**
- The acceptance reads "probe R3 scenario can no longer lose a delivery". R3's second half is the
  hourly sync: `apply_provider_counts` sets a Square-tracked item to Square's count. With
  write-back off, a delivery or count recorded in InventoryOS for such an item is now replaced at
  the next sync every time, not only after a failed push. Stopping that is the inbound half of
  C-34, which the roadmap gives to T1.18 ("`apply_provider_counts` stops touching stock").
  Nothing is silent any more (no push is attempted or reported, and the scan page says Square's
  sync will replace the number), but the delivery is not kept for those items until T1.18. No
  tenant uses Square tracking today (the owner's store runs Toast; C-40 no pilots yet).
  Recommendation: accept T0.08 as write-back only and leave the overwrite to T1.18.

**Files changed**
- `app/config.py`, `.env.example`, `app/orchestration/sync.py`, `receiving.py`, `scanning.py`,
  `app/web/receiving.py`, `scanning.py`, `dashboard.py`, templates `receiving_review.html`,
  `scan.html`; `tests/test_square_writeback.py`; `docs/audit/probes/test_audit_probes.py`;
  `NEEDS-YOU.md`; task index (T0.08 → In review); this log.

**Open issues**
- The snack-box module (`app/orchestration/boxes.py`) still adjusts Square stock when a box is
  packed, through its own client call. It leaves this repository in T0.05; left unchanged here.

**Next task**
- T0.10 structured logs and request ids; T0.11 governance scaffolding. T0.05 waits for the
  snack-box repository (OD-02).

---

## 2026-11-05 — Owner decisions on the invoice gaps (C-45, C-46, C-47)

- The owner's answers on the gaps recorded in the entry below: lines circled or marked "Return"
  by hand are left out of a scan (C-45); shipped beats ordered, including shipped weight (C-46);
  supplier item codes are not captured from invoices (C-47). The owner offered an invoice longer
  than one page (OD-09).
- `app/extract/invoice.py`: the system prompt carries C-45 and C-46;
  `tests/test_invoice_ai.py` checks both rules reach the model. Whether the model follows them is
  for the real-invoice trial (T4.15).
- The pack-notation fix below was merged by the owner as PR #13 (`2cf167b`); CI on its head
  `7b6fb4b` was green on all five jobs (SQLite 598 passed, 6 skipped; PostgreSQL 16 604 passed,
  0 skipped), and the same five were green on `main` after the merge.

---

## 2026-11-05 — Receiving: supplier pack notations from the owner's invoices (unplanned fix)

Not a roadmap task. The owner shared the front pages of four suppliers' invoices (Central
Boeki, USA Seafood, JFC, Hadson (Toko)). Running their printed pack wording through the
receiving conversion found live quantity bugs; the owner approved fixing them before T0.08.
The photos are not in this repository (it is public; they carry the store's address, phone and
supplier accounts).

**Found (each reproduced against `main` at `9fda108`)**
- `3.9OZ x 14 x 2 #` (Hadson; `#` means pieces) read as 14 packs of 2 lb (28 lb a bundle).
  For a counted item that sent the line to review as a weight; for an item tracked by weight it
  would have booked pounds of chocolate.
- Size-first notations (`2.4 oz x 10 #`), packs within cases (`10/5PK/2.70 FZ`, `3/6/6.76 FZ`)
  and `FZ` for fluid ounces were not read at all.
- Unit abbreviations `cs`, `BX`, `BUNDL`, `PK` (and plurals such as `boxes`) were not taken as
  packs.
- A quantity in pieces was multiplied by the whole case: JFC prints 30 EACH of
  `3/30/1.93 OZ` at $1.05 = $31.50, which became 30 × 90 pieces.

**Implemented** (`app/transform/receiving.py`)
- `parse_pack` reads a chain of numbers joined by `x`, `*` or `/` with exactly one weight or
  volume measure, in either order; the other numbers multiply into pieces per pack (`PK`, `PC`,
  `EA`, `#` allowed as piece markers). `#` is pounds only when it is the notation's only measure
  (`10/1.1#`, JFC). A chain with no measure, such as an expiry date `3/31/2027`, is skipped.
- `pack_word()` with `PACK_ABBREVIATIONS`; `fz` → fluid ounces.
- Conversion: a quantity in pieces counts pieces (one piece = the stated size); a counted item
  received by the pack gets its pieces (`0.5 BUNDL` of `3.9OZ x 14 x 2 #` → 14). Before, a
  counted item with a weight notation always went to review.
- `normalize_name` strips the same notations from names (alias keys are re-normalized on both
  sides when compared, so learned aliases keep matching).

**Verified**
- 27 new cases in `tests/test_receiving.py`, every notation copied from the four invoices; on
  `main` the file does not even collect (the new helpers do not exist) and the KitKat notation
  parses as (14, 2, lb). The existing receiving tests still pass (`6 x 1L`, `12x500g`, unit
  mismatch flagged).
- Full suite 598 passed, 6 skipped (571 + 27 new); ruff, ruff format, mypy (78 files) clean.

**Gaps recorded, not fixed (extractor and product; for T2.11 and the T4.15 trial)**
- **Barcodes:** printed per line on three of the four. They already help: matching looks for a
  stock item's UPC in the line's `raw_text`, which the model is told to copy as printed. There
  is no separate barcode field, so a barcode in its own column depends on the model including
  it in `raw_text`.
- **Returned and hand-corrected lines:** JFC's tempura line is circled "Return"; Hadson's
  KitKat price is hand-corrected 112 → 126. The prompt says to transcribe only print, so a
  returned line would be received. Needs a product decision on handwritten marks.
- **Ordered vs shipped:** USA Seafood's salmon is 1 case ordered, 46.96 lb shipped at a price per
  lb. The prompt asks for the quantity delivered; not checked against the real model.
- **Multi-page invoices:** JFC (1 of 3) and Hadson (1 of 4); a document holds one photo.
- **Supplier item codes** (JFC `05218`, Hadson `C019`) are not captured; they fit
  `SupplierItem` (T2.10).
- **Central Boeki** prints pieces per box (PC 40) beside a quantity already in pieces (12 EA);
  if the model put 40 into `pack_size` the line would be multiplied. Not checked against the
  real model.

**Files changed**
- `app/transform/receiving.py`, `tests/test_receiving.py`; task index (T0.07 → Completed);
  `RISK_AND_DEPENDENCY_REGISTER.md` (OD-09 partly met); this log.

**Next task**
- T0.08 disable Square write-back.

---

## 2026-11-05 — T0.07 completed

**Verified**
- PR #12 merged by the owner (`9fda108`, merge commit). CI on the PR head `5724f17`, all five
  required jobs green: SQLite 571 passed, 6 skipped; PostgreSQL 16 577 passed, 0 skipped; lint and
  types; dependency audit; Docker image. Same five green on `main` after the merge. Acceptance
  met: `/healthz` answers in under 200 ms while an extraction is held open.
- Still not done: a real invoice through streaming extraction.

**Files changed**
- Task index (T0.07 → Completed), this log.

---

## 2026-11-05 — T0.07 Async correctness (C8)

**Implemented**
- Every route is now plain `def`, so FastAPI runs it in its threadpool and a slow call no longer
  stops the event loop. The audit named four (`upload_invoice`, `apply_receipt`, `scan_expiry`,
  `square_webhook`); the same fault was in six more and they are fixed too: the Stripe webhook,
  the email-settings form, and the snack-box `pack`, `save_snack`, `save_settings`,
  `save_review`, `save_preferences` (all ran synchronous database calls on the event loop).
- `app/web/inputs.py`: `posted_form` and `raw_body`, async dependencies that read the body on the
  event loop before the route starts. Uploads read `photo.file` synchronously in the thread.
- Anthropic clients built with a 60 s timeout (`app/extract/__init__.py`; SDK default 600 s).
  Invoice extraction now streams (`messages.stream` → `get_final_message()`), so the 60 s limits
  a silence, not the whole reading: a non-streamed 16,000-token answer from a model that thinks by
  default can take longer than a minute, and a whole-request limit would have failed long
  invoices. The expiry reader (512 tokens) stays a single request.
- `CLAUDE.md`: routes are plain `def`; bodies come from `app/web/inputs.py`.

**Verified (locally)**
- `tests/test_async_correctness.py`: (1) while an extraction is held open in a worker thread,
  `/healthz` on the same event loop (httpx `ASGITransport`) answers before the extraction
  finishes and in under 200 ms; the acceptance's "sleeping 5 s" is replaced by an extractor held
  until the test releases it, which proves the same without a fixed wait. (2) No route endpoint
  is a coroutine function; the test walks the included routers (FastAPI 0.162 keeps them nested)
  and requires more than 50 routes, so it cannot pass by seeing none. With the old
  `app/web/receiving.py` both tests fail ("/healthz waited for the extraction to finish").
  The first version of these tests passed against the old code too; both flaws found that way
  are fixed above.
- `tests/test_invoice_ai.py`: the fake client supports streaming and records how it was built;
  new test asserts the timeout is at most 60 s.
- Full suite 571 passed, 6 skipped (568 + 3 new); ruff, ruff format, mypy (78 files) clean.

**Files changed**
- `app/web/inputs.py` (new), `app/web/receiving.py`, `scanning.py`, `webhooks.py`, `billing.py`,
  `settings.py`, `boxes.py`, `shop.py`, `app/extract/__init__.py`, `invoice.py`, `expiry.py`;
  `tests/test_async_correctness.py` (new), `tests/test_invoice_ai.py`; `CLAUDE.md`; task index
  (T0.06 → Completed, T0.07 → In review); this log.

**Open issues**
- Extraction still runs inside the upload request (a worker thread for its duration; the default
  threadpool has 40). Moving it to a job is T1.20.
- Streaming extraction has not been run against the real API in this task (tests use a fake;
  a real call would spend the owner's Anthropic credit).

**Decisions made**
- None new (T0.07 spec; streaming follows from its 60 s limit, reasons above).

**Next task**
- T0.08 disable Square write-back; T0.10 structured logs; T0.11 governance scaffolding.

---

## 2026-11-05 — T0.06 completed

**Verified**
- PR #11 merged by the owner (`9ae3707`, merge commit). CI on the PR head `3ad124a`, all five
  required jobs green: SQLite 568 passed, 6 skipped; PostgreSQL 16 574 passed, 0 skipped; lint and
  types; dependency audit; Docker image. Same five green on `main` after the merge. Acceptance
  met: an HTML file sent as a `.jpg` is refused, and a real image is served with its own type and
  `nosniff`; every response carries the headers (tested on representative routes); templates
  render. Probe R7 retired.
- Still not done: a real-browser click-through of the pages the CSP touches (recorded in the
  T0.06 entry).

**Files changed**
- Task index (T0.06 → Completed), this log.

---

## 2026-11-05 — T0.06 Security quick fixes (S3, S10, S11, S12)

**Implemented**
- **S3 stored XSS / S12 content sniffing.** `detect_media_type()` (`app/extract/images.py`) reads
  the type from the bytes: `%PDF-` in the first kilobyte, otherwise Pillow's header read (JPEG,
  phone multi-picture JPEG "MPO", PNG, WebP, GIF; decompression bombs rejected). `save_upload`
  refuses bytes that are none of these, stores them under the suffix for what they are
  (`STORED_SUFFIX`) with that type, and only logs the browser's Content-Type when it disagrees.
  The image route serves the allow-listed type for the stored suffix (never the stored
  `content_type`, which pre-T0.06 rows took from the uploader), `X-Content-Type-Options:
  nosniff`, and `Content-Disposition: inline` (`attachment` + `application/octet-stream` for a
  suffix outside the list).
- **SEC-13 size:** `MAX_UPLOAD_MB` default 20 → 15 (`app/config.py`, `.env.example`); it also
  bounds expiry photos, which share the setting.
- **S10 headers:** `app/web/headers.py`, an ASGI middleware outside everything (static files
  included): CSP with a per-request script nonce (`default-src 'self'`, `object-src 'none'`,
  `base-uri 'self'`, `frame-ancestors 'none'`, Google Fonts allowed, inline styles allowed),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: strict-origin-when-cross-origin`, and HSTS (one year, no subdomains) when the
  request is https. A header a route sets itself is kept.
- Templates: the three inline scripts carry the nonce; the three inline handlers moved into
  script (`data-confirm` + one listener in `base.html`; item form `change` listener; the
  snack-box referral form's action).
- **S11:** Jinja sees `PublicSettings` (base URL, support email, customer-link lifetime) instead
  of the full `Settings` object.
- Probe R7 retired (comment left in `docs/audit/probes/test_audit_probes.py`).
- Upload tests use real sample files (`tests/sample_files.py`); `CLAUDE.md` gains the CSP and
  upload rules.

**Verified (locally)**
- `tests/test_receiving_web.py` (8): HTML named `.jpg` and sent as `text/html` refused (replaces
  R7); a real JPEG sent as `text/html` stored and served as `image/jpeg` with `nosniff` and
  `inline`; PNG named `.jpg` stored and served as PNG; PDF by header; over 15 MB refused; a
  pre-fix row with `text/html` served as `image/jpeg`; an unknown suffix downloads. With the two
  receiving modules stashed, 6 of the 8 fail (the other two cover the size setting and T0.04's
  form fix).
- `tests/test_security_headers.py` (14): headers on `/`, `/login`, `/pricing`, a static file, a
  404 and four signed-in pages; HSTS only over https; every inline script carries that response's
  nonce; a fresh nonce per response; no template has an inline handler, `javascript:` URL or
  un-nonced inline script; templates see no secret.
- Full suite: 568 passed, 6 skipped (547 before + 21 new). ruff, ruff format, mypy (77 files)
  clean; docs checker 0 problems.
- **Not verified:** a real browser. The preview tool in this session reads another project's
  launch configuration, so the CSP was checked by tests and by reading every template and the
  vendored barcode library (no `eval`, no WebAssembly), not by loading pages.

**Files changed**
- `app/extract/images.py`, `app/extract/invoice.py`, `app/orchestration/receiving.py`,
  `app/web/receiving.py`, `app/web/headers.py` (new), `app/main.py`, `app/web/templating.py`,
  `app/config.py`, `.env.example`, 6 templates, `docs/audit/probes/test_audit_probes.py`;
  tests: `sample_files.py`, `test_security_headers.py` (new), `test_receiving_web.py`,
  `test_storage.py`, `test_invoice_ai.py`, `test_receiving_flow.py`, `test_square_writeback.py`,
  `test_deployment.py`; `CLAUDE.md`; task index (T0.04 → Completed, T0.06 → In review); this log.

**Open issues**
- SEC-13 lists JPEG, PNG, HEIC and PDF. WebP and GIF are still accepted (existing behaviour,
  not removed silently); HEIC needs a new dependency (`pillow-heif`), the owner's call.
- No `form-action` in the CSP: checkout and Square connect post to this app, which redirects to
  Stripe and Square, and browsers apply `form-action` to that redirect. Adding it needs those
  hosts listed and a browser check.
- Responses from an unhandled exception (500) are produced outside the middleware and carry no
  headers.
- A real-browser pass over the item form, expiring "Remove", the scan page and checkout.

**Decisions made**
- None new (SEC-08, SEC-09, SEC-13 and the T0.06 spec). Referrer policy
  `strict-origin-when-cross-origin` keeps the customer sign-in token in a query string from
  reaching other sites.

**Next task**
- T0.07 async correctness, T0.08 disable Square write-back, T0.10 structured logs, T0.11
  governance scaffolding (all Ready).

---

## 2026-11-05 — T0.04 completed

**Verified**
- PR #10 merged by the owner (`db03e94`, merge commit, so `.git-blame-ignore-revs` holds). All
  five jobs green on `main`. The owner approved making "Lint and types" required; the "Protect
  main" ruleset now requires all five checks (saved, confirmed after a reload). Acceptance met:
  CI runs ruff and mypy and passes on `main`; the commands are in `CLAUDE.md`.
- The CI-evidence commit below (`5042a2b`) was pushed after the merge started; it is carried
  into this branch.

**Files changed**
- Task index (T0.04 → Completed), this log.

---

## 2026-11-05 — T0.04 Coding standards: ruff + mypy

**Implemented** (ID-04; one commit per kind of change)
- `af1ec6b` config: ruff 0.17.4 and mypy 2.4.1 in the dev group (`uv.lock` gains only them and
  their 4 helper packages). ruff: line length 100; rules pycodestyle, pyflakes, isort, bugbear,
  pyupgrade and bandit (`S`). Ignored, each with its reason in `pyproject.toml`: E501 (the
  formatter owns code width; 85 long strings/comments remain) and UP042 (StrEnum changes `str()`
  output). Tests may assert, import fixtures across modules and use throwaway passwords;
  FastAPI's `Depends`/`Form`/... are allowed as defaults; `docs/` (frozen audit probes) excluded.
  mypy: gradual by default; strict flags for `app.transform.*` and for `app.access.*` and
  `app.ledger.*` before they exist; the five snack-box modules leaving in T0.05 skipped;
  `apscheduler`, `boto3`, `botocore` have no type information and are not checked.
- `7f3cb61` `ruff format .` only (118 files); `f47bbb8` ruff's safe autofixes only (sorted and
  unused imports, `datetime.UTC`). Both listed in the new `.git-blame-ignore-revs`.
- `4c5d47a` remaining lint by hand: sha1 box-rules fingerprint marked `usedforsecurity=False`
  (same digest), `l` → `bl`, `itertools.pairwise`, an f-string; four bandit false positives get
  `noqa` with the reason on the line above (dev secret placeholder that production refuses, a
  migration's constant SQL, the docs test's fixed subprocess, the migration test's seed SQL).
- `9c432bb` the 61 mypy errors left after scoping. Typing only, apart from two paths that
  crashed:
  - **Bug found by mypy:** receiving apply called `.strip()`/`.isdigit()` on form values; a file
    part posted under a text field's name raised AttributeError (500). New `_field()` treats it
    as empty; `tests/test_receiving_web.py` covers it and fails without the fix.
  - The alert digest now raises `LookupError` naming the tenant when alerts exist without a
    tenant row (was an AttributeError inside `allows`).
  - The two Anthropic calls use the SDK's request types; the media type is known only at runtime,
    so the content block is `cast` with that reason in a comment. Sentry's `Event`/`Hint` types
    for the scrubber.
- CI job **Lint and types**: `ruff check`, `ruff format --check`, `mypy`, on the locked tools.
- `CLAUDE.md` (commands, CI, "required CI checks"), `README.md` (lint/type commands; the stale
  "60 tests" note dropped).

**Verified (locally)**
- `ruff check .` all passed; `ruff format --check .` 131 files formatted; `mypy` no issues in 76
  files; strict flags pass on all 15 `app/transform` modules.
- Full suite after all changes: 546 passed, 6 skipped (unchanged); the new test passes, and fails
  with `AttributeError: 'UploadFile' object has no attribute 'strip'` when the fix is stashed.
- Workflow YAML parses (five jobs); docs checker 0 problems.
- **CI on PR #10 (commit `6fe3edb`), all five jobs green:** Lint and types ("131 files already
  formatted", mypy "no issues found in 76 source files"); SQLite 547 passed, 6 skipped, docs 0
  problems; PostgreSQL 16 553 passed, 0 skipped; dependency audit; Docker image.

**Files changed**
- `pyproject.toml`, `uv.lock`, `.git-blame-ignore-revs` (new), `.github/workflows/ci.yml`,
  `CLAUDE.md`, `README.md`, `tests/test_receiving_web.py` (new); 119 Python files changed in all
  (118 by the formatter, typing fixes in 19); `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md`
  (T0.04 → In review), this log.

**Open issues**
- "Lint and types" is not a required check until the owner adds it to "Protect main".
- `.git-blame-ignore-revs` lists the two mechanical commits by SHA: merge this PR with a merge
  commit (as before), not squash or rebase, or the SHAs change.
- The expiry reader forces `tool_choice` to its tool. That works on the configured
  `claude-opus-5`; Claude Opus 5.5, Sonnet 5.5 and Fable 5.1 reject forced tool choice, so a
  future model change there must move it to `auto` or structured output.
- 85 lines over 100 columns remain (strings and comments); not enforced.

**Decisions made**
- None new (ID-04). Scoping choices recorded above with their reasons.

**Next task**
- Stage 0 tasks now Ready: T0.06 security quick fixes, T0.07 async correctness, T0.08 disable
  Square write-back, T0.10 structured logs, T0.11 governance scaffolding. T0.05 still waits for
  the owner's snack-box repository.

---

## 2026-11-05 — T0.03 completed; Dependabot set to lockfile-only

**Implemented**
- `.github/dependabot.yml` (uv): `versioning-strategy: lockfile-only`, and major updates of
  `python-dateutil` ignored, each with a comment giving the reason.

**Verified**
- PR #4 merged by the owner (`d2957b8`); PR #3's commit came with it and GitHub marked PR #3
  merged. All four required checks green on `main` at `d2957b8`. Acceptance of T0.03 met: the
  image installs exactly the locked versions (Docker image job), pip-audit runs in CI with no
  finding.
- Dependabot's first run on `main` (config valid; 4 of 6 update jobs succeeded) opened PRs #5–#8.
  Each only raised a minimum in `pyproject.toml`, the default "increase" strategy for
  applications: #5 setuptools (build-system table, unused with `package = false`), #6 uvicorn (lock
  unchanged), #7 psycopg and #8 sentry-sdk, which would have **downgraded** the lock (3.3.8 →
  3.3.6, 2.74.0 → 2.73.0).
- The 2 failed jobs: both "dependency_file_not_resolvable" for `python-dateutil` 3.x, because
  botocore requires `python-dateutil>=2.1,<3.0.0`.
- GitHub's Dependabot options reference lists `uv` among the ecosystems that support
  `versioning-strategy`, and `lockfile-only` means pull requests only for lockfile updates.
- Dependabot YAML parses; docs checker 0 problems.
- **Not verified yet:** Dependabot's next run under the new configuration (it runs after this
  merges).

**Files changed**
- `.github/dependabot.yml`, `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md` (T0.03 → Completed), this
  log.

**Open issues**
- The owner closes PRs #5–#8 after this merges.
- Merged branches `docs/t0.02-protection`, `docs/record-t0.02` and `t0.03-lock-deps` still on
  GitHub.

**Decisions made**
- None new: lockfile-only keeps ID-05's intent (the lock is the source of exact versions; ranges
  in `pyproject.toml` change only by hand).

**Next task**
- T0.04 coding standards: ruff + mypy.

---

## 2026-11-05 — T0.03 Lock dependencies, audit, Dependabot

**Implemented**
- `uv.lock` (uv 0.13.7, ID-05): 68 packages resolved from `pyproject.toml`; `.python-version` = 3.12.
- `pyproject.toml`: dev tools moved from `[project.optional-dependencies]` to `[dependency-groups]`
  so they never reach the image; `[tool.uv] package = false` — the repository is an application
  and setuptools' flat-layout discovery refuses `app/` + `migrations/` (CI run #1's failure).
- `Dockerfile`: copies uv from the pinned `ghcr.io/astral-sh/uv:0.13.7` image, exports the runtime
  lock (no dev group) and installs it with `pip install --require-hashes`; uv is removed after.
- `.github/workflows/ci.yml`: both test jobs install with `uv sync --frozen` (fails if the lock is
  stale against `pyproject.toml`). New job **Dependency audit**: `uv export` of every locked
  package, `pip-audit==2.10.1 --disable-pip` (no install). New job **Docker image**: builds the
  image, lists its installed distributions, `scripts/check_lock_matches.py` fails on any version
  that differs from the lock or any package not in it.
- `.github/dependabot.yml`: weekly for uv (minor + patch grouped), GitHub Actions and Docker.
- `README.md` quickstart and `CLAUDE.md` (dependencies, tests, CI) switched to uv.

**Verified (locally)**
- `uv lock --check` consistent; runtime export: 60 hashed pins, no pytest.
- pip-audit on the full export: "No known vulnerabilities found".
- `check_lock_matches.py`: passes on an environment installed from the export; fails (exit 1) on
  a planted version mismatch.
- Full suite on the locked versions (fresh venv from the export): 546 passed, 6 skipped (the
  Postgres-only tests; the CI Postgres job runs them).
- Workflow and Dependabot YAML parse; docs checker 0 problems.
- **CI on PR #4 (commit `7202e41`), all four jobs green:** SQLite 546 passed, 6 skipped, docs
  0 problems; PostgreSQL 16 552 passed, 0 skipped; audit "No known vulnerabilities found"; Docker
  image built, "checked 58 installed packages against 68 locked" with no mismatch (58 = the 60
  runtime pins minus `tzdata`, Windows only, and `httpx2-jsfetch`, browser only).

**Files changed**
- `uv.lock`, `.python-version`, `scripts/check_lock_matches.py`, `.github/dependabot.yml` (new);
  `pyproject.toml`, `Dockerfile`, `.github/workflows/ci.yml`, `README.md`, `CLAUDE.md`,
  `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md` (T0.03 → In review), this log.

**Open issues**
- Resolved: the owner approved making "Dependency audit" and "Docker image" required; the
  "Protect main" ruleset now requires all four checks (saved and confirmed after a reload). PR #3
  (built before these jobs existed) can no longer merge on its own; PR #4 contains its commit and
  replaces it.
- The audit fails on any known vulnerability, not only critical ones (pip-audit has no severity
  filter); an accepted finding needs `--ignore-vuln` plus an ADR with a fix date.
- Existing local `.venv` folders were made with pip; `uv sync` replaces them.

**Decisions made**
- None new (ID-05 chose uv; the "uv" Dependabot ecosystem replaces "pip" because the lock is uv's).

**Next task**
- T0.04 coding standards: ruff + mypy (after this PR merges).

---

## 2026-11-05 — T0.02 completed: protection of `main` proven

**Implemented**
- Nothing new in code; this entry records the acceptance evidence for T0.02.

**Verified**
- **Direct push rejected:** pushing a commit straight to `main` returned "GH013: Repository rule
  violations found for refs/heads/main … 2 of 2 required status checks are expected"; `main` was
  unchanged (`c3dcaa9`).
- **Failing pull request blocked:** throwaway PR #2 (branch `ci/blocked-merge-check`, one test
  that asserts False) — both required checks "Failing after 2m · Required", and the "Merge pull
  request" button disabled. The owner closed PR #2 and deleted the branch.
- **Normal flow works:** PR #1 (documentation) passed both checks and was merged by the owner
  (`84a98a1`).
- Acceptance of T0.02 met: both jobs green on `main` (run #3), Postgres job with 0 skipped tests,
  required checks enforced, a failing test blocks merging.

**Files changed**
- `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md` (T0.02 → Completed), this log.

**Open issues**
- Merged branch `docs/t0.02-protection` still exists on GitHub (harmless; GitHub's
  "Automatically delete head branches" setting would remove such branches after merge).

**Decisions made**
- None.

**Next task**
- T0.03 lockfile, dependency audit, Dependabot (now Ready: T0.02 completed).

---

## 2026-11-05 — T0.02 CI on GitHub Actions

**Implemented**
- `.github/workflows/ci.yml`: runs on push to `main`, pull requests and manual dispatch; read-only
  permissions; superseded runs cancelled. Job **Tests (SQLite)**: install, `scripts/check_docs.py`,
  full suite. Job **Tests (PostgreSQL 16)**: `postgres:16-alpine` service, `TEST_DATABASE_URL` set
  (the existing conftest switch), full suite, and the step fails if any test is skipped so the
  Postgres-only tests must run. Python 3.12 (as the Dockerfile). Actions pinned to commit SHAs:
  `actions/checkout` v7.0.1 `3d3c42e…`, `actions/setup-python` v7.1.0 `6d4fd22…` (resolved with
  `git ls-remote`; both are lightweight tags, so the SHAs are commits).
- Audit probes stay out of CI: pytest collects only `tests/` (`pyproject.toml` testpaths).
- `CLAUDE.md`: CI line added.

**Verified**
- Workflow parses (PyYAML): two jobs, three triggers, steps as listed; Postgres URL matches the
  `postgresql` prefix the migration tests require.
- Locally: docs checker 0 problems; suite on SQLite passed in T0.01 (545 + the docs contract test).
- **Not verified yet:** an actual GitHub run (needs the push) and the PostgreSQL job (Docker is not
  running on this machine; the earlier audit run on Postgres 16 was 548/548).
- **Correction after the first run (run #1, commit `5db2399`): both jobs failed at Install.**
  setuptools refused the editable install ("Multiple top-level packages discovered in a
  flat-layout: ['app', 'migrations']") — the earlier local `top_level.txt` was not evidence for a
  fresh build. Fixed by installing dependencies only from `pyproject.toml` (as the Dockerfile
  does); the generator was run locally and lists all 21 requirements.
- **Run #2 (commit `fd1e3f0`):** Tests (SQLite) **passed** on GitHub — docs checker "0 problems",
  suite **546 passed, 6 skipped** in 1 m 43 s. Tests (PostgreSQL 16) failed before any test:
  Docker could not publish the database port ("failed to start userland proxy for port mapping
  0.0.0.0:5432 … timed out starting the userland proxy").
- **Fixes after run #2:** the Postgres job now runs in a `python:3.12-slim` container on the
  database's network and connects to host `postgres` (no published port); a workflow-level
  `shell: bash` default turns on `pipefail`, because GitHub's implicit shell does not, which would
  have let `pytest | tee` hide a failing run (demonstrated locally: exit 3 propagates).
- **Run #3 (commit `c3dcaa9`): both jobs green** (3 m 47 s). Tests (SQLite): docs checker "0
  problems", **546 passed, 6 skipped**. Tests (PostgreSQL 16, server 16.15, service healthy):
  **552 passed, 0 skipped** — the 6 Postgres-only tests ran. Read from the raw job logs.
- **Required check (OD-04), checked in GitHub settings after the owner enabled it:** the
  Branches page lists no branch protection rule; the Rulesets page shows none and states "Your
  rulesets won't be enforced on this private repository until you move to GitHub Team
  organization account." Not in effect; owner decision recorded as open in the risk register.
- Owner declined a plan upgrade and asked for the protections to be added. A ruleset "Protect
  main" was filled in through the settings page (Active; default branch; required checks
  Tests (SQLite) and Tests (PostgreSQL 16); block force pushes; restrict deletions) and submitted;
  GitHub did not save it — the Rulesets page still reports none. The classic branch-protection
  form carries the same notice ("won't be enforced on this private repository until you move to a
  GitHub Team or Enterprise organization account"). Server-side protection of `main` is not
  available on this plan.
- **Owner made the repository public** so the ruleset is enforced. The owner's saved ruleset
  had "Require status checks to pass" ticked but **no checks listed**; both checks were added and
  saved (verified after reload): Tests (PostgreSQL 16), Tests (SQLite). Final ruleset: Active ·
  default branch · restrict deletions · required checks with "up to date before merging" and "not
  required on creation" · block force pushes · empty bypass list. OD-04 done.
- **Public-exposure checks:** all 25 commits scanned — the only env file ever committed is
  `.env.example`; no secret-shaped strings; secret settings only ever had placeholder or test
  values; no uploads, images or databases ever committed. **Commit metadata carries the owner's
  personal email** on every commit (now public); rewriting history would need a force push, which
  the ruleset blocks and which is not recommended. The planning documents (pricing hypotheses,
  validation plans, JuJu context) are now public too.
- `CLAUDE.md`: pull-request workflow — `main` is never pushed to directly.
- **T0.01 → Completed** (its done-definition: merged and run by CI). **T0.02 stays In review**
  until the owner requires the check on `main` (OD-04) and a deliberately failing branch shows a
  blocked merge.

**Files changed**
- `.github/workflows/ci.yml` (new), `CLAUDE.md`, `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md`
  (status), this log.

**Open issues**
- Acceptance still needs: both jobs green on `main`; Postgres job with 0 skips; the owner enabling
  "require status checks" on `main` (OD-04) and one failing-test branch to prove a merge is blocked.
- T0.01 moves to Completed after the first green run.

**Decisions made**
- None (pinned versions are the latest releases of the two official actions).

**Next task**
- Owner: approve commit + push to trigger the first run. Then T0.03 (lockfile, audit, Dependabot).

---

## 2026-11-05 — Documentation contract and checker

**Implemented**
- `scripts/check_docs.py`: one checker for links/anchors (product, audit, roadmap), product ID
  definitions, traceability coverage of all spec requirements, roadmap task/decision/requirement
  references, index dependency order and entry/index agreement, and alignment of the master
  roadmap, task index and migration roadmap (milestone ranges, critical-path arrows, DM step
  ownership, naming and ordering). Exit code 1 on any problem.
- `tests/test_docs_contract.py`: runs the checker inside the test suite, so CI (T0.02) enforces it.
- Governance: roadmap README §4.4 "The development contract" (master roadmap authoritative;
  distinct task responsibilities such as T4.16 vs T4.17; explicit migration ownership; the checker
  proves references and ordering, not correctness; critical-path blockers resolved deliberately);
  `CLAUDE.md` documentation-contract rule; T0.05 marked as on the critical path (task entry, index,
  OD-02, handoff).

**Verified**
- Checker on the working tree: 87 tasks, 231 requirements, 0 problems.
- Negative test on a scratch copy with six planted errors (broken link, dropped requirement, forward
  dependency, entry/index mismatch, false critical-path arrow, migration step not named by its
  owner): all six reported, exit code 1.
- Checker and contract test run on a clean export of exactly the committed content (see commit).

**Files changed**
- `scripts/check_docs.py`, `tests/test_docs_contract.py` (new); `CLAUDE.md`; `docs/roadmap/` README,
  DEVELOPMENT_TASK_BREAKDOWN, RISK_AND_DEPENDENCY_REGISTER; this log.

**Open issues**
- T0.01 code remains uncommitted at the owner's request; T0.05 blocked on the owner's repository.

**Decisions made**
- None.

**Next task**
- First unfinished Stage 0 tasks: T0.06, T0.07, T0.08, T0.10, T0.11 (Ready); T0.02 after T0.01 is
  committed.

---

## 2026-11-05 — Roadmap, task breakdown and migration roadmap aligned (docs only)

**Implemented**
- Milestones stay as defined in MASTER_DEVELOPMENT_ROADMAP (M0 safe to build · M1 tenancy & access ·
  M2 ledger engine · M3 operations engine · M4 Standalone end-to-end · M5 Square connected · M6
  launch-ready → G-R1 · M7 R1.1 · M8–M10 expansion). The task index gained a **Milestone** column
  and the breakdown a milestone summary pointing to the master roadmap as authoritative.
- New **T4.16 Contract migration** owns DM-16, which had no task; the launch gate review is now
  **T4.17** (references renumbered in the roadmap, task breakdown, migration roadmap and security
  checklist).
- Dependencies: T1.01 now follows T0.05 (DM-1 before DM-2); T1.02 follows T1.01 (DM-2 precedes
  every new table); T4.17 names T4.16.
- Every task that owns a migration step names it (DM-1..DM-16); T2.01 backfills products from
  `sold_items` (DM-9); T2.11 creates history receivings for applied documents (DM-12).
- Master roadmap critical path rewritten so every arrow is a real dependency; Stage 0/1 exit
  criteria and gates G0, G1, G2, G5, G-R1 now retire each probe in the milestone whose task retires
  it (R3 in G0; PA1, PA5 in G1; PA4 in G5; PA2 in G-R1).

**Verified**
- Script: every master-roadmap milestone range matches the index; no task depends on a later
  milestone; every critical-path arrow is a dependency; every DM step has an owning task that
  precedes what it must precede and names the step. Task, decision and requirement IDs defined;
  index dependencies point backwards and match each task entry; all links and anchors resolve.

**Files changed**
- `docs/roadmap/` MASTER_DEVELOPMENT_ROADMAP, DEVELOPMENT_TASK_BREAKDOWN, DATA_MIGRATION_ROADMAP,
  TESTING_AND_QUALITY_STRATEGY, SECURITY_AND_RELEASE_CHECKLIST, README; this log.

**Open issues**
- Uncommitted, together with T0.01.

**Decisions made**
- None: milestone definitions unchanged; the proposed alternative numbering (launch at M7) was not
  adopted because six documents already use the existing definitions.

**Next task**
- Owner: commit when ready. Then T0.02.

---

## 2026-11-05 — T0.01 Hermetic test configuration

**Implemented**
- `app/config.py`: `Settings.settings_customise_sources` drops the `.env` source when
  `APP_ENV=test`; development and deployed environments still read `.env` as before.
- `tests/conftest.py`: before pinning test values, removes every inherited environment variable
  that names a `Settings` field, so neither a developer's `.env` nor their shell can change results;
  `APP_BASE_URL` pinned to `http://localhost:8000` (the value the audit's passing runs used — the
  task text suggested `http://testserver`; the existing default keeps results identical).
- `tests/test_settings.py`: 3 tests — `.env` ignored under test, still read with `APP_ENV=dev`,
  base URL not inherited.
- `CLAUDE.md`: test command note updated (no pin needed any more).

**Verified**
- Full suite, SQLite, developer `.env` present (with an https `APP_BASE_URL`), no variables pinned:
  **545 passed, 6 skipped** (the 6 are the Postgres-only tests). Before this change the same run
  failed 46 tests (audit T1).
- Same run with a hostile shell environment (`APP_BASE_URL=https://…`, `APP_ENV=production`, Stripe,
  Square webhook key, SMTP host set): **545 passed, 6 skipped**.
- Audit probes without the pin: 13 passed (they still confirm defects later tasks fix).
- Not run: PostgreSQL (needs Docker; T0.02 adds it to CI).

**Files changed**
- `app/config.py`, `tests/conftest.py`, `tests/test_settings.py` (new), `CLAUDE.md`,
  `docs/roadmap/DEVELOPMENT_TASK_BREAKDOWN.md` (status), this log.

**Open issues**
- Status In review until merged and run by CI (T0.02 done-definition).

**Decisions made**
- None beyond the base-URL value noted above.

**Next task**
- T0.02 CI on GitHub Actions (depends on T0.01); T0.06, T0.07, T0.08, T0.10, T0.11 are Ready.

---

## 2026-11-05 — Owner approved implementation decisions ID-01..ID-23 (docs only)

**Implemented**
- `PRODUCT_DECISIONS.md`: C-43 (ID-01..ID-23 approved as recommended) and C-44 (27 pending product
  recommendations approved via ID-14: PD-05, 07, 08, 10, 11, 12, 14, 15, 16, 17, 19, 20, 22, 23, 24,
  25, 26, 27, 29, 30, 32, 36, 37, 38, 39, 40, 41); those PD rows marked decided; PD-33 marked
  deferred to Stage 6. Still open: PD-06, PD-33, PD-34, PD-35.
- `SAAS_REQUIREMENTS.md` (trial, sales tax, refunds, retention) and `PRODUCT_SPECIFICATION.md`
  (PD-15) no longer show those items as open.
- Roadmap: decisions marked approved; T0.09 now blocked only on owner accounts (OD-03); OD-01 done;
  README handoff updated. Product README §6–7 updated.

**Verified**
- Scripts: links and anchors resolve across `docs/`; product IDs all defined; roadmap task,
  decision and requirement references consistent; index dependencies valid.

**Files changed**
- `docs/product/` PRODUCT_DECISIONS, SAAS_REQUIREMENTS, PRODUCT_SPECIFICATION, README;
  `docs/roadmap/` IMPLEMENTATION_DECISIONS, DEVELOPMENT_TASK_BREAKDOWN, RISK_AND_DEPENDENCY_REGISTER,
  README; this log.

**Open issues**
- Owner dependencies OD-02..OD-11 (snack-box repository, accounts, branch protection, content,
  invoices, JuJu values); validation decisions O-1..O-7; questions Q-1..Q-5. Nothing committed yet.

**Decisions made**
- None by the agent.

**Next task**
- T0.01 hermetic test configuration (Ready), on the owner's go-ahead.

---

## 2026-11-05 — Phase 3 roadmap and implementation handoff (docs only)

**Implemented**
- `docs/roadmap/`: 15 documents — README (governance, Claude Code handoff), gap analysis (56
  capability rows), target architecture, target domain and data architecture, operating-mode plan,
  SaaS and pricing plan, master roadmap (Stages 0–8, M0–M10, gates G0–G5 and G-R1), task
  breakdown (86 detailed tasks for Stages 0–4 + outline tasks for 4B–8, status index), data
  migration roadmap (DM-1..DM-16), testing and quality strategy, security and release checklist,
  risk and dependency register, customer validation plan, JuJu validation plan, implementation
  decisions (ID-01..ID-23).
- Repository-root `CLAUDE.md`: project instructions for implementation sessions (ID-19).
- `docs/product/README.md` §7: Phase 3 marked complete.

**Verified**
- Script checks: all task IDs, decision IDs and requirement IDs referenced in the roadmap are
  defined; index dependencies only point to earlier tasks and match each task's own entry;
  269 relative links and anchors across `docs/roadmap/`, `docs/audit/` and `docs/product/` resolve.
- The customer and JuJu validation plans were drafted by delegated agents and reviewed: no
  fabricated evidence; owner inputs marked.

**Files changed**
- `docs/roadmap/*` (new), `CLAUDE.md` (new), `docs/product/README.md`, this log. No application code,
  schema, configuration or dependency changed; no migration run; nothing deployed.

**Open issues**
- ID-01..ID-23 await owner approval; owner dependencies OD-01..OD-11; validation-plan decisions
  O-1..O-7; questions Q-1..Q-5.

**Decisions made**
- None by the agent.

**Next task**
- Owner: approve the IDs. Agent, on go-ahead: T0.01 (hermetic tests), the first Ready task.

---

## 2026-11-05 — Repository on GitHub (docs only)

**Implemented**
- Audit: TR-01 (only copy on one laptop) and OA-1 (push to GitHub) marked resolved; confirmed
  finding 10 updated.

**Verified**
- `origin` = `github.com/Souru0712/InventoryOS`; `main` in sync with `origin/main` at `601540b`
  (includes the owner's `scripts/send_test_webhook.py` commit). Unauthenticated GitHub API returns
  404, consistent with a private repository. Only `.env.example` is tracked among env/db files.

**Files changed**
- `docs/audit/TECHNICAL_RISK_REGISTER.md`, `docs/audit/AUDIT_DECISIONS_AND_OPEN_QUESTIONS.md`,
  this log.

**Open issues**
- No CI yet (TR-14). Local branch `docs/inventoryos-phase-1` (at `3fb4afa`, already in `main`'s
  history) exists only locally.

**Decisions made**
- None.

**Next task**
- Agent, on go-ahead: Phase 3 plan.

---

## 2026-11-04 — Owner answered the four audit questions (docs only)

**Implemented**
- `PRODUCT_DECISIONS.md`: C-37 resolved — the snack-box module moves to its own project, not
  deleted (owner's side project for a Japanese-snack convenience store); A-01 gains a data point
  (owner's store uses Toast; Square stays first); §7 D8 host: no budget ceiling, choose for
  compatibility, plan fit and automation, decided in Phase 3.
- `PRODUCT_GLOSSARY.md`: "Snack-box project" defined as separate from InventoryOS.
- Audit: questions §5 answered; U3 points to the owner's real invoices; OA-4 added; risk TR-31
  (no pilot businesses, requirements unvalidated) added; README range TR-01..31.

**Verified**
- Scripts: 192 links and anchors resolve; 659 referenced IDs defined in `docs/product/`.

**Files changed**
- `docs/product/PRODUCT_DECISIONS.md`, `docs/product/PRODUCT_GLOSSARY.md`,
  `docs/audit/AUDIT_DECISIONS_AND_OPEN_QUESTIONS.md`, `docs/audit/TECHNICAL_RISK_REGISTER.md`,
  `docs/audit/README.md`, this log.

**Open issues**
- OA-1 push to GitHub; nothing committed yet. Hosting choice deferred to Phase 3.

**Decisions made**
- None by the agent. Read "we want Square integration first" as Square remaining the first POS
  adapter; the build order C-27 (engine → Standalone → Square) is unchanged.

**Next task**
- Agent, on go-ahead: Phase 3 plan.

---

## 2026-11-04 — Owner approved the Phase 2 decisions AD-01..10 (docs only)

**Implemented**
- `docs/product/PRODUCT_DECISIONS.md`: C-33 scoped data access for tenant isolation (was PD-18);
  C-34 the ledger owns all stock, Square write-back off until the R1.1 outbox (was PD-09); C-35
  replace the inventory engine; C-36 InventoryOS owns the product catalog; C-37 snack-box module
  leaves before the Square pilot (was PD-28); C-38 items per organization; C-39 keep migration
  history; C-40 no pilots on the old engine; C-41 retire Starter/Pro; C-42 JuJu parity by
  specification until R1 (was PD-03). Contradiction 10 resolved; §7 audit mapping updated.
- Propagated to SAAS_REQUIREMENTS (SAAS-T-04), DOMAIN_MODEL (DR-09, `pos_correction`),
  INTEGRATION_REQUIREMENTS (§3.4 write-back off in R1), PRODUCT_SPECIFICATION (POS-FR-13),
  JUJU_REFERENCE_AND_PARITY, product README (decided list, Phase 2 complete); audit decisions doc,
  baseline `12-decisions.md`, risk register, SaaS, integration, migration, reuse and JuJu documents.

**Verified**
- Scripts: 192 relative links and anchors resolve; 659 referenced IDs defined in `docs/product/`;
  traceability still covers all 231 FR IDs exactly once.

**Files changed**
- `docs/product/*` (8 files), `docs/audit/*` (9 files), this log. No application code.

**Open issues**
- No P0 decision remains open. Owner actions: push this repository to GitHub (OA-1); four audit
  questions remain (snack-box plans, real invoices, pilots, hosting); the fifth was answered by C-38.
- Nothing committed yet.

**Decisions made**
- None by the agent. Clarified, not changed: under C-34 Square counts never change stock in R1;
  the blueprint's existing R1.1 write-back design (`pos_correction`) is unchanged.

**Next task**
- Owner: push to GitHub; answer the audit questions. Agent, on go-ahead: Phase 3 plan.

---

## 2026-11-04 — Phase 2 audit of existing projects (read-only)

**Implemented**
- `docs/audit/`: 13 Phase 2 documents (README, inventory project technical/database/integration
  audits, JuJu technical audit, JuJu ↔ InventoryOS comparison, requirements traceability, SaaS
  readiness, operating-mode readiness, reuse/adapt/rebuild matrix, risk register, data migration
  assessment, decisions and open questions). Baseline audit files 00–12 kept as evidence.
- `docs/audit/probes/test_phase2_probes.py`: 5 probes (PA1–PA5) asserting current behaviour; outside
  `tests/`, never collected by the suite.

**Verified**
- Existing app: 542 passed, 6 skipped (SQLite, `APP_BASE_URL` pinned); baseline probes 8/8; Phase 2
  probes 5/5. PostgreSQL run not repeated (548/548 earlier today).
- Traceability covers all 231 spec FR IDs exactly once (script). 188 relative links and anchors
  across `docs/audit/` and `docs/product/` resolve (script). No secret-shaped strings in the docs.
- JuJu: code read at `ae1e702`; tests not run (Node 24.15.0 < 24.22.2); post-CI diff is docs-only.
  JuJu repository unchanged.

**Files changed**
- `docs/audit/*` (new), this log. No application code, schema, config or dependency changed.
  Phase 1 documents not edited; `docs/product/README.md` §7 still shows Phase 2 "Not started" until
  the owner reviews this audit.

**Open issues**
- Owner decisions AD-01..10 (AD-01 = PD-18, P0); owner action OA-1 (push to GitHub).
- Not done: dependency vulnerability scan, load test, accessibility review, any live integration run.

**Decisions made**
- None by the agent.

**Next task**
- Owner: review the audit; answer AD-01..10 and the five questions. Phase 3 only on go-ahead.

---

## 2026-11-04 — Owner decision recorded: PD-21 login approach (docs only)

**Implemented**
- `PRODUCT_DECISIONS.md`: C-32 finish the existing in-app authentication (option 1): verification,
  reset, throttling (C-31), revocable server-side sessions and TOTP MFA; PD-21 marked decided.
- `SAAS_REQUIREMENTS.md`: options table relabelled as options considered, with the decision.
- README: PD-21 removed from open decisions; Phase 2 now sizes the login work.

**Verified**
- Script check: 0 broken links or anchors; all 649 referenced IDs defined.

**Files changed**
- `docs/product/` PRODUCT_DECISIONS, SAAS_REQUIREMENTS, README; this log.

**Open issues**
- Only P0 left: PD-18 (how the code enforces tenant isolation), decided in Phase 2.
- 16 P1 decisions remain open; they block only their own areas of the R1 build.

**Decisions made**
- None by the agent.

**Next task**
- Agent, on the owner's go-ahead: Phase 2 audit against `docs/product/`.

---

## 2026-11-04 — Owner decisions recorded: PD-13, PD-18 (product side), PD-21 must-haves (docs only)

**Implemented**
- `PRODUCT_DECISIONS.md`: C-29 release plan approved (was PD-13); C-30 one configurable product for
  every target segment with each business's data separated (owner's answer to PD-18); C-31 login
  must include verification, reset and throttling. PD-18 narrowed to the technical enforcement
  mechanism (Phase 2); PD-21 points to the options and recommends option 1.
- `SAAS_REQUIREMENTS.md`: new SAAS-T-11 (one codebase and schema for every tenant, differences as
  configuration); SAAS-A-02/03/05 marked confirmed; the PD-21 options table now compares in-app
  auth, a managed provider and a self-hosted identity server, with a recommendation.
- `PRODUCT_SPECIFICATION.md` release plan marked confirmed; README open-decision table updated.

**Verified**
- Script check: 0 broken links or anchors; all 648 referenced IDs defined.
- Facts behind the PD-21 options checked in code: bcrypt hashing (`app/security.py`), Starlette
  signed-cookie sessions (`app/main.py`), SMTP email (`app/config.py`, `app/notify/email.py`).

**Files changed**
- `docs/product/` PRODUCT_DECISIONS, SAAS_REQUIREMENTS, PRODUCT_SPECIFICATION, README; this log.

**Open issues**
- PD-21 needs the owner's choice of option. PD-18 mechanism is decided in Phase 2.

**Decisions made**
- None by the agent.

**Next task**
- Owner: choose the PD-21 option. Agent, on go-ahead: Phase 2 audit against `docs/product/`.

---

## 2026-11-04 — Phase 1 package checked against the re-issued brief (docs only)

**Implemented**
- Checked all 14 `docs/product/` files against every section of the re-issued Phase 1 brief instead
  of regenerating them, so the owner decisions C-24..C-28 are kept.
- Repository rename: references to the `POS-Inventory-Automation` folder updated to `InventoryOS`
  (README, vision, glossary, decisions); audit citations became relative links. The glossary no
  longer says InventoryOS "has no codebase", which contradicted C-25.
- `BUSINESS_WORKFLOWS.md`: WF-23 Employee operations gained steps and the full field table (it was
  the only one of the brief's 24 workflows without them); WF-17 gained explicit steps.
- `NON_FUNCTIONAL_REQUIREMENTS.md`: new §3a Scalability (SCL-01..07, with a ledger-growth sizing
  note); header points to SaaS for multi-tenancy, subscription lifecycle and retention.
- `PRODUCT_SPECIFICATION.md`: tap-count principle aligned with WF-23 and AC (open a task within 2
  taps; waste saved within 3 taps of choosing the item).
- `PRODUCT_DECISIONS.md`: contradictions 12 (brief's "don't assume the codebase is InventoryOS" vs
  C-24/C-25 — resolved: owner confirmed "Keep C-24/C-25") and 13 (existing `docs/audit/` vs "don't
  do Phase 2", resolved).

**Verified**
- Script check: 14/14 deliverables present; 0 broken links or anchors; all 644 referenced IDs
  defined. A planted bad ID and bad anchor were both caught.

**Files changed**
- `docs/product/` README, PRODUCT_VISION, PRODUCT_GLOSSARY, PRODUCT_DECISIONS,
  PRODUCT_SPECIFICATION, BUSINESS_WORKFLOWS, NON_FUNCTIONAL_REQUIREMENTS, INTEGRATION_REQUIREMENTS,
  JUJU_REFERENCE_AND_PARITY; this log. No application code changed.

**Open issues**
- P0 still open: PD-13 (R1 scope), PD-18 (tenant isolation), PD-21 (auth).
- StockPulse branding remains in code (`pyproject.toml` name `stockpulse`, `app/config.py`, etc.).

**Decisions made**
- None by the agent. Owner confirmed C-24/C-25 stand (contradiction 12).

**Next task**
- Owner: answer PD-13. Agent, on go-ahead: Phase 2 audit against `docs/product/`.

---

## 2026-11-04 — Owner decisions recorded: PD-01, PD-02, PD-31, PD-42 (docs only)

**Implemented**
- `docs/product/PRODUCT_DECISIONS.md`: C-24 InventoryOS is the new name for StockPulse; C-25 build on
  this codebase (repo rename pending, by the owner); C-26 target 21 paying customers, $1,000 MRR gates
  new revenue products; C-27 build order engine → Standalone → Square; C-28 `docs/product/` is the
  permanent docs home (follows from C-25). PD-03 and PD-21 narrowed by C-25.
- Propagated to README, vision, pricing, spec, glossary, JuJu parity and SaaS docs.

**Verified**
- Script check: 0 broken links; all PD/DR/AC/A/C/E/WF/FR references defined.

**Files changed**
- `docs/product/*`, this log.

**Open issues**
- StockPulse branding remains in code (`pyproject.toml` name, templates, `app/config.py`, marketing
  pages) — rebrand is code work, not done here.
- P0 still open: PD-13 (R1 scope), PD-18 (tenant isolation), PD-21 (auth).

**Decisions made**
- None by the agent; C-28 recorded as a direct consequence of C-25.

**Next task**
- Owner: rename the repository; answer PD-13. Agent, on go-ahead: Phase 2 audit against `docs/product/`.

---

## 2026-11-04 — InventoryOS Phase 1 revised: pricing by POS situation, operating modes (docs only)

**Implemented**
- Revised `docs/product/` to the owner's revised Phase 1 brief: files renamed to the brief's names
  (no numeric prefixes); new `PRICING_AND_PACKAGING.md`; vision rewritten (8 segments, journeys,
  competitive positioning, outcomes); workflows rewritten (24 + 4 supporting, full fields); roles
  rewritten (8 roles, location scope); spec gained operating modes, Module 15 Standalone
  Operations, UX structure, roadmap boundaries, prioritization matrix; domain gained
  InventoryTransaction, SalesTransaction/SalesSource, Plan/Entitlement, Transfer, DR-29..36;
  decisions rewritten with rationale/alternatives (C-01..23, PD-01..42, A-01..14, contradictions).
- The $49 single plan is superseded by the pricing hypotheses (Complete $99/location, Standalone
  $99, InventoryOS POS $149–199 future, Multi-location ~$149–199+, Advanced ~$199–299+).

**Verified**
- Script check: 0 broken links/anchors; every referenced PD, DR, AC, A, C, E, WF and FR ID is defined.
- JuJu `git status` clean; this repository changed only under `docs/` (untracked).

**Files changed**
- `docs/product/*` (13 renamed/updated + `PRICING_AND_PACKAGING.md` added).

**Open issues**
- P0: PD-01, PD-02, PD-13, PD-18, PD-21, PD-31, PD-42.

**Decisions made**
- None by the agent beyond recommendations recorded in `PRODUCT_DECISIONS.md` §2.

**Next task**
- Owner reviews Phase 1. Phase 2 (existing-project audit) only on the owner's go-ahead.

---

## 2026-11-04 — InventoryOS Phase 1: product blueprint (docs only)

**Implemented**
- `docs/product/README.md` + documents 01–12 defining InventoryOS (new SaaS, no codebase):
  vision, 14-module specification with release tags, workflows WF-01..13, domain model and
  integrity rules DR-01..28, roles/permissions, SaaS requirements, JuJu reference + capability
  matrix, integration requirements, NFRs + production readiness standard, decisions
  (C-01..19, PD-01..30, A-01..12), glossary, acceptance criteria (118 `AC-` IDs).
- No application code, schema, tests or existing documentation changed in either repository.

**Verified**
- Internal links/anchors and cross-referenced IDs (PD, DR, AC, A, WF) resolve: script check, 0 broken.
- JuJu repository `git status` clean; this repository only gained files under `docs/product/`.
- Not verified: anything requiring running either application (out of Phase 1 scope).

**Files changed**
- Added: `docs/product/README.md`, `docs/product/01-…` through `12-…`.

**Open issues**
- P0 owner decisions: PD-01 (InventoryOS vs StockPulse identity), PD-02 (foundation), PD-03
  (JuJu relationship), PD-13 (release plan), PD-18 (tenant isolation), PD-21 (auth).

**Decisions made**
- None by the agent beyond recommendations recorded in `docs/product/10-PRODUCT_DECISIONS.md` §2.

**Next task**
- Owner: answer P0 decisions. Then Phase 2 technical audit against `docs/product/`.

---

## 2026-11-04 — Master project audit (audit only)

**Implemented**
- Audit deliverables 1–17 and the founder decision list in `docs/audit/`.
- 8 audit probes in `docs/audit/probes/test_audit_probes.py` (outside `tests/`, not collected by
  the suite) that reproduce the critical findings.
- No application code, migrations, tests or configuration changed.

**Verified**
- Full suite, SQLite, with developer `.env`: 496 passed, 46 failed, 6 skipped.
- Full suite, SQLite, `APP_BASE_URL=http://localhost:8000`: 542 passed, 6 skipped.
- Full suite, PostgreSQL 16 in the compose network (empty `stockpulse_audit` DB): 548 passed.
- Probes: 8/8 pass on SQLite and on PostgreSQL 16.
- Not performed: dependency vulnerability scan (needs `pip-audit` install); any live call to
  Square, Stripe, SMTP or Anthropic; load testing of the async-route stall (C8).

**Files changed**
- Added: `docs/audit/00-INDEX.md` … `12-decisions.md`, `docs/audit/probes/test_audit_probes.py`,
  `docs/EXECUTION-LOG.md`.

**Open issues** (see `docs/audit/01-executive-audit.md#findings-register`)
- Critical: O1 no git remote; C2 same-day consumption loss; C4 swallowed Square writes; C6 no ledger.
- High: C3, C5, C7, C8, P1–P5, S1–S3, T1.

**Decisions made**
- None. D1–D13 await the founder (`docs/audit/12-decisions.md`).

**Next task**
- Founder: B01 — create a private GitHub repo and push.
- Agent, on approval: B02 — hermetic test configuration.
