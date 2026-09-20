# StockPulse

Automated inventory intelligence for small businesses on Square.

Connect Square → stock stays current → you get told what is running out → photograph a delivery
invoice and the counts update themselves, including back in Square so the storefront stops
overselling.

## Quickstart

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"
cp .env.example .env
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"   # ENCRYPTION_KEY
python -c "import secrets;print(secrets.token_hex(32))"                                     # SECRET_KEY
python -m app.cli migrate
python -m app.cli demo
uvicorn app.main:app --reload
```

Then open http://localhost:8000 and log in as `demo@stockpulse.local` / `demopassword`.

The demo seeds a café with all three kinds of stock, six weeks of sales, recipes, and a realistic
mix of healthy and low items — enough to run a sales call without a Square account.

```bash
pytest                                  # 60 tests, no network
python -m app.cli report --tenant 1     # what the weekly email contains
python -m app.cli alerts --tenant 1 --send
```

## The three inventory use cases

Businesses do not hold stock one way, so quantity is stored in base units (`each`, `g`, `ml`) and
displayed in whatever unit the owner thinks in.

| Dimension | Example | How the quantity stays current (`track_mode`) |
|---|---|---|
| **Count** | bagged beans, cartons, cups | `provider` — Square owns the count, we sync it |
| **Weight** | flour, deli ham, hopper beans | `derived` — last physical count minus recipe usage |
| **Volume** | milk, syrup, cold brew | `derived` — same, in millilitres |

`manual` is the third mode, for stock nobody sells directly.

Weight and volume only work because of recipes: one latte takes 220 ml of milk and 18 g of beans
out of stock (`recipe_components`). Every physical count, and every delivery received, re-anchors
the item so error cannot accumulate. Reorder logic is identical across all three: absolute reorder
point, plus days-of-cover against the supplier lead time.

## Receiving from a photo

The job that actually hurts is re-typing a delivery note into the POS. Instead:

1. `POST /app/receiving` — photo or PDF is stored as the receiving record (deduplicated by SHA-256).
2. `app/extract/invoice.py` reads it with Claude vision under a JSON schema, and checks the reply
   against that schema before anything downstream trusts it. The photo is turned upright and
   shrunk to 1568px first, because that is what the model reads anyway — the original is kept
   untouched in storage. No key configured → a stub extractor, and lines can be entered by hand.
3. `app/transform/receiving.py` (pure) matches each line to a stock item and resolves the quantity:
   `"4 cases of 6 x 1L"` → 24 000 ml, `"2 sacks"` → 50 kg via the item's pack size, `"3 boxes"` →
   1500 cups. A weight line against a volume item is **flagged, never guessed**.
4. You confirm in the review screen. Stock goes up, an `inventory_counts` row is written, and the
   supplier's wording is saved as an alias — the next invoice from them matches on its own.
5. Where Square owns the count, what arrived is pushed back as an adjustment via
   `inventory/changes/batch-create` - see below.

Every change a reviewer makes - the item, the quantity, the cost, the date, the supplier - is
recorded against the line as an `extraction_corrections` row, with what the reader said and what
the human made it. `python -m app.cli extraction-quality --tenant N` reads them back: the share of
lines nobody had to touch, which fields go wrong most, which suppliers are worst, and which product
names keep needing the same fix. That last one is usually solved by setting a barcode or an alias.
The receiving page shows the same summary.

## Dates on packaging

A photo of a best-before date is read twice: the model transcribes what is printed, and
`app/transform/dates.py` parses that transcription. Agreement is what earns the confidence to
apply a date without asking; anything else goes to a person. `10/11/26` is October 11th in Chicago
and November 10th in London, so it is read the way the merchant's country writes dates **and
flagged**, never applied silently. A packing date is always questioned, because it says nothing
about when stock goes off. Whatever is applied keeps its confidence and its kind on the batch, so
"why is this lot dated the 24th" is answerable months later.

`tests/corpus/printed_dates.json` is the regression corpus: every date shape the app has met, as
text, with what it must make of each. To score real photographs, put them in a folder with a
`cases.json` saying what each one really says, and run:

```bash
python -m app.cli expiry-corpus var/date-photos --prefer mdy --write-failures
```

It reports how many were read correctly with no question asked, how many were asked about, and how
many were wrong — a wrong date applied unasked is the only true failure, and `--write-failures`
adds each one to the corpus so it cannot come back unnoticed.

## What Square is told

Square owns the count for its own SKUs, so every write has to say which kind of fact it is.

| What happened | What Square is told |
|---|---|
| A delivery arrived, by barcode scan or invoice photo | `ADJUSTMENT` of what arrived, `+24` |
| Someone counted the shelf | `PHYSICAL_COUNT` of what they saw, `37` |
| Our own total, a previous count plus a delivery | nothing |
| A weight or volume derived from recipes | nothing; Square does not track it |

A physical count replaces whatever Square holds. Sent for a delivery it would erase any sale rung
up between the invoice being written and the push, so receiving goes through
`push_adjustments_to_square` and only an observation goes through `push_counts_to_square`.
Receipts push under an idempotency key of their own document, so a retry cannot add the delivery
twice. `tests/test_square_writeback.py` holds the rule to it.

## What each plan actually gets

The pricing page is marketing; `app/entitlements.py` is the product. Every feature question is
answered there, from the subscription state, so no route decides for itself.

```
account -> subscription status -> plan -> capabilities -> access
```

| | Starter | Pro |
|---|---|---|
| Locations | 1 | as many as they have |
| Health history | today's score | 30 days, with what moved |
| Reorder list as CSV | no | yes |
| Custom reorder points and par levels | no | yes |

A **30-day trial** runs on Pro, so the features worth paying for are the ones they use. Signing up
starts it; a nightly job ends it on its date, and `access()` recomputes from the dates anyway, so a
trial that ran out an hour ago is already closed.

| State | What works |
|---|---|
| `trialing`, `active` | everything the plan covers |
| `past_due` | everything, for a 7-day grace period while Stripe retries the card |
| `expired`, `canceled`, `paused`, `past_due` after grace | **read-only**: every page and number stays, Square keeps syncing, nothing can be changed |

Read-only is deliberate. Hiding a merchant's own inventory makes them leave rather than pay, and a
frozen sync would make their numbers wrong rather than merely locked. Writing routes hang off the
`require_writable` dependency, Pro-only ones call `require(tenant, "...")`, and a refusal renders a
sentence and a link rather than a 500. `tests/test_entitlements.py` walks one account through
trial, Starter, Pro, downgrade, failed payment, grace and cancellation.

## Billing

Stripe holds the money; `app/entitlements.py` holds what it buys. Between them sits
`app/orchestration/stripe_events.py`, the only code that reads a Stripe payload.

- **Prices are chosen here.** A form posts `starter` or `pro`; this process turns that into a price
  id. A browser cannot name its own price.
- **The account comes before the checkout.** Picking a plan on the pricing page signs the visitor
  up first, then sends them to Stripe, so a subscription always has an account to land on and
  nobody types their details twice. Skipping payment just starts the 30-day trial.
- **Webhooks decide state**, not the success page: `checkout.session.completed`,
  `customer.subscription.created/updated/deleted/paused/resumed`, `invoice.payment_failed` and
  `invoice.payment_succeeded`. Each handler is safe to run twice, because Stripe retries, and an
  event older than the last one applied is dropped - otherwise a late `updated` would revive a
  cancelled subscription. A status Stripe invents is treated as lapsed, never as paid.
- **Customers manage themselves.** Settings shows the plan, the status and the renewal date, with a
  button into Stripe's Customer Portal to change card, switch plan or cancel. No email to you.

```bash
python -m app.cli billing --tenant 1     # what the app thinks this account has paid for
```

Testing it end to end needs the Stripe CLI; the exact steps are in `NEEDS-YOU.md`.
`tests/test_billing.py` covers the same lifecycle with recorded payloads, including retries and
events arriving out of order.

## Layout

```
app/
  units.py                  dimensions and conversions (pure)
  models.py                 multi-tenant schema; every business row carries tenant_id
  providers/square/         ingestion + write-back only, no transform logic
  extract/invoice.py        invoice photo -> structured lines
  transform/                PURE: inventory math, alerts, reports, receiving, Square normalization
  load/                     idempotent tenant-scoped writes
  orchestration/            wiring: sync, receiving, alerts, reporting, scheduler
  notify/                   SMTP email
  web/                      marketing, auth, dashboard, receiving, billing
  cli.py                    operator commands
