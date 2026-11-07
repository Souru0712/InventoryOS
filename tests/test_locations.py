"""T1.04: locations without a POS (ADM-FR-02, DM-3), the business profile, and local "today"."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.db import Base, SessionLocal, engine
from app.load.upsert import upsert_locations
from app.main import app
from app.models import (
    AlertKind,
    Location,
    Plan,
    StockBatch,
    StockItem,
    SubscriptionStatus,
    Tenant,
)
from app.orchestration.alerts import all_proposals
from app.orchestration.subscriptions import change_plan
from app.orchestration.views import expiry_positions
from app.units import Dimension

LA = "America/Los_Angeles"


@pytest.fixture
def client():
    """Signed in as the owner of a business that has never connected Square."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        test_client.post(
            "/signup",
            data={
                "business_name": "Corner Smoothies",
                "email": "owner@corner.test",
                "password": "a-good-password",
            },
        )
        yield test_client


def _tenant() -> Tenant:
    with SessionLocal() as db:
        return db.scalars(select(Tenant)).one()


def _location_form(**changes) -> dict:
    form = {
        "name": "Main street",
        "address": "12 Main St",
        "timezone": LA,
        "currency": "usd",
        "business_day_cutoff_hour": "4",
        "open_days": ["mon", "tue", "wed", "thu", "fri", "sat"],
    }
    form.update(changes)
    return form


def _add(client, **changes):
    return client.post("/app/locations/new", data=_location_form(**changes), follow_redirects=False)


# --- a business without Square ------------------------------------------------------------------


def test_a_business_without_square_adds_a_location_then_its_stock(client):
    """Probe PA1 inverted: nothing used to create a location except the Square sync."""
    assert _add(client).status_code == 303

    with SessionLocal() as db:
        location = db.scalars(select(Location)).one()
        assert (location.provider, location.provider_location_id) == (None, None)
        assert location.timezone == LA and location.currency == "USD"
        assert location.address == "12 Main St"
        assert location.business_day_cutoff_hour == 4
        assert location.open_days == 0b0111111  # Monday to Saturday

    created = client.post(
        "/app/items/new",
        data={
            "name": "Mango",
            "location_id": str(location.id),
            "dimension": "weight",
            "stock_unit": "g",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with SessionLocal() as db:
        assert db.scalars(select(StockItem)).one().location_id == location.id


def test_the_item_form_says_to_add_a_location_first(client):
    page = client.get("/app/items/new")
    assert "/app/locations/new" in page.text


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": " "}, "name"),
        ({"timezone": "Pacific Time"}, "not a time zone"),
        ({"currency": "dollars"}, "three-letter"),
        ({"business_day_cutoff_hour": "24"}, "between midnight"),
        ({"open_days": ["mon", "funday"]}, "days the location opens"),
    ],
)
def test_a_location_the_form_cannot_trust_is_refused_with_a_reason(client, changes, message):
    response = _add(client, **changes)
    assert response.status_code == 400
    assert message in response.text
    with SessionLocal() as db:
        assert db.scalars(select(Location)).first() is None


def test_editing_a_location(client):
    _add(client)
    with SessionLocal() as db:
        location_id = db.scalars(select(Location)).one().id
    assert client.get(f"/app/locations/{location_id}").status_code == 200

    saved = client.post(
        f"/app/locations/{location_id}",
        data=_location_form(name="Harbour", timezone="Europe/London", open_days=["sat", "sun"]),
        follow_redirects=False,
    )
    assert saved.status_code == 303
    with SessionLocal() as db:
        location = db.get(Location, location_id)
        assert (location.name, location.timezone, location.open_days) == (
            "Harbour",
            "Europe/London",
            0b1100000,
        )


def test_square_keeps_the_fields_it_owns_and_the_owner_keeps_the_rest(client):
    tenant = _tenant()
    with SessionLocal() as db:
        location = Location(
            tenant_id=tenant.id,
            provider="square",
            provider_location_id="SQ-1",
            name="Square name",
            timezone="America/Chicago",
        )
        db.add(location)
        db.commit()
        location_id = location.id

    client.post(
        f"/app/locations/{location_id}",
        data=_location_form(name="Renamed", timezone=LA, currency="EUR", address="5 Pier"),
    )
    with SessionLocal() as db:
        location = db.get(Location, location_id)
        assert (location.name, location.timezone, location.currency) == (
            "Square name",
            "America/Chicago",
            "USD",
        )
        assert location.address == "5 Pier" and location.business_day_cutoff_hour == 4


# --- archiving and the plan's allowance ---------------------------------------------------------


def _starter(tenant_id: int) -> None:
    with SessionLocal() as db:
        tenant = db.get(Tenant, tenant_id)
        tenant.subscription_status = SubscriptionStatus.ACTIVE
        tenant.plan = Plan.STARTER
        db.commit()


def test_archiving_keeps_the_location_and_its_stock_on_record(client):
    _add(client)
    with SessionLocal() as db:
        location_id = db.scalars(select(Location)).one().id

    assert client.post(f"/app/locations/{location_id}/archive").status_code == 200
    with SessionLocal() as db:
        location = db.get(Location, location_id)
        assert location.archived_at is not None and location.is_active is False

    client.post(f"/app/locations/{location_id}/restore")
    with SessionLocal() as db:
        location = db.get(Location, location_id)
        assert location.archived_at is None and location.is_active is True


