"""What the customer has paid for, decided once and enforced on the server.

The matrix at the bottom walks one account through its whole life: trial, Standalone, Complete,
back again, a failed payment, the grace period, cancellation. Plans come from the catalogue
(T4.01, ADR-0089); the core is never gated by plan (C-41, E-05).
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.entitlements import (
    Access,
    NotEntitled,
    access,
    allows,
    entitlements,
    grace_days,
    location_allowance,
    require,
    start_trial,
    state,
    trial_days,
    usage_limit,
)
from app.main import app
from app.models import (
    BillingPlan,
    Entitlement,
    Location,
    StockItem,
    SubscriptionStatus,
    Tenant,
    TrackMode,
)
from app.orchestration.subscriptions import (
    apply_location_limit,
    change_plan,
    end_subscription,
    expire_due_trials,
    mark_past_due,
    start_subscription,
)
from app.plans import DEFAULT_PLAN, INITIAL_CATALOGUE, plan_by_code
from app.units import Dimension

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def catalogue_plan(code: str) -> BillingPlan:
    """A plan as the initial catalogue defines it, outside any database."""
    entry = next(e for e in INITIAL_CATALOGUE if e["code"] == code)
    plan = BillingPlan(code=code, name=entry["name"], billing_basis=entry["billing_basis"])
    plan.entitlement = Entitlement(
        allowed_modes=list(entry["allowed_modes"]),
        max_locations=entry["max_locations"],
        capability_flags=list(entry["capability_flags"]),
        limits=dict(entry["limits"]),
    )
    return plan


def account(status: SubscriptionStatus, plan: str = "complete", **fields) -> Tenant:
    return Tenant(
        name="Rosewood", billing_plan=catalogue_plan(plan), subscription_status=status, **fields
    )


# --- status to access ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, fields, expected",
    [
        (SubscriptionStatus.ACTIVE, {}, Access.FULL),
        (SubscriptionStatus.TRIALING, {"trial_ends_at": NOW + timedelta(days=3)}, Access.FULL),
        (
            SubscriptionStatus.TRIALING,
            {"trial_ends_at": NOW - timedelta(minutes=1)},
            Access.READ_ONLY,
        ),
        (SubscriptionStatus.PAST_DUE, {"past_due_since": NOW - timedelta(days=2)}, Access.FULL),
        (
            SubscriptionStatus.PAST_DUE,
            {"past_due_since": NOW - timedelta(days=grace_days() + 1)},
            Access.READ_ONLY,
        ),
        (SubscriptionStatus.EXPIRED, {}, Access.READ_ONLY),
        (SubscriptionStatus.CANCELED, {}, Access.READ_ONLY),
        (SubscriptionStatus.PAUSED, {}, Access.READ_ONLY),
    ],
)
def test_every_subscription_state_resolves_to_full_or_read_only(status, fields, expected):
    assert access(account(status, **fields), NOW) is expected


def test_a_trial_that_ran_out_an_hour_ago_closes_without_waiting_for_a_job():
    """Access is computed from the dates, so nothing is open just because a job has not run."""
    tenant = account(SubscriptionStatus.TRIALING, trial_ends_at=NOW - timedelta(hours=1))
    assert access(tenant, NOW) is Access.READ_ONLY
    assert tenant.subscription_status is SubscriptionStatus.TRIALING, (
        "the job still owns the column"
    )


# --- plan to entitlements (C-41, E-02..05, AC-SAAS-08) ------------------------------------------


def test_a_trial_is_of_the_plan_it_chose():
    """PD-56: no bigger plan during the trial, so nothing is taken away when it converts."""
    tenant = start_trial(account(SubscriptionStatus.ACTIVE, "standalone"), NOW)
    assert tenant.subscription_status is SubscriptionStatus.TRIALING
    assert tenant.trial_ends_at == NOW + timedelta(days=trial_days())
    assert entitlements(tenant).plan == "standalone"


@pytest.mark.parametrize("plan", ["complete", "standalone"])
def test_the_core_is_never_gated_by_plan(plan):
    """Complete and Standalone differ in sales source and locations, never in the core (E-05)."""
    tenant = account(SubscriptionStatus.ACTIVE, plan)
    for question in ("inventory_write", "add_location", "notifications"):
        assert allows(tenant, question, NOW) is True, (plan, question)
    assert entitlements(tenant).capability_flags == frozenset(), "no Advanced flags at launch"


def test_standalone_covers_one_location_and_complete_as_many_as_they_have():
    assert location_allowance(account(SubscriptionStatus.ACTIVE, "standalone")) == 1
    assert location_allowance(account(SubscriptionStatus.ACTIVE, "complete")) is None


def test_each_plan_names_its_sales_modes():
    standalone = entitlements(account(SubscriptionStatus.ACTIVE, "standalone"))
    complete = entitlements(account(SubscriptionStatus.ACTIVE, "complete"))
    assert standalone.allowed_modes == {"standalone"}
    assert complete.allowed_modes == {"connected", "standalone"}


def test_a_capability_beyond_the_core_is_whatever_the_plan_lists():
    tenant = account(SubscriptionStatus.ACTIVE, "complete")
    assert allows(tenant, "forecasting", NOW) is False
    tenant.billing_plan.entitlement.capability_flags = ["forecasting"]
    assert allows(tenant, "forecasting", NOW) is True, "configuration alone grants it"


def test_configuration_alone_changes_what_a_plan_allows(db, tenant):
    """AC-SAAS-08: editing the entitlement row, with no code change, moves the limits."""
    tenant.billing_plan = plan_by_code(db, "standalone")
    db.commit()
    assert location_allowance(tenant) == 1

    plan_by_code(db, "standalone").entitlement.max_locations = 3
    plan_by_code(db, "standalone").entitlement.limits = {"invoice_extractions_per_day": 5}
    db.commit()
    assert location_allowance(tenant) == 3
    assert usage_limit(tenant, "invoice_extractions_per_day") == 5


def test_a_business_made_without_a_plan_has_the_default_plans_entitlements(db, tenant):
    assert tenant.plan_id is None
    assert entitlements(tenant).plan == DEFAULT_PLAN


def test_a_schema_built_from_the_models_starts_with_the_catalogue(db):
    assert {plan.code for plan in db.query(BillingPlan)} == {"complete", "standalone"}
    assert all(plan.entitlement is not None for plan in db.query(BillingPlan))


def test_a_lapsed_account_cannot_write_but_keeps_reading():
    lapsed = account(SubscriptionStatus.EXPIRED, "complete")
    assert allows(lapsed, "inventory_write", NOW) is False
    assert allows(lapsed, "add_location", NOW) is False, "writing is writing, whatever the plan"
    # Nothing here hides a page: reading is never gated.
    assert access(lapsed, NOW) is Access.READ_ONLY


def test_requiring_something_the_plan_lacks_says_so():
    with pytest.raises(NotEntitled, match="not part of your plan"):
        require(account(SubscriptionStatus.ACTIVE, "standalone"), "forecasting", NOW)


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
        db.add(
            Location(
                tenant_id=tenant.id,
                provider="square",
                provider_location_id=f"LOC-{index}",
                name=f"Shop {index}",
            )
        )
    db.commit()
    return tenant


def test_standalone_tracks_one_location_and_keeps_the_others_on_record(db, three_locations):
    tenant = three_locations
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    tenant.billing_plan = plan_by_code(db, "standalone")
    db.commit()

    apply_location_limit(db, tenant)
    db.commit()

    active = db.query(Location).filter(Location.is_active.is_(True)).all()
    assert len(active) == 1, "Standalone covers one location"
    assert db.query(Location).count() == 4, "nothing is deleted (E-07); the rest wait"


def test_moving_to_complete_turns_the_other_locations_back_on(db, three_locations):
    tenant = three_locations
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    change_plan(db, tenant, "standalone")
    db.commit()
    assert db.query(Location).filter(Location.is_active.is_(True)).count() == 1

    change_plan(db, tenant, "complete")
    db.commit()
    assert db.query(Location).filter(Location.is_active.is_(True)).count() == 4


def test_a_plan_the_catalogue_does_not_have_is_refused(db, tenant):
    with pytest.raises(ValueError, match="no plan 'pro'"):
        change_plan(db, tenant, "pro")


# --- time passing ---------------------------------------------------------------------------------


def test_the_nightly_job_ends_trials_that_are_over(db, tenant):
    start_trial(tenant, NOW - timedelta(days=trial_days() + 1))
    db.commit()

    assert expire_due_trials(db, NOW) == [tenant.id]
    db.commit()

    assert tenant.subscription_status is SubscriptionStatus.EXPIRED
    assert entitlements(tenant).plan == DEFAULT_PLAN, "the plan stays; access is what closes"


def test_the_job_leaves_a_running_trial_alone(db, tenant):
    start_trial(tenant, NOW - timedelta(days=2))
    db.commit()

    assert expire_due_trials(db, NOW) == []
    assert tenant.subscription_status is SubscriptionStatus.TRIALING


# --- the whole life of an account -----------------------------------------------------------------


def test_the_complete_lifecycle(db, tenant):
    """Trial, Standalone, Complete, back, failed payment, grace, cancellation, read-only."""
    tenant.billing_plan = plan_by_code(db, "standalone")
    start_trial(tenant, NOW - timedelta(days=1))
    db.commit()
    assert entitlements(tenant).plan == "standalone" and access(tenant, NOW) is Access.FULL

    start_subscription(db, tenant, "standalone", subscription_id="sub_1", customer_id="cus_1")
    db.commit()
    assert location_allowance(tenant) == 1
    assert tenant.stripe_subscription_id == "sub_1"

    change_plan(db, tenant, "complete")
    db.commit()
    assert location_allowance(tenant) is None

    change_plan(db, tenant, "standalone")
    db.commit()
    assert location_allowance(tenant) == 1, "a downgrade takes the locations back"

    mark_past_due(db, tenant, NOW)
    db.commit()
    assert access(tenant, NOW) is Access.FULL, "the shop keeps working while the card is retried"
    assert access(tenant, NOW + timedelta(days=grace_days() + 1)) is Access.READ_ONLY

    # A retry that also fails must not restart the grace period.
    first_failure = tenant.past_due_since
    mark_past_due(db, tenant, NOW + timedelta(days=3))
    assert tenant.past_due_since == first_failure

    start_subscription(db, tenant, "standalone")
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


def signed_up(client, status: SubscriptionStatus, plan: str = "complete") -> int:
    client.post(
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
        tenant.billing_plan = plan_by_code(db, plan)
        if status is SubscriptionStatus.TRIALING:
            tenant.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        location = Location(
            tenant_id=tenant.id, provider="demo", provider_location_id="l1", name="Main"
        )
        db.add(location)
        db.flush()
        db.add(
            StockItem(
                tenant_id=tenant.id,
                location_id=location.id,
                slug="milk",
                name="Milk",
                dimension=Dimension.COUNT,
                stock_unit="each",
                track_mode=TrackMode.MANUAL,
                qty_base=Decimal("10"),
            )
        )
        db.commit()
        return tenant.id


def test_signing_up_starts_a_trial_on_the_default_plan(client):
    client.post(
        "/signup",
        data={
            "business_name": "Rosewood",
            "email": "owner@rosewood.test",
            "password": "a-good-password",
        },
    )
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
        assert tenant.billing_plan.code == DEFAULT_PLAN
    assert tenant.subscription_status is SubscriptionStatus.TRIALING
    assert tenant.trial_ends_at is not None


def test_signing_up_for_a_plan_trials_that_plan(client):
    client.post(
        "/signup",
        data={
            "business_name": "Rosewood",
            "email": "owner@rosewood.test",
            "password": "a-good-password",
            "plan": "standalone",
        },
    )
    with SessionLocal() as db:
        assert db.query(Tenant).one().billing_plan.code == "standalone"


def test_a_lapsed_account_can_still_read_every_page(client):
    signed_up(client, SubscriptionStatus.EXPIRED, "standalone")
    for path in ("/app", "/app/expiring", "/app/receiving", "/app/health", "/app/reorder"):
        assert client.get(path).status_code == 200, path


def test_a_lapsed_account_is_refused_when_it_tries_to_change_stock(client):
    signed_up(client, SubscriptionStatus.EXPIRED, "standalone")
    response = client.post(
        "/app/expiring/label",
        data={"stock_item_id": 1, "quantity": "4", "expires_on": "2030-01-01"},
    )
    assert response.status_code == 402
    assert "lapsed" in response.text
    with SessionLocal() as db:
        from app.models import StockBatch

        assert db.query(StockBatch).count() == 0, "the refusal has to be real, not cosmetic"


def test_an_expired_trial_is_refused_the_same_way(client):
    signed_up(client, SubscriptionStatus.TRIALING, "complete")
    assert client.post("/app/items/1/count", data={"quantity": "8"}).status_code == 402


@pytest.mark.parametrize("plan", ["standalone", "complete"])
def test_every_plan_gets_the_core_features_from_the_server(client, plan):
    """PA2 retired: reorder CSV and custom reorder points are core on every plan (E-05)."""
    signed_up(client, SubscriptionStatus.ACTIVE, plan)

    assert client.get("/app/reorder.csv").status_code == 200
    assert (
        client.post(
            "/app/items/1/thresholds", data={"reorder_point": "5"}, follow_redirects=False
        ).status_code
        == 303
    )
    assert client.get("/app/health").status_code == 200


def test_a_paying_account_is_never_shown_the_banner(client):
    signed_up(client, SubscriptionStatus.ACTIVE, "complete")
    page = client.get("/app")
    assert "trial" not in page.text.lower() or "left of your trial" not in page.text


def test_the_banner_tells_a_lapsed_account_what_happened(client):
    signed_up(client, SubscriptionStatus.EXPIRED, "standalone")
    assert "still here" in client.get("/app").text


# --- prices live at the provider (AC-SAAS-12, E-01, DR-36) ----------------------------------------

PLAN_PRICE = re.compile(r"\$\s?\d+(?:\.\d{2})?\s*(?:/|per|a)\s*(?:month|mo\b|location)", re.I)


def test_no_plan_price_is_written_in_the_product():
    """A plan names its provider price; the amount is never in code or app templates. The public
    marketing pages still quote the pricing hypotheses until T4.02 reads them from the plans."""
    root = Path(__file__).resolve().parent.parent / "app"
    found = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".py", ".html", ".txt"} or "__pycache__" in path.parts:
            continue
        if path.name == "marketing.py" or "marketing" in path.relative_to(root).parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PLAN_PRICE.search(line):
                found.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    assert found == [], "\n".join(found)
