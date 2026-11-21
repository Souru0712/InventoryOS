"""T3.10: switching a location between Standalone and Connected at a cutover business date
(WF-25, POS-FR-19, STD-FR-11, DR-31, AC-SAL-06)."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.daily_sales import manual_source
from app.db import Base, SessionLocal, engine
from app.items import NewItem, create_item
from app.jobs import _handlers, drain
from app.ledger.posting import balance
from app.main import app
from app.models import AuditEvent, Location, ProviderConnection, SalesTransaction, Tenant
from app.onboarding import checklist
from app.products import NewVariation, create_product
from app.recipes import NewLine, Owner, save_recipe
from app.sales import record_pos_order, record_sale
from app.sales_modes import ModeRuleError, mode, preview, switch, today_at
from app.transform.sales import SaleLine, SalesRuleError
from tests.accounts import confirm_email

D = Decimal
ZONE = "America/New_York"


@pytest.fixture
def shop(db, tenant, location):
    """A New York café tracking Square's SQ-1, its latte using 200 ml of milk."""
    location.timezone = ZONE
    location.provider, location.provider_location_id = "square", "SQ-1"
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    db.add(
        ProviderConnection(
            tenant_id=tenant.id, provider="square", merchant_id="M-1", access_token_enc="enc"
        )
    )
    milk = create_item(
        db,
        org,
        NewItem(
            name="Milk",
            location_ids=[location.id],
            dimension="volume",
            display_unit="ml",
            cost_per_unit="0.01",
        ),
    )
    size = create_product(db, org, name="Latte", variations=[NewVariation("12 oz")]).variations[0]
    save_recipe(
        db, org, Owner("variation", size.id), [NewLine(milk.id, "200", "ml")], from_the_start=True
    )
    db.commit()
    return org, location, size, milk


def _jobs():
    drain(handlers={**_handlers(), "send_alert_digest": lambda *_: None})


def _by_hand(db, org, place, size, day, qty):
    return record_sale(
        db,
        org,
        manual_source(db, org, place.id, day),
        source_reference=f"day:{day.isoformat()}",
        lines=[SaleLine("v", D(qty), size.id)],
        day=day,
    )


def _at_noon(day):
    return datetime.combine(day, time(12), ZoneInfo(ZONE)).astimezone(UTC)


def _pos(db, org, place, size, ref, qty, at):
    return record_pos_order(
        db,
        org,
        "square",
        place.id,
        reference=f"order:{ref}",
        occurred_at=at,
        lines=[SaleLine("line:A", D(qty), size.id, "VAR-LATTE", "Latte")],
    )


def _settle(db):
    db.commit()
    _jobs()
    db.expire_all()


def test_ac_sal_06_standalone_to_connected_from_a_past_cutover(db, shop):
    org, place, size, milk = shop
    today = today_at(place)
    cutover = today - timedelta(days=1)
    before = _by_hand(db, org, place, size, cutover - timedelta(days=1), 1)  # stays
    overlapping = _by_hand(db, org, place, size, cutover, 2)  # entered by hand, now replaced
    early = _pos(db, org, place, size, "o-0", 3, _at_noon(cutover - timedelta(days=1)))
    pos = _pos(db, org, place, size, "o-1", 4, _at_noon(cutover))
    _settle(db)
    assert (early.of_record, pos.of_record) == (False, False), "Square was not of record yet"
    assert balance(db, org, place.id, milk.id) == D(-600)  # the two hand days

    shown = preview(db, org, place.id, "pos_adapter", "square", cutover)
    assert shown.plan.voided == ((cutover, "manual"),) and shown.plan.counted == (cutover,)
    assert balance(db, org, place.id, milk.id) == D(-600), "a preview changes nothing"

    switch(db, org, place.id, "pos_adapter", "square", cutover)
    _settle(db)
    assert mode(db, org, place.id) == "connected"
    assert db.get(SalesTransaction, before.id).status == "completed", "prior history intact"
    assert db.get(SalesTransaction, overlapping.id).status == "voided"
    assert db.get(SalesTransaction, pos.id).of_record is True, "POS sales post from D"
    assert db.get(SalesTransaction, early.id).of_record is False, "POS before D is ignored"
    assert balance(db, org, place.id, milk.id) == D(-1000)  # 200 by hand + 800 from Square
    with pytest.raises(SalesRuleError, match="not entered by hand"):
        manual_source(db, org, place.id, today)
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "settings.sales_mode")).one()
    assert event.after["cutover"] == cutover.isoformat() and event.after["voided"] == 1


