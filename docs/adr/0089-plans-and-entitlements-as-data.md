# ADR-0089: Plans and entitlements are data; the core is never gated

- **Status:** Proposed
- **Decision ID:** none (implements C-41, E-01..10, SAAS-B-09, -10, -16, -17; DM-15)
- **Tasks:** T4.01

## Context

The product sold two hard-coded plans, Starter and Pro (`PLAN_IDS`, `CAPABILITIES` and the
`Plan` enum in `app/entitlements.py` and `app/models.py`). Starter withheld the reorder CSV,
custom reorder points and the 30-day health history, all of them core (probe PA2, audit G3). The
owner retired both plans in favour of a data-driven catalogue, Complete and Standalone first, with
prices only in billing configuration and the core never gated (C-41). PRICING_AND_PACKAGING §5
sets the principles: a subscription resolves to an entitlement set of allowed modes, maximum
locations, capability flags and usage limits, read through one server-side check (E-02); adding or
repricing a plan is configuration (E-03); usage limits with a real cost are entitlement limits
(E-09).

## Decision

- **Catalogue.** `plans` (code, name, billing basis `per_location` or `per_business`, the
  provider's price reference, active) and `entitlements` (one per plan: `allowed_modes`,
  `max_locations`, `capability_flags`, `limits`). A plan is retired, never deleted, so a business
  can stay on it (E-08). No row holds a price (E-01, DR-36). Read through `app/plans.py`.
- **Seed.** Migration `b7d2e9f4c1a6` seeds Vontry Complete (per location, connected and
  standalone, no location limit) and Vontry Standalone (per business, standalone, one location),
  both with `invoice_extractions_per_day` 40, and points every existing business at Complete
  (`tenants.plan_id`): there are no paying customers (C-40), and Complete is the whole product, as
  Pro and the trial were. A schema built from the models (tests, `APP_ENV=test`) gets the same
  rows from an `after_create` hook on `entitlements`.
- **One check.** `allows()` answers subscription questions (`inventory_write`, `add_location`,
  `notifications`) from access alone, whatever the plan (E-05), and anything else from the plan's
  capability flags; there are none at launch. `location_allowance()` reads `max_locations`;
  `usage_limit()` reads `limits`. The reorder CSV, custom reorder points and the health history
  are no longer gated.
- **The invoice-reading cap** moves from the `extraction_daily_cap` setting to the plan's
  `invoice_extractions_per_day` limit (E-09).
- **Trials** are of the plan the business chose at sign-up, Complete when it chose none (PD-56).
  An expired trial keeps its plan and is read-only (E-07); it no longer falls back to a floor plan.
- **Billing.** Checkout takes a sellable plan code and sends that plan's provider price; Stripe
  events map a price back to its plan through `plan_for_price()`. The `STRIPE_PRICE_STARTER`
  and `STRIPE_PRICE_PRO` settings are gone; until T4.02 sets the catalogue's provider prices,
  checkout answers 503 as it did with no price configured.
- **Legacy.** `tenants.plan` (Starter/Pro) stays, unread, for a later contract step; nothing
  writes it. The nightly and weekly loops (`subscribed_tenants()`) no longer filter on it: a
  lapsed business is evaluated and stays read-only, and `allows()` decides what it receives.

## Alternatives considered

- **Entitlement columns on `plans`.** Simpler, but the requirement and the domain model keep the
  entitlement set apart from the plan, and add-ons (SAAS-B-21) will contribute entitlements of
  their own.
- **Enforcing `allowed_modes` now.** A Standalone business would be refused a POS connection with
  no way to change plan before T4.02's plan change at cutover (SAAS-B-19, E-10). The modes are
  stored and exposed; T4.02 enforces them with the cutover.
- **Backfilling by POS connection** (connected businesses to Complete, the rest to Standalone).
  It would cap some development businesses at one location with no customer reason; Complete for
  all keeps today's behaviour, and the business's plan is chosen for real at checkout.
- **Keeping the invoice cap as a setting.** E-09 makes a costed usage limit part of the
  entitlement, visible to the customer (T4.08).

## Consequences

- Built on branch `stage4` with tests: `tests/test_entitlements.py` (the matrix, AC-SAAS-08,
  AC-SAAS-12's price check, PA2 inverted), `tests/test_billing.py`,
  `tests/test_migrations.py::test_dm15_...`. Probe PA2 is retired.
- Additive migration `b7d2e9f4c1a6` (DM-15, expand and backfill).
- The public pricing and landing pages still quote the pricing hypotheses and Starter/Pro wording
  until T4.02 reads them from the catalogue; the price check exempts them until then.
- A new capability beyond the core is a flag in a plan's `capability_flags`, asked through
  `allows()`; a new costed limit is a key in `limits`, read through `usage_limit()`.
