# Needs you

Everything in the build that I cannot do for you, in one place. I add to this file as new items
appear, and tick them off as they land.

Last updated: 2026-09-16, during Workstream 3. Workstreams 4-7 are queued and their owner items
are listed here too, so nothing waits on a conversation.

---

## Right now, in order

1. **Two business decisions** (section 10) - one line each, and they unblock Workstream 7's design.
2. **Square sandbox credentials** (section 1) - blocks Workstream 2 steps 7, 18-20.
3. **A public URL for webhooks** (section 2) - blocks anything Square or Stripe pushes to us.
4. **An email provider and sending domain** (section 6) - blocks Workstream 3 steps 1-6, 12-14.
5. **Hosting decisions** (section 3) - blocks the real staging and production deploy.

Everything else below is later, and is listed so nothing gets lost.

---

## 1. Square sandbox

Where: https://developer.squareup.com/apps -> your application -> **Sandbox** credentials.

Put these in `.env` yourself. Do not paste secrets into chat; I read `.env` from disk when I need
them, and `.env` is git-ignored.

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
- [ ] Copy the webhook **signature key** into `SQUARE_WEBHOOK_SIGNATURE_KEY`
- [ ] A sandbox seller with at least one location and a few tracked items

**Then I run:** connect, catalog sync, a sandbox sale, the webhook landing, a delivery received,
and the reconciliation putting it all right. Workstream 2 steps 18-19.

## 2. A public URL for webhooks

Square and Stripe both have to reach this machine.

- **ngrok**: `ngrok http 8080`, then set `APP_BASE_URL` to the https URL it prints. It changes on
  each restart unless you pay for a reserved domain.
- **Or the staging deploy**, once you pick a host (section 3).

The Square signature check rebuilds what Square signed from `APP_BASE_URL`, so it must match the
notification URL exactly: https, no trailing slash.

## 3. Hosting decisions

- [ ] **Where it runs.** Anything that runs a container with a persistent Postgres: Fly.io, Render,
      Railway, or a VPS with the compose file as-is. No host-specific code either way.
- [ ] **Managed Postgres.** `DATABASE_URL` accepts the `postgres://` form hosts hand out.
- [ ] **Invoice photo storage.** A persistent volume at `UPLOAD_DIR`, or an S3-compatible bucket
      (`STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`,
      `S3_SECRET_ACCESS_KEY`).
- [ ] **A domain**, so `APP_BASE_URL` is https. Production refuses to start without it.
- [ ] **Sentry DSN** (optional): `SENTRY_DSN`. Errors are only logged without it.

## 4. Production secrets, when you deploy

```bash
python -c "import secrets;print(secrets.token_hex(32))"                                      # SECRET_KEY
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"     # ENCRYPTION_KEY
```

- [ ] `SECRET_KEY` - session signing. Changing it logs everyone out.
- [ ] `ENCRYPTION_KEY` - encrypts Square tokens at rest. **Changing it orphans every stored token**
      and every merchant must reconnect. Keep a copy where you will not lose it.
- [ ] `APP_ENV=production`, `APP_BASE_URL=https://yourdomain`

## 5. Square production, after sandbox passes

- [ ] Production credentials: `SQUARE_APP_ID`, `SQUARE_APP_SECRET`, `SQUARE_ENV=production`
- [ ] Production webhook subscription and its own signature key
- [ ] Redirect URL under the real domain
- [ ] A real merchant willing to connect - also your first customer conversation

## 6. Email (Workstream 3, steps 1-6 and 12-14)

**Pick a provider.** My recommendation is Postmark for transactional mail: the best deliverability
of the cheap options, and bounce webhooks that are simple to consume. Resend is the easiest to set
up; SES is the cheapest at volume and the most work.

- [ ] Provider account created
- [ ] Sending domain added there, e.g. `mail.yourdomain.com`
- [ ] **SPF**: a TXT record on the sending domain, contents from the provider
      (typically `v=spf1 include:<provider> ~all`)
- [ ] **DKIM**: the CNAME or TXT records the provider gives you, one per selector
- [ ] **DMARC**: a TXT record at `_dmarc.yourdomain.com`. Start at
      `v=DMARC1; p=none; rua=mailto:you@yourdomain.com`, then tighten to `p=quarantine` once the
      reports come back clean
