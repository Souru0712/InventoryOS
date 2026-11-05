# 04 — Database, schema and inventory ledger

## Schema overview [CODE]

34 tables in `app/models.py`; 23 product, 11 snack box. Every business row carries `tenant_id`.

| Group | Tables |
|---|---|
| Tenancy / auth | `tenants`, `users`, `provider_connections` |
| Catalog | `locations`, `sold_items`, `stock_items`, `recipe_components`, `product_identifiers`, `stock_aliases` |
| Quantities | `inventory_counts`, `inventory_events`, `stock_batches`, `sales_daily` |
| Receiving | `documents`, `document_lines`, `extraction_corrections` |
| Intelligence | `alerts`, `promotions`, `health_snapshots` |
| Ops | `sync_runs`, `email_log`, `email_suppressions`, `webhook_events` |
| Snack box (private) | `box_settings`, `snack_profiles`, `customers`, `customer_login_tokens`, `customer_product_preferences`, `preference_changes`, `box_orders`, `box_lines`, `box_reviews`, `referrals`, `credit_transactions` |

## Strengths

- **Exact decimals.** `Qty` type: `NUMERIC(18,4)` on Postgres, text on SQLite, quantised on bind
  (`app/db.py:61`). No float drift in quantities.
- **Base-unit storage** (`each`, `g`, `ml`) with per-item `dimension` — count/weight/volume handled
  uniformly.
- **Migrations are disciplined.** `tests/test_migrations.py` diffs migrated schema against models,
  asserts a single head, downgrade-to-base, and upgrade over populated data. Passed on Postgres
  in this audit [RUN].
- **Natural-key uniqueness** where it matters: `(tenant_id, provider, provider_item_id)`,
  `(tenant_id, value)` for identifiers, `(tenant_id, sold_item_id, location_id, dt)` for sales.
- `credit_transactions` (snack box) is a correct append-only ledger with a unique `reference` —
  the pattern the inventory side lacks.

## Defects and gaps

| ID | Finding | Evidence |
|---|---|---|
| DB1 | **No CHECK constraints anywhere.** Negative quantities, `received_qty_base <= 0`, unknown `source` strings are all accepted by the database | `grep CheckConstraint` → 0 [CODE] |
| DB2 | **No `ON DELETE` rules.** Deleting a tenant or stock item fails on FKs or orphans rows; there is no account-deletion path to exercise it | `grep ondelete` → 0 |
| DB3 | **Naive timestamps.** All `DateTime` columns are `timestamp without time zone`; correctness relies on `SET TIME ZONE 'UTC'` per connection (`app/db.py:48`) and `_aware()` helpers repeated in 5 modules | 0 × `timezone=True` |
| DB4 | **No row-level locking.** Zero `with_for_update` / conditional updates; status checks are read-then-write (C5) | probe R4 [RUN] |
| DB5 | `users.email` globally unique and `users.tenant_id` single-valued: one person cannot belong to two businesses; no roles | `app/models.py:125` |
| DB6 | `provider_connections` unique on `(tenant_id, provider, merchant_id)` — the same Square merchant can attach to several tenants; webhook routing then picks `.first()` (S7) | `app/models.py:144`, `app/web/webhooks.py:38` |
| DB7 | Supplier is free text on `stock_items` and `documents`; no supplier entity, no price history | `app/models.py:224,380` |
| DB8 | `inventory_counts` stores both observations and receipts; `source` is an unconstrained string (`manual`, `opening`, `receipt`, `scan`, …) | `app/models.py:255` |
| DB9 | Recipes have no version or effective date; editing one would rewrite past theoretical consumption | `app/models.py:240` |
| DB10 | `sales_daily` aggregates away order identity, time of day and refunds | `app/models.py:270` |
| DB11 | Indexes are adequate for current volumes; missing for likely hot paths after R1/R2: `(tenant_id, stock_item_id, occurred_at)` on movements, `(tenant_id, location_id, closed_at)` on sales | — |

## Inventory ledger assessment

**Verdict: there is no inventory ledger.** There is a mutable balance plus two partial history
tables, neither of which can reconstruct the balance.

### What exists

| Store | Role | Written by |
|---|---|---|
| `stock_items.qty_base` | The balance everything reads | 7 paths, see below |
| `inventory_counts` | Anchor for derived (weight/volume) items | opening, manual count, scan count, invoice receipt, scan receipt |
| `inventory_events` | "Audit trail" | barcode scan receive/count, snack boxes **only** |

### Writes to `qty_base` and what they leave behind [CODE]

