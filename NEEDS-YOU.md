# Needs you

Everything left in the build that I cannot do, in the order that unblocks the most. Phase A is
finished — all of it, verified — so everything below is Phase B onwards.

Last updated: 2026-09-20, after the landing-page comps and the app redesign.

---

## Where the build stands

| Phase | State |
|---|---|
| **A — zero-account engineering** | ✅ done, 535 tests on SQLite, whole suite also run on Postgres |
| **B — external integrations** | ⏳ blocked on accounts and credentials: everything in this file |
| **C — deployment** | ◐ built and proven on a local Postgres + Docker replica; needs a host |
| **D — the real-customer simulation** | ⏳ needs B and C; the script is at the bottom of this file |

Phase A, item by item, as you listed it:

1. Invoice receiving → Square adjustment — **superseded 2026-11-05 (C-34, T0.08): write-back is
   off until R1.1.** InventoryOS writes nothing to Square; receiving and counts change InventoryOS
   only, and the push functions refuse to run while `SQUARE_WRITE_BACK` is off. In R1.1 they come
   back through the outbox, still sending a delivery as an adjustment and only an observation as a
   count. Until T1.18, Square's next sync still replaces the count of an item Square tracks.
2. PostgreSQL compatibility — ✅ driver installed, every migration runs, enum drops, FK-safe
   deletes; the whole suite passes on Postgres.
3. Scheduler isolation — ✅ never embedded when deployed, plus a Postgres advisory leader lock;
   two schedulers were started and only one ran jobs, and the standby took over in ~5s.
4. Persistent invoice-photo storage — ✅ photos live behind a storage key on a volume or in S3;
   survived a container restart and a full `compose down` + `up`.
5. Entitlement data model + trial lifecycle — ✅ one checker, enforced on 16 writing routes.
6. Stripe webhook lifecycle — ✅ created, updated, deleted, paused, resumed, payment failed and
   succeeded, all idempotent and safe against out-of-order delivery.
7. Plain-text email templates — ✅ every email renders its own `.txt`; the filler body is gone.

---

## 1. Square sandbox → production (your Phase B, item 8)

**Start here. It unblocks the largest part of the product.**

Where: https://developer.squareup.com/apps → your application → **Sandbox** credentials.

Put these in `.env` yourself. Don't paste secrets into chat — I read `.env` from disk, and it is
git-ignored.

```env
SQUARE_ENV=sandbox
SQUARE_APP_ID=sandbox-sq0idb-...
SQUARE_APP_SECRET=sandbox-sq0csb-...
SQUARE_WEBHOOK_SIGNATURE_KEY=...
```

In the Square dashboard, for the **sandbox** application:

- [ ] OAuth redirect URL: `{APP_BASE_URL}/connect/square/callback`
- [ ] Webhook notification URL: `{APP_BASE_URL}/webhooks/square`
- [ ] Webhook events: `inventory.count.updated`, `catalog.version.updated`, `payment.updated`,
      `refund.created`, `refund.updated`
- [ ] The webhook **signature key** into `SQUARE_WEBHOOK_SIGNATURE_KEY`
- [ ] A sandbox seller with one location and a few inventory-tracked items

**Then I run:** connect, catalog import, a sandbox sale, the webhook landing, a delivery received,
and the hourly reconciliation putting a missed webhook right.

Production comes after sandbox passes: production app id and secret, `SQUARE_ENV=production`, its
own webhook subscription and signature key, redirect URL on the real domain.

## 2. A public URL for webhooks

Square and Stripe both have to reach the app. Either:

- **ngrok**: `ngrok http 8000`, then set `APP_BASE_URL` to the https URL it prints. It changes each
  restart unless you pay for a reserved domain.
- **Or the staging deploy**, once you pick a host (section 5).

The Square signature check rebuilds what Square signed from `APP_BASE_URL`, so it has to match the
notification URL exactly: https, no trailing slash.

## 3. Email provider and sending domain (your Phase B, item 9)

**Pick a provider.** My recommendation is Postmark for transactional mail — best deliverability of
the cheap options, and bounce webhooks that are simple to consume. Resend is the easiest to set up;
SES is cheapest at volume and the most work.

- [ ] Provider account
- [ ] Sending domain added there, e.g. `mail.yourdomain.com`
- [ ] **SPF**: a TXT record on the sending domain, contents from the provider
      (typically `v=spf1 include:<provider> ~all`)
- [ ] **DKIM**: the CNAME or TXT records they give you, one per selector
- [ ] **DMARC**: TXT at `_dmarc.yourdomain.com`, starting at
      `v=DMARC1; p=none; rua=mailto:you@yourdomain.com`, tightened to `p=quarantine` once the
      reports come back clean
