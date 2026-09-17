"""What the customer has paid for, decided once and enforced on the server.

The matrix at the bottom walks one account through its whole life: trial, Starter, Pro, downgrade,
a failed payment, the grace period, cancellation.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.entitlements import (
    GRACE_DAYS,
    TRIAL_DAYS,
    Access,
    NotEntitled,
    access,
    allows,
    effective_plan,
    history_days,
    location_allowance,
    require,
    start_trial,
    state,
)
from app.main import app
from app.models import Location, Plan, StockItem, SubscriptionStatus, Tenant, TrackMode, User
from app.orchestration.subscriptions import (
    apply_location_limit,
    change_plan,
    end_subscription,
    expire_due_trials,
    mark_past_due,
    start_subscription,
)
from app.security import hash_password
from app.units import Dimension

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def account(status: SubscriptionStatus, plan: str = "pro", **fields) -> Tenant:
    return Tenant(name="Rosewood", plan=Plan(plan), subscription_status=status, **fields)


# --- status to access ---------------------------------------------------------------------------

@pytest.mark.parametrize("status, fields, expected", [
    (SubscriptionStatus.ACTIVE, {}, Access.FULL),
    (SubscriptionStatus.TRIALING, {"trial_ends_at": NOW + timedelta(days=3)}, Access.FULL),
    (SubscriptionStatus.TRIALING, {"trial_ends_at": NOW - timedelta(minutes=1)}, Access.READ_ONLY),
    (SubscriptionStatus.PAST_DUE, {"past_due_since": NOW - timedelta(days=2)}, Access.FULL),
    (SubscriptionStatus.PAST_DUE, {"past_due_since": NOW - timedelta(days=GRACE_DAYS + 1)}, Access.READ_ONLY),
    (SubscriptionStatus.EXPIRED, {}, Access.READ_ONLY),
    (SubscriptionStatus.CANCELED, {}, Access.READ_ONLY),
    (SubscriptionStatus.PAUSED, {}, Access.READ_ONLY),
])
def test_every_subscription_state_resolves_to_full_or_read_only(status, fields, expected):
    assert access(account(status, **fields), NOW) is expected


def test_a_trial_that_ran_out_an_hour_ago_closes_without_waiting_for_a_job():
    """Access is computed from the dates, so nothing is open just because a job has not run."""
    tenant = account(SubscriptionStatus.TRIALING, trial_ends_at=NOW - timedelta(hours=1))
    assert access(tenant, NOW) is Access.READ_ONLY
    assert tenant.subscription_status is SubscriptionStatus.TRIALING, "the job still owns the column"


# --- plan to capabilities -----------------------------------------------------------------------

def test_a_trial_is_given_the_full_product():
    tenant = start_trial(Tenant(name="Rosewood"), NOW)
    assert effective_plan(tenant) == "pro"
    assert tenant.trial_ends_at == NOW + timedelta(days=TRIAL_DAYS)
    assert allows(tenant, "reorder_csv", NOW) and allows(tenant, "custom_thresholds", NOW)


@pytest.mark.parametrize("capability", ["reorder_csv", "custom_thresholds", "history_30_day"])
def test_starter_does_not_get_the_pro_features(capability):
    assert allows(account(SubscriptionStatus.ACTIVE, "starter"), capability, NOW) is False
    assert allows(account(SubscriptionStatus.ACTIVE, "pro"), capability, NOW) is True


def test_starter_covers_one_location_and_pro_covers_as_many_as_they_have():
    assert location_allowance(account(SubscriptionStatus.ACTIVE, "starter")) == 1
    assert location_allowance(account(SubscriptionStatus.ACTIVE, "pro")) is None


def test_starter_sees_todays_score_and_pro_sees_the_month_behind_it():
    assert history_days(account(SubscriptionStatus.ACTIVE, "starter")) == 0
    assert history_days(account(SubscriptionStatus.ACTIVE, "pro")) == 30


def test_a_lapsed_pro_account_cannot_write_but_keeps_reading():
    lapsed = account(SubscriptionStatus.EXPIRED, "pro")
    assert allows(lapsed, "inventory_write", NOW) is False
    assert allows(lapsed, "custom_thresholds", NOW) is False, "writing is writing, whatever the plan"
    # Nothing here hides a page: reading is never gated.
    assert access(lapsed, NOW) is Access.READ_ONLY


def test_requiring_something_the_plan_lacks_says_what_to_do():
    with pytest.raises(NotEntitled, match="part of Pro"):
        require(account(SubscriptionStatus.ACTIVE, "starter"), "reorder_csv", NOW)


# --- what the page says --------------------------------------------------------------------------

def test_the_banner_stays_quiet_early_in_a_trial_and_speaks_near_the_end():
    early = account(SubscriptionStatus.TRIALING, trial_ends_at=NOW + timedelta(days=20))
    late = account(SubscriptionStatus.TRIALING, trial_ends_at=NOW + timedelta(days=3))

    assert state(early, NOW)["message"] is None
    assert "3 days left" in state(late, NOW)["message"]


def test_a_failed_payment_says_how_long_is_left_to_fix_it():
    tenant = account(SubscriptionStatus.PAST_DUE, past_due_since=NOW - timedelta(days=2))
    message = state(tenant, NOW)["message"]
    assert "did not go through" in message and "5 days" in message


def test_a_lapsed_account_is_told_its_data_is_still_there():
    message = state(account(SubscriptionStatus.EXPIRED), NOW)["message"]
    assert "still here" in message and "syncing" in message


# --- locations ------------------------------------------------------------------------------------

@pytest.fixture
def three_locations(db, tenant):
    for index in range(1, 4):
        db.add(Location(tenant_id=tenant.id, provider="square",
                        provider_location_id=f"LOC-{index}", name=f"Shop {index}"))
    db.commit()
    return tenant


def test_starter_tracks_one_location_and_keeps_the_others_on_record(db, three_locations):
    tenant = three_locations
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    tenant.plan = Plan.STARTER
    db.commit()

    apply_location_limit(db, tenant)
    db.commit()

    active = db.query(Location).filter(Location.is_active.is_(True)).all()
    assert len(active) == 1, "Starter covers one location"
    assert db.query(Location).count() == 4, "nothing is deleted; the rest wait for an upgrade"


def test_upgrading_turns_the_other_locations_back_on(db, three_locations):
    tenant = three_locations
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    change_plan(db, tenant, "starter")
    db.commit()
    assert db.query(Location).filter(Location.is_active.is_(True)).count() == 1

    change_plan(db, tenant, "pro")
    db.commit()
    assert db.query(Location).filter(Location.is_active.is_(True)).count() == 4


# --- time passing ---------------------------------------------------------------------------------

def test_the_nightly_job_ends_trials_that_are_over(db, tenant):
    start_trial(tenant, NOW - timedelta(days=TRIAL_DAYS + 1))
    db.commit()

    assert expire_due_trials(db, NOW) == [tenant.id]
    db.commit()

    assert tenant.subscription_status is SubscriptionStatus.EXPIRED
    assert tenant.plan is Plan.STARTER, "they fall back to the floor, not to nothing"


def test_the_job_leaves_a_running_trial_alone(db, tenant):
    start_trial(tenant, NOW - timedelta(days=2))
    db.commit()

    assert expire_due_trials(db, NOW) == []
    assert tenant.subscription_status is SubscriptionStatus.TRIALING


# --- the whole life of an account -----------------------------------------------------------------

def test_the_complete_lifecycle(db, tenant):
    """Trial, Starter, Pro, downgrade, failed payment, grace, cancellation, read-only."""
    start_trial(tenant, NOW - timedelta(days=1))
    db.commit()
    assert effective_plan(tenant) == "pro" and access(tenant, NOW) is Access.FULL

    start_subscription(db, tenant, "starter", subscription_id="sub_1", customer_id="cus_1")
    db.commit()
    assert effective_plan(tenant) == "starter"
    assert allows(tenant, "reorder_csv", NOW) is False
    assert tenant.stripe_subscription_id == "sub_1"

    change_plan(db, tenant, "pro")
    db.commit()
    assert allows(tenant, "reorder_csv", NOW) is True

    change_plan(db, tenant, "starter")
    db.commit()
    assert allows(tenant, "reorder_csv", NOW) is False, "a downgrade takes the feature back"

    mark_past_due(db, tenant, NOW)
    db.commit()
    assert access(tenant, NOW) is Access.FULL, "the shop keeps working while the card is retried"
    assert access(tenant, NOW + timedelta(days=GRACE_DAYS + 1)) is Access.READ_ONLY

    # A retry that also fails must not restart the grace period.
    first_failure = tenant.past_due_since
    mark_past_due(db, tenant, NOW + timedelta(days=3))
    assert tenant.past_due_since == first_failure

    start_subscription(db, tenant, "starter")
    db.commit()
    assert access(tenant, NOW) is Access.FULL and tenant.past_due_since is None

    end_subscription(db, tenant)
    db.commit()
    assert access(tenant, NOW) is Access.READ_ONLY
    assert allows(tenant, "inventory_write", NOW) is False


# --- enforced where it counts: on the server ------------------------------------------------------

@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def signed_up(client, status: SubscriptionStatus, plan: str = "pro") -> int:
    client.post("/signup", data={"business_name": "Rosewood", "email": "owner@rosewood.test",
                                 "password": "a-good-password"})
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
        tenant.subscription_status = status
        tenant.plan = Plan(plan)
        if status is SubscriptionStatus.TRIALING:
            tenant.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
        location = Location(tenant_id=tenant.id, provider="demo", provider_location_id="l1",
                            name="Main")
        db.add(location)
        db.flush()
        db.add(StockItem(tenant_id=tenant.id, location_id=location.id, slug="milk", name="Milk",
                         dimension=Dimension.COUNT, stock_unit="each", track_mode=TrackMode.MANUAL,
                         qty_base=Decimal("10")))
        db.commit()
        return tenant.id


def test_signing_up_starts_a_trial_on_the_full_product(client):
    client.post("/signup", data={"business_name": "Rosewood", "email": "owner@rosewood.test",
                                 "password": "a-good-password"})
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
    assert tenant.subscription_status is SubscriptionStatus.TRIALING
    assert effective_plan(tenant) == "pro"
    assert tenant.trial_ends_at is not None


def test_a_lapsed_account_can_still_read_every_page(client):
    signed_up(client, SubscriptionStatus.EXPIRED, "starter")
    for path in ("/app", "/app/expiring", "/app/receiving", "/app/health", "/app/reorder"):
        assert client.get(path).status_code == 200, path


def test_a_lapsed_account_is_refused_when_it_tries_to_change_stock(client):
    signed_up(client, SubscriptionStatus.EXPIRED, "starter")
    response = client.post("/app/expiring/label",
                           data={"stock_item_id": 1, "quantity": "4", "expires_on": "2030-01-01"})
    assert response.status_code == 402
    assert "lapsed" in response.text
    with SessionLocal() as db:
        from app.models import StockBatch
        assert db.query(StockBatch).count() == 0, "the refusal has to be real, not cosmetic"


def test_an_expired_trial_is_refused_the_same_way(client):
    signed_up(client, SubscriptionStatus.TRIALING, "pro")
    assert client.post("/app/items/1/count", data={"quantity": "8"}).status_code == 402


def test_starter_is_refused_the_pro_features_by_the_server(client):
    signed_up(client, SubscriptionStatus.ACTIVE, "starter")

    assert client.get("/app/reorder.csv").status_code == 402
    thresholds = client.post("/app/items/1/thresholds", data={"reorder_point": "5"})
    assert thresholds.status_code == 402
    assert "Pro" in thresholds.text


def test_pro_gets_them(client):
    signed_up(client, SubscriptionStatus.ACTIVE, "pro")

    assert client.get("/app/reorder.csv").status_code == 200
    assert client.post("/app/items/1/thresholds", data={"reorder_point": "5"},
                       follow_redirects=False).status_code == 303


def test_a_paying_account_is_never_shown_the_banner(client):
    signed_up(client, SubscriptionStatus.ACTIVE, "pro")
    page = client.get("/app")
    assert "trial" not in page.text.lower() or "left of your trial" not in page.text


def test_the_banner_tells_a_lapsed_account_what_happened(client):
    signed_up(client, SubscriptionStatus.EXPIRED, "starter")
    assert "still here" in client.get("/app").text