migrations/                 Alembic environment and revisions
```

The `transform/` layer takes and returns plain data — no ORM, no I/O — which is why the inventory
maths is testable without a database or a Square account.

## Migrations

Alembic owns the schema. The app runs `upgrade head` at startup, so a deploy migrates itself. On
Postgres the upgrade holds an advisory lock, so several processes starting at once migrate once.

```bash
python -m app.cli migrate                       # upgrade to head
alembic revision --autogenerate -m "add x"      # after changing app/models.py
alembic downgrade -1                            # step back one revision
alembic current                                 # what this database is on
```

Two things to know before you write a revision:

- **Always read the autogenerated file before committing it.** Autogenerate detects added and
  dropped things well, and renames not at all — it sees a rename as a drop plus an add, which
  destroys data.
- **`Qty` columns must render as `Qty()`, not as `sa.String` or `sa.Numeric`.** The type stores
  decimals as text on SQLite and as `NUMERIC(18,4)` on Postgres, so a migration that hard-codes
  either one is wrong on the other database. `migrations/env.py` has a `render_item` hook that
  handles this; `tests/test_migrations.py` fails if it stops working.

`tests/test_migrations.py` builds a database from the migrations alone and diffs it against
`Base.metadata`, so a model change with no matching revision fails the suite rather than
production. It also asserts a single head, and that `downgrade base` leaves nothing behind.

If you have a database created before Alembic was added, record it as already migrated instead of
re-running the initial revision against it:

```bash
python -m app.cli migrate --stamp
```

Under test (`APP_ENV=test`) the schema is built directly from the models for speed; every other
environment goes through migrations.

## Square setup

A sandbox seller starts with an empty catalog. `scripts/seed_square_sandbox.py` stocks one with
four inventory-tracked items and their counts, using the sandbox access token from the Developer
Dashboard. It matches on SKU, so running it twice creates nothing twice:

```bash
python scripts/seed_square_sandbox.py --dry-run   # what it would do
python scripts/seed_square_sandbox.py             # do it
```

1. Create an application at https://developer.squareup.com/apps.
2. Redirect URL: `{APP_BASE_URL}/connect/square/callback`.
3. Copy the app ID and secret into `.env`; keep `SQUARE_ENV=sandbox` until you have a real merchant.
4. Scopes requested: `MERCHANT_PROFILE_READ`, `ITEMS_READ`, `INVENTORY_READ`, `INVENTORY_WRITE`,
   `ORDERS_READ`. `INVENTORY_WRITE` is what lets corrected counts flow back.
5. Webhooks: notification URL `{APP_BASE_URL}/webhooks/square`, subscribed to
   `inventory.count.updated`, `catalog.version.updated`, `payment.updated`, `refund.created` and
   `refund.updated`. Put the signature key in `SQUARE_WEBHOOK_SIGNATURE_KEY`; unset, the endpoint
   refuses every event, and a bad signature is refused whatever the payload says.

Access tokens are Fernet-encrypted at rest and refreshed automatically a week before expiry.

Webhooks are the fast path; the hourly sync is the correct one. A count that arrives late, out of
order, or not at all is put right by the next run, which re-reads Square's own counts — so a sale
cannot quietly go missing here. A catalog event re-reads the catalog, at most once a minute
however many edits arrive. A variation deleted or archived in Square is marked inactive here and
never deleted: it leaves the shelf, the alerts and the reorder list, and its counts, receipts and
sales stay where they are. The dashboard says whether Square is connected, when it last synced,
and what the last failure was.

## Onboarding a customer by hand

This is the $149 setup package, and it is meant to be done manually for the first customers.

1. `python -m app.cli demo` — run the demo on your own machine for the sales call.
2. Sign them up, click **Connect Square**. The first sync pulls locations, catalog, counts, orders.
3. Countable, Square-tracked SKUs appear on their own. Add what Square does not count — the milk,
   the flour, the beans — with `Add item`, in the units they actually use.
4. Set recipes for the drinks and food that consume them. This is the work; it is also the moat.
5. Set reorder points from real sales: `python -m app.cli report --tenant N` shows what moves.
6. Set the weekly report day/hour on the tenant row, then `python -m app.cli report --tenant N --send`.
7. Have them photograph one delivery while you watch. That is the moment they get it.

## Configuration

Everything is env-driven; see `.env.example`. The ones that matter:

| Variable | Why |
|---|---|
| `ENCRYPTION_KEY` | Fernet key for POS tokens. Rotating it orphans every stored token. |
| `SECRET_KEY` | Session cookie signing. |
| `DATABASE_URL` | SQLite for dev, `postgresql+psycopg://…` for production. |
| `ANTHROPIC_API_KEY` | Invoice photo reading. Unset → manual entry, everything else works. |
| `SYNC_INTERVAL_MINUTES` | Background sync cadence (APScheduler). |
| `STORAGE_BACKEND` | Where invoice photos live: `local` (a persistent volume) or `s3`. |
| `SENTRY_DSN` | Error reports. Unset → errors are only logged. |
| `ALERT_COOLDOWN_HOURS` | Stops alert digests becoming noise. |
| `SQUARE_WEBHOOK_SIGNATURE_KEY` | Verifies Square's inventory webhooks. Unset means the endpoint refuses everything. |
| `EXPIRY_HORIZON_DAYS` | How far ahead stock that will not sell before its best-before is raised. Default 30. |

