"""AC-SAL-08 (STD-FR-07, WF-24): sales-derived reports show where their sales came from (manual,
imported, POS), and on Standalone days how many open days have sales ("N of M open days")."""

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
    assert (found.with_sales, found.open_days, found.missing) == (2, 6, 4)
    assert found.days_by_origin == {"manual": 2}
    unfinished = sales_coverage(
        1, first, THURSDAY, THURSDAY, NOT_SUNDAY, record, {first: {"manual"}}
    )
    assert (unfinished.with_sales, unfinished.open_days) == (1, 5)  # today without sales: not yet


def test_only_days_a_standalone_source_owns_need_sales():
    first = THURSDAY - timedelta(days=3)
    record = {
        first: "file_import",
        first + timedelta(days=1): "pos_adapter",
        # first + 2 has no source of record yet: nothing was expected
    }
    sold = {first + timedelta(days=1): {"pos_adapter"}}
    found = sales_coverage(1, first, THURSDAY, THURSDAY, 127, record, sold)
    assert (found.with_sales, found.open_days) == (0, 1)  # the imported day has none
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
    assert (main.with_sales, main.open_days) == (2, 5)
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
        assert "Sales on <strong>2 of 5 open days</strong>" in page
        assert "leave out the 3 days without any" in page
        assert "POS on 1 day." in page
        assert "Sales: POS, Entered by hand." in page  # cost of sales
        sheet = client.get("/app/reports.csv", params={"table": "sales_coverage", "range": "7d"})
    rows = {r["Location"]: r for r in csv.DictReader(io.StringIO(sheet.text))}
    assert rows["Main"]["Entered by hand"] == "2"
    assert (rows["Main"]["Open days with sales"], rows["Main"]["Open days"]) == ("2", "5")
    assert rows["Pier"]["POS"] == "1" and rows["Pier"]["Open days"] == ""
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
    assert report.sales_data == (SalesData("Main", "Entered by hand", 2, 7),)
    html, text = render_pair(
        "email/weekly_report",
        tenant=tenant,
        report=report,
        money=money,
        qty=qty,
        base_url="https://app.test",
    )
    for part in (html, text):
        assert "Entered by hand." in part and "Sales on 2 of 7 open days." in part
    assert "SALES LAST WEEK" in text
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        page = client.get("/app/report").text
    assert "Sales on 2 of 7 open days." in page
