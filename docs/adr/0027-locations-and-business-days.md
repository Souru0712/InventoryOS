# ADR-0027: Owner-managed locations, archiving, and business days

- **Status:** Accepted, 2026-11-06 (the owner merged the T1.04 pull request, #22: "Yes, merge it")
- **Decision ID:** none (implements T1.04, migration step DM-3; ADM-FR-01, ADM-FR-02, DR-20, AC-ADM-01)
- **Tasks:** T1.04; shapes T1.05 (location scopes), T2.12–T2.16 (daily sales, reports), T3.03 (mapping)

## Context

Only the Square sync created locations, so a business without Square could not add a single item
(probe PA1). A location already had an `is_active` flag that the plan limit switches (Starter
covers one location) and that the Square sync copies from Square's status. Every "today" in the
code was UTC's, so a lot dated today in Los Angeles counted as expired from 4 p.m. local time.

## Decision

1. **Owners add, edit and archive locations** (`/app/locations`). A location's provider ids are
   optional; one added by the owner has none and is never sent to Square.
2. **Archiving is its own state** (`archived_at`), separate from `is_active`. Archived locations
   are inactive, are skipped by the plan limit and keep `is_active` off through Square syncs, so
   neither an upgrade nor Square brings them back; restoring does, if the plan has room. Nothing
   is deleted (DR-24).
3. **The plan's allowance holds for adding and restoring**: active locations never exceed it.
4. **Square keeps the fields it supplies** for a location it created (name, time zone,
   currency): the form shows them read-only and the save ignores them. Address, cutoff and open
   days are the owner's on every location. Ownership of the rest moves with the mapping (T3.03).
5. **A business date is `business_date(instant, time_zone, cutoff_hour)`**
   (`app/transform/business_days.py`): the local date, or the day before when the local hour is
   earlier than the cutoff. Expiry is judged on each item's location business day unless the
   caller fixes a date: the dashboard, expiring page, health, markdown suggestions and the
   alert job all follow the location's clock (AC-ADM-01).
6. **Open days are a weekday mask** (Monday is bit 0, CHECK 0–127) with helpers beside
   `business_date`; nothing reads them yet (missing-sales checks arrive with daily sales, T2.12).

## Alternatives considered

- Archive by clearing `is_active` only: the next upgrade or Square sync would switch it back on.
- Let the owner overwrite Square's name and time zone: the hourly sync would quietly undo it.
- Keep UTC dates and convert at display: expiry and alert decisions would still be a day early
  or late for any business west of Greenwich.

## Consequences

- New per-location "today" logic calls `business_date` with the location's zone and cutoff; a
  report over business days (AC-ANL-05) uses the same helper.
- A Square-linked location's address, cutoff and open days survive syncs; its name, time zone and
  currency follow Square.
- Built in T1.04 with migration `912d17673f03` (additive; its downgrade gives owner-added locations
  `manual-<id>` provider ids so the older schema's constraints hold).