- [ ] Into `.env`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`
- [ ] A from-address on that domain: `SMTP_FROM`, `SUPPORT_EMAIL`
- [ ] A Gmail address and an Outlook/Hotmail address you control, for the delivery test

**Tell me which provider you chose** and I will wire its bounce webhook into the suppression list
that is already built. The payload shape and signature scheme are provider-specific, so that part
cannot be written blind.

Until this exists, email is written to the log instead of sent. That is why nothing has arrived.

## 4. Anthropic key, invoices, and packaging photos (your Phase B, item 10)

- [ ] `ANTHROPIC_API_KEY` in `.env`, and a spending limit you are comfortable with
- [ ] **3–5 real delivery notes**, photographed as a phone would take them, from *different*
      suppliers. Different layouts matter far more than more copies of one layout.
- [ ] **A dozen real products photographed** for dates, covering: large obvious dates, tiny print,
      curved packaging, low contrast; the wordings `BEST BY`, `EXP`, `USE BY`, `SELL BY`; and the
      formats `10/24/26`, `24 OCT 2026`, and an ambiguous `10/11/26`.
- [ ] A `cases.json` beside the date photos saying what each one actually says:

```json
[
  {"file": "yoghurt.jpg", "expected": "2026-10-24", "note": "tiny print on a curved pot"},
  {"file": "milk.jpg", "expected": "2026-09-22", "prefer": "dmy", "note": "low contrast"},
  {"file": "lot-code-only.jpg", "expected": null, "note": "no date printed at all"}
]
```

Then I run both harnesses:

```bash
python -m app.cli expiry-corpus var/date-photos --prefer mdy --write-failures
python -m app.cli extraction-quality --tenant 1
```

The first says how many dates were read correctly with no question asked; the second says how much
of each invoice a person still had to fix. Those two numbers are what "the AI is good enough" means
here, and neither can be answered without your photographs.

## 5. Hosting (your Phase C, items 13–18)

Nothing here blocks local work; all of it blocks a real deploy.

- [ ] **Where it runs.** Anything that runs a container with a persistent Postgres: Fly.io, Render,
      Railway, or a VPS with the compose file as-is. No host-specific code either way.
- [ ] **Managed Postgres.** `DATABASE_URL` accepts the `postgres://` form hosts hand out.
- [ ] **Invoice photo storage.** A persistent volume at `UPLOAD_DIR`, or an S3-compatible bucket:
      `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`,
      `S3_SECRET_ACCESS_KEY`.
- [ ] **A domain**, so `APP_BASE_URL` is https. Production refuses to start without it.
- [ ] **Sentry DSN** (optional): `SENTRY_DSN`. Errors are only logged without it.

Production secrets, generated per environment and never reused from dev:

```bash
python -c "import secrets;print(secrets.token_hex(32))"                                      # SECRET_KEY
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"     # ENCRYPTION_KEY
```

- [ ] `SECRET_KEY` — session signing. Changing it logs everyone out.
- [ ] `ENCRYPTION_KEY` — encrypts Square tokens at rest. **Changing it orphans every stored token**
      and every merchant has to reconnect. Keep a copy somewhere you will not lose it.
- [ ] `APP_ENV=production`, `APP_BASE_URL=https://yourdomain`

## 6. Stripe (your Phase B, item 11)

The code is finished and tested against recorded Stripe payloads. What is missing is the account
and one run through test mode.

- [ ] Stripe account, **test mode** first
- [ ] Starter product with a $29/month price; Pro product with a $59/month price
- [ ] Price ids into `.env`: `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PRO`
- [ ] `STRIPE_SECRET_KEY` (test key first, live key later)
- [ ] Customer Portal enabled in the dashboard, with cancellation allowed
- [ ] Stripe CLI installed

Then the lifecycle test. With the app running and `stripe listen` forwarding:

```bash
stripe listen --forward-to localhost:8000/billing/webhook
```

It prints a `whsec_...` — put that in `.env` as `STRIPE_WEBHOOK_SECRET` and restart. Then:

```bash
# 1. buy it: /pricing -> Start Starter -> signup -> Stripe checkout, card 4242 4242 4242 4242
python -m app.cli billing --tenant 1          # expect: starter, active

# 2. upgrade, then downgrade, in the Customer Portal from Settings -> Billing
python -m app.cli billing --tenant 1          # expect: pro, then starter again

# 3. a failed payment
stripe trigger invoice.payment_failed
python -m app.cli billing --tenant 1          # expect: past_due, access still full, grace shown

# 4. cancel in the portal
python -m app.cli billing --tenant 1          # expect: canceled, access read_only
```

