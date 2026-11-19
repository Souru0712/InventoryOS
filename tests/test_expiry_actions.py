"""T2.08: the expiry alert window, disposing from the queue, auto-dispose (BAT-FR-09..11, -14,
PD-29, AC-BAT-06, -07)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.batches import batch_view, date_stock
from app.items import NewItem, create_item, update_item
from app.ledger.posting import batch_remaining
from app.models import AuditEvent, ExceptionRecord, StockItem, Tenant, WasteEvent
from app.orchestration.alerts import evaluate_and_store
from app.products import NewVariation, create_product
from app.recipes import NewLine, Owner, save_recipe
from app.sales import add_source, record_sale
from app.transform.sales import SaleLine
from app.waste import dispose_batch, dispose_expired

NOW = datetime.now(UTC)
TODAY = NOW.date()


@pytest.fixture
def shelf(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))

    def stocked(name, item_type="raw", qty="20"):
        item = create_item(
            db,
            org,
            NewItem(
                name=name,
                location_ids=[location.id],
                dimension="count",
                display_unit="each",
                item_type=item_type,
                quantity=qty,
                cost_per_unit="1",
            ),
        )
        stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == item.id)).one()
        return item, stock

    db.commit()
    return org, stocked


def _soon(db):
    return db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "expiring_soon")).all()


def test_ac_bat_06_a_lot_in_the_window_is_flagged_once_at_its_locations_day(db, shelf, tenant):
    org, stocked = shelf
    item, stock = stocked("Yoghurt")
    date_stock(db, org, stock, Decimal(6), TODAY + timedelta(days=2))  # the day after tomorrow
    db.commit()
    for _ in range(2):  # evaluated again later the same day: still one alert
        evaluate_and_store(db, org, NOW)
        db.commit()
    [alert] = _soon(db)
    assert alert.status == "open" and "in 2 days" in alert.title
    # A window of one day, set on the item, flags nothing yet.
    update_item(
        db,
        org,
        item.id,
        name="Yoghurt",
        item_type="raw",
        category="",
        dimension="count",
        display_unit="each",
        notes="",
        shelf_life_days="",
        tracks_expiry=True,
        expiry_alert_days="1",
    )
    evaluate_and_store(db, org, NOW)
    db.commit()
    db.expire_all()
    assert alert.status == "resolved"
    # Three days out is outside the business's two-day window.
    other, other_stock = stocked("Cream")
    date_stock(db, org, other_stock, Decimal(2), TODAY + timedelta(days=3))
    evaluate_and_store(db, org, NOW)
    db.commit()
    assert [a.status for a in _soon(db)] == ["resolved"]


def _prepared_batch(db, org, stocked, location, expires):
    juice, stock = stocked("Golden juice", item_type="prepared")
    return juice, date_stock(db, org, stock, Decimal(5), expires)


def test_ac_bat_07_auto_dispose_writes_off_expired_prepared_batches_only_when_on(
    db, shelf, tenant, location
):
    org, stocked = shelf
    _juice, batch = _prepared_batch(db, org, stocked, location, TODAY - timedelta(days=1))
    _milk, milk_stock = stocked("Milk")
    raw_batch = date_stock(db, org, milk_stock, Decimal(5), TODAY - timedelta(days=1))
    db.commit()
    assert dispose_expired(db, org, NOW) == 0  # off by default (PD-29)
    db.get(Tenant, tenant.id).auto_dispose_prepared = True
    assert dispose_expired(db, org, NOW) == 1
    [event] = db.scalars(select(WasteEvent)).all()
    assert (event.reason_label, event.actor_user_id) == ("Past its date", None)
    assert batch_remaining(db, org, [batch.id, raw_batch.id]) == {
        batch.id: Decimal(0),
        raw_batch.id: Decimal(5),  # raw stock is never written off automatically
    }
    assert dispose_expired(db, org, NOW) == 0  # again: nothing left


def test_ac_bat_07_auto_dispose_waits_for_pending_sales(db, shelf, tenant, location):
    org, stocked = shelf
    juice, batch = _prepared_batch(db, org, stocked, location, TODAY - timedelta(days=1))
    drink = create_product(db, org, name="Golden shot", variations=[NewVariation("2 oz")])
    save_recipe(db, org, Owner("variation", drink.variations[0].id), [NewLine(juice.id, "1", "")])
    source = add_source(
        db, org, location_id=location.id, kind="manual", active_from=date(2026, 1, 1)
    )
    record_sale(
        db,
        org,
        source,
        source_reference="late",
        occurred_at=NOW,
        lines=[SaleLine("1", Decimal(1), drink.variations[0].id)],
    )
    db.get(Tenant, tenant.id).auto_dispose_prepared = True
    db.commit()
    assert dispose_expired(db, org, NOW) == 0  # the sale's consumption is still pending
    assert batch_remaining(db, org, [batch.id])[batch.id] == Decimal(5)


def test_disposing_a_lot_from_the_queue_logs_it_as_waste(db, shelf):
    org, stocked = shelf
    _item, stock = stocked("Ham")
    batch = date_stock(db, org, stock, Decimal(4), TODAY - timedelta(days=2))
    db.commit()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        done = client.post(f"/app/expiring/{batch.id}/dispose", follow_redirects=False)
        assert done.headers["location"] == "/app/expiring?disposed=1"
        empty = client.post(f"/app/expiring/{batch.id}/dispose", follow_redirects=False)
        assert empty.headers["location"] == "/app/expiring?error=empty"
    db.expire_all()
    assert batch_remaining(db, org, [batch.id])[batch.id] == Decimal(0)
    event = db.scalars(select(WasteEvent)).one()
    assert event.reason_label == "Past its date" and event.lines[0].batch_id == batch.id


def test_the_expiry_settings_are_saved_and_audited(db, shelf, tenant):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        saved = client.post(
            "/app/expiring/settings",
            data={"alert_days": "3", "auto_dispose": "yes"},
            follow_redirects=False,
        )
        assert saved.headers["location"] == "/app/expiring?saved=1"
        refused = client.post(
            "/app/expiring/settings", data={"alert_days": "x"}, follow_redirects=False
        )
        assert refused.headers["location"] == "/app/expiring?error=days"
        page = client.get("/app/expiring")
        assert "Expiry settings" in page.text
    db.expire_all()
    business = db.get(Tenant, tenant.id)
    assert (business.expiry_alert_days, business.auto_dispose_prepared) == (3, True)
    audited = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "settings.expiry_changed")
    ).one()
    assert audited.after == {"alert days": 3, "auto-dispose": True}


def test_a_prepared_batch_shows_its_use_by_label(db, shelf, location):
    org, stocked = shelf
    _juice, batch = _prepared_batch(db, org, stocked, location, TODAY + timedelta(days=1))
    view = batch_view(db, org, batch.id)
    assert view.item_type == "prepared"
    dispose_batch(db, org, batch.id, note="test")
    assert batch_remaining(db, org, [batch.id])[batch.id] == Decimal(0)
