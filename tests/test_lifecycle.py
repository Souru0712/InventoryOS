"""T4.03: the approved lifecycle policies (C-44; SAAS-B-03..08, SAAS-B-11, AC-SAAS-07).

A 30-day trial without a card and 7 days of grace are configuration; paying during the trial keeps
its days; Stripe's `unpaid` is read-only; a read-only business keeps reading, billing and its POS
ingestion while every write is refused.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.entitlements import Access, access, start_trial, state
from app.main import app
from app.models import Job, ProviderConnection, SubscriptionStatus, Tenant
from app.orchestration.stripe_events import handle_event
from app.plans import plan_by_code
from app.security import require_writable
from tests.test_async_correctness import api_routes

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

# Write routes a read-only business may still use, and why (SAAS §3: views, reports, export
# and billing work; POS ingestion continues; people can always sign in and secure their account).
OPEN_WHEN_READ_ONLY = {
    "/login": "signing in",
    "/login/code": "signing in",
    "/logout": "signing out",
    "/signup": "creating a business",
    "/password/forgot": "account security",
    "/password/reset": "account security",
    "/verify-email": "account security",
    "/app/verify-email/resend": "account security",
    "/invitations/accept": "joining a business",
    "/app/organizations/switch": "choosing a business",
    "/app/floor/person": "choosing who uses a shared device",
    "/app/sessions/end-all": "account security",
    "/app/settings/email": "account security",
    "/app/settings/password": "account security",
    "/app/settings/mfa/enable": "account security",
    "/app/settings/mfa/disable": "account security",
    "/app/settings/mfa/recovery-codes": "account security",
    "/app/team/{membership_id}/remove": "taking access away is never refused",
    "/app/team/invitations/{invitation_id}/withdraw": "taking access away is never refused",
    "/app/connection/disconnect": "revoking a POS's access is never refused",
    "/app/sync": "POS ingestion continues while read-only (PD-07)",
    "/webhooks/square": "POS ingestion continues while read-only (PD-07)",
    "/webhooks/resend": "email delivery reports",
    "/billing/checkout": "subscribing again (SAAS-B-08)",
    "/billing/portal": "billing",
    "/billing/webhook": "billing",
    "/app/exports": "an export works in every state (SAAS-D-02)",
}


def _calls(dependant) -> set:
    found = set()
    for dependency in dependant.dependencies:
        found.add(dependency.call)
        found |= _calls(dependency)
    return found


def test_every_write_route_is_refused_to_a_read_only_business_unless_listed():
    """AC-SAAS-07: a route that changes data depends on require_writable, or is listed above
    with its reason. A new route fails here until it chooses."""
    writes = [
        route
        for route in api_routes(app.routes)
        if set(route.methods) & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    open_routes = {r.path for r in writes if require_writable not in _calls(r.dependant)}
    assert open_routes - set(OPEN_WHEN_READ_ONLY) == set(), "guard these or list them"
    assert set(OPEN_WHEN_READ_ONLY) - open_routes == set(), "listed but guarded or gone"


# --- policies are configuration -------------------------------------------------------------------


def test_trial_and_grace_lengths_come_from_configuration(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "trial_days", 14, raising=False)
    monkeypatch.setattr(settings, "grace_days", 3, raising=False)

    tenant = start_trial(Tenant(name="Pier"), NOW)
    assert tenant.trial_ends_at == NOW + timedelta(days=14)

    tenant.subscription_status = SubscriptionStatus.PAST_DUE
    tenant.past_due_since = NOW
    assert access(tenant, NOW + timedelta(days=2)) is Access.FULL
    assert access(tenant, NOW + timedelta(days=3, minutes=1)) is Access.READ_ONLY


def test_the_approved_values_are_the_defaults():
    """C-44 (PD-05): 30 days of trial without a card, 7 days of grace."""
    settings = get_settings()
    assert (settings.trial_days, settings.grace_days) == (30, 7)


# --- Stripe's states map to access as SAAS §3 says ------------------------------------------------


def _event(status: str) -> dict:
    return {
        "id": f"evt_{status}",
        "type": "customer.subscription.updated",
        "created": int(datetime.now(UTC).timestamp()),
        "data": {
            "object": {
                "id": "sub_1",
                "customer": "cus_1",
                "status": status,
                "items": {"data": [{"id": "si_1", "quantity": 1, "price": {"id": "price_c"}}]},
            }
        },
    }


@pytest.mark.parametrize(
    "status, expected",
    [
        ("trialing", Access.FULL),
        ("active", Access.FULL),
        ("past_due", Access.FULL),  # the grace period, with a banner
        ("unpaid", Access.READ_ONLY),  # Stripe stopped retrying: read-only at once
        ("canceled", Access.READ_ONLY),
        ("incomplete_expired", Access.READ_ONLY),
        ("paused", Access.READ_ONLY),
    ],
)
def test_each_stripe_status_maps_to_its_access(db, tenant, status, expected):
    plan_by_code(db, "complete").provider_price_ref = "price_c"
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, _event(status))
    db.commit()
    assert access(tenant) is expected


def test_an_unpaid_business_is_told_to_update_its_card(db, tenant):
    tenant.stripe_customer_id = "cus_1"
    db.commit()
    handle_event(db, _event("unpaid"))
    db.commit()
    assert "Update your card" in state(tenant)["message"]


# --- read-only keeps ingestion going (PD-07) ------------------------------------------------------


def test_a_read_only_business_keeps_its_pos_ingestion(db, tenant):
    from app.orchestration.scheduler import queue_order_ingestion

    tenant.subscription_status = SubscriptionStatus.CANCELED
    db.add(
        ProviderConnection(
            tenant_id=tenant.id, provider="square", merchant_id="M-1", access_token_enc="enc"
        )
    )
    db.commit()
    queue_order_ingestion()
    db.expire_all()
    kinds = [job.kind for job in db.query(Job).filter_by(tenant_id=tenant.id)]
    assert kinds == ["ingest_orders"]


# --- paying during the trial keeps its days (C-44) ------------------------------------------------


class FakeStripe:
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
def trialing(monkeypatch):
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_test_x", raising=False)
    fake = FakeStripe()
    monkeypatch.setitem(__import__("sys").modules, "stripe", fake)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        plan_by_code(db, "complete").provider_price_ref = "price_c"
        db.commit()
    client = TestClient(app)
    client.__enter__()
    client.post(
        "/signup",
        data={"business_name": "Pier", "email": "o@pier.test", "password": "a-good-password"},
    )
    yield client, fake
    client.__exit__(None, None, None)


def _trial_ends_in(delta: timedelta) -> datetime:
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
        tenant.trial_ends_at = datetime.now(UTC).replace(microsecond=0) + delta
        db.commit()
        return tenant.trial_ends_at


def test_paying_during_the_trial_starts_billing_at_its_end(trialing):
    client, fake = trialing
    ends = _trial_ends_in(timedelta(days=20))
    client.post("/billing/checkout", data={"plan": "complete"}, follow_redirects=False)
    assert fake.created["subscription_data"]["trial_end"] == int(ends.timestamp())


def test_a_trial_nearly_over_is_billed_at_once(trialing):
    client, fake = trialing
    _trial_ends_in(timedelta(hours=30))
    client.post("/billing/checkout", data={"plan": "complete"}, follow_redirects=False)
    assert "trial_end" not in fake.created["subscription_data"]


def test_a_read_only_business_still_reads_and_can_subscribe_again(trialing):
    """AC-SAAS-07 and SAAS-B-08: pages and billing work; a write is refused."""
    client, fake = trialing
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
        tenant.subscription_status = SubscriptionStatus.EXPIRED
        db.commit()
    for page in ("/app", "/app/reports", "/app/settings", "/pricing"):
        assert client.get(page).status_code == 200, page
    assert client.post("/app/locations/new", data={"name": "Market"}).status_code == 402
    response = client.post("/billing/checkout", data={"plan": "complete"}, follow_redirects=False)
    assert response.status_code == 303
    assert "trial_end" not in fake.created["subscription_data"], "an expired trial is billed now"
