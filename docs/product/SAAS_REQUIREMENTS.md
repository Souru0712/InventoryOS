# SaaS Requirements

Authoritative source for **tenancy, authentication, account lifecycle, subscription/billing,
onboarding, data export and deletion**. InventoryOS is a multi-tenant commercial SaaS [CONFIRMED].

## 1. Tenancy and isolation

| ID | Requirement | Rel |
|---|---|---|
| SAAS-T-01 | Tenant = **Organization** (one business). Every tenant-owned record carries `organization_id` | R1 |
| SAAS-T-02 | Business locations belong to one organization; location-scoped records carry `location_id`. The number of active locations is set by the entitlement (Complete: location quantity; Standalone: one); memberships carry a location scope | R1 |
| SAAS-T-03 | Users are global identities; access to an organization only through an active Membership | R1 |
| SAAS-T-04 | Tenant scoping enforced by a **central mechanism** (e.g. database row-level security, or a mandatory scoped data-access layer) — never by per-route convention. Mechanism: mandatory scoped data-access layer, Postgres RLS later as a second guard (C-33) | R1 |
| SAAS-T-05 | Cross-tenant references impossible: every foreign key target must belong to the same organization (validated in the write path; DB-enforced where practical) | R1 |
| SAAS-T-06 | Background jobs run per organization with the organization id as explicit context; a job never queries across tenants except system-level schedulers that only enumerate tenant ids | R1 |
| SAAS-T-07 | Files stored under tenant-scoped keys; served via authorized, short-lived URLs or proxied responses (DR-28) | R1 |
| SAAS-T-08 | External webhooks route to exactly one organization via the connection's external merchant + location (DR-22) | R1 |
| SAAS-T-09 | Automated isolation tests: for every resource type, a user of org A receives 404/403 for org B's ids on read, write and file access | R1 |
| SAAS-T-10 | Support/admin access to a tenant is explicit, logged (AuditEvent with support actor) and time-boxed | R1 |
| SAAS-T-11 | One codebase and one schema serve every tenant. Differences between businesses and segments are tenant configuration and data, never tenant-specific code branches, schemas or deployments [CONFIRMED C-30] | R1 |

Context: the existing app is multi-tenant with checks repeated by hand per route (audit S8); JuJu
is single-store with no tenant concept but enforces roles with Postgres RLS. Neither is a drop-in
answer to SAAS-T-04; Phase 2 decides.

## 2. Authentication

| ID | Requirement | Rel |
|---|---|---|
| SAAS-A-01 | Registration with email + password; password policy ≥ 10 chars, breached-password check where available | R1 |
| SAAS-A-02 | Email verification before connecting a POS, uploading invoices (AI cost) or inviting staff [CONFIRMED C-31] | R1 |
| SAAS-A-03 | Login with throttling per account and per IP; generic error messages [CONFIRMED C-31] | R1 |
| SAAS-A-04 | Logout; "log out all devices" | R1 |
| SAAS-A-05 | Password reset by emailed single-use, expiring token; change password while signed in [CONFIRMED C-31] | R1 |
| SAAS-A-06 | Sessions: secure, HttpOnly, SameSite cookies where the architecture allows; idle and absolute timeouts; revocation on password change and membership revocation | R1 |
| SAAS-A-07 | MFA (TOTP) available to all; strongly prompted for Owners (PD-20 decides whether required) | R1 |
| SAAS-A-08 | Invitations: emailed link, expires in 7 days, single use, bound to invited email | R1 |
| SAAS-A-09 | Organization switcher for users with several memberships | R1 |
| SAAS-A-10 | SSO (Google/Microsoft) | Future |

**Options considered (PD-21, decided 2026-11-04 → C-32: option 1).** Every option had to meet SAAS-A-01..09, including the three
the owner made non-negotiable: verification, reset and throttling (C-31). Starting point: the
existing app already has sign-up, login and logout, bcrypt password hashing, signed-cookie sessions
and SMTP email; it lacks verification, reset, throttling (audit S1/S2) and revocable sessions (S5).