Tell me what any step prints if it disagrees. Live keys go nowhere near it until this passes.

## 7. Two design calls

**a. Photography for the landing page.** The v3 comps are in `var/design/comps/v3`. Two frames are
photography slots, composed in CSS for now so the layout renders and reviews:

- [ ] **The hero** — a back-of-house shelf, shot dark, one carton in focus with its printed date.
- [ ] **The dates frame** — a macro crop of a dot-matrix best-before print on a carton.

Real photographs at the same crop and grade would lift both. Shooting them on a phone against a
dark wall is enough; nothing here needs a studio. Tell me to ship the composed versions instead and
I will — they hold up, they are just not photography.

- [ ] Also decide whether v3 replaces the live landing page, or stays a reference. The live page is
      already on the same design system, so applying v3 is copy and layout work, not a rebuild.

**b. One commit needs splitting, or not.** Commit `22ee0b5` carries my redesign *and* your
uncommitted snack-box work (`app/web/boxes.py`, `app/web/shop.py`, `app/config.py`,
`tests/test_snack_box_private.py` and four others) under a message that only describes the
redesign. Nothing is lost and the suite is green. Say the word and I will split it into two.

## 8. The first customer

- [ ] One real business to run the $149 setup with, from the six types you listed
- [ ] Their Square account connected, their non-POS stock entered, recipes set

---

## Your decisions, already answered

In the code, in `app/entitlements.py`, as three constants:

- [x] **Trial: 30 days, on Pro.**
- [x] **Expired trial or subscription: read-only.** Everything stays visible and Square keeps
      syncing; receiving, counts and edits stop.
- [x] **Failed payment: 7-day grace**, then the same read-only state.

Change any of them by changing `TRIAL_DAYS`, `TRIAL_PLAN` or `GRACE_DAYS`.

---

## Phase D — the run that decides whether this is a product

Once sections 1, 3, 4 and 6 are in place, this is the one test worth doing. I can drive most of it;
the marked steps are yours because they need a phone, a real inbox, or a card.

| # | Step | Who | Checked by |
|---|---|---|---|
| 1 | New merchant signs up | me | trial starts, Pro, 30 days |
| 2 | Connect Square (sandbox seller) | **you** click approve | `app.cli billing`, connection shown on the dashboard |
| 3 | Existing catalog imports | me | items appear with SKUs and barcodes |
| 4 | Photograph a delivery note | **you** | the photo |
| 5 | AI extracts the lines | me | review screen populated |
| 6 | Correct one item | me | an `extraction_corrections` row with both values |
| 7 | Adjustment reaches Square | me | Square's count rises by what arrived, not to our total |
| 8 | Scan expiration dates | **you** photograph | batch created with its confidence |
| 9 | A Square sale arrives by webhook | me, in the sandbox | local count drops |
| 10 | Intelligence recalculates | me | low-stock and expiry alerts appear |
| 11 | **An alert email actually arrives** | me, you confirm the inbox | the email, not a log line |
| 12 | **The weekly report actually arrives** | me, you confirm | same |
| 13 | Health score updates | me | score and reasons move |
| 14 | Trial expires | me, by moving the date | read-only, sync continues |
| 15 | **Customer pays $29** | **you**, test card | `starter, active` |
| 16 | Starter restrictions apply | me | one location, no CSV, no custom thresholds |
| 17 | **Cancel through Stripe** | **you**, in the portal | `canceled, read_only`, data intact |

Steps 11 and 12 are the ones that have never been proven end to end, because nothing has sent a
real email yet. That is why the email provider is section 3 rather than last.

---

## Known about this machine

- Running the test suite from Windows against the published Postgres port (`localhost:5433`) hangs:
  Docker Desktop's port proxy stalls. Run it inside the network instead:

  ```bash
  docker compose run --rm --no-deps --user root -v "C:/Users/oscar/.vscode/POS-Inventory-Automation:/src" -w /src -e TEST_DATABASE_URL=postgresql+psycopg://stockpulse:stockpulse@db:5432/stockpulse_test web sh -c "pip install -q pytest pytest-asyncio pillow && python -m pytest -q"
  ```

- The local staging replica is at http://localhost:8080 (`docker compose up -d`), with its own
  Postgres and volumes. `docker compose down` keeps the data; `-v` wipes it.
- Docker Desktop once crashed over unreadable socket files in `%LOCALAPPDATA%\Docker\run`. The old
  folder was renamed to `run.stale-20260915172221` and can be deleted.
- The demo database has a test invoice applied to it from a UI check. Delete `stockpulse.db` and
  re-run `python -m app.cli demo` for a clean demo before a sales call.
