"""AC-SAL-08 (STD-FR-07, WF-24, PD-54): sales-derived reports show where their sales came from
(manual, imported, POS), and how Standalone days stand: sales, confirmed no sales, closed or
missing."""

import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.daily_sales import save_day
from app.items import NewItem, create_item
from app.models import Location
from app.notify.email import render_pair
from app.orchestration.reporting import weekly_report
from app.products import NewVariation, create_product
from app.recipes import NewLine, Owner, save_recipe
from app.reports import sales_coverage_report
from app.sales import add_source, record_sale
from app.transform.analytics import sales_coverage
from app.transform.business_days import open_days_mask
from app.transform.dashboard import DateRange
from app.transform.reports import SalesData, money, qty, week_bounds
from app.transform.sales import SaleLine, origin_names
from app.web.reports import TABLES, _Tables

D = Decimal
TODAY = datetime.now(UTC).date()  # the fixture location is in UTC with a midnight cutoff
WEEK = DateRange("7d")
THURSDAY = date(2026, 11, 26)
NOT_SUNDAY = open_days_mask(("mon", "tue", "wed", "thu", "fri", "sat"))


@pytest.fixture
def bar(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    juice = create_item(
        db,
        org,
        NewItem(
            name="Golden juice",
            location_ids=[location.id],
            dimension="volume",
            display_unit="ml",
            quantity="50000",
        ),
    )
    golden = create_product(db, org, name="Golden State", variations=[NewVariation("16 oz")])
    size = golden.variations[0]
    save_recipe(
        db, org, Owner("variation", size.id), [NewLine(juice.id, "480", "ml")], from_the_start=True
    )
    db.commit()
    return org, size


@pytest.fixture
def pier(db, tenant, bar):
    """A second location whose sales come from a POS, one sale yesterday."""
    org, size = bar
    place = Location(tenant_id=tenant.id, name="Pier", timezone="UTC")
    db.add(place)
    db.flush()
    pos = add_source(
        db,
        org,
        location_id=place.id,
        kind="pos_adapter",
        provider="square",
        active_from=TODAY - timedelta(days=30),
    )
    record_sale(
        db,
        org,
        pos,
        source_reference="order-1",
        lines=[SaleLine("uid-1", D(2), size.id)],
        day=TODAY - timedelta(days=1),
    )
    db.commit()
    return place


def _hand_days(db, org, location, size, *days):
    for day in days:
        save_day(db, org, location_id=location.id, day=day, quantities={size.id: D(3)})
    db.commit()


# --- the rule ------------------------------------------------------------------------------------


def test_an_open_day_counts_once_it_is_over_or_has_sales():
    first = THURSDAY - timedelta(days=6)  # Friday; Sunday is closed
    record = {first + timedelta(days=n): "manual" for n in range(10)}
    sold = {first: {"manual"}, THURSDAY: {"manual"}}
    found = sales_coverage(
        1, first, THURSDAY + timedelta(days=3), THURSDAY, NOT_SUNDAY, record, sold
    )
    # Fri, Sat, Mon, Tue, Wed are over; today has sales; Sunday is closed; the future never counts.
    assert (found.with_sales, found.expected, found.missing) == (2, 6, 4)
    assert found.days_by_origin == {"manual": 2}
    unfinished = sales_coverage(
        1, first, THURSDAY, THURSDAY, NOT_SUNDAY, record, {first: {"manual"}}
    )
    assert (unfinished.with_sales, unfinished.expected) == (1, 5)  # today without sales: not yet


def test_only_days_a_standalone_source_owns_need_sales():
    first = THURSDAY - timedelta(days=3)
    record = {
        first: "file_import",
        first + timedelta(days=1): "pos_adapter",
        # first + 2 has no source of record yet: nothing was expected
    }
    sold = {first + timedelta(days=1): {"pos_adapter"}}
    found = sales_coverage(1, first, THURSDAY, THURSDAY, 127, record, sold)
    assert (found.with_sales, found.expected) == (0, 1)  # the imported day has none
    pos_only = sales_coverage(1, first, THURSDAY, THURSDAY, 127, {first: "pos_adapter"}, {})
    assert not pos_only.standalone and pos_only.days_by_origin == {}


def test_origins_read_in_a_fixed_order():
    assert origin_names(["manual", "pos_adapter", "manual"]) == "POS, Entered by hand"
    assert origin_names(iter(["file_import"])) == "Imported"
    assert origin_names([]) == ""


# --- the reports ---------------------------------------------------------------------------------


def test_the_report_names_each_origin_and_standalone_coverage(db, bar, location, pier):
    org, size = bar
    _hand_days(db, org, location, size, TODAY - timedelta(days=5), TODAY - timedelta(days=3))
    found = {c.location_id: c for c in sales_coverage_report(db, org, WEEK)}
    main = found[location.id]
    # Hand entry started five days ago: those five days are over, today has nothing yet.
    assert main.days_by_origin == {"manual": 2}
    assert (main.with_sales, main.expected) == (2, 5)
    assert found[pier.id].days_by_origin == {"pos_adapter": 1}
    assert not found[pier.id].standalone  # a POS day is never "missing"


def test_the_page_and_csv_show_origin_and_coverage(db, bar, location, pier):
    from fastapi.testclient import TestClient

    from app.main import app

    org, size = bar
    _hand_days(db, org, location, size, TODAY - timedelta(days=5), TODAY - timedelta(days=3))
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        page = client.get("/app/reports", params={"range": "7d"}).text
        assert "Entered by hand on 2 days." in page
        assert "Covered <strong>2 of 5 expected days (40.0 %)</strong>" in page
        assert "3 missing, which figures from sales leave out" in page
        assert "POS on 1 day." in page
        assert "Sales: POS, Entered by hand." in page  # cost of sales
        sheet = client.get("/app/reports.csv", params={"table": "sales_coverage", "range": "7d"})
    rows = {r["Location"]: r for r in csv.DictReader(io.StringIO(sheet.text))}
    assert rows["Main"]["Entered by hand"] == "2"
    main = rows["Main"]
    assert (main["With sales"], main["Missing"], main["Expected days"]) == ("2", "3", "5")
    assert main["Coverage %"] == "40.0"
    assert rows["Pier"]["POS"] == "1" and rows["Pier"]["Expected days"] == ""
    tables = _Tables(db, org, WEEK, None, datetime.now(UTC), False, None)
    header, [row] = TABLES["cost_of_sales"](tables)
    assert dict(zip(header, row, strict=True))["Sales from"] == "POS, Entered by hand"


def test_the_weekly_summary_states_coverage(db, tenant, bar, location):
    from fastapi.testclient import TestClient

    from app.main import app

    org, size = bar
    start, end = week_bounds(TODAY)
    _hand_days(db, org, location, size, start, start + timedelta(days=2))
    report = weekly_report(db, org, today=TODAY)
    assert report.sales_data == (SalesData("Main", "Entered by hand", 2, 7, D("28.6")),)
    html, text = render_pair(
        "email/weekly_report",
        tenant=tenant,
        report=report,
        money=money,
        qty=qty,
        base_url="https://app.test",
    )
    for part in (html, text):
        assert "Entered by hand." in part and "Covered 2 of 7 expected days (28.6 %)." in part
    assert "SALES LAST WEEK" in text
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        page = client.get("/app/report").text
    assert "Covered 2 of 7 expected days (28.6 %)." in page


# --- PD-54: confirmed days without sales and one-off closures --------------------------------------


def test_a_day_is_sales_no_sales_closed_or_missing():
    from app.transform.analytics import day_status

    monday = date(2026, 11, 23)

    def status(day, sold=False, mark=None, origin="manual"):
        return day_status(day, THURSDAY, NOT_SUNDAY, origin, sold, mark)

    assert status(monday, sold=True) == "sales"
    assert status(monday, mark="no_sales") == "no_sales"
    assert status(monday, mark="closed") == "closed"
    assert status(monday) == "missing"
    assert status(monday - timedelta(days=1)) is None  # a weekly closed Sunday
    assert status(monday, origin="pos_adapter") is None  # a POS reports for itself
    assert status(THURSDAY) is None and status(THURSDAY, sold=True) == "sales"  # today
    assert status(THURSDAY + timedelta(days=1), mark="closed") is None  # the future


def test_closures_leave_the_denominator_and_confirmed_days_cover():
    first = THURSDAY - timedelta(days=6)  # Fri 20 to Wed 25 are over; Sunday 22 is weekly closed
    record = {first + timedelta(days=n): "manual" for n in range(7)}
    sold = {first: {"manual"}}
    marked = {first + timedelta(days=1): "no_sales", first + timedelta(days=3): "closed"}
    found = sales_coverage(1, first, THURSDAY, THURSDAY, NOT_SUNDAY, record, sold, marked)
    # Fri sales, Sat no sales, Mon closed, Tue and Wed missing; Sun and today not expected.
    assert (found.with_sales, found.no_sales, found.closed, found.missing) == (1, 1, 1, 2)
    assert (found.covered, found.expected, found.percent) == (2, 4, D("50.0"))


def test_saving_nothing_sold_confirms_the_day(db, bar, location):
    from sqlalchemy import select

    from app.daily_sales import missing_days
    from app.models import LocationDay, SalesTransaction

    org, size = bar
    day = TODAY - timedelta(days=2)
    assert save_day(db, org, location_id=location.id, day=day, quantities={size.id: D(0)}) is None
    db.commit()
    mark = db.scalars(select(LocationDay)).one()
    assert (mark.business_date, mark.status) == (day, "no_sales")
    assert db.scalars(select(SalesTransaction)).first() is None, "no sale is invented"
    flagged = {f.subject_id for f in missing_days(db, org, datetime.now(UTC))}
    assert f"{location.id}:{day.isoformat()}" not in flagged
    assert f"{location.id}:{(day + timedelta(days=1)).isoformat()}" in flagged
    found = {c.location_id: c for c in sales_coverage_report(db, org, WEEK)}[location.id]
    assert (found.no_sales, found.missing, found.covered) == (1, 1, 1)

    save_day(db, org, location_id=location.id, day=day, quantities={size.id: D(4)})
    db.commit()
    assert db.scalars(select(LocationDay)).first() is None, "sales speak for the day now"
    save_day(db, org, location_id=location.id, day=day, quantities={}, reason="Entered by mistake")
    db.commit()
    assert db.scalars(select(LocationDay)).one().status == "no_sales", "corrected to nothing sold"
    found = {c.location_id: c for c in sales_coverage_report(db, org, WEEK)}[location.id]
    assert (found.with_sales, found.no_sales) == (0, 1)


def test_a_closed_day_is_kept_apart_and_changes_no_sales(db, bar, location, pier):
    from sqlalchemy import func, select

    from app.daily_sales import missing_days
    from app.location_days import DayRuleError, close_day, reopen_day
    from app.models import AuditEvent, InventoryTransaction, SalesTransaction
    from app.transform.sales import SalesRuleError

    org, size = bar
    sold_day, holiday = TODAY - timedelta(days=3), TODAY - timedelta(days=2)
    _hand_days(db, org, location, size, sold_day)

    def counts():
        return (
            db.scalar(select(func.count()).select_from(SalesTransaction)),
            db.scalar(select(func.count()).select_from(InventoryTransaction)),
        )

    before = counts()
    with pytest.raises(DayRuleError, match="Say why"):
        close_day(db, org, location.id, holiday, "  ")
    with pytest.raises(DayRuleError, match="has sales recorded"):
        close_day(db, org, location.id, sold_day, "Holiday")
    with pytest.raises(DayRuleError, match="come from pos"):
        close_day(db, org, pier.id, TODAY - timedelta(days=1), "Holiday")
    close_day(db, org, location.id, holiday, "Thanksgiving")
    db.commit()
    with pytest.raises(SalesRuleError, match="marked closed"):
        save_day(db, org, location_id=location.id, day=holiday, quantities={size.id: D(1)})
    flagged = {f.subject_id for f in missing_days(db, org, datetime.now(UTC))}
    assert f"{location.id}:{holiday.isoformat()}" not in flagged
    found = {c.location_id: c for c in sales_coverage_report(db, org, WEEK)}[location.id]
    assert (found.with_sales, found.closed, found.missing, found.expected) == (1, 1, 1, 2)
    assert counts() == before, "a closure touches no sale and no stock"

    assert reopen_day(db, org, location.id, holiday) is True
    db.commit()
    found = {c.location_id: c for c in sales_coverage_report(db, org, WEEK)}[location.id]
    assert (found.closed, found.missing) == (0, 2)
    actions = [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id))]
    assert actions[-2:] == ["location.day_closed", "location.day_reopened"]


def test_the_daily_page_records_no_sales_and_closures(db, bar, location):
    from fastapi.testclient import TestClient

    from app.main import app

    no_sales, holiday = TODAY - timedelta(days=2), TODAY - timedelta(days=1)
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        saved = client.post(
            "/app/sales/daily", data={"location_id": location.id, "day": no_sales.isoformat()}
        )
        assert "Saved: no sales on" in saved.text and "Recorded: open, no sales." in saved.text
        closed = client.post(
            "/app/sales/daily/close",
            data={"location_id": location.id, "day": holiday.isoformat(), "reason": "Boiler"},
        )
        assert "Marked closed" in closed.text and "Closed this day." in closed.text
        assert "Boiler" in closed.text
        reopened = client.post(
            "/app/sales/daily/reopen",
            data={"location_id": location.id, "day": holiday.isoformat()},
        )
        assert "Open again" in reopened.text and 'id="close-day"' in reopened.text
