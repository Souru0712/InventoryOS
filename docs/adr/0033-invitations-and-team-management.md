# ADR-0033: Invitations and team management

- **Status:** Proposed, 2026-11-11 (in the T1.10 pull request; accepted when the owner merges it, which also approves the invitation limit below)
- **Decision ID:** none (implements T1.10; STF-FR-01, STF-FR-06, STF-FR-07, SAAS-A-08, DR-27, AC-STF-01, AC-STF-04, AC-STF-05)
- **Tasks:** T1.10; T1.12 (audit events will record these changes)

## Context

A business has staff, and none could be added: every account was the owner of the business it
signed up with. Memberships (ADR-0028), permission keys (ADR-0029), revocable sessions
(ADR-0030), confirmed addresses (ADR-0031) and throttles (ADR-0032) are in place; T1.10 connects
them so Owners and Administrators can invite, re-role and remove people.

## Decision

1. **An invitation is a row** (`invitations`, with `invitation_locations` for a location-limited
   scope): email, role, scope, inviter, the SHA-256 of a random token, created, expires (7 days),
   accepted, revoked. Inviting the same address again withdraws the earlier pending invitation,
   so only the latest link works.
2. **The link opens a page; its button posts the token** (as in ADR-0031). It works only for the
   invited address:
   - signed in as that address: one click joins;
   - signed in as someone else: refused (403), with a prompt to log out;
   - signed out, the address has an account: its password joins and signs in (throttled like
     the sign-in form);
   - signed out, no account: choosing a password creates the account, already confirmed (the
     email reached them).
   Joining switches the browser to the new business. A person removed earlier and invited back
   gets the invitation's role; an active member keeps theirs (an old link never changes a role).
3. **Who may do what** (`app/transform/team.py`, pure): `team.manage` (Owner, Administrator)
   invites, changes role and scope, removes members and withdraws invitations; touching an Owner
   (inviting one, changing one, removing one) also needs `owner.manage` (Owner only). Owners and
   Administrators always cover every location. Nobody changes their own access from the team
   page.
4. **A business always keeps one active Owner** (DR-27): demoting or removing the last one is
   refused whoever asks.
5. **Changes apply on the member's next request**: the role is read from the membership on every
   request (ADR-0029). Removing a member marks the membership revoked and ends the sessions acting
   for that business at once (`revoke_membership`, ADR-0030; AC-STF-04).
6. **Inviting waits for a confirmed address** (`require_verified`) and is throttled (security
   configuration; merging approves it): **20 invitations an hour per person**, then an hour's
   wait. Links that do not work count against the address, as in ADR-0032.
7. Inviting and changing a role follow the subscription's write rule (`require_writable`);
   removing a member and withdrawing an invitation never do: taking access away must always work.

## Alternatives considered

- Reuse `auth_tokens` for invitations: those belong to an existing user, and an invitation
  usually names someone without an account; it also carries a role and a scope.
- Accept only when signed in, with a "next" redirect through the sign-in and sign-up pages: one
  more redirect parameter to keep safe, and three pages to follow instead of one.
- Let people change their own role: an Owner could demote themselves by mistake and lose the
  team page; another Owner or Administrator does it instead.

## Consequences

- The team page is at `/app/team`, linked from Settings for roles with `team.manage`.
- Memberships are listed with `team(db, org)`; an invitation link is looked up across businesses
  only through `invitation_by_token_hash` in `app/access/system.py`.
- Audit events for these changes come with T1.12; leaving a business on one's own, ownership
  transfer and per-person PINs are separate features.
- Built in T1.10 with migration `a3c7d133e37d`.
