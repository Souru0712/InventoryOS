# ADR-0035: The business audit history

- **Status:** Accepted, 2026-11-12 (the owner merged the T1.12 pull request, #30: "Yes, merge it")
- **Decision ID:** none (implements T1.12; DR-23, SEC-14, ADM-FR-11, AC-ADM-04)
- **Tasks:** T1.12; T4.07 (support access records itself here), and every later task that adds a
  setting, mapping or recipe editor

## Context

Owners could not see who changed the business's settings, team, connection or plan (ADM-FR-11).
`inventory_events` records stock, not configuration. SEC-14 asks for an append-only history kept
at least a year.

## Decision

1. **One table** (`audit_events`): business, location (empty for business-wide changes), time,
   actor kind (`user`, `system`, `support`) and actor, action (`category.verb`), subject type and
   id, a one-line summary, the changed fields as they were and as they became (JSON), and the
   client address. Indexed by (business, time).
2. **One writer**, `record_audit()` in `app/audit.py`, called by:
   - settings: the business profile;
   - locations: added, changed, archived, restored;
   - team: invited, invitation withdrawn, joined (by the person joining), role or scope changed,
     removed;
   - connections: Square connected or reconnected;
   - billing: plan or status changed by a Stripe event, recorded as the product (`system`).
   A save that changes nothing records nothing.
3. **Append-only**: only `record_audit()` inserts; `update_scoped` refuses the table, and a test
   fails if any module other than the model, the writer and the scoped layer names it. Nothing
   deletes events, so they outlive the one-year minimum.
4. **Who sees what** (`/app/audit`, `audit.view`): Owners and Administrators see everything;
   Managers see the changes at their locations (a location-limited Manager only those
   locations'), never business-wide ones. Filters: kind of change and person; 100 at a time,
   newest first, with a link to older ones.

## Alternatives considered

- Database triggers to forbid UPDATE and DELETE: stronger, but they differ between SQLite and
  Postgres, and the account-deletion path (SAAS lifecycle) will need to remove a business's rows;
  revisit with T4.07, when support access needs the same guarantee.
- Reuse `inventory_events`: it is the stock trail, read by reports; mixing configuration into it
  would make every stock query filter it out.
- Log lines instead of rows: owners cannot read logs, and logs never carry email addresses.

## Consequences

- A new setting, mapping or recipe editor must call `record_audit()` in the same change
  (`CLAUDE.md`).
- Item master data edits (new items, thresholds, barcode links) are not yet recorded; they join
  with the item management screens (T1.14), recipes with T2.02, mappings with the catalog
  (T2.01).
- Built in T1.12 with migration `d8a2b6e4f1c3`.
