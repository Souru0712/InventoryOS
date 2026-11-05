# User Roles and Permissions

Authoritative source for **roles, permissions, access boundaries and location-specific access**.
Every operation is authorized on the server; the UI hides what a role cannot do, but hiding is
never the control [CONFIRMED].

## 1. Model

```
User ──< Membership >── Organization
            │  role (one per membership)
            └─ location scope: ALL locations | [list of locations]
Role = named set of permission keys (presets in code; custom roles = Advanced)
Check: can(user, permission, organization, location?) → membership active
       ∧ permission ∈ role ∧ (permission is org-level ∨ location ∈ scope) ∧ entitlement allows
```

- A user can belong to several organizations, with one role each.
- **Org-level permissions** (billing, team, integrations, settings, plan) ignore location scope and
  are granted only to Owner and Administrator.
- **Location-level permissions** (inventory operations, reports) apply only inside the member's
  location scope. With one location, scope is that location.
- Least privilege: new members default to Employee scoped to the inviter's location(s).
- ≥ 1 active Owner always (DR-27).
- Role or scope changes take effect on the user's next request; sessions are re-checked.

## 2. Roles

| Role | Purpose | Typical person | Scope | Release |
|---|---|---|---|---|
| **Owner** | Full control including subscription, ownership and deletion | Business owner(s) | Org-wide | R1 |
| **Administrator** | Everything except subscription, ownership transfer and deletion | Operations lead, bookkeeper | Org-wide | R1 |
| **Manager** | Runs a location's operations; sees costs; approves | Store manager, shift lead | Location(s) | R1 |
| **Inventory manager** | Items, counts, reconciliation, adjustments; sees costs | Inventory lead | Location(s) | R1.1 preset |
| **Purchasing staff** | Suppliers, POs, receiving, reorder; sees purchase costs | Buyer | Location(s) | R1.1 preset |
| **Production staff** | Production orders and batches, production recipes (view/edit per PD-41), waste | Prep cook, batch cook | Location(s) | R1.1 preset |
| **Employee** | Records operational events: receive, waste, count, production; no costs | Line staff | Location(s) | R1 |
| **Read-only** | Views inventory and reports, changes nothing | Accountant, investor, partner | Org-wide or location(s) | R1 |

Decision basis [RECOMMENDED, PD-11]: Owner, Administrator, Manager, Employee and Read-only cover all
R1 workflows. The three specialist presets are combinations of the same permission keys and add
little build cost, so they follow in R1.1 as **core** (not paywalled). **Custom roles** (editing
permission sets) and per-permission overrides are the "more granular permissions" listed under
Advanced (PRICING_AND_PACKAGING §9).

## 3. Permission matrix

O Owner · A Administrator · M Manager · IM Inventory manager · PS Purchasing staff · PrS Production
staff · E Employee · RO Read-only. ✓ allowed · — denied · ⏱ own records within the 30-minute undo
window · ≥T posts but above-threshold creates a review exception (R1) or needs approval (R1.1
option) · ¢ without cost fields.

