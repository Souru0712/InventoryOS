# ADR-0073: Employee home and the "logged by" picker

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.21: WF-23, UX-02, UX-03, PD-14 → C-44, WST-FR-03, AC-WST-01)
- **Tasks:** T2.21; R1.1 PIN switch (PD-14) replaces the picker on shared devices

## Context

Staff work on a phone or a shared tablet; the admin screens are too slow at the line, and on a
shared tablet every record named the tablet's account. PD-14 (decided): R1 "logged by" picker,
R1.1 PIN switch. The task allows no schema change.

## Decision

1. **`/app/floor` ("Today")**: today's stock and expiry alerts (through the exception views, so no
   amounts for a role without cost access), then the floor tasks the role allows (receive, make
   a batch, log waste, expiring, count, today's sales and usage when permitted), each one tap
   from the screen and two at most to its form. Large targets (88 px tiles, 48 px controls), one
   column under 480 px. An Employee lands there after signing in; everyone else keeps the
   dashboard. "Today" leads the menu.
2. **The picker names the person on every record without a new column.** On an Employee account
   (the shared floor device) the person picked is kept in the signed session cookie, per business;
   `require_org()` puts them in the request's `OrgContext.user_id`, so the records the device makes
   (waste, counts, batches, receivings, the ledger's actor) name them. The device account keeps its
   own permissions and session; only an active member with an active account can be picked, and a
   removed member stops being named. Other roles cannot pick: a Manager's actions are always their
   own.
3. **AC-WST-01** is the existing waste form (quantity, reason, save after choosing an item),
   checked at 375 px.

## Alternatives considered

- **A `logged_by` column on every record:** a schema change across many tables the task rules
  out; the PIN switch in R1.1 can revisit it.
- **Redirect every Employee from the dashboard:** the dashboard stays reachable; landing and the
  menu make the floor screen the default.

## Consequences

- The picked person, not the tablet account, is what history shows on a shared device; the request
  log keeps the session's own user. Playwright tests at 375 px (ID-08) await the browser test
  dependency; the layout was checked in the browser.
