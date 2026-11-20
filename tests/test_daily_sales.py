"""T2.12: daily sales entered by hand (STD-FR-02, -05, -06, PD-37, AC-SAL-02, -03, -05, -06)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.daily_sales import entered, save_day
from app.items import NewItem, create_item
from app.ledger.posting import balance
from app.models import ExceptionRecord, InventoryTransaction, Membership, Tenant, User
from app.orchestration.alerts import evaluate_and_store
from app.products import NewVariation, create_product
from app.recipes import NewLine, Owner, save_recipe
from app.sales import add_source
from app.security import hash_password
from app.transform.sales import SalesRuleError

D = Decimal
TODAY = datetime.now(UTC).date()  # the fixture location is in UTC with a midnight cutoff


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
    # In effect from the start: a day saved for TODAY (fixed at import) still has a recipe when
    # a run crosses midnight.
    save_recipe(
        db, org, Owner("variation", size.id), [NewLine(juice.id, "480", "ml")], from_the_start=True
    )
    db.commit()
    return org, juice, size


def _sales_postings(db):
    return db.scalars(
        select(InventoryTransaction).where(InventoryTransaction.kind.in_(["sale", "reversal"]))
    ).all()


def test_ac_sal_02_a_day_posts_its_consumption_once(db, bar, location):
    org, juice, size = bar
    saved = save_day(db, org, location_id=location.id, day=TODAY, quantities={size.id: D(40)})
    db.commit()
    assert saved is not None and saved.origin == "manual"
    assert balance(db, org, location.id, juice.id) == D(50000) - 40 * D(480)
    again = save_day(db, org, location_id=location.id, day=TODAY, quantities={size.id: D(40)})
    db.commit()
    assert again is None and len(_sales_postings(db)) == 1  # the same values change nothing


def test_ac_sal_03_correcting_a_day_reverses_it_with_the_reason(db, bar, location):
    org, juice, size = bar
    save_day(db, org, location_id=location.id, day=TODAY, quantities={size.id: D(40)})
    with pytest.raises(SalesRuleError, match="Say why"):
        save_day(db, org, location_id=location.id, day=TODAY, quantities={size.id: D(35)})
    save_day(
        db,
        org,
        location_id=location.id,
        day=TODAY,
        quantities={size.id: D(35)},
        reason="Five were comped",
    )
    db.commit()
    assert balance(db, org, location.id, juice.id) == D(50000) - 35 * D(480)
    reversal = db.scalars(
        select(InventoryTransaction).where(InventoryTransaction.kind == "reversal")
    ).one()
    assert "Five were comped" in reversal.reason
    assert entered(db, org, location.id, TODAY) == {size.id: D(35)}


def test_ac_sal_06_a_day_from_a_pos_is_not_entered_by_hand(db, bar, location):
    org, _juice, size = bar
    add_source(
        db,
        org,
        location_id=location.id,
        kind="pos_adapter",
        provider="square",
        active_from=date(2026, 1, 1),
    )
    with pytest.raises(SalesRuleError, match="not entered by hand"):
        save_day(db, org, location_id=location.id, day=TODAY, quantities={size.id: D(3)})


def test_ac_sal_05_an_open_day_without_sales_is_flagged_until_entered(db, bar, location):
    org, _juice, size = bar
    save_day(
        db,
        org,
        location_id=location.id,
        day=TODAY - timedelta(days=3),
        quantities={size.id: D(5)},
    )
    db.commit()
    evaluate_and_store(db, org)
    db.commit()
    missing = {
        e.subject_id
        for e in db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "sales_missing"))
    }
    yesterday = TODAY - timedelta(days=1)
    assert f"{location.id}:{yesterday.isoformat()}" in missing
    assert f"{location.id}:{(TODAY - timedelta(days=3)).isoformat()}" not in missing
    save_day(db, org, location_id=location.id, day=yesterday, quantities={size.id: D(2)})
    evaluate_and_store(db, org)
    db.commit()
    row = db.scalars(
        select(ExceptionRecord).where(
            ExceptionRecord.subject_id == f"{location.id}:{yesterday.isoformat()}"
        )
    ).one()
    assert row.status == "resolved"


def test_pd_37_employees_enter_sales_only_when_the_owner_allows(db, bar, tenant, location):
    from app.security import member_permissions

    user = User(
        tenant_id=tenant.id, email="emp@rosewood.test", password_hash=hash_password("x" * 12)
    )
    db.add(user)
    db.flush()
    membership = Membership(tenant_id=tenant.id, user_id=user.id, role="employee")
    db.add(membership)
    db.flush()
    assert "sales.record" not in member_permissions(db, membership)
    db.get(Tenant, tenant.id).employees_enter_sales = True
    db.flush()
    assert "sales.record" in member_permissions(db, membership)


def test_the_daily_sales_page(db, bar, location):
    _org, _juice, size = bar
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        page = client.get(f"/app/sales/daily?day={TODAY.isoformat()}")
        assert "Golden State 16 oz" in page.text
        saved = client.post(
            "/app/sales/daily",
            data={"location_id": location.id, "day": TODAY.isoformat(), f"qty_{size.id}": "12"},
        )
        assert "Saved." in saved.text
