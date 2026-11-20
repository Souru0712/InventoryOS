"""Phase 2 audit probes: each asserts CURRENT behaviour against the InventoryOS blueprint; a pass confirms the finding."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.entitlements import PLAN_IDS, allows
from app.models import Location, Plan, ProviderConnection, SubscriptionStatus, Tenant, User
from app.security import hash_password


# PA1 (a tenant without Square cannot create any stock item) retired in T1.04: owners add
# locations themselves; tests/test_locations.py::test_a_business_without_square_adds_a_location_then_its_stock
# asserts it in CI.

def test_PA2_starter_plan_gates_core_features(db, tenant):
    """Phase 1 E-05 says core is never gated; Starter withholds the reorder CSV and custom thresholds."""
    tenant.plan = Plan.STARTER
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    db.commit()
    assert PLAN_IDS == ("starter", "pro")
    assert allows(tenant, "reorder_csv") is False
    assert allows(tenant, "custom_thresholds") is False


# PA3 (one person cannot belong to two businesses) retired in T1.05: memberships; see
# tests/test_memberships.py::test_one_person_can_belong_to_two_businesses_but_only_once_to_each.

# PA4 (one Square merchant can attach to two businesses, S7) retired in T3.02: a partial unique
# index allows one active connection per merchant; see
# tests/test_connections.py::test_pa4_retired_a_merchant_is_actively_connected_to_one_business_at_most.


# PA5 (stored datetimes come back naive, DB3) retired in T1.01: every instant is timestamptz and
# tests/test_schema_hygiene.py::test_a_stored_instant_comes_back_aware_and_in_utc asserts it in CI.
