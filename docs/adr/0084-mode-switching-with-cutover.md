# ADR-0084: Mode switching with a cutover business date

- **Status:** Proposed, 2026-11-21 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T3.10: WF-25, POS-FR-19, STD-FR-11, DR-31, AC-SAL-06)
- **Tasks:** T3.10; T4.02 adds the plan change that follows the mode (PD-39)

## Context

A Standalone location that adopted Square kept its hand-entry source of record open-ended, so
Square's sales were kept but never counted, and were flagged `sales_source_overlap`. Going back the
other way left the POS source of record, so hand entry stayed refused.

## Decision

1. **One cutover function for both directions** (`cut_over()` in `app/sales.py`, the owner of
   sales sources). From the cutover date D, a location's sales come from the chosen source (a POS
   provider, hand entry or file imports) and from it alone (DR-31):
   - every other source of record stops the day before D (or is retired whole if it began on or
     after D), and its counted sales from D are voided, giving back what they took;
   - sales the chosen source already recorded from D, kept but not counted, start counting and
     are consumed;
   - earlier days are untouched.
2. **The cutover sticks.** The chosen source is marked `fixed_from` (migration `c4e8a2f6b9d1`), so
   a POS sale for an earlier day arriving late never counts by moving the source's start back. The
   automatic extension for a feed's older days remains for sources no one chose. The POS source a
   sale lands in is now the one counting that day, not the oldest.
3. **The rules** (`app/sales_modes.py`):
   - Connected needs a location tracking a POS location of an active connection.
   - D lies within 30 days of today, either way (PD-50).
   - Switching to the source already in use on D is refused.
   - The default D is today for joining a POS; for leaving one, it is the day after the POS's last
     day with sales (WF-25).
   - The switch is audited as `settings.sales_mode`, with D and the counts voided and counted.
4. **The screen** (`/app/locations/{id}/sales-source`) previews which days stop counting and which
   start, then switches. It is linked from each location on the Locations page. It takes
   `subscription.manage` (Owner only), because the plan follows the mode (PD-39).
5. **Onboarding**: the Connected path's step 7 is done once a location tracks a Square location,
   and step 8, "Map your Square products", is added (SAAS §5).

## Alternatives considered

- **Switching on disconnect automatically:** a disconnect may be brief, and a reconnect would then
  need switching back (PD-51). The disconnect message points to the switch instead.
- **A new permission key for the switch:** WF-25 makes it the Owner's, as plan changes are.

## Consequences

- After a disconnect, hand entry stays refused until the Owner switches the location.
- The plan change at cutover waits for T4.02.