| Option | What it means here | Strengths | Costs and risks |
|---|---|---|---|
| **1. Finish the in-app auth** [RECOMMENDED] | Keep the existing login and add verification, reset, throttling, server-side sessions and TOTP MFA, using maintained libraries for hashing, tokens and one-time codes | No vendor or per-user fees; users, memberships and roles stay in one database with the tenant model (SAAS-T-03); fits the server-rendered app; much of the flow exists | InventoryOS owns this security code and its tests; throttling must use shared storage (the database) to work across app instances; signed-cookie sessions cannot be revoked, so a server-side session table replaces them |
| **2. Managed identity provider** (e.g. Auth0, Clerk, WorkOS, AWS Cognito) | The provider hosts sign-up, login, verification, reset, throttling and bot protection, MFA and later SSO; the app keeps Organization, Membership and roles and trusts the provider's sign-in | Least security code to own; MFA, breached-password checks and SSO are usually built in | Monthly cost grows with users (prices change; compare at decision time); lock-in, since leaving can force password resets where hashes cannot be exported; user data in two places; another sub-processor and outage dependency; redirect-based login adds integration work |
| **3. Self-hosted open-source identity server** (e.g. Keycloak, Ory, Authentik) | Run an identity service next to the app | Option 2's features without per-user fees | Another service to operate, patch, secure and back up; heavy for a one-founder team at 21 customers |
| Supabase Auth | JuJu's approach | Works and is proven in JuJu | Ruled out: a managed provider like option 2 that also ties the Python app to a Supabase project it does not otherwise use |

Why option 1: the missing pieces are well understood and bounded, the scale is small (A-03), and
identity stays next to the memberships and tenant model it must agree with. Option 2 is the better
choice if the owner would rather not own security code, or if SSO (SAAS-A-10) becomes a sales
requirement early; revisit C-32 if that happens. Phase 2 sizes option 1's work.

Criteria: security defaults, MFA, email flows, multi-org membership support, cost at 21–500 users,
lock-in, fit with the chosen tenancy mechanism.

## 3. Account lifecycle

```
signed_up (unverified) → verified → onboarding → active (trialing|paid) → past_due (grace)
   → read_only (canceled/unpaid) → pending_deletion → deleted
                     ▲                    │
                     └──── resubscribe ◄──┘
```

| State | Access | POS ingestion | Alerts/emails |
|---|---|---|---|
| unverified | Profile + verification only | — | Verification email |
| trialing / active | Full | Yes | Yes |
| past_due (grace) | Full, banner | Yes | Yes + billing notices |
| read_only | View, reports, export, billing | Continues (keeps history whole for return) — PD-07 | Billing notices only |
| pending_deletion | Export + cancel deletion (Owner) | Stopped; POS tokens revoked | Deletion notices |
| deleted | None | — | Confirmation |

## 4. Billing

