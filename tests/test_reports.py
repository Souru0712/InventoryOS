"""T2.16: reports on ledger data with business days (ANL-FR-03, -05, -07, AC-ANL-01, -03, -05)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.costs import override_cost
from app.items import NewItem, create_item
from app.ledger.history import movement_totals
from app.ledger.posting import post_count, post_transaction, reverse_transaction
from app.models import Location, StockItem
from app.reports import consumption_report, expiry_report, variance_report
from app.transform.business_days import business_day_end, business_day_start
from app.transform.dashboard import DateRange
from app.transform.ledger import Line
from app.usage import Use, new_token, record_usage
from app.waste import ensure_reasons, record_waste
from tests.batch_helpers import dated

D = Decimal
NOW = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)
TODAY = NOW.date()
WEEK = DateRange("7d")


@pytest.fixture
def kitchen(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))

    def item(name, qty, cost):
        made = create_item(
            db,
            org,
            NewItem(
                name=name,
                location_ids=[location.id],
                dimension="count",
                display_unit="each",
                quantity=qty,
                cost_per_unit=cost,
            ),
        )
        return made

    flour = item("Flour", "100", "2")
    bread = item("Bread", "0", "5")
    db.commit()
    return org, flour, bread


def _post(db, org, location, kind, key, lines, at):
    return post_transaction(
        db,
        org,
        kind=kind,
        source_type="test",
        source_id=key,
        idempotency_key=f"test:{key}",
        lines=[Line(location.id, item_id, movement, D(qty)) for item_id, movement, qty in lines],
        occurred_at=at,
    )


def test_ac_anl_03_consumption_splits_and_matches_the_ledger(db, kitchen, location):
    org, flour, bread = kitchen
    at = NOW - timedelta(days=1)
    _post(db, org, location, "sale", "s1", [(flour.id, "sale_consumption", "-10")], at)
    undone = _post(db, org, location, "sale", "s2", [(flour.id, "sale_consumption", "-4")], at)
    reverse_transaction(db, org, undone.id, reason="corrected", occurred_at=at)
    _post(
        db,
        org,
        location,
        "production",
        "p1",
        [(flour.id, "production_input", "-20"), (bread.id, "production_output", "8")],
        at,
    )
    reason = next(r for r in ensure_reasons(db, org) if r.code != "expiration")
    record_waste(
        db,
        org,
        location_id=location.id,
        reason_id=reason.id,
        quantity="3",
        inventory_item_id=flour.id,
        now=at,
    )
    record_usage(
        db,
        org,
        location_id=location.id,
        uses=[Use(flour.id, "2")],
        reason="staff_meal",
        token=new_token(),
        now=at,
    )
    db.commit()

    report = consumption_report(db, org, WEEK, now=NOW)
    row = report.rows[(location.id, flour.id)]
    assert row.qty == {"sales": D(10), "production": D(20), "waste": D(3), "manual": D(2)}
    assert row.cents == {"sales": 2000, "production": 4000, "waste": 600, "manual": 400}
    assert (location.id, bread.id) not in report.rows  # output is not consumption

    windows = {
        location.id: (
            business_day_start(TODAY - timedelta(days=6), location.timezone, 0),
            business_day_end(TODAY, location.timezone, 0),
        )
    }
    ledger = movement_totals(
        db, org, windows, ("sale_consumption", "production_input", "waste", "manual_consumption")
    )
    assert report.totals_cents == {
        "sales": -ledger["sale_consumption"].value_cents,
        "production": -ledger["production_input"].value_cents,
        "waste": -ledger["waste"].value_cents,
        "manual": -ledger["manual_consumption"].value_cents,
    }


def test_ac_anl_05_a_movement_belongs_to_its_location_business_day(db, kitchen, location):
    org, flour, _bread = kitchen
    place = db.get(Location, location.id)
    place.timezone = "America/New_York"
    place.business_day_cutoff_hour = 4
    db.commit()
    # 02:00 in New York on the 19th is still the 18th's business day; 05:00 is the 19th's.
    late = datetime(2026, 11, 19, 7, 0, tzinfo=UTC)
    early = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    _post(db, org, location, "sale", "late", [(flour.id, "sale_consumption", "-1")], late)
    _post(db, org, location, "sale", "early", [(flour.id, "sale_consumption", "-5")], early)
    db.commit()
    today = consumption_report(db, org, DateRange("today"), now=NOW)
    assert today.rows[(location.id, flour.id)].qty["sales"] == D(5)
    yesterday = DateRange("custom", date(2026, 11, 18), date(2026, 11, 18))
    assert consumption_report(db, org, yesterday, now=NOW).rows[(location.id, flour.id)].qty[
        "sales"
    ] == D(1)


def test_count_variance_is_valued_at_the_count_and_a_later_cost_changes_nothing(
    db, kitchen, location
):
    org, flour, bread = kitchen
    at = NOW - timedelta(days=2)
    for item, counted, key in ((flour, "94", "c1"), (bread, "2", "c2")):
        post_count(
            db,
            org,
            location_id=location.id,
            inventory_item_id=item.id,
            counted=D(counted),
            source_type="count",
            source_id=key,
            idempotency_key=f"count:{key}",
            occurred_at=at,
        )
    db.commit()
    before = variance_report(db, org, WEEK, now=NOW)
    assert before.rows[(location.id, flour.id)].qty == D(-6)
    assert (before.short_cents, before.over_cents) == (-1200, 1000)

    override_cost(db, org, location_id=location.id, inventory_item_id=flour.id, cost=D(900))
    db.commit()
    after = variance_report(db, org, WEEK, now=NOW)
    assert (after.short_cents, after.over_cents) == (-1200, 1000)  # AC-ANL-01


def test_expiry_losses_and_stock_past_its_date(db, kitchen, location):
    org, flour, _bread = kitchen
    expired = next(r for r in ensure_reasons(db, org) if r.code == "expiration")
    other = next(r for r in ensure_reasons(db, org) if r.code != "expiration")
    at = NOW - timedelta(days=1)
    record_waste(
        db,
        org,
        location_id=location.id,
        reason_id=expired.id,
        quantity="5",
        inventory_item_id=flour.id,
        now=at,
    )
    record_waste(
        db,
        org,
        location_id=location.id,
        reason_id=other.id,
        quantity="1",
        inventory_item_id=flour.id,
        now=at,
    )
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == flour.id)).one()
    dated(db, stock, 10, TODAY - timedelta(days=2), cost=3)
    dated(db, stock, 10, TODAY + timedelta(days=5), cost=3)
    db.commit()

    report = expiry_report(db, org, WEEK, now=NOW)
    assert report.lost == {(location.id, flour.id): (D(5), 1000)}
    [risk] = report.at_risk
    assert (risk.remaining, risk.cents) == (D(10), 30)


def test_the_page_shows_each_report(db, kitchen, location):
    from fastapi.testclient import TestClient

    from app.main import app

    org, flour, _bread = kitchen
    _post(
        db,
        org,
        location,
        "sale",
        "s1",
        [(flour.id, "sale_consumption", "-10")],
        datetime.now(UTC) - timedelta(hours=1),
    )
    db.commit()
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        page = client.get("/app/reports", params={"range": "7d"})
        assert page.status_code == 200
        for heading in ("Consumption", "Count variance", "Expiry losses"):
            assert heading in page.text
        assert "Flour" in page.text and "$20.00" in page.text
        bad = client.get("/app/reports", params={"range": "custom", "from": "2026-11-20"})
        assert bad.status_code == 200 and "Give both" in bad.text
