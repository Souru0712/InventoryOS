"""Phase 2 audit probes: each asserts CURRENT behaviour against the InventoryOS blueprint; a pass confirms the finding."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.entitlements import PLAN_IDS, allows
from app.models import Location, Plan, ProviderConnection, SubscriptionStatus, Tenant, User
from app.security import hash_password


def test_PA1_a_tenant_without_square_cannot_create_any_stock_item(db):
    """Standalone blocker: signup creates no location, and stock items require one."""
    from app.main import app

    with TestClient(app) as client:
        r = client.post(
            "/signup",
            data={"business_name": "Corner Smoothies", "email": "owner@corner.test", "password": "a-good-password"},
            follow_redirects=False,
        )
        assert r.status_code in (200, 303)
        tenant = db.query(Tenant).filter(Tenant.name == "Corner Smoothies").one()
        assert db.query(Location).filter(Location.tenant_id == tenant.id).count() == 0

        r = client.post(
            "/app/items/new",
            data={"name": "Mango", "location_id": "1", "dimension": "weight", "stock_unit": "g"},
            follow_redirects=False,
        )
        assert r.status_code == 404  # no location this tenant owns, and no route creates one

        assert "Square is not connected" in client.get("/app").text


def test_PA2_starter_plan_gates_core_features(db, tenant):
    """Phase 1 E-05 says core is never gated; Starter withholds the reorder CSV and custom thresholds."""
    tenant.plan = Plan.STARTER
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    db.commit()
    assert PLAN_IDS == ("starter", "pro")
    assert allows(tenant, "reorder_csv") is False
    assert allows(tenant, "custom_thresholds") is False


def test_PA3_one_person_cannot_belong_to_two_businesses(db, tenant):
    """users.email is globally unique and users.tenant_id single-valued: no memberships."""
    other = Tenant(name="Second Shop")
    db.add(other)
    db.flush()
    db.add(User(tenant_id=other.id, email="owner@rosewood.test", password_hash=hash_password("a-good-password")))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_PA4_one_square_merchant_can_attach_to_two_tenants(db, tenant):
    """S7: nothing stops two businesses connecting the same merchant; webhooks then route to .first()."""
    other = Tenant(name="Second Shop")
    db.add(other)
    db.flush()
    for t in (tenant.id, other.id):
        db.add(ProviderConnection(tenant_id=t, provider="square", merchant_id="MERCHANT-1", access_token_enc="x"))
    db.commit()
    assert db.query(ProviderConnection).filter(ProviderConnection.merchant_id == "MERCHANT-1").count() == 2


def test_PA5_timestamps_are_naive(db, tenant):
    """DB3 still holds: stored datetimes come back without a time zone."""
    db.refresh(tenant)
    tenant.trial_started_at = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    db.commit()
    db.expire_all()
    assert db.get(Tenant, tenant.id).trial_started_at.tzinfo is None