| ID | Requirement | Rel |
|---|---|---|
| SAAS-B-01 | Plans at launch: **InventoryOS Complete** (hypothesis $99/location/month, Connected) and **InventoryOS Standalone** (hypothesis $99/month, one location); both include the complete core product. Catalogue and prices: [PRICING_AND_PACKAGING](PRICING_AND_PACKAGING.md) [CONFIRMED as hypotheses] | R1 |
| SAAS-B-02 | Provider: **Stripe** [CONFIRMED]. Hosted Checkout for purchase; Customer Portal for card update, invoices, cancellation | R1 |
| SAAS-B-03 | Subscription status mirrors Stripe: `trialing`, `active`, `past_due`, `unpaid`, `canceled`, `incomplete`, `incomplete_expired`, `paused` → mapped to access states in §3 | R1 |
| SAAS-B-04 | Webhooks: signature verified; processed idempotently by event id; out-of-order events resolved by re-fetching the subscription from Stripe (or event-time ordering) — never by arrival order | R1 |
| SAAS-B-05 | Renewal: automatic monthly; receipt email from Stripe | R1 |
| SAAS-B-06 | Failed payment: Stripe smart retries; in-app banner + email to Owners; **grace period** with full access (length PD-05, recommended 7 days), then read-only | R1 |
| SAAS-B-07 | Cancellation: at period end by default; full access until then; then read-only | R1 |
| SAAS-B-08 | Resubscribe from read-only restores full access immediately; no data lost | R1 |
| SAAS-B-09 | Entitlement check is a single server-side function used by every write path ("can this org write?") | R1 |
| SAAS-B-10 | Core capabilities are never gated: Complete and Standalone differ only in sales source; entitlements gate location count and beyond-core (Advanced) capabilities only (E-05) | R1 |
| SAAS-B-11 | Trial: length and whether a card is required — **[OPEN] PD-05** (existing app: 30-day trial) | R1 |
| SAAS-B-12 | One-off done-for-you setup fee — **[OPEN] PD-06** (existing app sells $149); not offered unless approved | — |
| SAAS-B-13 | Sales tax on the subscription (e.g. Stripe Tax) — **[OPEN] PD-22** | R1 |
| SAAS-B-14 | Refunds of subscription charges: manual by founder via Stripe; policy published — **[OPEN] PD-23** | R1 |
| SAAS-B-15 | Billing events (status changes, failures) written to AuditEvent and visible to Owners | R1 |
| SAAS-B-16 | Plans, prices, per-location rates and packages are configuration in the billing provider and plan table; no price appears in domain code (E-01, DR-36) | R1 |
| SAAS-B-17 | Entitlement set per subscription: allowed modes, max locations, capability flags, usage limits; one server-side check (E-02) | R1 |
| SAAS-B-18 | Complete billed by location quantity (active Connected locations); per-location pricing supports quantity or package models by configuration (E-06) | R1 |
| SAAS-B-19 | Self-serve plan change Standalone ↔ Complete tied to the operating-mode cutover (WF-25), prorated by Stripe; no data migration (E-10) | R1 |
| SAAS-B-20 | InventoryOS POS is not sold; interest in it is captured at signup as validation evidence (PD-35, PD-36) | R1 |
| SAAS-B-21 | Multi-location and Advanced are added as configuration (plans/add-ons), combinable with either mode, when the roadmap reaches them | Expansion / Advanced |

Existing-app note: billing lifecycle, entitlements and read-only lapse are built and tested
against recorded Stripe payloads, but for **two** plans (Starter $29 / Pro $59) and never run in
Stripe test mode (audit D1). InventoryOS requires the catalogue in §4.1; the existing capability-flag pattern is reusable, its plan list is not.

### 4.1 Plan catalogue (pricing hypotheses)

| Plan | Mode | Locations | Pricing hypothesis | Sold at launch |
|---|---|---|---|---|
| InventoryOS Complete | Connected | Quantity-based | $99 / location / month | Yes |
| InventoryOS Standalone | Standalone | One | $99 / month | Yes |
| InventoryOS POS | Native POS | — | $149–199 / month starting + pass-through costs | No — validation first |
| Multi-location | Any | Several | ~$149–199+ / month, configurable per location or package | No — expansion |
| Advanced | Any | Any | ~$199–299+ / month | No — expansion |

Full packaging matrix, entitlement principles and validation questions:
[PRICING_AND_PACKAGING](PRICING_AND_PACKAGING.md).

## 5. Onboarding

Goal [RECOMMENDED]: a Square-connected café with ≤150 catalog items reaches first value — top
sellers mapped to recipes and an opening count posted — **in under 45 minutes, without the
founder**. A dashboard checklist (CMD-FR-11) tracks each step; any step can be skipped and resumed. A Standalone café reaches first value — items, top 10 recipes,
opening count and first day's sales entered — in the same time.

