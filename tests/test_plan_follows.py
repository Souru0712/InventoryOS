"""T4.02: the subscription follows the business (SAAS-B-18, SAAS-B-19, WF-25, C-44, AC-SAAS-12).

Complete is billed per POS-connected location, Standalone per business; a cutover moves the plan
to the narrowest one that fits from its date; the pricing page reads the catalogue.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.billing import cutover_start, set_price, sync
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditEvent, Job, Location, SubscriptionStatus, Tenant
from app.orchestration.stripe_events import handle_event
from app.plans import plan_by_code, plan_of
from app.sales_modes import preview, switch, today_at
from app.transform.billing import Offer, Use, narrowest, quantity, unfit
from tests.test_sales_modes import _settle, shop  # noqa: F401  (the fixture, by name)

COMPLETE = Offer(
    "complete", "Vontry Complete", "per_location", frozenset({"connected", "standalone"}), None
)
STANDALONE = Offer("standalone", "Vontry Standalone", "per_business", frozenset({"standalone"}), 1)


# --- the rules ------------------------------------------------------------------------------------


def test_the_narrowest_plan_that_fits_is_chosen():
    assert narrowest([COMPLETE, STANDALONE], Use(("standalone",))) == STANDALONE
    assert narrowest([COMPLETE, STANDALONE], Use(("connected",))) == COMPLETE
    assert narrowest([COMPLETE, STANDALONE], Use(("standalone", "standalone"))) == COMPLETE
    assert narrowest([STANDALONE], Use(("connected",))) is None


def test_per_location_bills_the_connected_locations_and_at_least_one():
    assert quantity(COMPLETE, Use(("connected", "connected", "standalone"))) == 2
    assert quantity(COMPLETE, Use(())) == 1
    assert quantity(STANDALONE, Use(("standalone",))) == 1


def test_a_plan_that_does_not_fit_says_why():
    assert "sales from a POS" in unfit(STANDALONE, Use(("connected",)))
    assert "covers 1 location; you have 2" in unfit(STANDALONE, Use(("standalone",) * 2))
    assert unfit(COMPLETE, Use(("connected", "standalone"))) is None


# --- the plan follows a cutover (WF-25, C-44) -----------------------------------------------------


def test_a_cutover_to_square_moves_a_standalone_trial_to_complete(db, shop):  # noqa: F811
    org, place, _size, _milk = shop
    tenant = db.get(Tenant, org.tenant_id)
    tenant.billing_plan = plan_by_code(db, "standalone")
    db.commit()

    shown = preview(db, org, place.id, "pos_adapter", "square", today_at(place))
    assert shown.next_plan is not None and shown.next_plan.code == "complete"

    switch(db, org, place.id, "pos_adapter", "square", today_at(place))
    _settle(db)
    assert plan_of(db.get(Tenant, org.tenant_id)).code == "complete"
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "billing.plan_follows")).one()
    assert event.before == {"plan": "standalone"}
    assert event.after == {"plan": "complete", "quantity": 1}


def test_a_future_cutover_changes_the_plan_on_its_own_day(db, shop):  # noqa: F811
    org, place, _size, _milk = shop
    cutover = today_at(place) + timedelta(days=3)
    switch(db, org, place.id, "pos_adapter", "square", cutover)
    db.commit()

    job = db.scalars(select(Job).where(Job.kind == "sync_subscription")).one()
    assert job.run_at == cutover_start(place, cutover)
    assert job.payload == {"reason": "cutover"}


def test_only_a_cutover_moves_a_business_to_a_narrower_plan(db, tenant, monkeypatch):
    """Adding or archiving a location resyncs; a plan that still fits is kept until a cutover."""
    org = OrgContext.system(tenant.id)
    monkeypatch.setattr("app.billing._mode", lambda db, org, place: "standalone")
    assert plan_of(tenant).code == "complete"

    assert sync(db, org, "locations") == {"changed": False}
    assert sync(db, org, "cutover") == {"changed": True, "plan": "standalone"}
    db.commit()
    assert plan_of(tenant).code == "standalone"


def test_a_plan_that_stops_fitting_moves_without_waiting_for_a_cutover(db, tenant, monkeypatch):
    org = OrgContext.system(tenant.id)
    tenant.billing_plan = plan_by_code(db, "standalone")
    db.add(Location(tenant_id=tenant.id, name="Pier", timezone="UTC"))
    db.commit()
    monkeypatch.setattr("app.billing._mode", lambda db, org, place: "standalone")

    assert sync(db, org) == {"changed": True, "plan": "complete"}, "two locations need Complete"


# --- Stripe: quantity and price, prorated --------------------------------------------------------


class FakeStripe:
    """Records what was asked of Stripe."""

    def __init__(self):
        self.modified: list = []
        self.prices: dict = {}
        outer = self

        class Subscription:
            @staticmethod
            def modify(subscription_id, items, proration_behavior):
                outer.modified.append((subscription_id, items, proration_behavior))

            @staticmethod
            def retrieve(subscription_id):
                return {"id": subscription_id, "items": {"data": [{"id": "si_found"}]}}

        class Price:
            @staticmethod
            def retrieve(price_id):
                return outer.prices[price_id]

        self.Subscription = Subscription
        self.Price = Price


@pytest.fixture
def stripe(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setattr("app.billing.stripe_client", lambda: fake)
    return fake


def _subscribed(db, tenant, quantity=1, item="si_1"):
    plan_by_code(db, "complete").provider_price_ref = "price_complete"
    plan_by_code(db, "standalone").provider_price_ref = "price_standalone"
    tenant.billing_plan = plan_by_code(db, "complete")
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    tenant.stripe_subscription_id, tenant.stripe_subscription_item_id = "sub_1", item
    tenant.billed_quantity = quantity
    db.commit()


def test_a_second_connected_location_is_billed_and_prorated(db, tenant, monkeypatch, stripe):
    org = OrgContext.system(tenant.id)
    _subscribed(db, tenant)
    db.add(Location(tenant_id=tenant.id, name="Pier", timezone="UTC"))
    db.commit()
    monkeypatch.setattr("app.billing._mode", lambda db, org, place: "connected")

    assert sync(db, org) == {"changed": True, "plan": "complete", "quantity": 2}
    assert stripe.modified == [
        ("sub_1", [{"id": "si_1", "price": "price_complete", "quantity": 2}], "create_prorations")
    ]
    assert plan_of(tenant).code == "complete", "Stripe's webhook applies the change, not us"

    tenant.billed_quantity = 2  # what the webhook then reports
    assert sync(db, org) == {"changed": False}
    assert len(stripe.modified) == 1, "a repeat asks nothing of Stripe"


def test_leaving_square_moves_a_subscription_to_standalone_at_stripe(
    db, tenant, monkeypatch, stripe
):
    org = OrgContext.system(tenant.id)
    _subscribed(db, tenant, item=None)
    monkeypatch.setattr("app.billing._mode", lambda db, org, place: "standalone")

    sync(db, org, "cutover")
    assert stripe.modified == [
        (
            "sub_1",
            [{"id": "si_found", "price": "price_standalone", "quantity": 1}],
            "create_prorations",
        )
    ]
    assert tenant.stripe_subscription_item_id == "si_found"


def test_stripes_webhook_records_the_item_and_its_quantity(db, tenant):
    _subscribed(db, tenant, quantity=None, item=None)
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    subscription = {
        "id": "sub_1",
        "customer": "cus_1",
        "status": "active",
        "items": {"data": [{"id": "si_9", "quantity": 3, "price": {"id": "price_complete"}}]},
    }
    handle_event(
        db,
        {
            "id": "evt_1",
            "type": "customer.subscription.updated",
            "created": int(datetime.now(UTC).timestamp()),
            "data": {"object": subscription},
        },
    )
    db.commit()
    assert (tenant.stripe_subscription_item_id, tenant.billed_quantity) == ("si_9", 3)


def test_a_plan_is_pointed_at_a_stripe_price_and_shows_it(db, stripe):
    """AC-SAAS-12: a new price is configuration; new checkouts take it, no deployment."""
    stripe.prices["price_new"] = {
        "id": "price_new",
        "active": True,
        "unit_amount": 9900,
        "currency": "usd",
        "recurring": {"interval": "month"},
    }
    stripe.prices["price_old"] = {"id": "price_old", "active": False, "recurring": {}}
    plan = set_price(db, plan_by_code(db, "complete"), "price_new")
    assert (plan.provider_price_ref, plan.display_amount_cents, plan.display_currency) == (
        "price_new",
        9900,
        "USD",
    )
    with pytest.raises(ValueError, match="not an active recurring price"):
        set_price(db, plan, "price_old")


# --- checkout and the pricing page --------------------------------------------------------------


class FakeCheckout:
    def __init__(self):
        self.created: dict = {}
        outer = self

        class Session:
            @staticmethod
            def create(**kwargs):
                outer.created = kwargs
                return type("S", (), {"url": "https://checkout.stripe.test/s"})()

        self.checkout = type("C", (), {"Session": Session})
        self.api_key = None


@pytest.fixture
def signed_in(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x", raising=False)
    fake = FakeCheckout()
    monkeypatch.setitem(__import__("sys").modules, "stripe", fake)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        plan_by_code(db, "complete").provider_price_ref = "price_complete"
        plan_by_code(db, "standalone").provider_price_ref = "price_standalone"
        db.commit()
    client = TestClient(app)
    client.__enter__()
    client.post(
        "/signup",
        data={"business_name": "Pier", "email": "o@pier.test", "password": "a-good-password"},
    )
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
        for name in ("Main", "Harbour"):
            db.add(Location(tenant_id=tenant.id, name=name, timezone="UTC"))
        db.commit()
    yield client, fake
    client.__exit__(None, None, None)


def test_checkout_bills_complete_per_connected_location(signed_in, monkeypatch):
    client, fake = signed_in
    monkeypatch.setattr("app.billing._mode", lambda db, org, place: "connected")
    response = client.post("/billing/checkout", data={"plan": "complete"}, follow_redirects=False)
    assert response.status_code == 303
    assert fake.created["line_items"] == [{"price": "price_complete", "quantity": 2}]


def test_checkout_refuses_a_plan_that_does_not_fit(signed_in, monkeypatch):
    client, _fake = signed_in
    monkeypatch.setattr("app.billing._mode", lambda db, org, place: "standalone")
    response = client.post("/billing/checkout", data={"plan": "standalone"})
    assert response.status_code == 400
    assert "covers 1 location; you have 2" in response.text


def test_adding_a_location_queues_a_billing_sync(signed_in):
    client, _fake = signed_in
    client.post(
        "/app/locations/new",
        data={"name": "Market", "timezone": "UTC", "currency": "USD"},
        follow_redirects=False,
    )
    with SessionLocal() as db:
        kinds = [job.kind for job in db.scalars(select(Job))]
    assert "sync_subscription" in kinds


def test_the_pricing_page_reads_the_catalogue():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        plan = plan_by_code(db, "complete")
        plan.display_amount_cents, plan.display_currency, plan.display_interval = (
            9900,
            "USD",
            "month",
        )
        db.commit()
    with TestClient(app) as client:
        page = client.get("/pricing").text
        landing = client.get("/").text
    assert "Vontry Complete" in page and "Vontry Standalone" in page
    assert "$99.00" in page and "per location a month" in page
    assert "Price announced at launch" in page, "Standalone names no price yet"
    for retired in ("Starter", "Pro</", "setup", "$149", "$29", "$59"):
        assert retired not in page and retired not in landing, retired