Secure session cookies switch on automatically when `APP_BASE_URL` is https.

## Deploying

One image, two processes, one Postgres database, one place for photos.

| Process | Command | How many |
|---|---|---|
| web | the image's default command (uvicorn) | as many as traffic needs |
| scheduler | `python -m app.cli scheduler` | one runs the jobs; any extra copy stands by |

- **Database.** Managed Postgres. `DATABASE_URL` accepts the `postgres://` URL hosts hand out.
- **Photos.** `STORAGE_BACKEND=s3` with any S3-compatible bucket, or `local` with `UPLOAD_DIR` on a
  persistent volume. The database stores only the key, `<tenant>/<sha256>.<ext>`.
- **Scheduled jobs.** Under `APP_ENV=staging` or `production` no job runs in a web process. The
  scheduler holds a Postgres advisory lock, so a second copy, or the old container still running
  during a redeploy, waits instead of syncing twice. The weekly report checks the email log before
  sending, so a restarted job does not send it again.
- **Health.** `/healthz` answers while the process is up: use it for liveness. `/readyz` checks the
  database, that the schema is at the latest migration, and that storage is reachable, and returns
  503 until all three pass: route traffic on it. Neither says why a check failed.
- **Errors.** Set `SENTRY_DSN`. Cookies, form bodies, query strings and auth or signature headers
  are removed before an event leaves the process.
