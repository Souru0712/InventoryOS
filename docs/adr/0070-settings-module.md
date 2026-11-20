# ADR-0070: Settings module

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.19: ADM-FR-01, -03..06, AC-ADM-01, -02, -06)
- **Tasks:** T2.19; T2.20 (onboarding sets the same settings)

## Context

Business configuration was spread across screens or needed a database edit: the weekly summary's
schedule, the review thresholds (constants in code), the alert email's timing, the units an item
form offers, how a new product is made. A modifier's scale factor changed in place, so a sale
recorded or reprocessed after a change read the new factor even for an earlier moment
(contrary to AC-ADM-06).

## Decision

1. **`business_settings`**, one row per business made on first read, written only by
   `app/business_settings.py`: units offered first (metric or US), default production mode, the
   alert email's hour (none: as soon as something is found), the count review amount and share,
   the adjustment review amount. The schedule and expiry window stay on the tenant row where they
   began, and the same service writes them; the profile keeps its one writer, the form on
   `/app/settings` (T1.04). Every change is a
   `settings.*` audit event with what changed.
2. **Where they take effect**, read when they run (AC-ADM-02): counts and adjustments pass the
   thresholds to `needs_review()`; the evaluation job queues the alert email only in the chosen
   hour on the business's clock (deduped per local day); a new product takes the default mode;
   the item forms list the business's units first; the weekly summary uses the schedule.
3. **`modifier_scales`**, append-only: a scale factor from an instant on. `save_modifier()`
   appends one when a factor is set or changed; consumption reads `scale_at()` at the sale's own
   moment (the latest set by then, else the first ever set), so a change applies to later sales
   and a reprocessed earlier sale keeps its factor (AC-ADM-06). Existing factors are backfilled
   as in effect from the start.
4. **Two screens**: `/app/settings/business` (`settings.manage`: schedule, defaults, links
   to locations, team, categories, waste reasons, modifiers, Employee sales entry) and
   `/app/settings/alerts` (`alerts.configure`, so Managers too: expiry window, alert email hour,
   thresholds, and who receives which email). Recipients stay each person's own choice: a person
   is global across businesses (ADR-0028), so one business must not change their email settings.

## Alternatives considered

- **Key-value settings:** loses the CHECKs and types each setting needs.
- **Settings as tenant columns:** the tenant row already mixes billing and profile.

## Consequences

- Adjustment reasons are still the fixed R1 list (ADM-FR-04 lists them as configurable): making
  them configurable needs a reasons table and a change to the adjustment CHECK. Recorded for a
  decision.
- AC-ADM-01 (time zone moves "today" and expiry) was met by T1.04's location settings; the
  weekly summary and alert email now follow the business's clock too.
