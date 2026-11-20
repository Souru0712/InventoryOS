# ADR-0077: The external catalog and location mapping

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T3.03: POS-FR-03, POS-FR-04, SQ-05..09, audit G11 and SQ8;
  DM-13)
- **Tasks:** T3.03; T3.04 maps external products and modifiers onto the business's catalog

## Context

The sync created a business's locations from Square's (G11), so a business could not choose which
Square location fed which of its own locations. Modifiers were not synced at all, and the catalog
existed only as `sold_items`, keyed by business rather than connection. A Square milligram was
read as a gram (SQ8).

## Decision

1. **Three tables record what a POS reports**, each unique per (connection, external id):
   `external_locations`, `external_products` (one row per variation, the mapping grain, with the
   POS's price and unit as information only) and `external_modifiers` (with their list name).
   `app/pos_catalog.py` is their only writer (a test enforces it). It is provider-neutral and
   stores the adapter contract's normalized types.
2. **A listing is complete.** What the POS no longer lists is marked inactive, never deleted, so
   mappings and history stay (SQ-08). Re-syncing the same listing changes nothing.
3. **Tracking is the business's choice.** A Square location is tracked when one of the
   business's locations carries its id (`provider_location_id`, the column the legacy sales and
   stock paths already read). The sync no longer creates or edits locations. Only an existing,
   active location that tracks nothing else can be chosen, so the tracked count stays within the
   plan's location allowance. A business with no location adds one first. Tracking changes are
   audited as `connection.location_tracked` and `connection.location_untracked`.
4. **One read feeds both paths.** Until T3.12 removes the legacy sync, `_pull_catalog` reads
   Square's locations and catalog once (now including modifier lists) and hands normalized
   records to `app/pos_catalog.py`, alongside the legacy `sold_items` writes the sales path still
   reads.
5. **DM-13** copies Square location rows and Square-origin `sold_items` into the new tables under
   the business's latest Square connection. A copied product's item id is unknown and its name is
   whole until the next sync fills them in. Rows of a business with no connection are reported
   and skipped. The mapping half of DM-13 lands with `product_mappings` in T3.04.
6. **Units are informational** (SQ-09). `METRIC_MILLIGRAM` maps to `mg`, and nothing converts
   with a POS unit.

## Alternatives considered

- **A separate mapping table for locations:** every legacy reader already keys on
  `provider_location_id`, and a location tracks one POS location at most.
- **Creating a location from a Square location in one step:** that would duplicate location
  creation and its plan check outside the locations screen.
- **Reading the catalog through the adapter now:** it would fetch Square's catalog twice per
  sync until the legacy path goes.

## Consequences

- A newly connected business sees its Square locations untracked and brings in no sales until it
  maps one.
- `upsert_locations` and `normalize_locations` are gone.
