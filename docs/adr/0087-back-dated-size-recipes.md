# ADR-0087: A size's recipe may start in the past; past sales take it only when applied

- **Status:** Accepted, 2026-11-26 (owner: "merge PR #65"; merge commit 3c5755d)
- **Decision ID:** none (implements C-51, PD-53; amends ADR-0051 and DR-16 for product sizes)
- **Tasks:** none (owner decision C-51, a follow-up to T2.02, T3.04 and the T3.11 run)

## Context

ADR-0051 made recipe versions start now or later, so anything that happened keeps the version in
effect at its own time (DR-16). The T3.11 sandbox run sold a product before it had a recipe; the
recipe saved afterwards could never cover that sale (PD-53). The owner decided (C-51) that a
version may carry an explicit effective date earlier than when it is saved, but that saving must
never apply it to past sales: past sales change only when the owner explicitly authorizes a
previewed reprocessing, within PD-17's bounds (from the last count of what the size uses, at most
30 days back). Versions stay append-only.

## Decision

- **Back-dating:** `save_recipe()` accepts a past `effective_from` for a product size's recipe
  only, no earlier than 30 days back and later than every version the recipe already has
  (`back_date_floor()`, `check_effective_from(back_to=...)`). Modifiers and prepared items keep
  now or later (PD-55). Saving changes no sale and no movement.
- **Applying:** `app/recipe_reprocess.py` finds the size's counted sales that the version now
  covers but that did not take stock by it (their line names another version, or none). Its
  preview shows how many sales and what they would take, from the last count of what the size
  uses (PD-17's `default_start()`), at most 30 days back. On confirmation it queues the
  `reprocess_recipe` job, audited as `recipe.applied_to_past`. The job reverses and posts each
  sale again at its own moment (`reprocess_sale()`); a sale already on the version is left alone,
  so a repeat does nothing.
- **Screens:** the recipe page says when its current version starts before it was saved and how
  many past sales still took the earlier recipe, linking to the preview at
  `/app/recipes/variation/<id>/apply`.

## Alternatives considered

- **Applying a back-dated version automatically.** Rejected by the owner: history would change
  without authorization.
- **Only a size's first version may start earlier** (the PD-53 recommendation). The owner's
  decision covers any version; requiring it to start after the version before keeps every
  version reachable.
- **Back-dating every kind of recipe.** A modifier's or prepared item's version reaches sales and
  production runs of many products; applying it would need its own preview across all of them.
  Left for the owner (PD-55).

## Consequences

- Built on branch `after-stage3` with tests (`tests/test_recipe_backdating.py`).
- Additive migration `a3c9e5f1b7d4`: `reprocess_recipe` joins the job kinds.
- Between saving and applying, `effective_version()` at a covered sale's moment names the new
  version while that sale's posted use followed the earlier one; the ledger stays the record of
  what was taken. A sale corrected in that time (a new revision) takes the new version, being
  posted afresh at its moment.
