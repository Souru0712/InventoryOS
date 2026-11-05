# 07 — Test coverage report

## Results from this audit [RUN]

| Run | Environment | Result |
|---|---|---|
| 1 | SQLite, Windows venv, developer `.env` present | **496 passed, 46 failed, 6 skipped** |
| 2 | SQLite, same, `APP_BASE_URL=http://localhost:8000` | **542 passed, 0 failed, 6 skipped** |
| 3 | **PostgreSQL 16** (`postgres:16-alpine`, compose network, empty `stockpulse_audit` DB) | **548 passed, 0 failed, 0 skipped** |
| 4 | Audit probes, SQLite | 8 / 8 confirm their findings |
| 5 | Audit probes, PostgreSQL 16 | 8 / 8 confirm their findings |

Previously reported "413 passing, 6 skipped" is out of date; `NEEDS-YOU.md` says 535. Actual
collected count is 548 (≈470 test functions plus parametrisation).

**The 6 SQLite skips are the Postgres-only tests** (5 in `tests/test_migrations.py`, 1 advisory
lock in `tests/test_deployment.py`); all 6 ran and passed on Postgres.

### T1 — the suite is not hermetic

Run 1's 46 failures have one cause: `Settings` reads `.env` (`app/config.py:12`) and
`tests/conftest.py` does not pin `APP_BASE_URL`. The local `.env` sets an `https://` base URL,
so the session cookie becomes `Secure`, the HTTP `TestClient` drops it, and every signed-in test
lands on the login page. Run 3 was unaffected only because `compose.yaml` injects its own
`APP_BASE_URL`. Any other key in `.env` that conftest does not override (Square webhook key,
Stripe keys, S3, `SQUARE_ENV`) also leaks into tests.

Fix: pin every setting in `tests/conftest.py` and disable the env file under test.

## Test inventory by type

| Type | Files | Functions | Notes |
|---|---|---|---|
| Unit (pure `transform/`, units, dates) | `test_units`, `test_inventory`, `test_alerts`, `test_barcodes`, `test_dates`, `test_expiry`, `test_health`, `test_promotions`, `test_receiving`, `test_square_normalize`, `test_expiry_corpus`, `test_snack_box_rules` | ~200 | Strongest area |
| Integration with DB | `test_receiving_flow`, `test_health_flow`, `test_promotions_flow`, `test_scanning`, `test_entitlements`, `test_email`, `test_square_catalog`, `test_square_writeback`, `test_storage`, `test_invoice_ai`, `test_expiry_photos` | ~145 | Use a real DB, mocked HTTP |
| HTTP / route (`TestClient`) | `test_web`, `test_webhooks`, `test_billing`, parts of the flow files | ~60 | |
| POS (Square) | `test_square_*`, `test_webhooks` | 27 | All against recorded/mocked payloads |
| Webhook | `test_webhooks` (Square), `test_billing` (Stripe) | ~30 | Signature, ordering, duplicates (Stripe) |
| Authorization / tenant | 6 cross-tenant tests + `test_entitlements` | ~30 | No role tests (no roles exist) |
| Database / migrations | `test_migrations` | 6 | Model/migration diff, single head, downgrade, populated upgrade |
| Deployment | `test_deployment` | 18 | Config refusal, scheduler lock, readiness, Sentry scrubbing |
| End-to-end (browser) | — | 0 | None |
| Live external (Square sandbox, Stripe test mode, real SMTP, real Anthropic) | — | 0 | None |
| Snack box (private) | 5 files | ~70 | |

## Critical workflows

| # | Workflow | Covered | Gap |
|---|---|---|---|
| 1 | Receive an invoice | ✓ `test_receiving_flow`, `test_invoice_ai` | Concurrent apply (C5), Square failure (C4) untested |
| 2 | Update inventory | partial | No ledger to assert against; counts on PROVIDER items only via scan |
| 3 | Create a batch | ✓ `test_expiry`, `test_scanning` | — |
| 4 | Assign an expiration date | ✓ `test_expiry_photos`, `test_dates`, corpus | Real photos never scored |
| 5 | Process a completed sale | ✓ webhook count + hourly recovery (`test_webhooks`, `test_square_catalog`) | Order-level not modelled |
| 6 | Deduct recipe ingredients | partial `test_inventory` | Same-day loss is **asserted as correct** (`test_consumption_only_counts_days_after_the_count`); no multi-location recipe test |
| 7 | Record waste | ✗ | Feature missing |
| 8 | Process a refund | ✗ (product) | Snack box only |
| 9 | Duplicate webhooks | partial | Stripe ✓; Square stale-order ✓; Square duplicate event id ✗ |
| 10 | Reconcile inventory | ✓ `test_the_hourly_sync_recovers_a_sale_whose_webhook_never_arrived` | — |
| 11 | Physical count | ✓ `test_a_physical_count_sends_the_number_someone_read_off_the_shelf`, scan count | Dashboard count route has no test for failed push |
| 12 | Low-stock alert | ✓ `test_alerts`, `test_email` | Delivery never real |
| 13 | Cross-tenant access | partial (6 routes) | No parametrised test over every route |

## Missing coverage to add (with the fix that needs it)

1. Hermetic config (T1) — before anything else.
2. Concurrency: double apply of a document, on Postgres (R4).
3. Square failure → outbox → retry → lands (R3).
4. Same-day consumption after a count/receipt; multi-location recipes (R2).
5. Parametrised cross-tenant test over every route that takes an id.
6. Ledger invariant: `sum(movements) == qty_base` for every item after every flow (R1).
7. Waste flow; refund ingestion ignored-safely.
8. Security: login throttle, image content-type, CSRF once added.
9. One Playwright smoke run (signup → connect stub → receive → count) in CI — optional pre-launch.

## Audit probes

Source: [probes/test_audit_probes.py](probes/test_audit_probes.py) — outside `tests/`, so the normal suite never collects it. Each asserts current
behaviour; once a fix lands the matching probe should fail and be replaced by a real test.

| Probe | Confirms |
|---|---|
| R1 | C2 — count-day sales after the count never deducted |
| R2 | C3 — recipe consumption crosses locations |
| R3 | C4 — failed Square push on receipt is erased by the next sync; invoice receipt writes no `inventory_events` row |
| R4 | C5 — concurrent apply raises stock twice |
| R5 | Duplicate inventory webhook: balance safe, no event-id record, no movement row |
| R6 | Batch removal is a hard delete |
| R7 | S3 — invoice image served with uploader's `Content-Type`, no `nosniff` |
| R8 | S1, S4 — no CSRF token on login form; 30 failed logins never throttled |

Run from the repo root:

```bash
APP_BASE_URL=http://localhost:8000 python -m pytest -q -p no:cacheprovider -p tests.conftest --rootdir=. docs/audit/probes/test_audit_probes.py
```
