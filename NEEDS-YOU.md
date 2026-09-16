# Needs you

Everything in the build that I cannot do for you, in one place. I add to this file whenever a new
one appears, and tick items off as they land.

Last updated: 2026-09-16, after Workstream 2.

---

## Right now, in order

1. **Square sandbox credentials** - blocks the rest of Workstream 2 (steps 7, 18-20).
2. **A public URL for webhooks** - blocks testing anything Square pushes to us.
3. **Hosting decisions** - blocks Workstream 1 steps 19-20, the real staging and production deploy.

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
      (locally, `http://localhost:8000/connect/square/callback`, or your ngrok URL)
- [ ] Webhook notification URL: `{APP_BASE_URL}/webhooks/square`
- [ ] Webhook events subscribed: `inventory.count.updated`, `catalog.version.updated`,
      `payment.updated`, `refund.created`, `refund.updated`
- [ ] Copy the webhook **signature key** into `SQUARE_WEBHOOK_SIGNATURE_KEY`
- [ ] Create a sandbox seller with at least one location and a few tracked items

Scopes are requested by the app itself and need no setup from you: `MERCHANT_PROFILE_READ`,
`ITEMS_READ`, `INVENTORY_READ`, `INVENTORY_WRITE`, `ORDERS_READ`, `ORDERS_WRITE`, `PAYMENTS_READ`,
`PAYMENTS_WRITE`.

**What I do once this is in place:** connect a sandbox seller, sync the catalog, ring up a sandbox
sale, watch the webhook land, receive a delivery, and prove the reconciliation puts everything
right. That is Workstream 2 steps 18-19.

## 2. A public URL for webhooks

Square has to be able to reach this machine. Either is fine:

- **ngrok** (quickest): install it, run `ngrok http 8080`, then set `APP_BASE_URL` to the https URL
  it prints. Note the URL changes each restart unless you have a paid reserved domain.
- **The staging deploy**, once you have picked a host (section 3).

The signature check uses `APP_BASE_URL` to rebuild what Square signed, so it must match the
notification URL exactly, including https and no trailing slash.

## 3. Hosting decisions

None of these block local work; all of them block a real deploy.

- [ ] **Where it runs.** Anything that runs a container and gives a persistent Postgres: Fly.io,
      Render, Railway, a small VPS with the compose file as-is. My recommendation is whichever you
      already have an account with; the app has no host-specific code.
- [ ] **Managed Postgres.** `DATABASE_URL` accepts the `postgres://` form hosts hand out.
- [ ] **Invoice photo storage.** Either a persistent volume mounted at `UPLOAD_DIR`, or an
      S3-compatible bucket (AWS S3, Cloudflare R2, Backblaze B2, DigitalOcean Spaces):
      `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`,
      `S3_SECRET_ACCESS_KEY`.
- [ ] **A domain**, so `APP_BASE_URL` is https. Production refuses to start without it.
- [ ] **Sentry DSN** (optional but recommended): `SENTRY_DSN`. Errors are only logged without it.

## 4. Production secrets, when you deploy

Generate these per environment, never reuse the dev ones:

```bash
python -c "import secrets;print(secrets.token_hex(32))"                                      # SECRET_KEY
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"     # ENCRYPTION_KEY
```

- [ ] `SECRET_KEY` - session signing. Changing it logs everyone out.
- [ ] `ENCRYPTION_KEY` - encrypts Square tokens at rest. **Changing it orphans every stored token**
      and every merchant has to reconnect. Keep a copy somewhere you will not lose.
- [ ] `APP_ENV=production`, `APP_BASE_URL=https://yourdomain`

## 5. Square production, after sandbox passes

- [ ] Production application credentials: `SQUARE_APP_ID`, `SQUARE_APP_SECRET`, `SQUARE_ENV=production`
- [ ] Production webhook subscription and its own signature key
- [ ] Redirect URL under the real domain
- [ ] A real merchant willing to connect (this is also your first customer conversation)

## 6. Email, for alerts and the weekly report

- [ ] An SMTP provider account (Postmark, Resend, SES, Mailgun): `SMTP_HOST`, `SMTP_PORT`,
      `SMTP_USER`, `SMTP_PASSWORD`
- [ ] A from-address on a domain you control: `SMTP_FROM`, `SUPPORT_EMAIL`
- [ ] SPF and DKIM records on that domain, or the mail lands in spam

Unset, email is logged instead of sent, which is why nothing has been emailed so far.

## 7. Invoice photo reading

- [ ] `ANTHROPIC_API_KEY` for the production environment, and a spending limit you are comfortable
      with. Unset, invoices fall back to manual entry and everything else still works.

## 8. Billing, when you get there

- [ ] Stripe account and keys: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- [ ] Price objects for the two plans: `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PRO`
- [ ] Stripe webhook endpoint pointing at the deployed app

## 9. The first customer

- [ ] One real business to run the $149 setup with, from the six types you listed
- [ ] Their Square account connected, their non-POS stock entered, recipes set

---

## Already done

- [x] Reboot and Docker Desktop running again (2026-09-16)
- [x] Local staging keys generated for the Docker replica; they live in this session's scratchpad
      and are not the ones you deploy with
- [x] Git repository created, `main` branch, baseline tagged `workstream-1-verified`

## Known about this machine

- Running the test suite from Windows against the published Postgres port (`localhost:5433`) hangs:
  Docker Desktop's port proxy stalls. Run tests inside the network instead:

  ```bash
  docker compose run --rm --no-deps --user root -v "C:/Users/oscar/.vscode/POS-Inventory-Automation:/src" -w /src -e TEST_DATABASE_URL=postgresql+psycopg://stockpulse:stockpulse@db:5432/stockpulse_test web sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q"
  ```

- Docker Desktop previously crashed at startup over unreadable socket files in
  `%LOCALAPPDATA%\Docker\run`. The old folder was renamed to `run.stale-20260915172221`; it holds
  nothing but dead sockets and can be deleted.
