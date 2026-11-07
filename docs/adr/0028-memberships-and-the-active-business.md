# ADR-0028: Memberships and the business a request acts for

- **Status:** Accepted, 2026-11-06 (the owner merged the T1.05 pull request, #23: "Yes, merge it")
- **Decision ID:** none (implements T1.05, migration step DM-4; SAAS-T-03, SAAS-A-09, STF-FR-10, AC-STF-07)
- **Tasks:** T1.05; shapes T1.06 (permissions), T1.07 (sessions), T1.10 (invitations), T4.16 (contract)

## Context

A user row carried one `tenant_id`, so a person could belong to one business only (probe PA3), and
the request's scope came from that column. Staff, bookkeepers and owners of several businesses
need one login with access to several, each with its own role and locations.

## Decision

1. **`memberships`** (tenant, user, role, status, `all_locations`) with **`membership_locations`**
   for a location-limited member. Unique per (tenant, user). Roles are the R1 presets (owner,
   administrator, manager, employee, read_only) and status is active or revoked, both checked by the
   database. Their permissions arrive in T1.06.
2. **The business a request acts for comes from an active membership only.** The session keeps
   the chosen membership (`mid`); without a valid choice the person's oldest active membership is
   used; with none, the answer is 403. `OrgContext.for_membership` carries the business, the user
   and the location scope. Signing in clears the previous choice.
3. **Users are global identities.** `select_scoped` refuses `User`; a business reaches its people
   through `members(db, org)` (active members with active accounts), which alerts and the weekly
   report now use. The membership list for the switcher is a named lookup
   (`app/access/system.py: memberships_of`).
4. **Switching** posts a membership id the person holds (`/app/organizations/switch`); any other
   id answers 404. The header offers it when a person has more than one business.
5. **Backfill, then contract later**: every existing user became the active Owner of the business
   it signed up with (DM-4). `users.tenant_id` stays until the contract step (DM-16, T4.16) and no
   access decision reads it.

## Alternatives considered

- Choose the business by subdomain or URL prefix: one more routing scheme to secure, and nothing
  in R1 needs bookmarkable cross-business links.
- Keep users per business (one row per business): the same email would mean different people,
  passwords would multiply, and SAAS-T-03 asks for global identities.

## Consequences

- A revoked membership loses access on the person's next request.
- A location-limited membership carries its locations into `OrgContext`. At first, pages that
  read rows whose location is known only through their item refused it; T1.06 gave those rows a
  location path (ADR-0029), so every page works for such a member. No screen creates one before
  invitations (T1.10).
- Built in T1.05 with migration `545dd2eaa736`.