def test_the_plan_allowance_holds_for_new_and_restored_locations(client):
    _add(client)
    _starter(_tenant().id)  # Starter covers one location

    refused = _add(client, name="Second")
    assert refused.status_code == 400
    assert "covers 1 location" in refused.text

    with SessionLocal() as db:
        first = db.scalars(select(Location)).one().id
    client.post(f"/app/locations/{first}/archive")
    assert _add(client, name="Second").status_code == 303  # archiving made room

    restore = client.post(f"/app/locations/{first}/restore", follow_redirects=False)
    assert restore.headers["location"].endswith("error=limit")
    with SessionLocal() as db:
        assert db.get(Location, first).archived_at is not None


def test_an_archived_location_stays_off_whatever_square_or_the_plan_says(db, tenant):
    location = db.scalars(select(Location).where(Location.tenant_id == tenant.id)).one()
    location.provider, location.provider_location_id = "square", "SQ-9"
    location.archived_at = datetime.now(UTC)
    location.is_active = False
    db.commit()

    row = {"provider_location_id": "SQ-9", "name": "Main", "timezone": "UTC", "currency": "USD"}
    upsert_locations(db, tenant.id, [row | {"is_active": True}])
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    change_plan(db, tenant, "pro")  # an upgrade turns covered locations back on
    db.commit()
    db.expire_all()
    assert db.get(Location, location.id).is_active is False


# --- the business profile -----------------------------------------------------------------------


def test_the_business_profile_is_saved(client):
    saved = client.post(
        "/app/settings/business",
        data={
            "business_name": "Corner Smoothies Ltd",
            "address": "1 Corner",
            "contact_email": "Books@Corner.test",
        },
        follow_redirects=False,
    )
    assert saved.headers["location"].endswith("saved=business")
    tenant = _tenant()
    assert (tenant.name, tenant.address, tenant.contact_email) == (
        "Corner Smoothies Ltd",
        "1 Corner",
        "books@corner.test",
    )


@pytest.mark.parametrize(
    ("form", "error"),
    [({"business_name": " "}, "name"), ({"business_name": "X", "contact_email": "nope"}, "email")],
)
def test_a_business_profile_it_cannot_use_is_refused(client, form, error):
    response = client.post("/app/settings/business", data=form, follow_redirects=False)
    assert response.headers["location"].endswith(f"error={error}")
    assert _tenant().name == "Corner Smoothies"


# --- AC-ADM-01: "today" follows the location's clock --------------------------------------------


def _dated_lot(db, tenant, location, expires_on: date) -> StockItem:
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug=f"milk-{location.id}",
        name="Milk",
        dimension=Dimension.COUNT,
        stock_unit="each",
        qty_base=Decimal("6"),
    )
    db.add(item)
    db.flush()
    db.add(
        StockBatch(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            received_qty_base=Decimal("6"),
            expires_on=expires_on,
        )
    )
    db.commit()
    return item


def test_a_lot_dated_today_in_los_angeles_is_not_expired_by_utc_midnight(db, tenant, location):
    """AC-ADM-01. 03:00 UTC on the 6th is still the evening of the 5th in Los Angeles."""
    now = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)
    los_angeles = Location(
        tenant_id=tenant.id, provider=None, provider_location_id=None, name="LA", timezone=LA
    )
    db.add(los_angeles)
    db.flush()
    in_utc = _dated_lot(db, tenant, location, date(2026, 11, 5))
    in_la = _dated_lot(db, tenant, los_angeles, date(2026, 11, 5))
    org = OrgContext.system(tenant.id)

    expired = {
        position.item.id: position.positions[0].expired
        for position in expiry_positions(db, org, now=now)
    }
    assert expired == {in_utc.id: True, in_la.id: False}

    expired_alerts = {
        proposal.stock_item_id
        for proposal in all_proposals(db, org, now)
        if proposal.kind == AlertKind.EXPIRED.value
    }
    assert in_utc.id in expired_alerts and in_la.id not in expired_alerts

    # Moving the location's clock moves its day.
    los_angeles.timezone = "UTC"
    db.commit()
    assert all(position.positions[0].expired for position in expiry_positions(db, org, now=now))


def test_the_cutoff_keeps_the_small_hours_on_the_day_before(db, tenant, location):
    location.business_day_cutoff_hour = 4
    db.commit()
    item = _dated_lot(db, tenant, location, date(2026, 11, 5))
    org = OrgContext.system(tenant.id)

    at_two = datetime(2026, 11, 6, 2, 0, tzinfo=UTC)  # before 04:00: still the 5th
    at_five = at_two + timedelta(hours=3)
    assert expiry_positions(db, org, now=at_two)[0].positions[0].expired is False
    assert expiry_positions(db, org, now=at_five)[0].positions[0].expired is True
    assert expiry_positions(db, org, now=at_two)[0].item.id == item.id


def test_a_given_date_still_applies_to_every_location(db, tenant, location):
    """Callers that pass today (reports, tests) get that date everywhere."""
    _dated_lot(db, tenant, location, date(2026, 11, 5))
    org = OrgContext.system(tenant.id)
    [position] = expiry_positions(db, org, today=date(2026, 11, 5))
    assert position.positions[0].expired is False


def test_the_location_and_settings_pages_render(client):
    _add(client)
    listing = client.get("/app/locations")
    form = client.get("/app/locations/new")
    settings = client.get("/app/settings")
    assert listing.status_code == form.status_code == settings.status_code == 200
    assert "Main street" in listing.text and "America/Los_Angeles" in listing.text
    assert "Mon, Tue, Wed, Thu, Fri, Sat" in listing.text
    assert 'list="time-zones"' in form.text and "Europe/London" in form.text
    assert 'action="/app/settings/business"' in settings.text
