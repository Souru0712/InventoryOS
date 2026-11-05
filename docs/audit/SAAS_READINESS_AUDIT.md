# SaaS readiness audit

Phase 2, 2026-11-04. Read-only. Baseline: [SAAS_REQUIREMENTS](../product/SAAS_REQUIREMENTS.md),
[USER_ROLES_AND_PERMISSIONS](../product/USER_ROLES_AND_PERMISSIONS.md),
[PRICING_AND_PACKAGING](../product/PRICING_AND_PACKAGING.md) (entitlements E-01..10), owner
decisions C-29..C-32. Subject: the existing app. Labels as in the
[technical audit](INVENTORY_PROJECT_TECHNICAL_AUDIT.md).

## Summary

| Area | Readiness | One-line verdict |
|---|---|---|
| Multi-tenancy | **Partial** | Every row has `tenant_id` and every route checks it, but by hand; no central mechanism (S8); webhook routing ambiguous (S7) |
| Authentication | **Partial** | Sign-up/login/logout with bcrypt; no verification, reset, throttling (S1, S2), sessions not revocable (S5). C-32 says finish it in-app |
| Authorization | **Missing** | One user = one tenant = full access; no roles, memberships or location scope |
| Billing | **Partial, strongest SaaS area** | Stripe Checkout, Portal, verified webhooks, out-of-order protection, grace and read-only lapse, all tested on recorded payloads; never run in Stripe test mode; one fixed-quantity line item |
| Entitlements | **Partial** | One server-side `allows()` check exists; plan catalogue is code; gates core features (contrary to E-05) |
| Location architecture | **Partial** | Locations exist per tenant but only Square creates them; no operating mode, address or business-day cutoff |
| Onboarding | **Missing** | Signup → dashboard "Connect Square"; no checklist, no POS-situation question, no imports |
| Export / deletion | **Missing** | Reorder CSV only; no deletion |
| Audit logging | **Missing** | No AuditEvent; partial `inventory_events` |
| Tenant administration | **Partial** | CLI only (`app/cli.py`); no impersonation controls (none needed yet) |

## 1. Multi-tenancy [CODE, RUN]

| Requirement | Status | Evidence |
|---|---|---|
| SAAS-T-01 tenant id on every record | **Met** | `app/models.py` docstring line 1; every business table |
| SAAS-T-02 locations belong to one org, scoped by entitlement | Partial | `locations.tenant_id`; plan limit flips `is_active` (`app/orchestration/subscriptions.py:43-66`), counting Square-inactive locations (SQ7) |
| SAAS-T-03 global users with memberships | **Not met** | `users.email` unique, `users.tenant_id` single (`app/models.py:125,128`); probe PA3 [RUN] |
| SAAS-T-04 central scoping mechanism | **Not met** | Each route repeats `db.get()` + `tenant_id !=` (S8); e.g. `app/web/dashboard.py:319-321` |
| SAAS-T-05 no cross-tenant references | Not enforced | FKs by id only; relies on route checks [INFERENCE] |
| SAAS-T-06 jobs run per org with explicit context | Met in practice | `app/orchestration/scheduler.py:37-52` iterates tenant ids |
| SAAS-T-07 tenant-scoped files, authorized serving | Partial | Files behind a storage key and an authorized route, but served with the uploader's content type (S3, probe R7) |
| SAAS-T-08 webhooks route to exactly one org | **Not met** | `.first()` over possibly several connections (S7, probe PA4) |
| SAAS-T-09 isolation tests for every resource | Partial | 6 cross-tenant tests; counts, receiving apply, item create, settings uncovered (S8) |
| SAAS-T-10 support access logged | N/A today | No support access feature |
| SAAS-T-11 one codebase/schema, differences as configuration (C-30) | **At risk** | Snack-box module is tenant-specific code gated by tenant id (`SNACK_BOX_TENANT_IDS`, `app/entitlements.py:224-237`) — exactly what C-30 forbids |

**Tenant isolation mechanism (was PD-18, now C-33).** Options that fit this codebase:

| Option | Fit | Notes |
|---|---|---|
| A. Mandatory scoped data-access layer (`scoped(db, Model, ctx)` / repository functions taking an org+location context; raw `db.get` banned by a test) | **Best fit** [RECOMMENDED] | Works on SQLite tests and Postgres; matches the existing load/transform layering; enforceable by a static test |
| B. Postgres row-level security with `SET app.org_id` per request | Strong second guard | Not testable on SQLite (tests would need Postgres for isolation); pooled connections need careful reset; adds defense in depth |
| C. Schema/database per tenant | Poor fit | Contradicts C-30's "one schema"; heavy migrations at 21–500 tenants |

Recommendation: **A now, B as defense in depth once CI runs on Postgres**; both satisfy SAAS-T-04
and DR-35 when location scope is part of the context. **Approved 2026-11-04 as C-33.**

## 2. Authentication (C-31, C-32) [CODE, RUN]

| Requirement | Status | Evidence |
|---|---|---|
| SAAS-A-01 email + password, ≥ 10 chars | Met (no breached-password check) | `app/web/auth.py:38-40`, `MIN_PASSWORD_LENGTH` |
| SAAS-A-02 verification | **Missing** | `app/web/auth.py:66-80` logs in immediately |
| SAAS-A-03 throttling | **Missing** | probe R8: 30 failed logins all 401, none delayed |
| SAAS-A-04 logout, log out everywhere | Partial | Logout is `GET` (S4); no server-side sessions to revoke (S5) |
| SAAS-A-05 reset, change password | **Missing** | No routes |
| SAAS-A-06 secure sessions, timeouts, revocation | Partial | Signed cookie, `SameSite=Lax`, Secure on https (`app/main.py:60-64`); 14-day default; no rotation or revocation |
| SAAS-A-07 TOTP MFA | **Missing** | — |
| SAAS-A-08 invitations | **Missing** | — |
| SAAS-A-09 org switcher | **Missing** (no memberships) | — |
| CSRF | Missing (relies on `SameSite=Lax`) | probe R8 |

