# SaaS and pricing implementation plan

Phase 3, 2026-11-05. Planning only. How subscriptions, plans and entitlements are built so the
commercial model in [PRICING_AND_PACKAGING](../product/PRICING_AND_PACKAGING.md) can be sold and
changed without code changes. Current state: [SaaS readiness audit](../audit/SAAS_READINESS_AUDIT.md)
— lifecycle logic reusable; plan catalogue hard-coded (G3, probe PA2).

## 1. Three independent dimensions (C-06)

| Dimension | Values | Where it lives | Changes the data model? |
|---|---|---|---|
| **Operating mode** | Standalone · Connected · Native POS (future) | Location's sales source of record per business date (DR-31); allowed modes in the entitlement set | No |
| **Business scale** | Number of active locations | Entitlement `max_locations`; Stripe quantity for per-location plans | No |
| **Capability level** | Core (always) · Advanced flags | Entitlement capability flags | No (DR-32) |

A customer switches mode or adds locations by configuration and billing changes only — never a
different application or database (brief §3.6).

## 2. Data model (T4.01)

```
plans            code · name · billing_basis (per_location | per_business) · provider_price_ref · active
entitlements     plan_code → allowed_modes[] · max_locations (null = by quantity) · capability_flags[] · usage_limits{}
subscriptions    tenant · plan_code · quantity · status · period · trial_end · grace_until · provider ids
add-ons (later)  multi_location, advanced → extra flags / limits, combinable
```

- Prices exist only in Stripe and in plan configuration (`provider_price_ref`); domain code never
  reads a price (E-01, DR-36). The pricing page reads display prices from configuration (T4.02).
- `allows(tenant, capability)` stays the single server-side check (E-02); it reads the resolved
  entitlement set instead of the `CAPABILITIES` dict (`app/entitlements.py:40-51`).
- **Core capabilities are never gated** (E-05): every R1 module is available in Complete and
  Standalone; they differ only in allowed sales sources.

## 3. Launch catalogue (hypotheses, configurable)

| Plan code | Offering | Billing basis | Allowed modes | Max locations | Price reference |
|---|---|---|---|---|---|
| `complete` | InventoryOS Complete | per location (quantity = active Connected locations) | connected, standalone | by quantity | Stripe price (hypothesis $99/location/month) |
| `standalone` | InventoryOS Standalone | per business | standalone | 1 (PD-32: extra Standalone locations priced like Complete until Multi-location exists) | Stripe price (hypothesis $99/month) |
| — | InventoryOS POS | not sold (SAAS-B-20) | — | — | interest captured at sign-up |
| `multi_location` add-on | Expansion | per location or package (PD-33, decided at Stage 6) | any | many | later |
| `advanced` add-on | Expansion | per business (PD-34) | any | any | later |

## 4. Subscription lifecycle

Reuse the tested lifecycle (`app/orchestration/subscriptions.py`, `app/orchestration/stripe_events.py`,
`app/entitlements.py:104-150`):

```
trialing → active → past_due (grace) → read_only (expired | canceled | unpaid) → pending_deletion → deleted
                ▲                              │
                └────────── resubscribe ◄──────┘
```

| Policy | Recommendation (approval: ID-14) | Task |
|---|---|---|
| Trial | 30 days, no card (PD-05) | T4.03 |
| Grace | 7 days with full access (PD-05) | T4.03 |
| Read-only | View, reports, export, billing; **ingestion continues** so history stays whole (PD-07) | T4.03 |
| Retention after cancellation | 90 days read-only, then deletion after notice (PD-07) | T4.06 |
| Plan change on mode switch | Self-serve at cutover, prorated by Stripe (PD-39) | T4.02 |
| Upgrade/downgrade locations | Quantity change; a downgrade sets surplus locations inactive, never deletes (existing `apply_location_limit` behaviour) | T4.02 |
| Cancellation | At period end; read-only after | existing |
| Refunds of subscription charges | No partial-month refunds (PD-23) | policy page T4.12 |
| Sales tax on subscriptions | Stripe Tax, confirmed with an accountant (PD-22) | T4.02 |

## 5. Billing integration boundary

| InventoryOS owns | Stripe owns |
|---|---|
| Plan catalogue codes and entitlement sets; subscription state as received from verified webhooks; audit of billing events | Payment methods, invoices, receipts, proration, dunning, tax calculation |

Webhooks: signature verified, idempotent by event id, ordering by event time (existing,
`tenants.stripe_event_at`). No card data touches InventoryOS (SEC-20).

## 6. Usage limits (only where justified)

| Limit | Why | Default (proposed) |
|---|---|---|
| Invoice extraction per tenant per day | AI cost abuse (S9, PD-25) | Cap set after the T4.15 trial |
| Export jobs per tenant per hour | Protect the worker | Small number |
| Users per tenant | None at launch (A-03 sizing ≤ 15 is a design assumption, not a limit) | — |

## 7. Onboarding and account administration

Onboarding (T2.20, Connected steps in T3.10) records the POS situation answer, adapter demand and
Scenario C interest as validation evidence ([CUSTOMER_VALIDATION_PLAN](CUSTOMER_VALIDATION_PLAN.md)).
Account administration: plan and entitlements view (T4.08), team (T1.10), audit history (T1.12),
export (T4.05), deletion (T4.06), founder console (T4.07).

## 8. Expansion readiness built into R1

| Expansion | Built in R1 | Added later |
|---|---|---|
| Multi-location | Location on every location-scoped record; location-scoped memberships (model); per-location balances; quantity billing | Transfers, consolidated reports, central purchasing, pricing (Stage 6) |
| Advanced | Capability flags in entitlements; audit events; API key model planned in R1.1 (T4B.03) | Forecasting, automation, public API, custom roles (Stage 7) |
| Native POS | Owned catalog, SalesTransaction origin, mode as an entitlement dimension | Everything checkout-related (Stage 8 only) |

## 9. Configurability tests (acceptance)

- AC-SAAS-08: Complete and Standalone expose the same core capabilities.
- AC-SAAS-12: changing a plan price in configuration changes what new subscriptions pay, with no
  deploy.
- A new plan code with a different entitlement set can be added by data migration/config only.
- Static test: no numeric price literal in domain modules (`app/transform/`, services).
