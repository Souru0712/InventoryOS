"""Stripe's events applied to an account: every state in the lifecycle, in the wrong order too."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.entitlements import Access, access, allows, effective_plan
from app.main import app
from app.models import Plan, SubscriptionStatus, Tenant
from app.orchestration.stripe_events import handle_event, plan_for_price, price_for_plan

STARTER_PRICE, PRO_PRICE = "price_starter_29", "price_pro_59"
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def prices(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_price_starter", STARTER_PRICE, raising=False)
    monkeypatch.setattr(settings, "stripe_price_pro", PRO_PRICE, raising=False)
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x", raising=False)
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_x", raising=False)
    return settings


def stamp(moment: datetime) -> int:
    return int(moment.timestamp())


def subscription(plan_price: str, status: str = "active", **fields) -> dict:
    return {
        "id": fields.pop("id", "sub_1"),
        "customer": fields.pop("customer", "cus_1"),
        "status": status,
        "current_period_start": stamp(NOW),
        "current_period_end": stamp(NOW + timedelta(days=30)),
        "items": {"data": [{"price": {"id": plan_price}}]},
        **fields,
    }


def event(kind: str, obj: dict, created: datetime = NOW) -> dict:
    return {"id": f"evt_{kind}", "type": kind, "created": stamp(created), "data": {"object": obj}}


# --- prices are ours, not the browser's ----------------------------------------------------------


def test_a_plan_maps_to_the_configured_price_and_back():
    assert price_for_plan("starter") == STARTER_PRICE
    assert price_for_plan("pro") == PRO_PRICE
    assert plan_for_price(PRO_PRICE) == "pro"
    assert price_for_plan("enterprise") is None


def test_a_price_we_do_not_sell_is_not_turned_into_a_plan():
    assert plan_for_price("price_somebody_elses") is None


def test_the_checkout_form_cannot_name_its_own_price(db, tenant, client_for):
    """A posted plan is a key into our table, never a price id we would pass to Stripe."""
    client = client_for(SubscriptionStatus.TRIALING)
    response = client.post("/billing/checkout", data={"plan": "price_pro_59"})
    assert response.status_code == 400


# --- the lifecycle, event by event ---------------------------------------------------------------


def test_checkout_completing_records_who_they_are(db, tenant):
    result = handle_event(
        db,
        event(
            "checkout.session.completed",
            {
                "id": "cs_1",
                "customer": "cus_1",
                "subscription": "sub_1",
                "client_reference_id": str(tenant.id),
                "metadata": {"tenant_id": str(tenant.id), "plan": "starter"},
            },
        ),
    )
    db.commit()

    assert result["tenant_id"] == tenant.id
    assert tenant.stripe_customer_id == "cus_1"
    assert tenant.stripe_subscription_id == "sub_1"
    assert tenant.subscription_status is SubscriptionStatus.ACTIVE
    assert effective_plan(tenant) == "starter"


def test_a_created_subscription_grants_the_plan_it_paid_for(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()

    handle_event(db, event("customer.subscription.created", subscription(PRO_PRICE)))
    db.commit()

    assert tenant.subscription_status is SubscriptionStatus.ACTIVE
    assert effective_plan(tenant) == "pro"
    assert tenant.current_period_end == NOW + timedelta(days=30)
    assert allows(tenant, "reorder_csv", NOW) is True


def test_an_upgrade_in_stripe_is_an_upgrade_here(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, event("customer.subscription.created", subscription(STARTER_PRICE)))
    db.commit()
    assert allows(tenant, "reorder_csv", NOW) is False

    handle_event(
        db,
        event(
            "customer.subscription.updated",
            subscription(PRO_PRICE),
            created=NOW + timedelta(minutes=1),
        ),
    )
    db.commit()
    assert effective_plan(tenant) == "pro" and allows(tenant, "reorder_csv", NOW) is True


def test_a_downgrade_takes_the_pro_features_back(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, event("customer.subscription.created", subscription(PRO_PRICE)))
    handle_event(
        db,
        event(
            "customer.subscription.updated",
            subscription(STARTER_PRICE),
            created=NOW + timedelta(minutes=1),
        ),
    )
    db.commit()

    assert effective_plan(tenant) == "starter"
    assert allows(tenant, "reorder_csv", NOW) is False
    assert access(tenant, NOW) is Access.FULL, "a downgrade is still a paying customer"


def test_a_failed_payment_starts_the_grace_period(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, event("customer.subscription.created", subscription(STARTER_PRICE)))
    db.commit()

    handle_event(
        db,
        event(
            "invoice.payment_failed",
            {"id": "in_1", "customer": "cus_1", "subscription": "sub_1"},
            created=NOW + timedelta(days=1),
        ),
    )
    db.commit()

    assert tenant.subscription_status is SubscriptionStatus.PAST_DUE
    assert tenant.past_due_since is not None
    assert access(tenant, NOW + timedelta(days=2)) is Access.FULL
    assert access(tenant, NOW + timedelta(days=9)) is Access.READ_ONLY


def test_a_retry_that_works_puts_them_back(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, event("customer.subscription.created", subscription(STARTER_PRICE)))
    handle_event(
        db, event("invoice.payment_failed", {"customer": "cus_1"}, created=NOW + timedelta(days=1))
    )
    db.commit()

    handle_event(
        db,
        event(
            "invoice.payment_succeeded",
            {
                "customer": "cus_1",
                "period_start": stamp(NOW),
                "period_end": stamp(NOW + timedelta(days=30)),
            },
            created=NOW + timedelta(days=2),
        ),
    )
    db.commit()

    assert tenant.subscription_status is SubscriptionStatus.ACTIVE
    assert tenant.past_due_since is None


def test_pausing_a_subscription_makes_the_account_read_only(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, event("customer.subscription.created", subscription(PRO_PRICE)))
    db.commit()

    handle_event(
        db,
        event(
            "customer.subscription.paused",
            subscription(PRO_PRICE, "paused"),
            created=NOW + timedelta(days=1),
        ),
    )
    db.commit()

    assert tenant.subscription_status is SubscriptionStatus.PAUSED
    assert access(tenant, NOW) is Access.READ_ONLY


def test_resuming_it_gives_the_plan_back(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, event("customer.subscription.created", subscription(PRO_PRICE)))
    handle_event(
        db,
        event(
            "customer.subscription.paused",
            subscription(PRO_PRICE, "paused"),
            created=NOW + timedelta(days=1),
        ),
    )
    handle_event(
        db,
        event(
            "customer.subscription.resumed",
            subscription(PRO_PRICE),
            created=NOW + timedelta(days=2),
        ),
    )
    db.commit()

    assert tenant.subscription_status is SubscriptionStatus.ACTIVE
    assert access(tenant, NOW) is Access.FULL


def test_cancelling_ends_it_and_keeps_everything_readable(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, event("customer.subscription.created", subscription(PRO_PRICE)))
    db.commit()

    handle_event(
        db,
        event(
            "customer.subscription.deleted",
            subscription(PRO_PRICE, "canceled"),
            created=NOW + timedelta(days=40),
        ),
    )
    db.commit()

    assert tenant.subscription_status is SubscriptionStatus.CANCELED
    assert access(tenant, NOW + timedelta(days=41)) is Access.READ_ONLY
    assert allows(tenant, "inventory_write", NOW) is False


# --- retries and wrong order ----------------------------------------------------------------------


def test_the_same_event_twice_changes_nothing_the_second_time(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    payload = event("customer.subscription.created", subscription(PRO_PRICE))

    handle_event(db, payload)
    db.commit()
    first = (tenant.subscription_status, tenant.plan, tenant.current_period_end)

    handle_event(db, payload)
    db.commit()
    assert (tenant.subscription_status, tenant.plan, tenant.current_period_end) == first


def test_an_older_event_arriving_late_cannot_undo_a_newer_one(db, tenant):
    """Stripe does not promise order. A stale `updated` must not revive a cancelled account."""
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, event("customer.subscription.created", subscription(PRO_PRICE)))
    handle_event(
        db,
        event(
            "customer.subscription.deleted",
            subscription(PRO_PRICE, "canceled"),
            created=NOW + timedelta(days=40),
        ),
    )
    db.commit()

    result = handle_event(
        db,
        event(
            "customer.subscription.updated",
            subscription(PRO_PRICE),
            created=NOW + timedelta(days=39),
        ),
    )
    db.commit()

    assert result["ignored"] == "stale event"
    assert tenant.subscription_status is SubscriptionStatus.CANCELED


def test_a_status_stripe_invents_is_treated_as_lapsed_not_as_paid(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(
        db, event("customer.subscription.updated", subscription(PRO_PRICE, "something_new"))
    )
    db.commit()

    assert access(tenant, NOW) is Access.READ_ONLY, "unknown means read-only, never free Pro"


def test_an_event_for_an_account_we_do_not_know_is_dropped(db):
    assert handle_event(db, event("customer.subscription.created", subscription(PRO_PRICE))) == {
        "ignored": "unknown account"
    }


def test_an_event_we_do_not_handle_is_left_alone(db, tenant):
    assert handle_event(db, event("customer.created", {"id": "cus_1"})) == {
        "ignored": "customer.created"
    }


# --- the endpoints --------------------------------------------------------------------------------


class FakeStripe:
    """Stands in for the stripe package: records what was asked of it, verifies nothing."""

    def __init__(self):
        self.api_key = None
        self.created: dict = {}
        outer = self

        class Session:
            @staticmethod
            def create(**kwargs):
                outer.created = kwargs
                return type("S", (), {"url": "https://checkout.stripe.test/session"})()

        class Portal:
            @staticmethod
            def create(**kwargs):
                outer.created = kwargs
                return type("S", (), {"url": "https://billing.stripe.test/portal"})()

        self.checkout = type("C", (), {"Session": Session})
        self.billing_portal = type("B", (), {"Session": Portal})
        self.Webhook = type(
            "W",
            (),
            {
                "construct_event": staticmethod(
                    lambda payload, signature, secret: json.loads(payload)
                )
            },
        )


@pytest.fixture
def stripe(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setitem(__import__("sys").modules, "stripe", fake)
    return fake


@pytest.fixture
def client_for(monkeypatch):
    def build(status: SubscriptionStatus, plan: str = "pro", customer: str | None = None):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        test_client = TestClient(app)
        test_client.__enter__()
        test_client.post(
            "/signup",
            data={
                "business_name": "Rosewood",
                "email": "owner@rosewood.test",
                "password": "a-good-password",
            },
        )
        with SessionLocal() as db:
            tenant = db.query(Tenant).one()
            tenant.subscription_status = status
            tenant.plan = Plan(plan)
            tenant.stripe_customer_id = customer
            db.commit()
        return test_client

    return build


def test_choosing_a_plan_in_the_app_goes_to_stripe_with_our_price(client_for, stripe):
    client = client_for(SubscriptionStatus.TRIALING)
    response = client.post("/billing/checkout", data={"plan": "pro"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://checkout.stripe.test/")
    assert stripe.created["line_items"] == [{"price": PRO_PRICE, "quantity": 1}]
    assert stripe.created["metadata"]["plan"] == "pro"
    assert stripe.created["mode"] == "subscription"


def test_a_visitor_who_picks_a_plan_signs_up_first_then_pays(stripe):
    """Account creation comes before checkout, so nothing is orphaned in Stripe."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        assert 'name="plan" value="starter"' in client.get("/signup?plan=starter").text

        page = client.post(
            "/signup",
            data={
                "business_name": "Rosewood",
                "email": "o@rosewood.test",
                "password": "a-good-password",
                "plan": "starter",
            },
        )
        assert page.status_code == 200
        assert 'action="/billing/checkout"' in page.text

        with SessionLocal() as db:
            tenant = db.query(Tenant).one()
        assert tenant.subscription_status is SubscriptionStatus.TRIALING, (
            "the trial starts either way"
        )


