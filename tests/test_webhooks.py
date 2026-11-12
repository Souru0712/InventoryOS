"""Square tells us when stock moves at the till. Only if it can prove it is Square.

Since T1.18 its count is noted, never applied: the ledger owns every item's quantity (C-34).
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Location, Membership, ProviderConnection, StockItem, Tenant, TrackMode, User
from app.providers.square.webhooks import signature_for, verify_signature
from app.security import hash_password
from app.units import Dimension

KEY = "test-signature-key"
MERCHANT = "MERCHANT-1"


@pytest.fixture
def client(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    settings = get_settings()
    monkeypatch.setattr(settings, "square_webhook_signature_key", KEY, raising=False)
    monkeypatch.setattr(settings, "app_base_url", "https://stockpulse.test", raising=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def connected():
    """A tenant with a live Square connection and one POS-tracked item."""
    with SessionLocal() as db:
        tenant = Tenant(name="Rosewood")
        db.add(tenant)
        db.flush()
        owner = User(
            tenant_id=tenant.id,
            email="o@rosewood.test",
            password_hash=hash_password("a-good-password"),
        )
        db.add(owner)
        db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=owner.id, role="owner"))
        db.add(
            ProviderConnection(
                tenant_id=tenant.id,
                provider="square",
                merchant_id=MERCHANT,
                access_token_enc="x",
                status="active",
            )
        )
        location = Location(
            tenant_id=tenant.id, provider="square", provider_location_id="LOC-1", name="Main"
        )
        db.add(location)
        db.flush()
        item = StockItem(
            tenant_id=tenant.id,
            location_id=location.id,
            slug="coke",
            name="Coca-Cola 12oz",
            dimension=Dimension.COUNT,
            stock_unit="each",
            track_mode=TrackMode.PROVIDER,
            provider_variation_id="VAR-COKE",
            qty_base=Decimal("34"),
        )
        db.add(item)
        db.commit()
        return item.id


def event(quantity: str, calculated_at: datetime) -> dict:
    return {
        "merchant_id": MERCHANT,
        "type": "inventory.count.updated",
        "data": {
            "object": {
                "inventory_counts": [
                    {
                        "catalog_object_id": "VAR-COKE",
                        "location_id": "LOC-1",
                        "state": "IN_STOCK",
                        "quantity": quantity,
                        "calculated_at": calculated_at.isoformat().replace("+00:00", "Z"),
                    }
                ]
            }
        },
    }


def post(client, payload: dict, signature: str | None = None):
    body = json.dumps(payload).encode()
    url = "https://stockpulse.test/webhooks/square"
    header = signature if signature is not None else signature_for(url, body, KEY)
    return client.post(
        "/webhooks/square",
        content=body,
        headers={"x-square-hmacsha256-signature": header, "content-type": "application/json"},
    )


def quantity_now(item_id: int) -> Decimal:
    with SessionLocal() as db:
        return db.get(StockItem, item_id).qty_base


# --- signature ------------------------------------------------------------------------------


def test_the_signature_covers_the_url_as_well_as_the_body():
    # Square signs url + body, so the same body at a different URL is a different signature.
    body = b'{"a":1}'
    assert signature_for("https://a.test/hook", body, KEY) != signature_for(
        "https://b.test/hook", body, KEY
    )


def test_a_good_signature_verifies_and_a_tampered_body_does_not():
    url, body = "https://stockpulse.test/webhooks/square", b'{"type":"x"}'
    good = signature_for(url, body, KEY)
    assert verify_signature(url, body, good, KEY)
    assert not verify_signature(url, b'{"type":"y"}', good, KEY)
    assert not verify_signature(url, body, good, "another-key")


def test_an_empty_signature_or_key_is_never_valid():
    assert not verify_signature("https://x.test", b"{}", "", KEY)
    assert not verify_signature("https://x.test", b"{}", "sig", "")


def test_an_unsigned_call_is_refused(client, connected):
    response = post(client, event("99", datetime.now(UTC)), signature="not-the-signature")
    assert response.status_code == 400
    assert quantity_now(connected) == Decimal("34")


# --- applying -------------------------------------------------------------------------------


def test_a_count_at_the_till_is_noted_but_never_changes_our_stock(client, connected):
    response = post(client, event("30", datetime.now(UTC)))
    assert response.status_code == 200
    assert response.json()["seen"] == 1
    assert quantity_now(connected) == Decimal("34")
    with SessionLocal() as db:
        assert db.get(StockItem, connected).last_synced_at is not None


def test_a_count_calculated_before_our_last_read_is_ignored(client, connected):
    # An offline till can deliver yesterday's number today; older news is not even noted.
    post(client, event("30", datetime.now(UTC)))
    stale = datetime.now(UTC) - timedelta(days=1)
    response = post(client, event("99", stale))
    assert response.json()["seen"] == 0
    assert quantity_now(connected) == Decimal("34")


def test_another_event_type_is_acknowledged_and_ignored(client, connected):
    payload = event("1", datetime.now(UTC))
    payload["type"] = "order.created"
    body = post(client, payload).json()
    assert body["ignored"] == "order.created"
    assert quantity_now(connected) == Decimal("34")


def test_a_merchant_we_do_not_know_is_ignored_not_retried(client, connected):
    payload = event("5", datetime.now(UTC))
    payload["merchant_id"] = "SOMEONE-ELSE"
    response = post(client, payload)
    assert response.status_code == 200
    assert response.json()["ignored"] == "unknown merchant"
    assert quantity_now(connected) == Decimal("34")


def test_the_endpoint_refuses_everything_until_the_key_is_configured(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(get_settings(), "square_webhook_signature_key", "", raising=False)
    with TestClient(app) as unconfigured:
        assert unconfigured.post("/webhooks/square", content=b"{}").status_code == 503
