# ADR-0029: Permission keys, role presets, and what a role sees

- **Status:** Accepted, 2026-11-07 (the owner merged the T1.06 pull request, #24: "Yes, merge it")
- **Decision ID:** none (implements T1.06; STF-FR-02, STF-FR-03, CMD-FR-14, DR-35, AC-STF-02, AC-STF-08, AC-CMD-03)
- **Tasks:** T1.06; every later task that adds a route or a cost figure

## Context

Memberships (T1.05) carry a role, but nothing checked it: an Employee could change settings and
see every cost. USER_ROLES_AND_PERMISSIONS §3 is the matrix of keys per role, §5 says costs never
leave the server for a role without them, and DR-35 says location scope applies to every list,
report and export. T1.02 left rows whose location is known only through a parent refused for a
location-limited member.

## Decision

1. **The catalogue is code** (`app/access/permissions.py`): every R1 key with its operation, and
   the five R1 presets as sets of keys. Owner holds every key. A grant the matrix gives "within
   the undo window" (Employee voids) is left out until the operation that enforces the window
   exists. An unknown key is an error when the module loads, not a quiet denial.
2. **Every product route declares `require_permission(key)`**, which admits a member whose role
   holds the key (403 otherwise) and returns the request's `OrgContext`. The public site, signing
   in and out, signed webhooks, and three personal routes (own settings, email preferences,
   switching business) are listed as such; a test fails for any other route without a key.
3. **`OrgContext` carries the role's permissions** (`org.can(key)`); a system context holds all of
   them, a bare one none.
4. **Costs follow `inventory.view_costs`.** Pages hide money for a role without it (dashboard,
   expiring, receiving), JSON payloads drop cost fields (`without_costs`), and a role without
   `receiving.edit_costs` cannot change what an invoice says a line cost. Menus and buttons show
   only what the role may use; hiding is never the control, the route is.
5. **Location scope reaches every table**: rows whose location is their item's or document's
   are filtered through the parent; a test proves every tenant table resolves under a limit.

Route keys follow the matrix's operation names. Where the matrix has no row: dating stock on the
shelf is `count.record`; markdowns are `product.edit` to act and `reports.financial` to view; the
health page is `reports.operational`; Square sync and recalculation are `mapping.manage`; the
weekly report is `reports.financial`; the reorder CSV is `data.export`.

## Alternatives considered

- Check roles by name in each route (`if role in (...)`): the matrix would live in fifty places.
- Strip costs only in templates: JSON consumers (the scanner) would still receive them.
- A permission table in the database: custom roles are Advanced (PD-11); presets in code are
  testable and reviewable now.

## Consequences

- A new route needs a key, and a new money figure needs a `can(request, "inventory.view_costs")`
  guard; the matrix test (every role against every declared route) and the Employee cost test
  catch omissions on the routes and pages they cover.
- Keys without a route yet (production, waste, sales, exceptions, team) are tested when their
  routes arrive; the declaration test makes that automatic.
- Built in T1.06.
