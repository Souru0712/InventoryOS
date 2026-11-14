"""Markdowns against real stock: suggested from the shelf, decided by a person, measured after."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.access import OrgContext
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Location,
    Promotion,
    PromotionStatus,
    SoldItem,
    StockBatch,
    StockItem,
    Tenant,
    TrackMode,
)
from app.orchestration.promotions import accept, dismiss, end, running, snooze, suggestions_for
from app.units import Dimension
from tests.sales_helpers import sold_on

TODAY = date(2026, 9, 13)


@pytest.fixture
def gatorade(db, tenant, location):
    """24 on the shelf, selling a bit over one a day, best before in 9 days."""
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="gatorade",
        name="Gatorade Lemon-Lime 28oz",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.PROVIDER,
        provider_variation_id="VAR-GAT",
        qty_base=Decimal("24"),
        cost_per_base_cents=Decimal("80"),
        retail_per_base_cents=Decimal("199"),
    )
    sold = SoldItem(
        tenant_id=tenant.id, provider_item_id="VAR-GAT", name="Gatorade", price_cents=199
    )
    db.add_all([item, sold])
    db.flush()
    for back in range(28):
        if back % 5:
            sold_on(db, location, sold, TODAY - timedelta(days=back), "1.5")
    db.add(
        StockBatch(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            received_qty_base=Decimal("24"),
            expires_on=TODAY + timedelta(days=9),
            cost_per_base_cents=Decimal("80"),
        )
    )
    db.commit()
    return item


def test_a_lot_that_will_not_sell_in_time_is_suggested(db, tenant, gatorade):
    [suggestion] = suggestions_for(db, OrgContext.system(tenant.id), TODAY)
    assert suggestion.item.name == "Gatorade Lemon-Lime 28oz"
    assert suggestion.urgency == "medium"
    assert suggestion.suggested_price_cents < 199
    assert suggestion.reasons


def test_an_item_with_no_selling_price_is_never_suggested(db, tenant, gatorade):
    gatorade.retail_per_base_cents = None
    db.commit()
    assert suggestions_for(db, OrgContext.system(tenant.id), TODAY) == []


def test_stock_that_clears_in_time_is_not_suggested(db, tenant, gatorade):
    gatorade.qty_base = Decimal("4")
    db.commit()
    assert suggestions_for(db, OrgContext.system(tenant.id), TODAY) == []


def test_accepting_records_the_decision_and_stops_the_nagging(db, tenant, gatorade):
    [suggestion] = suggestions_for(db, OrgContext.system(tenant.id), TODAY)
    promotion = accept(db, OrgContext.system(tenant.id), suggestion, 149, user_id=None, today=TODAY)
    db.commit()

    assert promotion.status is PromotionStatus.ACTIVE
    assert promotion.accepted_price_cents == 149
    assert promotion.normal_price_cents == 199
    assert promotion.velocity_before is not None
    assert promotion.units_before_base == Decimal("24")
    assert promotion.ends_on == TODAY + timedelta(days=9)
    assert promotion.reason  # the explanation is kept with the decision
    assert suggestions_for(db, OrgContext.system(tenant.id), TODAY) == []


def test_dismissing_stops_it_coming_back(db, tenant, gatorade):
    [suggestion] = suggestions_for(db, OrgContext.system(tenant.id), TODAY)
    dismiss(db, OrgContext.system(tenant.id), suggestion)
    db.commit()
    assert suggestions_for(db, OrgContext.system(tenant.id), TODAY) == []


def test_a_snooze_wears_off(db, tenant, gatorade):
    [suggestion] = suggestions_for(db, OrgContext.system(tenant.id), TODAY)
    snooze(db, OrgContext.system(tenant.id), suggestion, days=1, today=TODAY)
    db.commit()

    assert suggestions_for(db, OrgContext.system(tenant.id), TODAY) == []
    # Tomorrow it is worked out again from whatever the shelf holds then.
    assert len(suggestions_for(db, OrgContext.system(tenant.id), TODAY + timedelta(days=1))) == 1


def test_a_running_markdown_reports_what_sales_did(db, tenant, location, gatorade):
    [suggestion] = suggestions_for(db, OrgContext.system(tenant.id), TODAY)
    accept(db, OrgContext.system(tenant.id), suggestion, 149, today=TODAY - timedelta(days=2))
    sold = db.query(SoldItem).one()
    for back in range(3):
        day = TODAY - timedelta(days=back)
        sold_on(db, location, sold, day, "6")  # four times the usual rate since the markdown
    db.commit()

    [live] = running(db, OrgContext.system(tenant.id), TODAY)
    assert live.days_running == 2
    assert live.velocity_during > live.promotion.velocity_before
    assert live.lift > Decimal("1")


def test_ending_a_markdown_records_where_the_stock_finished(db, tenant, gatorade):
    [suggestion] = suggestions_for(db, OrgContext.system(tenant.id), TODAY)
    promotion = accept(db, OrgContext.system(tenant.id), suggestion, 149, today=TODAY)
    gatorade.qty_base = Decimal("3")
    db.commit()

    end(db, OrgContext.system(promotion.tenant_id), promotion)
    db.commit()
    assert promotion.status is PromotionStatus.ENDED
    assert promotion.units_after_base == Decimal("3")


# --- through the browser ----------------------------------------------------------------------


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def signed_up_with_risk(client, name="Rosewood", email="o@rosewood.test") -> int:
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
            slug="bars",
            name="Protein bars",
            dimension=Dimension.COUNT,
            stock_unit="each",
            track_mode=TrackMode.MANUAL,
            qty_base=Decimal("24"),
            cost_per_base_cents=Decimal("80"),
            retail_per_base_cents=Decimal("249"),
        )
        db.add(item)
        db.flush()
        db.add(
            StockBatch(
                tenant_id=tenant.id,
                stock_item_id=item.id,
                received_qty_base=Decimal("24"),
                expires_on=date.today() + timedelta(days=9),
            )
        )
        db.commit()
        return item.id


def test_the_page_shows_the_recommendation_and_its_reasons(client):
    signed_up_with_risk(client)
    response = client.get("/app/promotions")
    assert response.status_code == 200
    assert "Protein bars" in response.text
    assert "on the shelf" in response.text  # the reasoning is shown, not just a number


def test_starting_a_markdown_at_a_chosen_price(client):
    item_id = signed_up_with_risk(client)
    response = client.post(
        "/app/promotions/accept",
        data={"stock_item_id": item_id, "price": "1.49"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        promotion = db.query(Promotion).one()
        assert promotion.accepted_price_cents == 149
        assert promotion.status is PromotionStatus.ACTIVE


def test_starting_one_without_a_price_uses_the_suggestion(client):
    item_id = signed_up_with_risk(client)
    client.post("/app/promotions/accept", data={"stock_item_id": item_id, "price": ""})
    with SessionLocal() as db:
        promotion = db.query(Promotion).one()
        assert promotion.accepted_price_cents == promotion.suggested_price_cents


def test_a_price_that_is_not_a_number_is_refused(client):
    item_id = signed_up_with_risk(client)
    assert (
        client.post(
            "/app/promotions/accept", data={"stock_item_id": item_id, "price": "cheap"}
        ).status_code
        == 400
    )


def test_acting_on_a_suggestion_that_no_longer_applies_is_refused(client):
    item_id = signed_up_with_risk(client)
    with SessionLocal() as db:
        db.get(StockItem, item_id).qty_base = Decimal("0")  # the shelf cleared meanwhile
        db.commit()
    assert client.post("/app/promotions/accept", data={"stock_item_id": item_id}).status_code == 404


def test_one_tenant_cannot_mark_down_another_tenants_stock(client):
    item_id = signed_up_with_risk(client, "A", "a@rosewood.test")
    client.post("/logout")
    signed_up_with_risk(client, "B", "b@rosewood.test")
    for path in ("accept", "dismiss", "snooze"):
        assert (
            client.post(f"/app/promotions/{path}", data={"stock_item_id": item_id}).status_code
            == 404
        ), path