- [ ] Credentials into production secrets: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`
- [ ] A from-address on that domain: `SMTP_FROM`, `SUPPORT_EMAIL`

**Once the provider is chosen, tell me which one** and I will wire its bounce webhook to the
suppression list that is already built (steps 12-13). It is a small adapter, but the payload shape
and signature scheme are provider-specific, so it cannot be written blind.

**Delivery testing (step 14)** needs real inboxes: a Gmail address and an Outlook/Hotmail address
you control. I will trigger one of each email type; you tell me where they landed, including spam.

Until all of this exists, email is logged instead of sent, which is why nothing has arrived yet.

## 7. Invoice photo reading (Workstream 4)

The pipeline is finished and tested apart from the part only real paper can prove:

- [ ] `ANTHROPIC_API_KEY` in `.env` for local development, and in production secrets later
- [ ] A spending limit on that key you are comfortable with
- [ ] **Real invoices**: photograph 3-5 delivery notes from different suppliers, as a phone would
      take them, and drop them in a folder for me. Different layouts matter far more than more
      copies of the same one. This is step 17, and it is what turns "the model returned JSON" into
      "this saves the owner time".

Unset, invoices fall back to manual entry and everything else still works. Photos are now turned
upright and shrunk before they are sent, so a 12 MB phone photo costs what a 200 KB one does, and
the original is still kept.

## 8. Best-before scanning (Workstream 5)

The harness is built and the text shapes are covered offline. What is missing is photographs of
real packaging, which only you can take.

- [ ] Photograph real products covering: large obvious dates, tiny printed dates, curved packaging,
      low-contrast dates
- [ ] Cover the wordings: `BEST BY`, `EXP`, `USE BY`, `SELL BY`
- [ ] Cover the formats: numeric (`10/24/26`), textual (`24 OCT 2026`), and ambiguous (`10/11/26`)
- [ ] Put them in one folder with a `cases.json` saying what each one actually says:

```json
[
  {"file": "yoghurt.jpg", "expected": "2026-10-24", "note": "tiny print on a curved pot"},
  {"file": "milk.jpg", "expected": "2026-09-22", "prefer": "dmy", "note": "low contrast"},
  {"file": "lot-code-only.jpg", "expected": null, "note": "no date printed at all"}
]
```

Then, with `ANTHROPIC_API_KEY` set:

```bash
python -m app.cli expiry-corpus var/date-photos --prefer mdy --write-failures
```

It prints how many were read correctly with no question asked, how many were asked about, and how
many were wrong — and adds every failure to the regression corpus. A dozen products is enough to
answer "does this beat typing them in".

## 9. Stripe billing (Workstream 6)

- [ ] Stripe account, test mode first
- [ ] Starter product and a $29/month price; Pro product and a $59/month price
- [ ] Price IDs into config: `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PRO`
- [ ] `STRIPE_SECRET_KEY` (test key first, live key later)
- [ ] `STRIPE_WEBHOOK_SECRET` from the webhook endpoint pointing at `{APP_BASE_URL}` (or from
      `stripe listen` while testing locally)
- [ ] Customer Portal enabled in the Stripe dashboard, with cancellation allowed
- [ ] The Stripe CLI installed, for the lifecycle test in step 18

## 10. Business decisions I should not make for you

Workstream 7 is built on these, and its first two steps are exactly these questions:

- [ ] **Trial length.** 14 days is the usual answer for this kind of tool.
- [ ] **What the trial exposes**, Starter or Pro. Pro converts better and makes the downgrade
      sting; Starter avoids disappointment. My recommendation: Pro for the trial, since the
      Pro-only features are the ones worth paying for.
- [ ] **What an expired trial can still do.** My recommendation: everything stays viewable and
      Square syncing continues, but receiving, counts and edits stop. Losing data visibility makes
      people leave rather than pay.
- [ ] **Failed payment.** My recommendation: a 7-day grace period with a banner, then the same
      read-only state as an expired trial.

One line each is enough; if you would rather I pick, say so and I will use the recommendations
above and note them in the code.

## 11. The first customer

- [ ] One real business to run the $149 setup with, from the six types you listed
- [ ] Their Square account connected, their non-POS stock entered, recipes set

---

## Already done

- [x] Reboot and Docker Desktop running again (2026-09-16)
- [x] Git repository, `main` branch, baseline tagged `workstream-1-verified`
- [x] Local staging keys for the Docker replica - session scratchpad, not for deployment

## Known about this machine

- Running the suite from Windows against the published Postgres port (`localhost:5433`) hangs:
  Docker Desktop's port proxy stalls. Run it inside the network instead:

  ```bash
  docker compose run --rm --no-deps --user root -v "C:/Users/oscar/.vscode/POS-Inventory-Automation:/src" -w /src -e TEST_DATABASE_URL=postgresql+psycopg://stockpulse:stockpulse@db:5432/stockpulse_test web sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q"
  ```

- Docker Desktop once crashed at startup over unreadable socket files in
  `%LOCALAPPDATA%\Docker\run`. The old folder was renamed to `run.stale-20260915172221`; it holds
  nothing but dead sockets and can be deleted.
