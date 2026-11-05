# Pricing and Packaging

Authoritative source for **offerings, pricing hypotheses, packaging and entitlement principles**.

> **Status: hypotheses.** Every price here is the owner's pricing hypothesis (recovered from the
> 2026-11-04 pricing discussion and restated in the Phase 1 brief). None is a published price and
> **no customer validation has been done yet**. Prices live in billing configuration, never in
> domain logic (E-01).

## 1. Three pricing dimensions

InventoryOS pricing is not one ladder of tiers. It combines three independent dimensions
[CONFIRMED]:

| Dimension | Values | What it changes |
|---|---|---|
| **Operating mode** — where sales data comes from | Standalone · Connected (external POS) · Native POS (future) | The sales source; never the core engine |
| **Business scale** | Single location · Multi-location | Location count, cross-location features |
| **Capability level** | Complete core operations · Advanced | Capabilities beyond the core |

```
                    Capability level
                    ┌─────────────────────────┐
                    │ Advanced (add-on)        │   ← forecasting, automation, APIs …
                    ├─────────────────────────┤
                    │ Complete core operations │   ← always included
                    └─────────────────────────┘
   Operating mode:  Standalone | Connected | Native POS (future)
   Business scale:  Single location | Multi-location
```

Combinations are allowed where they make sense, e.g. multi-location Standalone, or multi-location
Connected with Advanced [CONFIRMED]. They are **one product and one codebase** configured by
plans and entitlements, not separate systems.

## 2. Offering catalogue

| Offering | Customer scenario | Pricing hypothesis | Sold at launch? |
|---|---|---|---|
| **InventoryOS Complete** | A — already has a POS and keeps it | **$99 / location / month** | Yes |
| **InventoryOS Standalone** | B — no POS and doesn't want one | **$99 / month, one location** | Yes |
| **InventoryOS POS** (native) | C — no POS but wants one | **$149–199 / month starting**; payment processing, hardware, card readers, transaction fees, taxes and third-party services **not included** | **No** — future, validation required |
| Multi-location (expansion) | Several locations, centralized management | **~$149–199+ / month**, depending on location count and features; per-location pricing or packages, configurable | No — expansion roadmap |
| Advanced (expansion) | Sophisticated requirements | **~$199–299+ / month**, subject to validation and scope | No — expansion roadmap |

## 3. Packaging matrix

| | **Complete** | **Standalone** | **InventoryOS POS (future)** | **Multi-location** | **Advanced** |
|---|---|---|---|---|---|
| **Customer type** | Independent operator with Square (or another supported POS) | Operator with no POS by choice: cash/card terminal, markets, catering, production kitchens, very small shops | Operator who wants checkout and inventory in one system | Operator with 2+ stores | Growing operator needing forecasting, automation, APIs |
| **POS requirement** | Supported external POS (Square first) | None | None — InventoryOS provides it | Any mode | Any mode |
| **Operating mode** | Connected | Standalone | Native POS | Combines with any mode | Combines with any mode |
| **Included capabilities** | Complete core (§4) + POS connection, product/variant/modifier mapping, automatic order-line consumption, refunds/voids handling, POS reconciliation, integration health | Complete core (§4) + daily sales entry, sales file import, manual usage records, count-based usage, missing-day detection | Checkout, menu, orders, transactions, sales reporting, payment-processor integration, automatic consumption, unified reporting | Multiple stores, location-specific stock, transfers, centralized and comparative reporting, location-scoped roles, consolidated purchasing, cross-location visibility, central admin | Advanced analytics, demand/inventory forecasting, automated replenishment, advanced procurement, supplier performance, advanced reporting, API access, expanded integrations, custom automation, granular/custom permissions, extended audit history, configurable business rules |
| **Limitations** | Only supported POS providers; consumption accuracy depends on mappings and recipes | Sales data is as good as what is entered or imported; **automatic sales-to-inventory reconciliation is limited**; daily (not per-order) granularity; manual and imported records are labelled as such | Not built; requires validation of demand, scope, regulation, costs | Not built beyond a location-aware data model | Not built; contents unvalidated |
| **Pricing hypothesis** | $99 / location / month | $99 / month (one location) | $149–199 / month starting + pass-through costs | ~$149–199+ / month | ~$199–299+ / month |
| **Upgrade path** | → Multi-location (add locations); → Advanced; → Standalone if the POS is dropped | → Complete when a supported POS is adopted (same data, cutover date); → Multi-location; → Advanced; → InventoryOS POS if launched | → Multi-location; → Advanced | Combines with Advanced | — |
| **Cost drivers** | Hosting/DB per tenant, POS API sync volume, webhook processing, invoice extraction, email, support for mapping | Hosting/DB, invoice extraction, email, support for setting up sales entry/imports | Payment processor fees and compliance, hardware logistics, offline operation, tax and receipts, device support, much higher support load | Per-location data volume, cross-location reporting, onboarding per store | Compute for forecasting, API traffic and abuse protection, integration maintenance |
| **Questions requiring validation** | Will owners pay $99/location? Is per-location acceptable for 2–3 store operators? Which POS after Square? | Is there enough demand from no-POS businesses? Is $99 right when sales data is manual? How much do they want daily entry vs imports? | Do Scenario C prospects exist in numbers? Will they pay $149–199 plus processing? Build, white-label or partner? Regulatory and support burden? | Price per location vs package? Which features justify the premium over N × Complete? | Which advanced features do customers ask for first? Price sensitivity? |

## 4. Capability inclusion

