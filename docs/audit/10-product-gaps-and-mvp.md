# 10 — Product gaps, MVP specification, onboarding requirements

## Product gap analysis

Gaps that stop a juice bar or café owner using the product **without the founder**:

| ID | Gap | Why it blocks | Evidence |
|---|---|---|---|
| P1 | **No way to create or edit recipes.** Only the demo seed writes `recipe_components` | Weight/volume tracking — the juice-bar use case — cannot work; README's onboarding step 4 cannot be done by a customer | `app/cli.py:351` is the only writer |
| P2 | **No waste logging** (reasons, cost, who) | Waste prevention is the headline pitch; the only way to remove spoiled stock is to overwrite a count | — |
| P3 | **No item edit / deactivate** | A typo in unit, pack size or cost is permanent | `app/web/dashboard.py` has create, count, thresholds only |
| P4 | **No password reset / change, no email verification** | First forgotten password = support ticket | S2 |
| P5 | **No staff access** | Owners will not hand employees their login to log waste or receive deliveries | DB5 |
| P6 | **No org settings UI** (business name, timezone, weekly report day/hour) | README tells the founder to edit the tenant row | `README.md` "Onboarding" step 6 |
| P7 | **No data export / account deletion** | Required by the brief and by any privacy policy; reduces purchase risk | — |
| P8 | **No Square disconnect / reconnect state** | Expired or revoked token leaves a silent failure | SQ5 |
| P9 | Derived engine defects (C2, C3) | Numbers are wrong for exactly the target customer | [04](04-database-and-ledger.md) |
| P10 | Single-plan pricing not reflected | Pricing page sells Starter $29 / Pro $59 | `app/entitlements.py:49`, `NEEDS-YOU.md` §6 |

## MVP specification

**Customer:** one independent juice bar or café, one location, on Square, owner plus 1–5 staff.
**Plan:** one subscription (hypothesis $49/mo), everything included for one location (decision D1).

### In scope

| Area | Must do | Status today |
|---|---|---|
| Account | Sign up, verify email, log in (throttled), reset/change password, log out everywhere | partial |
| Staff | Invite by email; roles **Owner** (everything, billing) and **Staff** (receive, count, waste, view) | missing |
| Square | Connect, import catalog and counts, hourly reconciliation, inventory webhooks, disconnect/reconnect | mostly built, unproven live |
| Items | Create, edit, deactivate; count/weight/volume; pack sizes; cost; supplier text; barcodes | edit missing |
| Recipes | Map a Square item to ingredients and quantities in any unit; edit; see ingredient cost per drink | missing |
| Opening stock | Guided first count of every active item | partial (per-item count) |
| Receiving | Invoice photo → review → apply; barcode receive; Square adjustment with retry; batch + expiry capture | built; integrity fixes needed |
| Counts | Physical count, recorded as observation + variance movement, attributed | partial |
| Waste | Log waste by reason (spoiled, expired, prep error, remake, damaged, other), cost snapshot, actor; weekly waste cost | missing |
| Expiry | Batches, FEFO, expiring queue, markdown suggestions | built |
| Alerts | Low stock, out of stock, expiring — email digest | built, never delivered |
| Report | Weekly email: stock value, waste cost, money at risk, reorder list | partial (no waste) |
| Reorder | List by supplier + CSV | built |
| Billing | One plan, 30-day trial, Stripe checkout/portal, read-only lapse | built for two tiers |
| Data | CSV export of items, movements, waste, receipts; account deletion on request | missing |
| Integrity | Ledger, idempotent receipts, outbox retry, correct derived math | missing |

### Explicitly out of MVP

Purchase orders, supplier entities and price history, transfers, multi-location UI beyond what
exists, recipe versions, production/prep records, margin analytics, variance reports, refunds
handling beyond "ignore safely", forecasting, any second POS, SMS, mobile app.

### MVP acceptance

The `NEEDS-YOU.md` Phase D script, extended with: staff member logs waste from a phone; recipe
created in the UI depletes milk after a sandbox sale; delivery applied twice concurrently raises
stock once; Square outage during receipt is retried and lands.

## Customer onboarding requirements

Target: an owner gets to first value in **under 45 minutes, without the founder**. The $149
done-for-you setup stays available as an option, not a requirement.

| Step | Requirement | Exists |
|---|---|---|
| 1 | Sign up, verify email, business name + timezone | name only |
| 2 | Connect Square; show what was imported (locations, items, tracked SKUs) | connect yes; summary no |
| 3 | Choose the one location (single-plan) | automatic, oldest first |
| 4 | Add non-POS stock (milk, produce, cups) — bulk add or CSV import | one-by-one form |
| 5 | Build recipes for the top 10 sellers, with unit pickers | **no** |
| 6 | Opening count: one screen, all items, phone-friendly | no |
| 7 | Set alert recipients and report day | recipients per user yes; schedule no |
| 8 | Receive the first delivery by photo | yes |
| 9 | Invite staff | no |
| 10 | Checklist on the dashboard showing which of the above are done | no |
| — | In-app help link and support email on every page | footer email only |
| — | Cancellation and export without contacting the founder | cancel yes (Stripe portal); export no |
