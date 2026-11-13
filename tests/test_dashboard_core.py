"""T1.25: the core dashboard on the ledger and exceptions (CMD-FR-01..04, -10, -12..14,
AC-CMD-01..04, PD-26)."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.adjustments import adjust
from app.costs import set_cost
from app.exceptions import record_exception
from app.ledger.posting import post_transaction, reverse_transaction
from app.main import app
from app.models import (
    Membership,
    RecipeComponent,
    SalesDaily,
    SoldItem,
    StockBatch,
    StockItem,
    TrackMode,
    User,
)
from app.orchestration.alerts import evaluate_and_store
from app.orchestration.dashboard import needs_attention, period_money
from app.security import hash_password
from app.transform.business_days import business_date, business_day_end, business_day_start
from app.transform.dashboard import DateRange, RangeError, check_range, completeness_note
from app.transform.exceptions import Finding, attention_key
from app.transform.ledger import Line
from app.units import Dimension
from tests.ledger_helpers import opened

D = Decimal
PASSWORD = "a-good-password"  # noqa: S105 (the test accounts', as in conftest)
MONEY = re.compile(r"\$\s?\d")


def stock(db, location, name, qty="10", cost="100", reorder="0"):
    item = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=name.lower().replace(" ", "-"),
        name=name,
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
        reorder_point_base=D(reorder),
    )
    db.add(item)
    db.flush()
    set_cost(
        db,
        OrgContext.system(location.tenant_id),
        location_id=location.id,
        inventory_item_id=item.inventory_item_id,
        cost=D(cost),
        source_type="opening",
    )
    item.qty_base = D(qty)
    opened(db, item)
    db.commit()
    return item


def member(db, tenant, role):
    user = User(
        tenant_id=tenant.id, email=f"{role}@rosewood.test", password_hash=hash_password(PASSWORD)
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
    db.commit()


def signed_in(email="owner@rosewood.test") -> TestClient:
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": email, "password": PASSWORD})
    return client


def waste(db, org, location, item, qty, when, key):
    return post_transaction(
        db,
        org,
        kind="waste",
        source_type="test",
        source_id=key,
        idempotency_key=f"waste:{key}",
        lines=[Line(location.id, item.inventory_item_id, "waste", D(-qty))],
        occurred_at=when,
    )


# --- the rules (pure) --------------------------------------------------------------------------


def test_a_period_is_counted_back_from_each_locations_own_today():
    today = date(2026, 11, 13)
    assert DateRange().days_at(today) == (date(2026, 11, 7), today)  # 7 days by default
    assert check_range("today").days_at(today) == (today, today)
    assert check_range("30d").days_at(today) == (date(2026, 10, 15), today)
    custom = check_range("custom", date(2026, 11, 1), date(2026, 11, 5))
    assert custom.days_at(today) == (date(2026, 11, 1), date(2026, 11, 5))
    assert custom.label == "01 Nov 2026 to 05 Nov 2026"
    for bad in (
        ("week",),
        ("custom", None, today),
        ("custom", today, date(2026, 11, 1)),
        ("custom", date(2025, 1, 1), today),
    ):
        with pytest.raises(RangeError):
            check_range(*bad)


def test_a_business_day_runs_from_one_cutoff_to_the_next():
    start = business_day_start(date(2026, 11, 13), "America/New_York", 4)
    assert start == datetime(2026, 11, 13, 9, tzinfo=UTC)  # 04:00 EST
    assert business_day_end(date(2026, 11, 12), "America/New_York", 4) == start - timedelta(
        microseconds=1
    )


def test_needs_attention_ranks_by_severity_then_money_at_risk():
    now = 1_000.0
    keys = {
        "drift": attention_key("critical", None, now - 50),
        "big": attention_key("warning", 9_000, now - 99),
        "small": attention_key("warning", 100, now),
        "info": attention_key("info", 50_000, now),
    }
    assert sorted(keys, key=keys.__getitem__) == ["drift", "big", "small", "info"]
    assert completeness_note(D("0"), 0) is None
    assert completeness_note(D("5.0000"), 1) == (
        "5 sold without a recipe in this period (1 product); "
        "ingredient use reads low until they have one."
    )


# --- AC-CMD-01: needs attention, each linking to its own record --------------------------------


def test_ac_cmd_01_low_stock_an_expired_batch_and_an_unmapped_product_need_attention(
    db, tenant, location
):
    org = OrgContext.system(tenant.id)
    milk = stock(db, location, "Milk", qty="2", reorder="5")
    eggs = stock(db, location, "Eggs", qty="12")
    db.add(
        StockBatch(
            tenant_id=tenant.id,
            stock_item_id=eggs.id,
            received_qty_base=D(12),
            expires_on=date.today() - timedelta(days=3),
            source="label",
        )
    )
    latte = SoldItem(tenant_id=tenant.id, provider_item_id="sq-oat-latte", name="Oat latte")
    db.add(latte)
    db.flush()
    evaluate_and_store(db, org)
    # Its producer arrives with the consumption engine (T2.05); the dashboard shows it already.
    record_exception(
        db,
        org,
        Finding(
            type="unmapped_product",
            subject_type="sold_item",
            subject_id=str(latte.id),
            title="Oat latte sold 5 times and is not mapped to any stock.",
        ),
    )
    db.commit()

    page = signed_in().get("/app").text
    attention = page[page.index("Needs attention") : page.index("Stock on hand")]
    assert "Low stock" in attention and "Past its date, still in stock" in attention
    assert "Product not mapped" in attention and "Oat latte sold 5 times" in attention
    assert f'href="/app/catalog/{milk.inventory_item_id}">Milk</a>' in attention
    assert f'href="/app/expiring#item-{eggs.id}">Eggs</a>' in attention
    assert ">Reorder it</a>" in attention  # each warning says what to do (CMD-FR-12)
    expiring = signed_in().get("/app/expiring").text
    assert f'id="item-{eggs.id}"' in expiring  # the link lands on the batch's own row


def test_the_list_puts_the_costliest_problem_first_and_follows_the_location(db, tenant, location):
    org = OrgContext.system(tenant.id)
    then = datetime.now(UTC) - timedelta(hours=2)
    for document, cents, when in (("1", 9_000, then), ("2", 100, datetime.now(UTC))):
        record_exception(  # the costlier one is older: money outranks recency
            db,
            org,
            Finding(
                type="receiving_discrepancy",
                subject_type="document",
                subject_id=document,
                title=f"Delivery {document} differs from its invoice.",
                location_id=location.id,
                money_impact_cents=cents,
            ),
            now=when,
        )
    record_exception(
        db,
        org,
        Finding(
            type="job_failed",
            subject_type="job_kind",
            subject_id="nightly",
            title="Nightly failed 5 times in a row and has stopped.",
            severity="critical",
        ),
    )
    db.commit()
    owner = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    shown = [(v.type, v.title[:10]) for v in needs_attention(db, owner)]
    assert shown == [
        ("job_failed", "Nightly fa"),  # critical first, though it has no amount
        ("receiving_discrepancy", "Delivery 1"),
        ("receiving_discrepancy", "Delivery 2"),
    ]
    elsewhere = needs_attention(db, owner, location_id=location.id + 1000)
    assert [v.type for v in elsewhere] == ["job_failed"]  # business-wide stays in every view


# --- AC-CMD-02: sales without a recipe ---------------------------------------------------------


def test_ac_cmd_02_sales_without_a_recipe_are_named_and_use_is_labelled_incomplete(
    db, tenant, location
):
    milk = stock(db, location, "Milk", qty="40")
    today = business_date(datetime.now(UTC), location.timezone, location.business_day_cutoff_hour)
    latte = SoldItem(tenant_id=tenant.id, provider_item_id="sq-oat", name="Oat latte")
    flat = SoldItem(tenant_id=tenant.id, provider_item_id="sq-flat", name="Flat white")
    db.add_all([latte, flat])
    db.flush()
    db.add(
        RecipeComponent(
            tenant_id=tenant.id, sold_item_id=flat.id, stock_item_id=milk.id, qty_base_per_sale=D(1)
        )
    )
    for sold, day, units in (
        (latte, today, 5),
        (flat, today, 7),
        (latte, today - timedelta(20), 9),
    ):
        db.add(
            SalesDaily(
                tenant_id=tenant.id,
                sold_item_id=sold.id,
                location_id=location.id,
                dt=day,
                units=D(units),
            )
        )
    db.commit()

    owner = signed_in()
    page = owner.get("/app").text
    assert "5 sold without a recipe in this period (1 product)" in page
    assert page.count('<span class="pill warning">incomplete</span>') == 2  # cover, uses / day
    assert "14 sold without a recipe" in owner.get("/app?range=30d").text
    assert "5 sold without a recipe" in owner.get("/app?range=today").text


# --- AC-CMD-03: an Employee's response carries no money ----------------------------------------


def test_ac_cmd_03_an_employees_dashboard_response_carries_no_money(db, tenant, location):
    milk = stock(db, location, "Milk", qty="20", reorder="30")
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    waste(db, org, location, milk, 2, datetime.now(UTC), "w1")
    adjust(  # worth more than the review threshold: a financial exception
        db,
        org,
        inventory_item_id=milk.inventory_item_id,
        location_id=location.id,
        qty_base=D(-60),
        reason="correction",
        override=True,
        override_reason="Spilled in the walk-in",
    )
    evaluate_and_store(db, OrgContext.system(tenant.id))
    db.commit()
    member(db, tenant, "employee")

    owner = signed_in().get("/app").text
    assert MONEY.search(owner) and "Adjustment to review" in owner and "Waste" in owner
    employee = signed_in("employee@rosewood.test").get("/app")
    assert employee.status_code == 200
    assert not MONEY.search(employee.text), MONEY.search(employee.text)
    for hidden in ("Adjustment to review", "Stock on hand", "Waste &middot;", "at stake"):
        assert hidden not in employee.text
    assert "Below zero" in employee.text  # the operational problem still shows
    assert "item location" not in employee.text  # named by its item, linked to it


# --- AC-CMD-04: money over a period, in location-local days ------------------------------------


def test_ac_cmd_04_waste_over_7_days_is_the_waste_of_those_7_local_days(db, tenant, location):
    location.timezone = "America/New_York"
    location.business_day_cutoff_hour = 4
    db.commit()
    milk = stock(db, location, "Milk", qty="100", cost="100")  # $1.00 each
    org = OrgContext.system(tenant.id)
    now = datetime.now(UTC)
    today = business_date(now, location.timezone, 4)
    first = business_day_start(today - timedelta(days=6), location.timezone, 4)
    waste(db, org, location, milk, 1, now, "today")
    waste(db, org, location, milk, 2, first, "first-instant")  # day 1 of 7: in
    waste(db, org, location, milk, 4, first - timedelta(minutes=1), "day-before")  # day 8: out
    yesterday = now - timedelta(days=1)
    undone = waste(db, org, location, milk, 8, yesterday, "undone")
    reverse_transaction(
        db, org, undone.id, reason="entered twice", occurred_at=yesterday + timedelta(minutes=1)
    )
    db.commit()

    week = period_money(db, org, check_range("7d"), now)
    assert (week.waste.value_cents, week.waste.movements) == (-300, 2)  # the reversal nets out
    assert period_money(db, org, check_range("today"), now).waste.value_cents == -100
    assert period_money(db, org, check_range("30d"), now).waste.value_cents == -700
    custom = check_range("custom", today - timedelta(days=7), today - timedelta(days=7))
    assert period_money(db, org, custom, now).waste.value_cents == -400
    page = signed_in().get("/app?range=7d").text
    assert "Waste &middot; last 7 days" in page and "$3.00" in page


def test_a_period_the_rules_refuse_falls_back_to_7_days_and_says_why(db, tenant, location):
    stock(db, location, "Milk")
    page = signed_in().get("/app?range=custom&from=2026-11-10&to=2026-11-01").text
    assert "The first day comes after the last. Showing the last 7 days." in page
    assert "Waste &middot; last 7 days" in page
    assert "A day is a date like" in signed_in().get("/app?range=custom&from=soon&to=x").text
