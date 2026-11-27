"""Phase 2 audit probes: each asserts CURRENT behaviour against the InventoryOS blueprint; a pass confirms the finding.

Every Phase 2 probe is retired; each note names the CI test that now asserts the opposite.
"""

# PA1 (a tenant without Square cannot create any stock item) retired in T1.04: owners add
# locations themselves; tests/test_locations.py::test_a_business_without_square_adds_a_location_then_its_stock
# asserts it in CI.

# PA2 (Starter withholds the reorder CSV and custom thresholds, G3) retired in T4.01: plans and
# entitlements are data and the core is never gated (C-41, E-05);
# tests/test_entitlements.py::test_every_plan_gets_the_core_features_from_the_server and
# ::test_the_core_is_never_gated_by_plan assert it in CI.

# PA3 (one person cannot belong to two businesses) retired in T1.05: memberships; see
# tests/test_memberships.py::test_one_person_can_belong_to_two_businesses_but_only_once_to_each.

# PA4 (one Square merchant can attach to two businesses, S7) retired in T3.02: a partial unique
# index allows one active connection per merchant; see
# tests/test_connections.py::test_pa4_retired_a_merchant_is_actively_connected_to_one_business_at_most.

# PA5 (stored datetimes come back naive, DB3) retired in T1.01: every instant is timestamptz and
# tests/test_schema_hygiene.py::test_a_stored_instant_comes_back_aware_and_in_utc asserts it in CI.
