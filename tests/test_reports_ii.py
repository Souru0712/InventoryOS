"""T2.17: margins, cost of sales, receiving, count-based usage, CSV (ANL-FR-06, -08, -12..14,
AC-ANL-04, -06, -08)."""

import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.daily_sales import save_day
from app.items import NewItem, create_item
from app.ledger.posting import post_transaction
from app.models import StockItem
from app.products import NewVariation, create_product
from app.receivings import add_line, post_receiving, start_receiving
from app.recipes import NewLine, Owner, save_recipe
from app.reports import (
    consumption_report,
    cost_of_sales_report,
    count_usage_report,
    margin_report,
    receiving_report,
)
from app.suppliers import SupplierFields, create_supplier
from app.transform.analytics import Count, Timed, count_intervals
from app.transform.business_days import business_date
from app.transform.dashboard import DateRange
from app.transform.ledger import Line
from app.web.reports import TABLES, _Tables
from tests.ledger_helpers import counted_to

D = Decimal
MONTH = DateRange("30d")


def _at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def test_ac_anl_08_the_worked_example():
    d1, d8 = date(2026, 11, 1), date(2026, 11, 8)
    [interval] = count_intervals(
        [
            Timed(_at(d1 + timedelta(days=2)), "receipt", D(60)),
            Timed(_at(d1 + timedelta(days=4)), "waste", D(-10)),
        ],
        [Count(_at(d1), d1, D(100)), Count(_at(d8), d8, D(40))],
    )
    assert interval.used == D(110) and interval.days == 7
    assert interval.parts["received"] == D(60) and interval.parts["wasted"] == D(-10)


@pytest.fixture
def shop(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    beans = create_item(
        db,
        org,
        NewItem(
            name="Beans",
            location_ids=[location.id],
            dimension="count",
            display_unit="each",
            cost_per_unit="2",
        ),
    )
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == beans.id)).one()
    today = business_date(datetime.now(UTC), location.timezone, location.business_day_cutoff_hour)
    db.commit()
    return org, beans, stock, today


def test_ac_anl_08_on_the_ledger_with_no_sales(db, shop, location):
    org, beans, stock, today = shop
    d1, d8 = today - timedelta(days=9), today - timedelta(days=2)
    counted_to(db, stock, 100, at=_at(d1, 9))
    for kind, movement, qty, day in (
        ("receipt", "receipt", "60", d1 + timedelta(days=2)),
        ("waste", "waste", "-10", d1 + timedelta(days=4)),
    ):
        post_transaction(
            db,
            org,
            kind=kind,
            source_type="test",
            source_id=kind,
            idempotency_key=f"test:{kind}",
            lines=[Line(location.id, beans.id, movement, D(qty))],
            occurred_at=_at(day),
        )
    counted_to(db, stock, 40, at=_at(d8, 18))
    db.commit()
    [interval] = count_usage_report(db, org, MONTH)[(location.id, beans.id)]
    assert (interval.opening.qty_base, interval.closing.qty_base, interval.used) == (
        D(100),
        D(40),
        D(110),
    )


def test_ac_anl_04_cost_of_sales_shows_recipe_coverage(db, shop, location):
    org, beans, stock, today = shop
    counted_to(db, stock, 50)
    latte = create_product(db, org, name="Latte", variations=[NewVariation("Regular", "450")])
    tea = create_product(db, org, name="Tea", variations=[NewVariation("Regular", "300")])
    save_recipe(db, org, Owner("variation", latte.variations[0].id), [NewLine(beans.id, "1", "")])
    save_day(
        db,
        org,
        location_id=location.id,
        day=today,
        quantities={latte.variations[0].id: D(4), tea.variations[0].id: D(2)},
    )
    db.commit()
    report = cost_of_sales_report(db, org, DateRange("today"))
    assert (report.lines, report.complete, report.coverage_percent) == (2, 1, D("50.0"))
    assert report.cents == 800  # 4 beans at $2, as each sale took them
    assert report.cents == consumption_report(db, org, DateRange("today")).totals_cents["sales"]


def test_receiving_discrepancies_and_price_changes(db, shop, location):
    org, beans, _stock, _today = shop
    mill = create_supplier(db, org, SupplierFields(name="Mill Co"))
    first = start_receiving(db, org, location_id=location.id, supplier_id=mill.id)
    add_line(db, org, first.id, inventory_item_id=beans.id, accepted="10", unit_price="2.00")
    post_receiving(db, org, first.id, now=datetime.now(UTC) - timedelta(days=3))
    second = start_receiving(db, org, location_id=location.id, supplier_id=mill.id)
    add_line(
        db,
        org,
        second.id,
        inventory_item_id=beans.id,
        invoiced="12",
        arrived="10",
        accepted="10",
        unit_price="2.50",
    )
    post_receiving(db, org, second.id)
    db.commit()
    report = receiving_report(db, org, MONTH)
    [short] = report.shortfalls
    assert short.found.short == D(2) and short.supplier == "Mill Co"
    [change] = report.changes
    assert (change.before_cents, change.after_cents, change.percent) == (
        D(200),
        D(250),
        D("25.0"),
    )


def test_margins_by_size_at_todays_costs(db, shop, location):
    org, beans, _stock, _today = shop
    latte = create_product(db, org, name="Latte", variations=[NewVariation("Regular", "4.00")])
    save_recipe(db, org, Owner("variation", latte.variations[0].id), [NewLine(beans.id, "1", "")])
    db.commit()
    _place, [row] = margin_report(db, org)
    assert (row.price_cents, row.cost_cents, row.percent) == (400, D(200), D("50.0"))


def test_ac_anl_06_every_table_exports_and_money_needs_cost_access(db, shop, location):
    from fastapi.testclient import TestClient

    from app.main import app

    org, beans, stock, today = shop
    counted_to(db, stock, 5)
    db.commit()
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        for table in TABLES:
            sheet = client.get("/app/reports.csv", params={"table": table, "range": "30d"})
            assert sheet.status_code == 200, table
            assert next(csv.reader(io.StringIO(sheet.text)))  # a header row
        assert client.get("/app/reports.csv", params={"table": "nope"}).status_code == 404
    no_money = _Tables(db, org, MONTH, None, datetime.now(UTC), False, None)
    with pytest.raises(HTTPException):
        TABLES["margins"](no_money)
    header, _rows = TABLES["consumption"](no_money)
    assert not any("cost" in column.lower() for column in header)


def test_the_csv_carries_the_page_figures(db, shop, location):
    org, beans, stock, today = shop
    counted_to(db, stock, 50)
    post_transaction(
        db,
        org,
        kind="sale",
        source_type="test",
        source_id="s",
        idempotency_key="test:s",
        lines=[Line(location.id, beans.id, "sale_consumption", D(-7))],
        occurred_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db.commit()
    header, [row] = TABLES["consumption"](
        _Tables(db, org, MONTH, None, datetime.now(UTC), True, None)
    )
    figures = dict(zip(header, row, strict=True))
    page = consumption_report(db, org, MONTH).rows[(location.id, beans.id)]
    assert figures["Sales"] == "7" and figures["Sales cost"] == "14.00"
    assert page.qty["sales"] == D(7) and page.cents["sales"] == 1400


def test_an_empty_location_means_every_location(db, shop, location):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        assert client.get("/app", params={"location": ""}).status_code == 200  # a form's "All"
        assert client.get("/app/reports", params={"location": ""}).status_code == 200
        assert client.get("/app/reports", params={"location": "abc"}).status_code == 404