**Complete core operations** are included in both Complete and Standalone [CONFIRMED: Standalone
is "not a stripped-down trial version"]:

| Core capability | Complete | Standalone |
|---|---|---|
| Dashboard and alerts | ✓ | ✓ |
| Inventory items, units, conversions, storage areas, min/max levels | ✓ | ✓ |
| Inventory ledger, adjustments, counts, valuation | ✓ | ✓ |
| Recipes, variants, modifiers, costing, version history | ✓ | ✓ |
| Production orders and batches, expiry, FEFO | ✓ | ✓ |
| Suppliers, purchase orders, receiving, invoices | ✓ | ✓ |
| Waste and loss | ✓ | ✓ |
| Reconciliation and exceptions | ✓ | ✓ |
| Reorder suggestions | ✓ | ✓ |
| Core reports (valuation, consumption, waste, expiry loss, recipe cost/margin, variance, purchasing) | ✓ | ✓ |
| Staff roles and permissions | ✓ | ✓ |
| Data export | ✓ | ✓ |
| Sales source | External POS, automatic | Daily entry, file import, manual usage |
| Theoretical vs actual usage | ✓ per order line | Partial — days with sales entered |
| POS-only features (sync health, POS refunds/voids, POS reconciliation) | ✓ | — not applicable |

**Never gated** (must stay in the base offerings even when Advanced exists): ledger and history,
basic audit trail, recipes, batches/expiry, receiving, waste, counts, reorder suggestions, core
reports, export, standard roles.

## 5. Entitlement principles

| ID | Principle |
|---|---|
| E-01 | Prices, currencies, billing intervals and per-location rates live in the billing provider and plan configuration — never in domain logic or code constants |
| E-02 | A subscription resolves to an **entitlement set**: `{allowed_modes, max_locations, capability_flags[], limits{}}`. Code asks "is this organization entitled to X?" through one server-side check |
| E-03 | Plans, add-ons and packages are configuration; adding or repricing a plan requires no code change |
| E-04 | Entitlements never change the data model or business rules; they only allow or refuse operations (DR-32) |
| E-05 | Core capabilities (§4) are never behind an entitlement other than "subscription active" |
| E-06 | Per-location pricing must support both **quantity-based** (N × rate) and **package** (base price including N locations) models, chosen by configuration |
| E-07 | Downgrade or lapse never deletes data: capabilities beyond the new entitlement become read-only; data stays exportable |
| E-08 | Existing customers can be kept on an earlier price (grandfathering) without code changes |
| E-09 | Usage limits with real cost (e.g. invoice extraction per day) are entitlement limits, visible to the customer |
| E-10 | A mode change (Standalone ↔ Connected) is a plan change at a cutover date with no data migration |

## 6. Upgrade and downgrade paths

```
 Standalone ──(adopts supported POS, cutover date)──► Complete
     ▲                                                   │
     └───────────(drops POS, cutover date)───────────────┘
     │                                                   │
     ├──► + locations ──► Multi-location ◄── + locations ┤
     └──► + Advanced add-on ◄────────────────────────────┘
 Standalone ──(if InventoryOS POS launches)──► InventoryOS POS
```

## 7. Cost model notes [ASSUMED, to verify]

| Cost | Driver | Notes |
|---|---|---|
| Hosting, database, storage | Tenants, data volume, retention | Target ≤ ~$5 / tenant / month at launch scale (vision guardrail) |
| POS integration | Sync frequency, order volume, provider rate limits | Incremental sync, not full re-reads |
| Assisted invoice extraction | Invoices per tenant per day | Capped per tenant (E-09) |
| Email | Alerts, digests, auth | Low |
| Onboarding and support | Mapping help, recipe setup | Largest early cost; drives the open setup-fee decision (PD-06) |
| Native POS (future) | Processor fees, hardware, compliance, offline, device support | Pass-through to customer; large fixed build and support cost |

## 8. Validation plan (proposed; nothing validated yet)

| Question | Method | Evidence that would confirm |
|---|---|---|
| $99 Complete willingness to pay | Pilot customers with a paid conversion after trial | ≥ 60 % of qualified pilots convert at $99 |
| Standalone demand | Track Scenario B share among inbound leads; pilots | Meaningful share of signups; retention similar to Complete |
| Per-location acceptance | Interviews with 2–3 location operators | They accept N × $99 or name a package price |
| Native POS demand | Record Scenario C interest at signup; pre-commitment offers | ≥ 10 qualified prospects willing to pre-commit at $149+ (PD-35) |
| Advanced contents | Feature requests from paying customers | ≥ 3 paying customers asking for the same capability (PD-34) |

## 9. Contradictions and history

| Item | Resolution status |
|---|---|
| The first Phase 1 brief specified one $49/month plan | **Superseded** by this catalogue (C-05) |
| Commercial target "21 customers = $1,029 MRR" was set at $49 | Resolved: 21 paying customers is the target; $1,000 MRR gates new revenue products (C-26) |
| Existing inventory app codes Starter $29 / Pro $59 tiers | Implementation gap for Phase 2; not a product option |
| Complete is per location ($198 for two) while Multi-location is ~$149–199+ | Overlap — PD-33 |
| Advanced lists "more granular permissions" and "advanced audit history" | Standard roles and the core audit trail stay in the base offerings; Advanced adds custom roles and extended history/exports only (PD-34) |
| Owner's rule: no new revenue product until the first one earns $1,000 | Native POS stays behind validation and that gate (PD-35) |
