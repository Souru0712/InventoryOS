"""T3.09: integration health and sales-source status (POS-FR-02, POS-FR-27, CMD-FR-08, AC-CMD-05,
AC-CMD-06)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.daily_sales import manual_source
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Location, ProviderConnection, SyncRun, SyncStatus, Tenant
from app.products import NewVariation, create_product
from app.providers.base import NormalizedEvent
from app.sales import record_sale
from app.sales_status import sales_status
from app.transform.integration_health import ConnectionHealth, Reads
from app.transform.sales import SaleLine
from app.webhook_events import record
from tests.accounts import confirm_email

NOW = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)


def health(**changes):
    base = {
        "provider": "square",
        "status": "active",
        "tracked": 1,
        "reads": Reads(last_success=NOW - timedelta(minutes=5), runs_24h=12),
        "waiting_events": 0,
        "token_expires_at": None,
        "unmapped": 0,
        "now": NOW,
    }
    return ConnectionHealth(**(base | changes))


@pytest.mark.parametrize(
    ("changes", "state"),
    [
        ({}, "ok"),
        ({"reads": Reads(last_success=NOW - timedelta(hours=3))}, "warning"),  # stale
        (
            {
                "reads": Reads(
                    last_success=NOW - timedelta(minutes=30),
                    last_failure=NOW - timedelta(minutes=5),
                )
            },
            "warning",
        ),  # the last read failed
        ({"waiting_events": 2}, "warning"),
        ({"status": "revoked"}, "critical"),
        ({"status": "disconnected"}, "off"),
        ({"tracked": 0, "reads": Reads()}, "setup"),
    ],
)
def test_a_connection_is_judged_by_its_reads_its_events_and_its_status(changes, state):
    assert health(**changes).state == state


def test_the_error_rate_counts_the_last_24_hours():
    assert health(reads=Reads(runs_24h=10, failures_24h=3)).error_rate == pytest.approx(0.3)
    assert health(reads=Reads()).error_rate is None


# --- through the pages ---------------------------------------------------------------------------


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
        tenant_id = db.scalars(select(Tenant)).one().id
    yield client, tenant_id
    client.__exit__(None, None, None)


def _connected(db, tenant_id: int) -> ProviderConnection:
    place = Location(
        tenant_id=tenant_id,
        name="Main St",
        timezone="UTC",
        provider="square",
        provider_location_id="SQ-1",
    )
    connection = ProviderConnection(
        tenant_id=tenant_id, provider="square", merchant_id="M-1", access_token_enc="enc"
    )
    db.add_all([place, connection])
    db.flush()
    return connection


def test_ac_cmd_05_a_sync_failing_for_hours_warns_with_the_last_success_not_zero(owner):
    client, tenant_id = owner
    now = datetime.now(UTC)
    with SessionLocal() as db:
        _connected(db, tenant_id)
        last = now - timedelta(hours=3)
        db.add(
            SyncRun(
                tenant_id=tenant_id,
                kind="orders",
                status=SyncStatus.SUCCESS,
                started_at=last,
                finished_at=last,
            )
        )
        for minutes in (60, 30, 5):
            db.add(
                SyncRun(
                    tenant_id=tenant_id,
                    kind="orders",
                    status=SyncStatus.FAILED,
                    started_at=now - timedelta(minutes=minutes),
                    error="square is down",
                )
            )
        db.commit()
    page = client.get("/app").text
    assert 'data-status="stale"' in page
    assert f"Square sales last arrived {last:%d %b %H:%M} UTC, more than two hours ago" in page
    assert "may be missing, not zero" in page
    health_panel = client.get("/app/connection").text
    assert "3 of 4" in health_panel and "(over two hours ago)" in health_panel


def test_ac_cmd_06_standalone_shows_the_last_day_entered_and_the_missing_days(owner):
    client, tenant_id = owner
    with SessionLocal() as db:
        org = OrgContext(tenant_id=tenant_id, permissions=role_permissions("owner"))
        place = Location(tenant_id=tenant_id, name="Kiosk", timezone="UTC")
        db.add(place)
        db.flush()
        size = create_product(db, org, name="Bagel", variations=[NewVariation("Regular")])
        today = datetime.now(UTC).date()
        entered = today - timedelta(days=3)
        record_sale(
            db,
            org,
            manual_source(db, org, place.id, entered),
            source_reference=f"day:{entered.isoformat()}",
            lines=[SaleLine("v", Decimal(4), size.variations[0].id)],
            day=entered,
        )
        db.commit()
    page = client.get("/app").text
    assert f"Sales last entered for {entered:%a %d %b}." in page
    for gap in (today - timedelta(days=2), today - timedelta(days=1)):
        assert f"{gap:%a %d %b}" in page.split("Missing:")[1].split("</p>")[0]
    assert "Square sales" not in page, "no POS status in Standalone mode"


def test_the_health_panel_counts_waiting_events_and_unmapped_products(owner):
    client, tenant_id = owner
    with SessionLocal() as db:
        _connected(db, tenant_id)
        db.add(
            SyncRun(
                tenant_id=tenant_id,
                kind="orders",
                status=SyncStatus.SUCCESS,
                finished_at=datetime.now(UTC),
            )
        )
        late = record(db, "square", NormalizedEvent("evt-1", "order.updated"), tenant_id)
        late.received_at = datetime.now(UTC) - timedelta(minutes=30)
        record(db, "square", NormalizedEvent("evt-2", "order.updated"), tenant_id)  # just now
        db.commit()
    panel = client.get("/app/connection").text
    assert "Webhook events waiting</th><td>1 (received over 15 minutes ago" in panel
    page = client.get("/app").text
    assert 'data-status="behind"' in page, "an event behind is a warning"


# --- which locations are which -------------------------------------------------------------------


def test_a_tracking_location_is_connected_and_the_rest_standalone(db, tenant, location):
    connection = _connected(db, tenant.id)
    db.commit()
    org = OrgContext.system(tenant.id)
    modes = {s.name: s.mode for s in sales_status(db, org, NOW)}
    assert modes == {"Main St": "connected", location.name: "standalone"}

    connection.status = "revoked"
    db.commit()
    [main] = [s for s in sales_status(db, org, NOW) if s.name == "Main St"]
    assert main.health.state == "critical"

    connection.status, connection.access_token_enc = "disconnected", None
    db.commit()
    assert {s.mode for s in sales_status(db, org, NOW)} == {"standalone"}
    assert [s.name for s in sales_status(db, org, NOW, location.id)] == [location.name]