**Step 0 — "How do you take payments today?"** routes the customer (WF-01):

| Answer | Path | Plan |
|---|---|---|
| "I use Square" (or another supported POS) | Connected | Complete |
| "No POS, and I don't want one" | Standalone | Standalone |
| "No POS, but I want one" | Standalone now, or a supported POS + Complete; interest recorded for InventoryOS POS validation | Standalone or Complete (PD-36) |
| "I use a POS you don't support yet" | Standalone with sales file import; POS name recorded as adapter demand | Standalone |

| # | Step [CONFIRMED list] | Square-connected path | Standalone path |
|---|---|---|---|
| 1 | Create organization | Sign up → verify email → business name | Same |
| 2 | Configure location | Address, **time zone** (pre-filled from browser/Square), currency, business-day cutoff | Same, no Square pre-fill |
| 3 | Add/import inventory items | Square catalog imported as sellable products; ingredients added via CSV import, starter template (R1.1) or quick-add grid | CSV import, starter template (R1.1) or quick-add grid |
| 4 | Configure units | Display units per item; purchase packs; cross-dimension conversions prompted when a recipe needs them | Same |
| 5 | Add suppliers | Name + lead time + delivery days (contact optional) | Same |
| 6 | Create recipes | Start with top sellers ranked by Square sales volume (last 30 days); "Start from" scales between sizes | Products created manually, then recipes |
| 7 | Connect POS (optional) | OAuth → choose location → import summary (items, variations, modifiers) | Skipped; choose daily sales entry or sales file import, set open days and first business date (WF-16) |
| 8 | Map products to recipes | Suggested matches by name; unmapped list with sales counts | N/A |
| 9 | Configure alerts | Low-stock thresholds (bulk set by category), expiry window, email recipients, digest time | Same |
| 10 | Begin operations | Guided **opening count** (one screen, phone-friendly) → posts `opening_balance`; first receiving; invite staff | Same |

Rules:
- Onboarding never blocks the product: partial setup works and the dashboard shows what is
  incomplete (CMD-FR-13).
- Sales arriving before recipes exist are stored and can be reprocessed after mapping (WF-28).
- Opening count timestamps define the start of reliable variance reporting.

### Support during onboarding
- In-app help link and support email on every page.
- Optional founder-assisted setup call — commercial terms PD-06.

## 6. Data export, retention and deletion

| ID | Requirement | Rel |
|---|---|---|
| SAAS-D-01 | Owner/Manager can export all business data as CSV (zip): items, conversions, suppliers, recipes + versions, movements, batches, receivings + invoices (files), waste, counts, exceptions | R1 |
| SAAS-D-02 | Export available in every state except deleted | R1 |
| SAAS-D-03 | Retention after cancellation: **[OPEN] PD-07** (audit recommended read-only 90 days, then deletion) | R1 |
| SAAS-D-04 | Deletion on Owner request: confirmation, 7-day cancellable window, then hard deletion of tenant data and files; backups age out per backup retention; written confirmation | R1 |
| SAAS-D-05 | Personal data of staff (names, emails) deleted with the organization; AuditEvents keep actor ids only where legally needed | R1 |
| SAAS-D-06 | Privacy policy and terms published before first paying customer | R1 |

## 7. Support and operations

| ID | Requirement | Rel |
|---|---|---|
| SAAS-S-01 | Support contact (email) on every page; response target published (e.g. 1 business day) | R1 |
| SAAS-S-02 | Status/health page or uptime monitor for the founder; customer-facing status page optional | R1 |
| SAAS-S-03 | Founder admin tools: find tenant, view sync health, re-run sync, impersonation **only** with audit (SAAS-T-10) | R1 |
| SAAS-S-04 | Product analytics limited to operational usage counts (no third-party trackers in the app without consent) | R1 |
| SAAS-S-05 | Tenant administration: suspend/restore a tenant, export on the owner's behalf, process deletion, view entitlement and usage — all audited | R1 |