- **HTTPS.** Terminate TLS at the platform. Uvicorn trusts `X-Forwarded-*`, so redirects stay https
  and session cookies are `Secure`.

Staging and production refuse to start on SQLite, with a placeholder or short `SECRET_KEY`, without
`ENCRYPTION_KEY`, or (production only) with a non-https `APP_BASE_URL`.

A production replica on one machine, with Postgres and photos on named volumes:

```bash
export SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
docker compose up --build -d        # http://localhost:8080
```

The same suite runs against Postgres when pointed at an empty database:

```bash
TEST_DATABASE_URL=postgresql+psycopg://stockpulse:stockpulse@localhost:5433/stockpulse_test pytest
```

## Promotions

Markdown suggestions come from stock, not from a model: days left sets an urgency tier, the tier
sets a discount band, and how much faster the lot must sell picks a point inside that band. Every
recommendation carries the sentences that produced it, and none is ever applied automatically -
StockPulse tells you what to change the price to; you change it on Square.

Two rules keep it honest. Nothing is suggested when the lot is on course to sell in time, so the
page stays empty on a good week. And no price goes below a 20% margin over cost unless the item's
floor is relaxed, so a markdown never quietly sells at a loss.

## Inventory health

One score out of 100, and never without its reasons. Five dimensions, each scored by a rule
written next to it on the page:

| Dimension | Weight | Scores zero when |
|---|---|---|
| Stock availability | 30% | all stock, weighted by sales, is at or below its reorder point |
| Expiration risk | 25% | 10% of stock value may expire unsold or is past its date |
| Overstock | 20% | all stock value holds over 60 days of sales, or is not selling |
| Reorder health | 15% | everything above its reorder point will run out before a delivery |
| Data completeness | 10% | no stock has a cost, a reorder point, dates, or a recent count |

Expiry gets a steeper zero point than the others on purpose: scored linearly, 4% of the shelf
going in the bin would read as 96, excellent. Availability and reorder health are weighted by the
value of what sells, so an empty Coke fridge costs more points than an obscure flavour.

A dimension with nothing to measure is left out and the rest are reweighted. A store that has
dated nothing does not get a perfect expiry score for it; the data completeness dimension is
what records the gap.

Each night at 02:15 UTC the score is kept per tenant, with every dimension's figures, so the next
day can say why it moved and compare it with the business's own 30-day average - never with other
stores. `python -m app.cli health --tenant N` prints it; add `--snapshot` to keep the night by hand.

The dashboard leads with the score and today's actions. `/app/reorder` groups what to order by
supplier, and downloads as CSV.

## Snack boxes (private, not part of the product)

The snack box shop (`app/orchestration/boxes.py`, `app/web/boxes.py`, `app/web/shop.py` and their
tables) is the operator's own side project. It lives here so that one Square connection and one
shelf count serve both, but it is **not a StockPulse feature**: no plan includes it, billing never
mentions it, and `app/entitlements.py:owns_snack_box` is the only thing that grants it.

Access is by account id from `SNACK_BOX_TENANT_IDS`, empty by default. A customer's deployment
therefore has no snack box at all: the admin pages and the public shop both answer 404, the
navigation has no link, and the housekeeping job skips every account but the ones listed.

```bash
SNACK_BOX_TENANT_IDS=1
```

## Deliberately not built

No warehouse, no forecasting model, no multi-provider abstraction, no mobile app, no
role-based permissions, no in-app purchase orders.

Toast and Clover are one module each in `app/providers/`, behind the same normalization contract —
but they are not built, and should not be until Square customers are paying.
