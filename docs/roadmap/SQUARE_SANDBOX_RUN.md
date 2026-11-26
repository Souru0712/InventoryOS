# Square sandbox end-to-end run (T3.11)

The Connected path, run once against a real Square sandbox seller and recorded (NFR §8 gate 4,
TR-11). Every step names the acceptance criterion it covers, what to do, and what must be true
afterwards. The automated suite already covers each criterion with recorded payloads. This run
proves the same against Square itself.

**Needs the owner:** a Square developer account with a sandbox seller (OD-05, OA-2), and where the
app runs for the run: staging or this computer with a tunnel (PD-52).

## 1. Before the run

| What | Where | Detail |
|---|---|---|
| Square application | Developer Dashboard → Applications | Toggle to **Sandbox**. Note the sandbox *Application ID*, *Application secret* and the default test seller's *Sandbox access token* |
| OAuth redirect URL | App → OAuth (Sandbox) | `{APP_BASE_URL}/connect/square/callback` |
| Webhook subscription | App → Webhooks (Sandbox) | URL `{APP_BASE_URL}/webhooks/square`; API version `2025-01-23` (the app's `SQUARE_API_VERSION`); events `order.created`, `order.updated`, `order.fulfillment.updated`, `catalog.version.updated`, `oauth.authorization.revoked`. Note its *Signature key* |
| The app's settings | Staging environment (or `.env` locally) | `SQUARE_ENV=sandbox`, `SQUARE_APP_ID`, `SQUARE_APP_SECRET`, `SQUARE_WEBHOOK_SIGNATURE_KEY`, `APP_BASE_URL` (the public HTTPS address), `ENCRYPTION_KEY`; `SQUARE_WRITE_BACK` unset. The scheduler must run (`RUN_SCHEDULER_IN_WEB=true` locally): order reads every 5 minutes, checks every 6 hours |
| The seller's token | The operator's own `.env` only | `SQUARE_SANDBOX_ACCESS_TOKEN`, read by `scripts/seed_square_sandbox.py`; never on staging, never printed |
| A public HTTPS address | Staging (T0.09, C-49) or a tunnel to `localhost:8000` | Square delivers webhooks only to a public HTTPS address. Without one, sales still arrive by the 5-minute read, but steps 9, 10 and 17 need it |

## 2. The run

Record each step's time and result in the log (section 3).
`python -m app.cli pos-report --tenant N` prints what has reached InventoryOS at any point.

| # | Step | Do | Must be true | Covers |
|---|---|---|---|---|
| 1 | Seed the seller | `python scripts/seed_square_sandbox.py catalog` | 4 items, 5 variations (Latte 12 oz and 16 oz, Cold brew, Croissant, Saffron by the milligram), lists Milk and Ice | — |
| 2 | Business | Sign up, confirm the email, add the location "Main" with its time zone | Dashboard shows "No sales entered yet" | — |
| 3 | Connect | `/app/connection` → Connect Square → Allow | Square's consent lists only merchant profile, items and orders; connected; `pos-report` shows the merchant | AC-POS-01, SQ-02 |
| 4 | Track and import | `/app/connection`: the Square location tracked by Main | Catalog "4 items, 5 variations and 3 modifiers"; `pos-report` units include `mg` and categories Coffee, Bakery, Pantry | AC-POS-01, T3.03 |
| 5 | Second business | A second business connects the same seller | "already connected to another business"; the first stays connected | AC-POS-11 |
| 6 | Map | `/app/mappings`: "Make it one of my products" for Latte 12 oz and 16 oz and Croissant (not Cold brew yet); create the modifiers Oat milk (no effect) and Light ice (scale 1.15) and map Square's to them; give Latte 12 oz a recipe (milk 240 ml) | Mappings saved and audited | POS-FR-06 |
| 7 | Sales from Square | Locations → Main → "Where sales come from" → Square from today | Preview, then switched; hand entry for today refused | AC-SAL-06 |
| 8 | Sell | `sell LATTE-12:2:oat` | Within 5 minutes of completion: a sale, two latte lines, milk down 480 ml at the order's completion time; dashboard "Square sales last arrived …" | AC-POS-02 |
| 4b | Second location | `seed_square_sandbox.py location Downtown`; add the location Downtown, keep Milk there with a count; refresh the catalog; Downtown tracks Square's Downtown and takes its sales from Square; `--location Downtown sell LATTE-12:1` and `--location InventoryOS sell LATTE-12:1` | Each sale lands at its own location; milk goes down only there | POS-FR-02 |
| 9 | Duplicates | The Developer Console's webhook log offers no resend (2026-11): copy that order's `order.created` payload from the log and post it 3 times to `/webhooks/square`, signed with the subscription key over URL + body; wait for the next scheduled read | Events answered `duplicate`; the sale's revision stays 1; milk unchanged | AC-POS-03 |
| 10 | Forged webhook | `python scripts/send_test_webhook.py` | The forged call answers 400 and stores nothing | AC-POS-10 |
| 11 | Open and cancelled | `open LATTE-12:1`, then `cancel LATTE-12:1` | Both staged (`pos-report` orders open 1, canceled 1); no sale, no consumption | AC-POS-04 |
| 12 | Refund | `refund ORDER_FROM_8 --cents 450` | Connection page "1 refund recorded"; `pos-report` refunds 1, 0 unmatched, and its money line net lowered by 4.50; milk unchanged | AC-POS-05 |
| 13 | Comp | `sell LATTE-12:1 --comp` | Consumption 240 ml; net 0 | POS-FR-26 |
| 14 | Unmapped | `sell BREW-16:1`; then map Cold brew and give it a recipe; reprocess from the preview | `unmapped_product` opened, then resolved on mapping; the preview names 1 sale; after the job, consumption posted once at the order's time | AC-POS-06 |
| 15 | Missed webhook | Turn the webhook subscription off; `sell LATTE-16:1`; turn it back on | The sale arrives by the scheduled read within 60 minutes | AC-POS-07 |
| 16 | Daily check | After the business day closes, run the `reconcile_orders` job (or wait for the 6-hourly check) | Sync history "Daily check: 1 day(s) checked, 0 differed" | AC-POS-08 (the mismatch itself is in the suite: a sandbox order cannot be skipped on purpose) |
| 17 | Revocation | Production: the seller's Square dashboard → Apps → remove the app. Sandbox (no apps page; the console revokes only its own token): `POST /oauth2/revoke` with the app's client id and the connection's token, `Authorization: Client <secret>`, from outside the app. It also ends the console token the script uses | `oauth.authorization.revoked` arrives: connection revoked, critical `sync_failure`, Owner email logged; dashboard "Square stopped accepting the connection" | AC-POS-09 |
| 18 | Reconnect, disconnect | Reconnect; then Disconnect | Reconnect resolves the exception; disconnect confirms the revocation at Square, clears the tokens, keeps history | SQ-04 |
| 19 | Record | `python -m app.cli pos-report --tenant N` | Its output goes into the log | — |

## 3. Run log

Copy into `docs/EXECUTION-LOG.md` as the T3.11 entry.

| # | Time (UTC) | Result | Notes (order ids, minutes to arrive) |
|---|---|---|---|
| 1–19 | | pass / fail | |

A failed step is a defect against its task. It is recorded with the `pos-report` output and fixed
before T3.11 is complete (Acceptance: "recorded run with every step passing").

## 4. Known questions this run answers

Answered by the 2026-11-22 run (entry in `docs/EXECUTION-LOG.md`):

- **Refund shape:** Square puts the refund on a new return order (COMPLETED, `returns` naming the
  source order, `refunds` with the money); the original order gets neither. The Orders API's
  refund status is APPROVED, never COMPLETED (fixed in the normalizer after this run).
- **Categories:** `reporting_category` reaches external products.
- **Completion:** a paid order with no fulfilment completes on payment.
- **Consent screen:** Square skips it when the application is already authorized for the seller
  (as after creating the test account's token in the console); it shows after a revocation.
- **OAuth in the sandbox:** the seller test account must be opened from the Developer Console in
  the same browser first, or Square answers 400.