def test_the_portal_is_how_a_customer_cancels(client_for, stripe):
    client = client_for(SubscriptionStatus.ACTIVE, "starter", customer="cus_1")
    response = client.post("/billing/portal", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://billing.stripe.test/")
    assert stripe.created["customer"] == "cus_1"
    assert stripe.created["return_url"].endswith("/app/settings")


def test_settings_offers_the_portal_only_once_there_is_something_to_manage(client_for, stripe):
    with_subscription = client_for(SubscriptionStatus.ACTIVE, "starter", customer="cus_1")
    assert "Manage or cancel subscription" in with_subscription.get("/app/settings").text

    on_trial = client_for(SubscriptionStatus.TRIALING)
    page = on_trial.get("/app/settings").text
    assert "Manage or cancel subscription" not in page and "See the plans" in page


def test_the_webhook_applies_the_event_and_only_with_a_signature(client_for, stripe):
    client = client_for(SubscriptionStatus.TRIALING)
    with SessionLocal() as db:
        tenant_id = db.query(Tenant).one().id

    payload = event(
        "checkout.session.completed",
        {
            "customer": "cus_1",
            "subscription": "sub_1",
            "metadata": {"tenant_id": str(tenant_id), "plan": "pro"},
        },
    )
    response = client.post(
        "/billing/webhook", content=json.dumps(payload), headers={"stripe-signature": "t=1,v1=x"}
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.get(Tenant, tenant_id).subscription_status is SubscriptionStatus.ACTIVE

    def refuse(payload, signature, secret):
        raise ValueError("bad signature")

    stripe.Webhook.construct_event = staticmethod(refuse)
    assert (
        client.post(
            "/billing/webhook", content="{}", headers={"stripe-signature": "nope"}
        ).status_code
        == 400
    )
