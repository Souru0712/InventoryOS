"""Smoke tests for the pages a customer actually touches."""

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def test_public_pages_render(client):
    for path in ["/", "/pricing", "/terms", "/privacy"]:
        response = client.get(path)
        assert response.status_code == 200, path
    assert client.get("/healthz").json()["status"] == "ok"


def test_signup_creates_a_workspace_and_lands_on_the_dashboard(client):
    response = client.post(
        "/signup",
        data={
            "business_name": "Rosewood Coffee",
            "email": "owner@rosewood.test",
            "password": "a-good-password",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Rosewood Coffee" in response.text
    assert "Connect Square" in response.text


def test_short_passwords_are_rejected(client):
    response = client.post(
        "/signup",
        data={"business_name": "X", "email": "x@rosewood.test", "password": "short"},
    )
    assert response.status_code == 400
    assert "at least 10 characters" in response.text


def test_signed_out_visitors_are_sent_to_login(client):
    response = client.get("/app", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_rejects_a_wrong_password(client):
    client.post(
        "/signup",
        data={
            "business_name": "Rosewood Coffee",
            "email": "owner@rosewood.test",
            "password": "a-good-password",
        },
    )
    client.post("/logout")
    response = client.post(
        "/login", data={"email": "owner@rosewood.test", "password": "wrong-password-x"}
    )
    assert response.status_code == 401


def test_one_tenant_cannot_read_another_tenants_documents(client):
    client.post(
        "/signup",
        data={"business_name": "A", "email": "a@rosewood.test", "password": "a-good-password"},
    )
    client.post("/logout")
    client.post(
        "/signup",
        data={"business_name": "B", "email": "b@rosewood.test", "password": "a-good-password"},
    )
    assert client.get("/app/receiving/1").status_code == 404


# --- expiry pages -----------------------------------------------------------------------------

from datetime import date, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Location, StockBatch, StockItem, Tenant, TrackMode  # noqa: E402
from app.units import Dimension  # noqa: E402


def signed_up_with_stock(client, name="Rosewood Coffee", email="owner@rosewood.test") -> int:
    """Sign up, then give the new workspace a location and one item. Returns the item id."""
    client.post(
        "/signup", data={"business_name": name, "email": email, "password": "a-good-password"}
    )
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.name == name).one()
        location = Location(
            tenant_id=tenant.id, provider="demo", provider_location_id=f"loc-{name}", name="Main"
        )
        db.add(location)
        db.flush()
        item = StockItem(
            tenant_id=tenant.id,
            location_id=location.id,
            slug="yoghurt",
            name="Natural yoghurt 500g",
            dimension=Dimension.COUNT,
            stock_unit="each",
            track_mode=TrackMode.MANUAL,
            qty_base=Decimal("10"),
            cost_per_base_cents=Decimal("110"),
        )
        db.add(item)
        db.commit()
        return item.id


def test_the_expiring_page_explains_itself_before_anything_is_dated(client):
    signed_up_with_stock(client)
    response = client.get("/app/expiring")
    assert response.status_code == 200
    assert "No dated stock yet" in response.text


def test_dating_shelf_stock_adds_a_lot_without_changing_the_count(client):
    item_id = signed_up_with_stock(client)
    soon = (date.today() + timedelta(days=3)).isoformat()
    response = client.post(
        "/app/expiring/label",
        data={"stock_item_id": item_id, "quantity": "10", "expires_on": soon},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Natural yoghurt 500g" in response.text
    # No sales signal, three days out: worst case stands in, and the money is shown.
    assert "$11.00" in response.text
    with SessionLocal() as db:
        assert db.query(StockBatch).one().source == "label"
        assert db.get(StockItem, item_id).qty_base == Decimal("10")


def test_a_bad_quantity_is_refused_with_a_reason(client):
    item_id = signed_up_with_stock(client)
    response = client.post(
        "/app/expiring/label",
        data={"stock_item_id": item_id, "quantity": "0", "expires_on": "2030-01-01"},
        follow_redirects=True,
    )
    assert "Enter a quantity above zero" in response.text
    with SessionLocal() as db:
        assert db.query(StockBatch).count() == 0


def test_a_wrongly_entered_date_can_be_removed(client):
    item_id = signed_up_with_stock(client)
    client.post(
        "/app/expiring/label",
        data={"stock_item_id": item_id, "quantity": "4", "expires_on": "2030-01-01"},
    )
    with SessionLocal() as db:
        batch_id = db.query(StockBatch).one().id
    client.post(f"/app/expiring/{batch_id}/remove")
    with SessionLocal() as db:
        assert db.query(StockBatch).count() == 0
        assert db.get(StockItem, item_id).qty_base == Decimal("10")


def test_a_date_with_a_markdown_decided_on_it_can_still_be_removed(client):
    """Postgres enforces the promotion's reference to the lot; the markdown stays on record."""
    from app.models import Promotion, PromotionStatus

    item_id = signed_up_with_stock(client)
    client.post(
        "/app/expiring/label",
        data={"stock_item_id": item_id, "quantity": "4", "expires_on": "2030-01-01"},
    )
    with SessionLocal() as db:
        batch = db.query(StockBatch).one()
        db.add(
            Promotion(
                tenant_id=batch.tenant_id,
                stock_item_id=item_id,
                stock_batch_id=batch.id,
                status=PromotionStatus.ACTIVE,
                ends_on=date(2030, 1, 1),
            )
        )
        db.commit()
        batch_id = batch.id

    assert (
        client.post(f"/app/expiring/{batch_id}/remove", follow_redirects=False).status_code == 303
    )
    with SessionLocal() as db:
        assert db.query(StockBatch).count() == 0
        promotion = db.query(Promotion).one()
        assert promotion.stock_batch_id is None
        assert promotion.ends_on == date(2030, 1, 1)


def test_one_tenant_cannot_date_or_remove_another_tenants_stock(client):
    item_id = signed_up_with_stock(client, "A", "a@rosewood.test")
    client.post(
        "/app/expiring/label",
        data={"stock_item_id": item_id, "quantity": "4", "expires_on": "2030-01-01"},
    )
    with SessionLocal() as db:
        batch_id = db.query(StockBatch).one().id
    client.post("/logout")
    signed_up_with_stock(client, "B", "b@rosewood.test")
    assert (
        client.post(
            "/app/expiring/label",
            data={"stock_item_id": item_id, "quantity": "1", "expires_on": "2030-01-01"},
        ).status_code
        == 404
    )
    assert client.post(f"/app/expiring/{batch_id}/remove").status_code == 404
    with SessionLocal() as db:
        assert db.query(StockBatch).count() == 1


def test_the_dashboard_leads_with_money_at_risk_once_stock_is_dated(client):
    item_id = signed_up_with_stock(client)
    client.post(
        "/app/expiring/label",
        data={
            "stock_item_id": item_id,
            "quantity": "10",
            "expires_on": (date.today() + timedelta(days=2)).isoformat(),
        },
    )
    response = client.get("/app")
    assert response.status_code == 200
    assert "May expire unsold" in response.text
    assert "$11.00" in response.text
