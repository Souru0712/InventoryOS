"""T2.14: direct usage records (STD-FR-04, WF-16)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.daily_sales import save_day
from app.items import NewItem, create_item
from app.ledger.posting import balance
from app.models import ExceptionRecord
from app.products import NewVariation, create_product
from app.recipes import NewLine, Owner, save_recipe
from app.transform.business_days import business_date
from app.transform.ledger import BelowZero
from app.transform.usage import UsageRuleError
from app.usage import Use, consumption, new_token, record_usage, records

D = Decimal


@pytest.fixture
def kitchen(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    beans = create_item(
        db,
        org,
        NewItem(
            name="Beans",
            location_ids=[location.id],
            dimension="weight",
            display_unit="g",
            quantity="5000",
        ),
    )
    milk = create_item(
        db,
        org,
        NewItem(
            name="Milk",
            location_ids=[location.id],
            dimension="volume",
            display_unit="ml",
            quantity="2000",
        ),
    )
    db.commit()
    today = business_date(datetime.now(UTC), location.timezone, location.business_day_cutoff_hour)
    return org, beans, milk, today


def test_a_usage_record_changes_stock_and_shows_as_manual_usage(db, kitchen, location):
    org, beans, milk, today = kitchen
    latte = create_product(db, org, name="Latte", variations=[NewVariation("Regular")])
    save_recipe(db, org, Owner("variation", latte.variations[0].id), [NewLine(beans.id, "18", "g")])
    save_day(
        db,
        org,
        location_id=location.id,
        day=today,  # the recipe took effect now: an earlier day has none in effect
        quantities={latte.variations[0].id: D(10)},
    )
    assert record_usage(
        db,
        org,
        location_id=location.id,
        uses=[Use(beans.id, "0.5", "kg"), Use(milk.id, "300")],
        reason="staff_meal",
        note="Friday lunch",
        token=new_token(),
    )
    db.commit()
    assert balance(db, org, location.id, beans.id) == D(5000 - 180 - 500)
    assert balance(db, org, location.id, milk.id) == D(1700)

    used = {
        u.inventory_item_id: u
        for u in consumption(db, org, since=today - timedelta(days=6), until=today)
    }
    assert (used[beans.id].from_sales, used[beans.id].manual) == (D(180), D(500))
    assert (used[milk.id].from_sales, used[milk.id].manual) == (D(0), D(300))
    [shown] = records(db, org, since=today, until=today)
    assert shown.reason == "Staff meal: Friday lunch"
    assert shown.used == {beans.id: D(500), milk.id: D(300)}


def test_the_same_form_sent_twice_posts_once(db, kitchen, location):
    org, beans, _milk, _today = kitchen
    token = new_token()
    kwargs = {"location_id": location.id, "uses": [Use(beans.id, "100")], "reason": "sample"}
    assert record_usage(db, org, token=token, **kwargs) is True
    assert record_usage(db, org, token=token, **kwargs) is False
    assert balance(db, org, location.id, beans.id) == D(4900)


@pytest.mark.parametrize(
    ("reason", "note", "uses", "message"),
    [
        ("", "", [("100", "")], "Choose why"),
        ("other", "", [("100", "")], "Say what it was used for"),
        ("sample", "", [], "Enter what was used"),
        ("sample", "", [("0", "")], "above zero"),
        ("sample", "", [("1", "cup")], ""),  # no such unit for the item
    ],
)
def test_a_usage_record_needs_a_reason_and_a_quantity(
    db, kitchen, location, reason, note, uses, message
):
    org, beans, _milk, _today = kitchen
    with pytest.raises(UsageRuleError, match=message or None):
        record_usage(
            db,
            org,
            location_id=location.id,
            uses=[Use(beans.id, q, u) for q, u in uses],
            reason=reason,
            note=note,
            token=new_token(),
        )


def test_below_zero_needs_the_override(db, kitchen, location):
    org, beans, _milk, _today = kitchen
    employee = OrgContext(tenant_id=org.tenant_id, permissions=frozenset({"sales.record"}))
    with pytest.raises(BelowZero):
        record_usage(
            db,
            employee,
            location_id=location.id,
            uses=[Use(beans.id, "6", "kg")],
            reason="catering",
            token=new_token(),
        )
    db.rollback()
    assert record_usage(
        db,
        org,
        location_id=location.id,
        uses=[Use(beans.id, "6", "kg")],
        reason="catering",
        token=new_token(),
    )
    db.commit()
    assert balance(db, org, location.id, beans.id) == D(-1000)
    types = set(db.scalars(select(ExceptionRecord.type)))
    assert "negative_balance" in types


def test_recording_usage_through_the_page(db, kitchen, location):
    from fastapi.testclient import TestClient

    from app.main import app

    _org, beans, _milk, _today = kitchen
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        page = client.get("/app/usage")
        assert page.status_code == 200 and "Record usage" in page.text
        token = page.text.split('name="token" value="', 1)[1].split('"', 1)[0]
        form = {
            "location_id": location.id,
            "token": token,
            "reason": "sample",
            "item_1": beans.id,
            "qty_1": "25",
        }
        done = client.post("/app/usage", data=form)
        assert "Usage recorded." in done.text and "Sample or tasting" in done.text
        again = client.post("/app/usage", data=form)
        assert "already recorded" in again.text
