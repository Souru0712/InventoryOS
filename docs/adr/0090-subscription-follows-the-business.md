# ADR-0090: The subscription follows the business: per-location quantity, plan at cutover

- **Status:** Proposed
- **Decision ID:** none (implements SAAS-B-01, -02, -18, -19, C-44 (PD-39), E-01, E-06, E-10)
- **Tasks:** T4.02

## Context

T4.01 (ADR-0089) made plans data: Complete (billed per location, either sales mode, no location
limit) and Standalone (billed per business, hand entry or imports, one location). Checkout still
sold one unit of a plan, nothing kept Stripe in line when a business added a location or changed
its sales mode, plans' Stripe prices could not be set, and the pricing page hard-coded Starter,
Pro and a setup package. The owner decided that a mode change is a self-serve plan change at the
cutover, prorated (C-44); Complete is billed by active Connected locations (SAAS-B-18).

## Decision

- **Fit and quantity** (`app/transform/billing.py`, pure). A plan fits when its entitlements
  cover the sales modes of the business's active locations and their number. A plan billed per
  location is billed for the active locations taking sales from a POS, at least one; a plan billed
  per business is one unit.
- **Checkout** refuses a plan that does not fit, with the reason, and sends the plan's provider
  price with that quantity.
- **The plan follows the mode** (`app/billing.py`). Confirming a cutover queues the
  `sync_subscription` job for the cutover date's start at the location; it moves the business to
  the narrowest plan that fits (fewest modes, then fewest locations). Adding, archiving or
  restoring a location queues the same job for the quantity; it changes the plan only when the
  current one no longer fits. The cutover preview names the plan the business moves to.
- **Applying it.** Before a business subscribes, the job changes its plan at once (audited
  `billing.plan_follows`). With a Stripe subscription it asks Stripe to change the item's price and
  quantity with `create_prorations` (audited) and Stripe's webhook applies the result; the webhook
  now records the subscription item and its quantity (`tenants.stripe_subscription_item_id`,
  `billed_quantity`). A repeat that finds nothing to change asks nothing of Stripe.
- **Prices** are set with `python -m app.cli plans --code <plan> --price <stripe price id>`: the
  price must be active and recurring; its amount, currency and interval are copied onto the plan
  for display only (E-01). New checkouts take the new price; existing subscriptions keep theirs
  (E-08, AC-SAAS-12).
- **The pricing and landing pages** list the active plans from the catalogue with their display
  price ("Price announced at launch" until one is set), and copy chosen by what each plan's
  entitlements allow. The done-for-you setup is no longer offered: SAAS-B-12 sells it only once
  PD-06 is approved. The FAQ no longer says counts are written back to Square (C-34).

## Alternatives considered

- **Stripe lookup keys or price metadata instead of price ids.** They survive repricing without a
  configuration change, but add a Stripe call to every checkout. A price id in the plan row is
  enough: an old price that no longer maps keeps the business on its current plan.
- **Reading prices from Stripe on every pricing-page view.** Always current, but puts Stripe's
  latency and keys behind a public page. The display copy is refreshed whenever the price is set.
- **Downgrading as soon as a plan stops being needed.** A business on Complete that has not
  connected yet would drop to Standalone every night. Only a cutover moves a business to a
  narrower plan.
- **Counting every active location for Complete.** SAAS-B-18 counts Connected locations; a
  Standalone location inside a Complete business is not billed (PD-57).

## Consequences

- Built on branch `stage4` with tests (`tests/test_plan_follows.py`).
- Additive migration `c8e3f1a5d2b7`: the subscription item and quantity on `tenants`, the display
  price on `plans`, `sync_subscription` among the job kinds.
- The manual half of AC-SAAS-11 (a real Stripe test-mode switch with proration) is T4.04's run.
- Marketing copy on the pricing and landing pages is new and is the owner's to review.