| Permission key | Operation | O | A | M | IM | PS | PrS | E | RO |
|---|---|---|---|---|---|---|---|---|---|
| `inventory.view` | Items, quantities, batches, history | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `inventory.view_costs` | Unit costs, values, cost snapshots | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | ✓ |
| `item.edit` | Items, units, conversions, levels, categories, storage areas | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `product.edit` | Sellable products, variations, modifiers | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `recipe.view` | Recipe cards and instructions | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ ¢ | ✓ ¢ | ✓ |
| `recipe.edit` | Product recipes, modifier effects | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `production_recipe.edit` | Production recipes (yield, shelf life) | ✓ | ✓ | ✓ | ✓ | — | PD-41 | — | — |
| `production.record` | Record/complete a production order | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | — |
| `production.plan` (R2) | Plan production orders | ✓ | ✓ | ✓ | ✓ | — | ✓ | — | — |
| `production.void` | Void a production order | ✓ | ✓ | ✓ | ✓ | — | ⏱ | ⏱ | — |
| `receiving.record` | Start receiving, upload invoice, enter quantities | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | — |
| `receiving.post` | Post a receiving | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ (PD-19) | — |
| `receiving.edit_costs` | Enter/override costs, resolve price variances | ✓ | ✓ | ✓ | — | ✓ | — | — (flagged) | — |
| `receiving.correct` | Correct a posted receiving | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `supplier.manage` | Suppliers and supplier items | ✓ | ✓ | ✓ | — | ✓ | — | — | — |
| `po.manage` (R1.1) | Create, submit, close, cancel POs | ✓ | ✓ | ✓ | — | ✓ | — | — | — |
| `reorder.view` | Reorder suggestions | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ ¢ | ✓ |
| `waste.record` | Log waste | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | — |
| `waste.void` | Void waste | ✓ | ✓ | ✓ | ✓ | — | ⏱ | ⏱ | — |
| `count.record` | Run and post a count | ✓ | ✓ | ✓ | ✓ | — | — | ✓ ≥T | — |
| `count.review` | Review/approve variances, set reasons | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `adjustment.create` | Manual adjustment | ✓ | ✓ | ✓ ≥T | ✓ ≥T | — | — | — | — |
| `adjustment.override_negative` | Allow a manual movement to cross zero | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `sales.record` | Enter/correct daily sales and usage (Standalone) | ✓ | ✓ | ✓ | — | — | — | PD-37 | — |
| `sales.import` | Import sales files, manage column mappings | ✓ | ✓ | ✓ | — | — | — | — | — |
| `transfer.manage` (Exp.) | Create/send/receive transfers | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `exception.view` | See exceptions | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ ¢ | ✓ ¢ (operational only) | ✓ |
| `exception.resolve` | Acknowledge, resolve, dismiss | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `reports.operational` | Consumption, counts, production, waste quantities | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| `reports.financial` | Valuation, waste cost, margins, variance cost, purchasing | ✓ | ✓ | ✓ | ✓ | ✓ (purchasing) | — | — | ✓ |
| `data.export` | CSV exports | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `mapping.manage` | POS/import product mapping, reprocess consumption | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `pos.manage` (org) | Connect/disconnect POS, location mapping, operating mode, cutover | ✓ | ✓ | — | — | — | — | — | — |
| `alerts.configure` | Thresholds, expiry window, recipients | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| `location.manage` (org) | Create/archive locations, time zone, open days | ✓ | ✓ | — | — | — | — | — | — |
| `settings.manage` (org) | Business profile, reasons, units | ✓ | ✓ | — | — | — | — | — | — |
| `team.manage` (org) | Invite, change role/scope (not above own), revoke | ✓ | ✓ | — | — | — | — | — | — |
| `owner.manage` (org) | Add/remove Owners, transfer ownership | ✓ | — | — | — | — | — | — | — |
| `subscription.manage` (org) | Plan, location quantity, payment method, cancel | ✓ | — | — | — | — | — | — | — |
| `audit.view` | Business audit history | ✓ | ✓ | ✓ (own locations) | — | — | — | — | — |
| `account.delete` (org) | Request organization deletion | ✓ | — | — | — | — | — | — | — |

**Read-only (lapsed subscription)**: all write permissions suspended for every role; viewing,
reports, export and `subscription.manage` remain ([SAAS_REQUIREMENTS §3](SAAS_REQUIREMENTS.md#3-account-lifecycle)).

## 4. Approval requirements

| Action | R1 behaviour | R1.1 option | Default threshold (configurable) |
|---|---|---|---|
| Count variance | Posts; lines ≥T raise `count_variance_high` for review | Hold ≥T lines for approval before posting | \|value\| ≥ $25 or \|qty\| ≥ 20 % of expected |
| Manual adjustment (Manager, IM) | Posts; ≥T raises Owner/Admin review | Approval before posting | \|value\| ≥ $50 |
| Void after 30-minute window | Manager+ / IM, reason required | — | — |
| Employee-entered receiving cost | Accepted; flagged if price variance ≥T | Cost confirmation before post | ≥ 15 % vs last price |
| Negative-crossing manual movement | Override permission + reason | — | any |
| Reprocessing consumption | Manager+ with preview | — | — |
| Operating mode change / cutover | Owner/Admin with preview | — | — |

Approvals record approver, time and note on the source record and in AuditEvent.

## 5. Access boundaries

- Roles without `inventory.view_costs` never receive cost, value or margin fields from the server —
  enforced in the data access layer, not only the UI (JuJu precedent: column-level grants).
- Exceptions and alerts are filtered by audience on the server (Employees see operational ones
  without amounts).
- Supplier contact details: roles with `supplier.manage` or `reports.financial`.
- Employee attribution (who logged what) visible to Manager+ and IM; Employees see their own history.
- Location scope applies to every list, report, export and file download.
- Founder/support access to a tenant is a separate, logged, time-boxed path (SAAS-T-10), never a role.

## 6. Shared devices

Counter tablets are often signed in as one account. R1: attribution is the signed-in user plus an
optional "logged by" picker on waste, count and production forms (PD-14). R1.1: per-person PIN
switch on a device session (STF-FR-09).

## 7. Authentication strength by role

| Role | R1 requirement | Recommendation |
|---|---|---|
| Owner, Administrator | Verified email + password; MFA available | Strong prompt; consider required (PD-20) |
| Manager, Inventory manager, Purchasing staff | Same | Prompt |
| Production staff, Employee, Read-only | Verified email + password | Optional |
