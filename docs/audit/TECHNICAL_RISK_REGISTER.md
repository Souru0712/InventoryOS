# Technical risk register

Phase 2, 2026-11-04. Read-only. Prioritized by severity, then timing. Finding IDs: baseline (C, S,
SQ, DB, O, T) from [01-executive-audit](01-executive-audit.md#findings-register); Phase 2 gaps
G1–G15 from the [traceability matrix](INVENTORYOS_REQUIREMENTS_TRACEABILITY.md#3-gap-register-g1g15).

Likelihood and impact: High / Medium / Low. Severity: Critical / High / Medium / Low.
Timing: **Now** (before any build work) · **Engine** (first build milestone: ledger and core) ·
**Pre-customer** (before the first paying customer) · **Pre-Square-pilot** · **Post-launch**.

| ID | Category | Risk | Evidence | Likelihood | Impact | Severity | Mitigation | Dependencies | Timing |
|---|---|---|---|---|---|---|---|---|---|
| TR-01 | Operational | ~~The only copy of the code is on one laptop: no git remote, no backup of the repository~~ **Resolved 2026-11-05:** pushed to the private GitHub repository `Souru0712/InventoryOS`; local `main` matches `origin/main` (CI still to set up, TR-14) | `git remote` empty [RUN]; O1 | Medium | High (total loss) | **Critical** | Push to a private GitHub repository; enable CI there | Owner action | **Now** |
| TR-02 | Data integrity | No inventory ledger: balances are overwritten by seven code paths; history cannot explain a number | C6; [DB audit §4](INVENTORY_PROJECT_DATABASE_AUDIT.md#4-inventory-update-logic) | High | High | **Critical** | Ledger with InventoryTransaction + movements; balances as projection (DR-01..07) | Ledger design approved (C-35); TR-07 context | Engine |
| TR-03 | Data integrity | Theoretical consumption loses same-day sales after a count/receipt and drains the wrong location | C2, C3; probes R1, R2 [RUN] | High (daily for juice bars) | High | **Critical** | Replace the daily-aggregate engine with order-line consumption on the ledger; do not patch | TR-02, TR-22 | Engine |
| TR-04 | Product scope / architecture | Standalone mode is impossible: no location, product, recipe or sales record can exist without Square; no alerts without Square | G1, G2, G14; probe PA1 [RUN] | High (certain) | High (half the launch offering) | **Critical** | Location/product creation, sales capture (STD-FR-02..04), alerts for every tenant | TR-02, TR-22 | Engine → Standalone |
| TR-05 | Multi-tenancy | Tenant isolation depends on every route remembering a manual check; no central enforcement | S8; `app/web/dashboard.py:319-321` | Medium | High (cross-tenant leak) | **High** | Mandatory scoped data access (C-33) + isolation test over every route; RLS later | C-33 (approved) | Engine (before new routes multiply) |
| TR-06 | Architecture | Square owns the product catalog (`sold_items` are Square variations) and, by default, stock counts (`TrackMode.PROVIDER`) | `app/models.py:171,212`, `app/load/upsert.py:74,197`; G12 | High | High | **High** | InventoryOS-owned catalog with mapping; ledger as authority; Square counts as observations (C-34) | C-34 (approved) | Engine |
| TR-07 | Data integrity | Invoice can be applied twice under concurrent requests | C5; probe R4 [RUN] | Medium | High | **High** | Conditional status transition / row lock; idempotency key on posting | TR-02 | Engine |
| TR-08 | Integration | A failed Square write on receipt or count is swallowed, then the next sync erases the delivery | C4; probe R3 [RUN] | Medium | High | **High** | Local commit first, outbox with retry and surfaced failures; disable write-back until then (POS-FR-13 is R1.1) | TR-02 | Engine (disable) / Pre-Square-pilot (outbox) |
| TR-09 | Security | No login throttling, no password reset, no email verification | S1, S2; probe R8 [RUN] | High | High | **High** | Finish in-app auth (C-31, C-32) | — | Pre-customer |
| TR-10 | Security | Stored XSS: invoice images served with the uploader's content type, no `nosniff` | S3; probe R7 [RUN] | Medium | High | **High** | Serve images with sniffed/allow-listed type, `nosniff`, `Content-Disposition` (SEC-08, SEC-13) | — | Pre-customer (small fix) |
| TR-11 | Integration | Nothing external has ever run live: Square sandbox, Stripe test mode, SMTP delivery, Anthropic on real documents | `NEEDS-YOU.md`; baseline 06 | High | High | **High** | Sandbox and test-mode runs as gates (NFR §8 gates 4–6) | Owner credentials | Pre-customer / Pre-Square-pilot |
| TR-12 | Reliability | No backups or restore procedure | O2 | Medium | High | **High** | Managed Postgres with PITR; restore drill (REL-05) | Host choice | Pre-customer |
| TR-13 | Billing | Plan catalogue hard-coded (Starter/Pro), core features gated, one fixed-quantity line item — the approved pricing model cannot be sold | G3; probe PA2 [RUN]; `app/web/billing.py:56` | High (certain) | Medium | **High** | Data-driven Plan/Entitlement; per-location quantity; prices only in Stripe/config (E-01..10) | — | Pre-customer |
| TR-14 | Maintainability | No CI; tests not hermetic (developer `.env` fails 46 tests); no linter/type checker; unpinned dependencies | T1, O5; [RUN] needed `APP_BASE_URL` pin | High | Medium | **High** | Hermetic conftest, CI with Postgres service, lockfile, lint/type config (MNT-03/04) | TR-01 | **Now** |
| TR-15 | Data integrity | Recipes cannot express juice-bar reality: no versions (history rewrites itself), no cross-dimension conversions (lb carrots → mL juice), no prepared items | G5, G7; DB9 | High | High | **High** | RecipeVersion, ItemUnitConversion, prepared items (RCP, INV-FR-05) | TR-02 | Engine |
| TR-16 | Product scope | R1 is large for a solo founder: 48 R1 rows, of which 31 partial and 14 missing | [Traceability §5](INVENTORYOS_REQUIREMENTS_TRACEABILITY.md#5-scorecard) | High | High | **High** | Keep C-29 boundaries; sequence by C-27; acceptance criteria per module; resist adding R1.1 items | Phase 3 plan | Phase 3 |
| TR-17 | Security | Sessions cannot be revoked; no CSRF tokens; no security headers; logout on GET | S4, S5, S10 | Medium | Medium | Medium | Server-side sessions, CSRF tokens, headers (with C-32) | TR-09 | Pre-customer |
| TR-18 | Security | Square OAuth asks every merchant for payment and order-write scopes the product does not use | S6; `app/providers/square/oauth.py:12-22` | High | Medium | Medium | Scope split; remove snack-box scopes with the module | TR-24 | Pre-Square-pilot |
| TR-19 | Multi-tenancy | One Square merchant can attach to several tenants; webhooks go to `.first()` | S7; probe PA4 [RUN] | Low | High | Medium | Unique active connection per merchant | — | Pre-Square-pilot |
| TR-20 | Security / cost | Unverified sign-ups get unlimited AI extraction on the founder's key | S9 | Medium | Medium | Medium | Verified email gate + per-tenant cap (RCV-FR-14) | TR-09 | Pre-customer |
| TR-21 | Performance | Blocking I/O inside `async def` routes stalls the worker (Anthropic timeout 600 s, Square back-off sleeps) | C8 | High | Medium | Medium | Plain `def` routes now; extraction in a job later | — | Engine (cheap) |
| TR-22 | Architecture | Sales stored as daily aggregates: no order, time, modifier or refund identity | DB10; `app/models.py:270` | High | Medium | Medium | SalesTransaction/Line/Return shared by all modes | — | Engine |
| TR-23 | Data integrity | Naive timestamps, no CHECK constraints, no delete rules, batch hard delete | DB1–DB3, C9; probes PA5, R6 [RUN] | Medium | Medium | Medium | `timestamptz` migration, constraints, soft-delete/waste for batches | — | Engine |
| TR-24 | Product scope | Private snack-box module lives inside the product: tenant-specific code (contrary to C-30), shared webhook router, extra scopes, 35 routes | `SNACK_BOX_TENANT_IDS`, `app/entitlements.py:224-237` | High | Medium | Medium | Extract or remove (C-37, approved) | Owner's answer on the snack-box business | Before Square pilot |
| TR-25 | Scalability | Hourly sync re-reads 60 days of orders per tenant, sequentially | SQ2; `app/orchestration/sync.py:51,171` | Medium (beyond ~50 tenants) | Medium | Medium | Incremental cursor + webhooks (replaced by TR-22 work) | TR-22 | Pre-Square-pilot |
| TR-26 | Deployment | Never deployed; no host, DNS, TLS, email provider chosen | baseline 03, 09 | High | Medium | Medium | Deploy a staging environment early; production gate per NFR §8 | TR-01, TR-12 | Pre-customer |
| TR-27 | Observability | Plain-text logs without request ids; no integrity metrics; Sentry unset | O3, O4 | Medium | Medium | Medium | JSON logs, request ids, OBS-05 metrics, uptime check | — | Pre-customer |
| TR-28 | Operational | Invoice extraction accuracy and cost on real supplier invoices unknown | `app/extract/invoice.py`; mocked tests only | Medium | Medium | Medium | Real-invoice trial with the extraction-quality report (`app/orchestration/extraction_quality.py`) | Owner invoices | Pre-customer |
| TR-29 | Integration | Square unit `METRIC_MILLIGRAM` mapped to grams | SQ8 | Low | Medium | Low | Fix in normalize | — | Pre-Square-pilot |
| TR-30 | Product scope | JuJu interoperability pressure (merge repos or databases) before InventoryOS R1 exists | [JuJu comparison](JUJU_INVENTORYOS_CAPABILITY_COMPARISON.md); C-42 | Low | Medium | Low | Keep parity by specification (C-42) until R1; generic sales API in R1.1 | — | Post-launch |
| TR-31 | Product scope | No pilot businesses: segment profiles, workflows and pricing hypotheses are unvalidated (A-14), and C-40 keeps pilots off the old engine anyway | Owner answer 2026-11-04 | High | High | **High** | Start validation conversations ([PRICING_AND_PACKAGING §8](../product/PRICING_AND_PACKAGING.md#8-validation-plan-proposed-nothing-validated-yet)) during the engine build so pilots are ready when C-40 lifts; JuJu remains the live reference | — | Now, in parallel |

## Top five, in one line each

1. ~~**TR-01** push the code somewhere other than this laptop~~ — done 2026-11-05 (private GitHub repository).
2. **TR-02/03** the stock numbers are wrong by design for juice bars; the ledger replaces the engine.
3. **TR-04** half of the launch offering (Standalone) has no path through the app at all.
4. **TR-05** tenant isolation must become mechanical before the route count doubles.
5. **TR-09/10/11** auth hardening, the XSS fix and live integration runs gate the first paying customer.