| Path | File | Count row | Event row | Actor | Idempotency |
|---|---|---|---|---|---|
| Square webhook / sync | `app/load/upsert.py:197` | no | no | — | absolute value |
| Derived recompute | `app/orchestration/views.py:257` (`refresh_derived_quantities`) | no | no | — | recompute |
| Invoice receipt | `app/orchestration/receiving.py:329` | yes (`receipt`) | **no** | **no** | status check, racy (C5) |
| Scan receipt | `app/orchestration/scanning.py:234` | yes (`scan`) | yes | yes | none |
| Manual count | `app/web/dashboard.py:410` | yes (`manual`) | **no** | **no** | none |
| Scan count | `app/web/scanning.py:239` | yes | yes | yes | none |
| Snack boxes | `app/orchestration/boxes.py:266,283` | no | yes | — | `count_applied` flag |

### Against the brief's ledger requirements

| Requirement | Status |
|---|---|
| Unique id | ✓ (per table) |
| Organisation | ✓ `tenant_id` |
| Location | ✗ not on counts/events (derivable via stock item) |
| Product | ✓ |
| Batch | ✗ |
| Quantity (delta) | partial — counts store totals, events store delta + total |
| Unit | ✗ (implicit base unit) |
| Event type | partial — free-text `source` / `action` |
| Source + related transaction | partial — counts carry a note string; events carry `box_order_id` only |
| Actor | partial — events only |
| Timestamp | ✓ (naive UTC) |
| Idempotency key | ✗ |
| Append-only | ✗ balance overwritten; batches hard-deleted (C9) |

### Correctness defects in the derived (weight/volume) engine

**C2 — Sales on the day of a count or delivery, after it, are never deducted.** [RUN, probe R1]
`consumed_since()` (`app/transform/inventory.py:93`) keeps days with `start < day`, and each
receipt writes a new anchor (`app/orchestration/receiving.py:331`). Counted 1,000 ml at 08:00,
300 ml sold later that day → the item reads 1,000 ml that day and 800 ml after 200 ml more the
next day; the 300 ml is lost permanently. A juice bar receiving produce every morning loses
almost every day's consumption, so derived stock barely falls and low-stock alerts never fire.
`tests/test_inventory.py::test_consumption_only_counts_days_after_the_count` encodes this as
intended behaviour.

**C3 — Recipe consumption ignores location.** [RUN, probe R2] `SaleRow` has no location
(`app/transform/inventory.py:60`); `consumption()` applies every location's sales to the single
stock item named in the recipe. 10 lattes sold at location B drained 2 L of location A's milk.
Irrelevant for single-location tenants; wrong for every multi-location one.

**C5 — An invoice can be applied twice.** [RUN, probe R4] Two concurrent requests both pass
`document.status is APPLIED` (`app/orchestration/receiving.py:288`); stock rose 10 → 58 instead of
34, and two receipt anchors were written. Square is protected by the `receipt-{id}` idempotency
key; local derived stock is not and stays doubled.

### Recommended ledger design (R1, needs approval D3)

```
inventory_movements
  id               bigint pk
  tenant_id        fk, not null
  location_id      fk, not null
  stock_item_id    fk, not null
  batch_id         fk null
  movement_type    enum: opening | receipt | count_variance | sale_consumption | waste
                         | transfer_out | transfer_in | pos_correction | manual_adjustment
  qty_delta_base   numeric(18,4) not null, check (qty_delta_base <> 0)
  unit             varchar(16) not null           -- base unit at time of writing
  unit_cost_cents  numeric(18,4) null             -- cost snapshot for valuation and waste
  source           varchar(32) not null           -- invoice | scan | count | square | user
  source_ref       varchar(120) null              -- document_line:812, square_order:…, count:55
  actor_user_id    fk null                        -- null = system
  reason           varchar(32) null               -- waste / adjustment reason
  occurred_at      timestamptz not null
  recorded_at      timestamptz not null default now()
  idempotency_key  varchar(160) not null
  unique (tenant_id, idempotency_key)
```

- `stock_items.qty_base` stays as a cached projection, updated in the same transaction as the
  movement insert; a nightly job verifies `sum(qty_delta_base) = qty_base`.
- **Counts become observations** (`inventory_counts` kept as-is) that *generate* a
  `count_variance` movement for the difference.
- **POS-owned SKUs**: a Square count that differs from the projection writes a `pos_correction`
  movement — the history explains every change, and Square stays authoritative.
- Corrections are reversing movements, never updates. No `DELETE` path in application code.
- Migration: backfill one `opening` movement per stock item equal to current `qty_base`.
  Non-destructive; existing tables stay.
