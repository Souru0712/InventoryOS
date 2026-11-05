"""The snack box shop is private: no plan buys it, and an account that is not on the list is never
told it exists."""

import json

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.entitlements import CAPABILITIES, allows, owns_snack_box
from app.models import BoxSettings, Location, ProviderConnection, Tenant
from app.orchestration import scheduler
from app.providers.square.webhooks import signature_for
from tests.snack_box_support import (  # noqa: F401  (fixtures)
    open_store,
    paid_box,
    shopper,
    square,
    web,
)

OWNER = {"business_name": "Snack Shop", "email": "owner@shop.test", "password": "a-good-password"}
RIVAL = {"business_name": "Rival Cafe", "email": "rival@cafe.test", "password": "a-good-password"}


def furnish(square_fake, open_shop: bool = True) -> tuple[int, int]:
    """One signed-up account with a stocked, paid snack box behind it."""
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        location = Location(
            tenant_id=tenant.id, provider="demo", provider_location_id="L1", name="Main"
        )
        db.add(location)
        db.flush()
        open_store(db, tenant, location, square_fake)
        order = paid_box(db, tenant, shopper(db, tenant))
        db.commit()
        return tenant.id, order.id


def test_no_plan_includes_it(db, tenant):
    assert not owns_snack_box(tenant)
    assert not any("snack" in field for limits in CAPABILITIES.values() for field in vars(limits))
    assert allows(tenant, "snack_box") is False


def test_a_customers_deployment_has_no_snack_box_at_all(web, square):
    web.post("/signup", data=RIVAL)
    store, order = furnish(square)
    get_settings().snack_box_tenant_ids = ""  # this account was never on the list

    for path in [
        "/app/boxes",
        f"/app/boxes/{order}",
        "/app/boxes/stock",
        "/app/boxes/snacks",
        "/app/boxes/settings",
        "/app/boxes/referrals",
    ]:
        assert web.get(path).status_code == 404, path
    assert web.post(f"/app/boxes/{order}/pack", data={"line": "1"}).status_code == 404
    assert web.post(f"/app/boxes/{order}/refund").status_code == 404
    assert web.get(f"/shop/{store}").status_code == 404
    assert web.post(f"/shop/{store}/login", data={"email": "shopper@mail.test"}).status_code == 404
    assert "/app/boxes" not in web.get("/app").text


def test_the_operators_own_account_keeps_it(web, square):
    web.post("/signup", data=OWNER)
    store, order = furnish(square)  # open_store puts this account on the list
    assert web.get("/app/boxes").status_code == 200
    assert web.get(f"/app/boxes/{order}").status_code == 200
    assert web.get(f"/shop/{store}").status_code == 200
    assert "/app/boxes" in web.get("/app").text


def test_housekeeping_skips_accounts_that_are_not_on_the_list(
    db, tenant, location, square, monkeypatch
):
    open_store(db, tenant, location, square)
    rival = Tenant(name="Rival Cafe")
    db.add(rival)
    db.flush()
    db.add(BoxSettings(tenant_id=rival.id))
    db.commit()

    seen = []
    monkeypatch.setattr(
        scheduler, "expire_unpaid", lambda session, tenant_id: seen.append(tenant_id) or 0
    )
    monkeypatch.setattr(scheduler, "reconcile", lambda session, tenant_id: [])
    scheduler.box_housekeeping()
    assert seen == [tenant.id]


def test_the_square_webhook_runs_no_box_logic_for_another_merchant(web, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "square_webhook_signature_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "app_base_url", "https://stockpulse.test", raising=False)
    with SessionLocal() as db:
        tenant = Tenant(name="Rival Cafe")
        db.add(tenant)
        db.flush()
        db.add(
            ProviderConnection(
                tenant_id=tenant.id,
                provider="square",
                merchant_id="M-RIVAL",
                access_token_enc="x",
                status="active",
            )
        )
        db.commit()

    body = json.dumps(
        {
            "merchant_id": "M-RIVAL",
            "type": "payment.updated",
            "event_id": "E1",
            "data": {
                "object": {"payment": {"id": "P1", "status": "COMPLETED", "order_id": "SQ-1"}}
            },
        }
    ).encode()
    signature = signature_for("https://stockpulse.test/webhooks/square", body, "test-key")
    response = web.post(
        "/webhooks/square",
        content=body,
        headers={"x-square-hmacsha256-signature": signature, "content-type": "application/json"},
    )
    assert response.json() == {"ignored": "not a snack box merchant"}