What C-32 reuses: bcrypt hashing (`app/security.py:20-27`), login/logout routes, templates, SMTP
sender (`app/notify/email.py`), `email_log`/`email_suppressions`. What it adds: verification and
reset token table, server-side session table (fixes S5 and enables STF-FR-06), DB-backed throttle
counters, TOTP, invitations, CSRF tokens.

## 3. Authorization [CODE]

`require_tenant` and `require_writable` (`app/security.py:60-93`) are the only gates: "signed in
to this tenant" and "subscription allows writes". **No roles, no permission keys, no location
scope.** The blueprint's eight roles and permission matrix need: Membership (role + location
scope), a permission-key check per operation (STF-FR-03), and tests per permission. Nothing to
refactor here — it is new work on a clean seam.

## 4. Billing [CODE, RUN]

| Capability | Status | Evidence |
|---|---|---|
| Checkout, Portal | Implemented, verified with recorded Stripe payloads | `app/web/billing.py:37-95`, `tests/test_billing.py` |
| Webhook verification, idempotency, ordering | Implemented and verified | `construct_event`; `tenants.stripe_event_at` drops older events (`app/models.py:113-116`) |
| Lifecycle: trialing, active, past_due + 7-day grace, expired/canceled/paused → read-only, resubscribe | Implemented and verified | `app/entitlements.py:21-25,104-150`, `app/orchestration/subscriptions.py`, `tests/test_entitlements.py` |
| Read-only keeps syncing (PD-07 recommendation) | Implemented | `app/entitlements.py:200-205` message and `Access.READ_ONLY` |
| Per-location quantity billing (SAAS-B-18) | **Missing** | `line_items=[{"price": price_id, "quantity": 1}]` (`app/web/billing.py:56`) |
| Plan catalogue as configuration (SAAS-B-16, E-01) | **Partial** | Stripe price ids come from env (`app/config.py:60-61`) ✓; plan codes are a DB enum and code constants (`app/models.py:30-35`, `app/entitlements.py:27-28`) ✗; **displayed prices hard-coded** $29 / $59 / $149 (`app/web/marketing.py:16,30,43`) ✗ |
| Plans match the blueprint (Complete, Standalone; add-ons later) | **Not met** | Starter/Pro only |
| Stripe test mode end to end | **Never run** | `NEEDS-YOU.md` |
| Trial | 30 days, no card, on Pro (`app/entitlements.py:21-23`) | PD-05 still open |

## 5. Entitlements (E-01..10) [CODE, RUN]

| Principle | Status | Evidence |
|---|---|---|
| E-01 prices never in domain logic | Partial | Prices not in domain logic ✓; displayed in marketing code ✗ |
| E-02 one entitlement set, one server-side check | **Pattern exists** | `allows()` "the one question every route asks" (`app/entitlements.py:132-150`) |
| E-05 core never gated | **Violated** | Starter withholds reorder CSV and custom thresholds (`app/entitlements.py:50`); probe PA2 [RUN] |
| E-06 per-location quantity or packages | Missing | §4 |
| Operating mode as an entitlement | Missing | No mode concept |
| Location limit | Implemented (Starter 1, Pro unlimited) | `app/entitlements.py:50-51`, `app/orchestration/subscriptions.py:43` |
| Integration / advanced capability flags | Missing | Capabilities dataclass has 4 fields |
| Upgrade/downgrade | Implemented for Starter ↔ Pro, reruns location limit | `app/orchestration/subscriptions.py:94-100` |

Verdict: **refactor, not rebuild.** Keep `allows()` as the single check and the lifecycle logic;
replace the `CAPABILITIES` dict and `Plan` enum with a plan/entitlement table (Plan, Entitlement
in DOMAIN_MODEL §4.1) loaded per subscription; remove gating of core features.

## 6. Location architecture [CODE]

`Location` (`app/models.py:157-169`): tenant, `provider` (default `square`),
`provider_location_id` (required), name, timezone, currency, `is_active`. Missing for the
blueprint: address, business-day cutoff (ADM-FR-02), open days, operating mode / sales source of
record (ADM-FR-14, DR-31), and a way to create one without a POS (G1, G11). Stock identity is
per location (`stock_items` unique on tenant+location+slug), which is compatible with
location-specific inventory but duplicates item definitions across locations
([database audit §3](INVENTORY_PROJECT_DATABASE_AUDIT.md#3-domain-model-compatibility-with-the-blueprint)).

## 7. Onboarding [CODE]

Signup (`app/web/auth.py:29-82`) creates a tenant, starts a 30-day trial and one user, then sends
the user to checkout or the dashboard. The dashboard's only path is **Connect Square**
(`app/web/templates/app/dashboard.html:19-37,90-92`); the receiving page says "Connect Square (or
add a location)" but no route adds a location (`app/web/templates/app/receiving.html:38`). None of
SAAS §5 (POS-situation question, checklist, imports, opening count, team invite) exists. The
existing `README.md` onboarding is founder-assisted (step 6 edits the report schedule in the
database).

## 8. Readiness verdict

The SaaS shell is **a good start for billing and lifecycle, and new work for everything about
people and places**: memberships, roles, location scope, central tenant scoping, onboarding,
export/deletion and audit events. None of it requires discarding existing code; all of it must
exist before the first paying customer (R1).
