"""Inventory health against a real database: the score, the history, and the pages it drives."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    HealthSnapshot,
    Location,
    Plan,
    SalesDaily,
    SoldItem,
    StockBatch,
    StockItem,
    Tenant,
    TrackMode,
)
from app.orchestration.health import current, expiry_figures, over_time, record_snapshot
from app.orchestration.views import expiry_positions
from app.units import Dimension
from app.web.dashboard import _expiry_summary

NOW = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
TODAY = NOW.date()


def stock(db, tenant, location, slug, **overrides) -> StockItem:
    defaults = dict(
        tenant_id=tenant.id,
        location_id=location.id,
        slug=slug,
        name=slug.replace("-", " ").title(),
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.PROVIDER,
        provider_variation_id=f"VAR-{slug}",
        qty_base=Decimal("40"),
        reorder_point_base=Decimal("10"),
        cost_per_base_cents=Decimal("100"),
        lead_time_days=3,
    )
    item = StockItem(**{**defaults, **overrides})
    db.add(item)
    db.flush()
    return item


def sells(db, tenant, location, item, per_day, days=28, today=TODAY):
    sold = SoldItem(
        tenant_id=tenant.id,
        provider_item_id=item.provider_variation_id,
        name=item.name,
        price_cents=199,
    )
    db.add(sold)
    db.flush()
    for back in range(days):
        db.add(
            SalesDaily(
                tenant_id=tenant.id,
                sold_item_id=sold.id,
                location_id=location.id,
                dt=today - timedelta(days=back),
                units=Decimal(per_day),
            )
        )


# --- the score from live stock ------------------------------------------------------------------


def test_health_and_the_expiring_page_agree_on_the_money(db, tenant, location):
    milk = stock(
        db,
        tenant,
        location,
        "milk",
        track_mode=TrackMode.MANUAL,
        provider_variation_id=None,
        qty_base=Decimal("20"),
        cost_per_base_cents=Decimal("50"),
    )
    db.add_all(
        [
            StockBatch(
                tenant_id=tenant.id,
                stock_item_id=milk.id,
                received_qty_base=Decimal("8"),
                expires_on=TODAY - timedelta(days=1),
            ),
            StockBatch(
                tenant_id=tenant.id,
                stock_item_id=milk.id,
                received_qty_base=Decimal("12"),
                expires_on=TODAY + timedelta(days=2),
            ),
        ]
    )
    db.commit()

    horizon = get_settings().expiry_horizon_days
    expiries = expiry_positions(db, tenant.id, TODAY)
    figures = expiry_figures(expiries, horizon)
    page = _expiry_summary(expiries, horizon)

    assert figures.expired_cents > 0 and figures.at_risk_cents > 0
    assert figures.expired_cents == page["expired_cents"]
    assert figures.at_risk_cents == page["at_risk_cents"]


def test_a_real_store_is_scored_with_its_reasons(db, tenant, location):
    coke = stock(db, tenant, location, "coke", qty_base=Decimal("0"))
    sells(db, tenant, location, coke, "12")
    water = stock(db, tenant, location, "water", qty_base=Decimal("200"))
    sells(db, tenant, location, water, "5")
    db.commit()

    health = current(db, tenant.id, now=NOW)
    assert health.report.score is not None
    assert health.report.component("stock").figures["out_of_stock"] == 1
    assert any("out of stock" in action.text for action in health.actions.urgent)
    # Nothing is dated, so expiry is left out rather than awarded a perfect score.
    assert health.report.component("expiry").measured is False


# --- history ------------------------------------------------------------------------------------


def test_a_night_is_kept_once_however_often_it_runs(db, tenant, location):
    stock(db, tenant, location, "coke")
    db.commit()
    record_snapshot(db, tenant.id, now=NOW)
    record_snapshot(db, tenant.id, now=NOW)
    db.commit()

    [row] = db.query(HealthSnapshot).all()
    assert row.dt == TODAY
    assert json.loads(row.detail_json)["components"]["stock"]["figures"]["tracked"] == 1


def test_the_next_day_explains_why_the_score_moved(db, tenant, location):
    coke = stock(db, tenant, location, "coke", qty_base=Decimal("0"))
    sells(db, tenant, location, coke, "12")
    water = stock(db, tenant, location, "water", qty_base=Decimal("200"))
    sells(db, tenant, location, water, "5")
    db.commit()

    yesterday = NOW - timedelta(days=1)
    record_snapshot(db, tenant.id, now=yesterday)
    coke.qty_base = Decimal("300")  # the delivery arrived overnight
    db.commit()

    report = current(db, tenant.id, now=NOW).report
    trend = over_time(db, tenant.id, report, TODAY)
    assert trend.compared_with == yesterday.date()
    assert trend.change.points > 0
    assert any("fewer at or below the reorder point" in line for _, line in trend.change.lines)


def test_the_comparison_with_the_monthly_average_needs_three_nights(db, tenant, location):
    stock(db, tenant, location, "coke")
    db.commit()
    for back in (3, 2, 1):
        record_snapshot(db, tenant.id, now=NOW - timedelta(days=back))
    db.commit()

    trend = over_time(db, tenant.id, current(db, tenant.id, now=NOW).report, TODAY)
    assert trend.benchmark is not None
    assert trend.benchmark.days == 3
    assert [row.dt for row in trend.history] == [TODAY - timedelta(days=d) for d in (3, 2, 1)]


def test_a_damaged_snapshot_costs_the_comparison_not_the_page(db, tenant, location):
    stock(db, tenant, location, "coke")
    db.add(
        HealthSnapshot(
            tenant_id=tenant.id,
            dt=TODAY - timedelta(days=1),
            score=80,
            band="healthy",
            detail_json="{not json",
        )
    )
    db.commit()

    trend = over_time(db, tenant.id, current(db, tenant.id, now=NOW).report, TODAY)
    assert trend.change is None
    assert trend.compared_with == TODAY - timedelta(days=1)


def test_the_nightly_job_keeps_every_active_tenant_and_skips_cancelled_ones(db, tenant, location):
    from app.orchestration import scheduler

    stock(db, tenant, location, "coke")
    closed = Tenant(name="Closed Down", plan=Plan.CANCELED)
    db.add(closed)
    db.commit()

    scheduler.record_health_snapshots()

    db.expire_all()
    assert db.query(HealthSnapshot).filter_by(tenant_id=tenant.id).count() == 1
    assert db.query(HealthSnapshot).filter_by(tenant_id=closed.id).count() == 0


def test_the_cli_prints_the_score_and_keeps_a_snapshot(db, tenant, location, capsys):
    from app.cli import main

    stock(db, tenant, location, "coke")
    db.commit()
    assert main(["health", "--tenant", str(tenant.id), "--snapshot"]) == 0
    out = capsys.readouterr().out
    assert "inventory health:" in out
    assert "snapshot kept" in out


# --- the pages ----------------------------------------------------------------------------------


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def workspace(client, name="Rosewood", email="o@rosewood.test", items=()) -> int:
    client.post(
        "/signup", data={"business_name": name, "email": email, "password": "a-good-password"}
    )
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.name == name).one()
        location = Location(
            tenant_id=tenant.id, provider="demo", provider_location_id=f"loc-{name}", name="Main"
        )
        db.add(location)
        db.flush()
        for spec in items:
            db.add(
                StockItem(
                    tenant_id=tenant.id,
                    location_id=location.id,
                    dimension=Dimension.COUNT,
                    stock_unit="each",
                    track_mode=TrackMode.MANUAL,
                    reorder_point_base=Decimal("10"),
                    cost_per_base_cents=Decimal("100"),
                    **spec,
                )
            )
        db.commit()
        return tenant.id


def item(slug, qty, **extra) -> dict:
    return {
        "slug": slug,
        "name": extra.pop("name", slug.replace("-", " ").title()),
        "qty_base": Decimal(qty),
        **extra,
    }


def test_the_dashboard_leads_with_health_and_todays_actions(client):
    workspace(client, items=[item("coke", "0"), item("water", "50")])
    response = client.get("/app")
    assert response.status_code == 200
    assert "Inventory health" in response.text
    assert "1 urgent" in response.text
    assert "Reorder 1 product that is out of stock" in response.text


def test_an_empty_workspace_still_renders_without_a_health_block(client):
    workspace(client)
    response = client.get("/app")
    assert response.status_code == 200
    assert "Inventory health" not in response.text


def test_the_health_page_shows_every_dimension_and_the_rule_behind_it(client):
    workspace(client, items=[item("coke", "0"), item("water", "50")])
    response = client.get("/app/health")
    assert response.status_code == 200
    for label in (
        "Stock availability",
        "Expiration risk",
        "Overstock",
        "Reorder health",
        "Data completeness",
    ):
        assert label in response.text, label
    assert "Not scored" in response.text  # nothing is dated
    assert "scores zero" in response.text  # the expiry rule is written out


def test_the_reorder_list_groups_by_supplier_with_the_unassigned_last(client):
    workspace(
        client,
        items=[
            item("oat-milk", "0", supplier="Harbour Dairy"),
            item("mystery-crisps", "0"),
            item("water", "50"),
        ],
    )
    response = client.get("/app/reorder")
    assert response.status_code == 200
    assert "Harbour Dairy" in response.text and "No supplier set" in response.text
    assert response.text.index("Harbour Dairy") < response.text.index("No supplier set")
    assert "Water" not in response.text


def test_the_reorder_csv_downloads_and_will_not_run_a_formula(client):
    workspace(client, items=[item("sneaky", "0", name="=HYPERLINK(1)", supplier="Harbour Dairy")])
    response = client.get("/app/reorder.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "'=HYPERLINK(1)" in response.text


def test_signed_out_visitors_cannot_see_health_or_reorders(client):
    for path in ("/app/health", "/app/reorder", "/app/reorder.csv"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login"


def test_one_tenant_never_sees_another_tenants_reorder_list(client):
    workspace(client, "A", "a@rosewood.test", items=[item("secret", "0", name="Secret Item")])
    client.get("/logout")
    workspace(client, "B", "b@rosewood.test")
    assert "Secret Item" not in client.get("/app/reorder").text
    assert "Secret Item" not in client.get("/app/reorder.csv").text


class _NestedLinks(HTMLParser):
    """Counts links opened while another link is still open."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nested = 0

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.nested += 1 if self.depth else 0
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "a" and self.depth:
            self.depth -= 1


@pytest.mark.parametrize("path", ["/app", "/app/health", "/app/reorder"])
def test_no_page_puts_a_link_inside_a_link(client, path):
    # Invalid HTML the text-only checks cannot see: the browser closes the outer link early and
    # throws everything after it out of its box. It emptied the dashboard's health card once.
    workspace(client, items=[item("coke", "0"), item("water", "50", supplier="Harbour Dairy")])
    parser = _NestedLinks()
    parser.feed(client.get(path).text)
    assert parser.nested == 0