def test_connected_to_standalone_from_the_day_after_the_last_pos_day(db, shop):
    org, place, size, milk = shop
    today = today_at(place)
    switch(db, org, place.id, "pos_adapter", "square", today - timedelta(days=3))
    _pos(db, org, place, size, "o-1", 1, _at_noon(today - timedelta(days=2)))
    _settle(db)
    shown = preview(db, org, place.id, "manual")
    assert shown.cutover == today - timedelta(days=1), "the day after the last POS day"
    switch(db, org, place.id, "manual", None, shown.cutover)
    _settle(db)
    assert mode(db, org, place.id) == "standalone"
    _by_hand(db, org, place, size, shown.cutover, 2)  # hand entry works from D
    late = _pos(db, org, place, size, "o-2", 5, _at_noon(today))
    _settle(db)
    assert late.of_record is False, "a POS sale after the switch is kept, not counted"
    assert balance(db, org, place.id, milk.id) == D(-600)


def test_the_cutover_follows_the_locations_own_business_day(db, shop):
    """22:00 in New York on the day before the cutover is still the day before."""
    org, place, size, _milk = shop
    cutover = today_at(place)
    switch(db, org, place.id, "pos_adapter", "square", cutover)
    eve = datetime.combine(cutover - timedelta(days=1), time(22), ZoneInfo(ZONE)).astimezone(UTC)
    first = datetime.combine(cutover, time(1), ZoneInfo(ZONE)).astimezone(UTC)
    assert eve.date() == cutover, "in UTC it is already the cutover date"
    db.commit()
    assert _pos(db, org, place, size, "late", 1, eve).of_record is False
    assert _pos(db, org, place, size, "early", 1, first).of_record is True


def test_the_rules(db, shop):
    org, place, *_rest = shop
    today = today_at(place)
    with pytest.raises(ModeRuleError, match="within 30 days"):
        preview(db, org, place.id, "manual", None, today - timedelta(days=31))
    switch(db, org, place.id, "pos_adapter", "square", today)
    with pytest.raises(ModeRuleError, match="already takes its sales from Square"):
        preview(db, org, place.id, "pos_adapter", "square", today)
    other = Location(tenant_id=place.tenant_id, name="Kiosk", timezone="UTC")
    db.add(other)
    db.flush()
    with pytest.raises(ModeRuleError, match="tracks first"):
        preview(db, org, other.id, "pos_adapter", "square", today)


def test_onboarding_has_a_mapping_step_on_the_square_path(db, shop):
    from app.models import Onboarding

    org, *_rest = shop
    db.add(Onboarding(tenant_id=org.tenant_id, payment_answer="pos_supported", skipped=""))
    db.commit()
    keys = [step.key for step in checklist(db, org)]
    assert keys[keys.index("sales") + 1] == "mapping"


# --- the screen ----------------------------------------------------------------------------------


@pytest.fixture
def owner():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    client = TestClient(app)
    client.__enter__()
    client.post(
        "/signup",
        data={"business_name": "Corner", "email": "o@corner.test", "password": "a-good-password"},
    )
    confirm_email("o@corner.test")
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        place = Location(tenant_id=tenant.id, name="Main", timezone="UTC")
        db.add(place)
        db.commit()
        place_id = place.id
    yield client, place_id
    client.__exit__(None, None, None)


def test_the_owner_previews_then_switches_on_the_page(owner):
    client, place_id = owner
    page = client.get(f"/app/locations/{place_id}/sales-source", params={"to": "file"}).text
    assert "takes its sales from <strong>sales file imports</strong>" in page
    today = datetime.now(UTC).date().isoformat()
    done = client.post(
        f"/app/locations/{place_id}/sales-source", data={"to": "file", "cutover": today}
    )
    assert "takes its sales from sales file imports from" in done.text
    refused = client.get(f"/app/locations/{place_id}/sales-source", params={"to": "pos"}).text
    assert "Connect Square first." in refused
