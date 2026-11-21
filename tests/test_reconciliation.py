"""T3.08: reconciliation and failure recovery (POS-FR-10, POS-FR-12, SQ-12, SQ-17, AC-POS-07,
AC-POS-08). The POS is a fake adapter that honours the windows it is asked about."""

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import order_ingestion
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import ExceptionRecord, Job, SalesTransaction, SyncRun, SyncStatus, Tenant
from app.orchestration import scheduler
from app.order_ingestion import ingest_orders, reconcile_orders
from app.providers.base import AdapterError, OrderPage
from app.providers.square.client import SquareClient
from tests.accounts import confirm_email
from tests.test_order_ingestion import NOW, latte, order, shop  # noqa: F401

YESTERDAY = NOW.date() - timedelta(days=1)
NOON = datetime.combine(YESTERDAY, time(12), UTC)


class WindowedPos:
    """Orders the POS has; each read returns only what falls in the window it was asked for."""

    def __init__(self):
        self.orders = []
        self.fails: AdapterError | None = None

    def fetch_orders(self, _credentials, location_ids, since, until, cursor=None):
        if self.fails:
            raise self.fails
        found = [o for o in self.orders if since <= (o.updated_at or o.occurred_at) <= until]
        return OrderPage(tuple(found))

    def completed_orders(self, _credentials, location_ids, start, end):
        if self.fails:
            raise self.fails
        found = [
            o
            for o in self.orders
            if o.state == "completed"
            and start <= o.occurred_at < end
            and o.external_location_id in location_ids
        ]
        return OrderPage(tuple(found))


@pytest.fixture
def pos(shop, monkeypatch):  # noqa: F811
    windowed = WindowedPos()
    monkeypatch.setattr(order_ingestion, "adapter_for", lambda _provider: windowed)
    return shop[0], windowed


def _sales(db) -> list[SalesTransaction]:
    return list(db.scalars(select(SalesTransaction)))


def _mismatch(db) -> ExceptionRecord | None:
    return db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "pos_reconciliation_mismatch")
    ).one_or_none()


def test_ac_pos_07_an_order_delivered_late_is_picked_up_within_the_hour(db, pos):
    org, square = pos
    ingest_orders(db, org, NOW)  # the first read: the whole backfill
    late = order("o-late", latte("A", 1), at=NOW - timedelta(minutes=40))
    square.orders = [late]  # an offline payment Square delivers now, stamped 40 minutes ago
    ingest_orders(db, org, NOW + timedelta(minutes=5))
    assert _sales(db) == [], "outside the incremental window"
    stats = ingest_orders(db, org, NOW + timedelta(minutes=65))
    assert stats["reread"] == 1 and stats["sales"] == 1, "the hourly re-read finds it"


def test_ac_pos_08_a_skipped_order_opens_a_mismatch_is_read_again_and_clears(db, location, pos):
    org, square = pos
    kept = [order(f"o-{i}", latte("A", 1), at=NOON + timedelta(minutes=i)) for i in (1, 2)]
    square.orders = kept
    ingest_orders(db, org, NOW)
    skipped = order("o-3", latte("A", 2), at=NOON + timedelta(minutes=3))
    square.orders = [*kept, skipped]  # Square has it; we never read it

    stats = reconcile_orders(db, org, NOW)
    db.commit()
    assert (stats["mismatched"], stats["sales"]) == (1, 1), "noticed and read again"
    problem = _mismatch(db)
    assert problem.status == "open" and problem.subject_id == f"{location.id}:{YESTERDAY}"
    assert "recorded 3 orders" in problem.title and "had 2" in problem.title
    run = db.scalars(select(SyncRun).where(SyncRun.kind == "reconciliation")).one()
    assert run.status is SyncStatus.SUCCESS and '"pos": [3, 3], "ours": [2, 2]' in run.stats_json

    assert reconcile_orders(db, org, NOW + timedelta(hours=6)).get("mismatched", 0) == 0
    db.commit()
    db.refresh(problem)
    assert problem.status == "resolved", "the next check matches"


def test_a_line_count_that_differs_is_a_mismatch_too(db, pos):
    org, square = pos
    first = order("o-1", latte("A", 1), at=NOON)
    square.orders = [first]
    ingest_orders(db, org, NOW)
    square.orders = [replace(first, version=2, lines=(*first.lines, latte("B", 1)))]
    assert reconcile_orders(db, org, NOW)["mismatched"] == 1
    assert len(db.scalars(select(SalesTransaction)).all()) == 2, "the newer version, a revision"


def test_a_failed_read_is_kept_and_cleared_by_the_next_success(db, pos):
    org, square = pos
    square.fails = AdapterError("square is down", revoked=False)
    for minutes in (0, 5):
        assert ingest_orders(db, org, NOW + timedelta(minutes=minutes))["failed"] == 1
    db.commit()
    failure = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "sync_failure")
    ).one()
    assert (failure.status, failure.severity, failure.occurrences) == ("open", "warning", 2)
    runs = db.scalars(select(SyncRun).where(SyncRun.kind == "orders")).all()
    assert [r.status for r in runs] == [SyncStatus.FAILED, SyncStatus.FAILED]

    square.fails = None
    ingest_orders(db, org, NOW + timedelta(minutes=10))
    db.commit()
    db.refresh(failure)
    assert failure.status == "resolved"


def test_a_failed_check_leaves_open_mismatches_alone(db, pos):
    org, square = pos
    square.orders = [order("o-1", latte("A", 1), at=NOON)]
    ingest_orders(db, org, NOW)
    square.orders.append(order("o-2", latte("A", 1), at=NOON + timedelta(minutes=1)))
    reconcile_orders(db, org, NOW)
    db.commit()
    square.fails = AdapterError("square is down", revoked=False)
    assert reconcile_orders(db, org, NOW + timedelta(hours=6))["failed"] == 1
    db.commit()
    assert _mismatch(db).status == "open", "an unfinished check resolves nothing"


def test_reconciliation_runs_on_a_clock_as_a_job(db, pos):
    class Recorder:
        ids: list[str] = []

        def add_job(self, func, *args, id, **kwargs):
            self.ids.append(id)

    scheduler._add_jobs(Recorder())
    assert "queue_order_reconciliation" in Recorder.ids
    scheduler.queue_order_reconciliation()
    with SessionLocal() as other:
        assert other.scalars(select(Job).where(Job.kind == "reconcile_orders")).one()


def test_square_counts_a_day_by_completion_time(monkeypatch):
    bodies = []

    def page(self, method, path, body, key):
        bodies.append(body)
        return iter([])

    monkeypatch.setattr(SquareClient, "_paginate", page)
    SquareClient("token").completed_orders(["L1"], NOON, NOON + timedelta(days=1))
    query = bodies[0]["query"]
    assert "closed_at" in query["filter"]["date_time_filter"]
    assert query["filter"]["state_filter"]["states"] == ["COMPLETED"]


def test_the_sync_history_lists_reads_checks_and_failures():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        client.post(
            "/signup",
            data={
                "business_name": "Corner",
                "email": "o@corner.test",
                "password": "a-good-password",
            },
        )
        confirm_email("o@corner.test")
        with SessionLocal() as db:
            tenant = db.scalars(select(Tenant)).one()
            db.add_all(
                [
                    SyncRun(
                        tenant_id=tenant.id,
                        kind="orders",
                        status=SyncStatus.SUCCESS,
                        stats_json='{"fetched": 4, "sales": 3, "reread": 1}',
                    ),
                    SyncRun(
                        tenant_id=tenant.id,
                        kind="reconciliation",
                        status=SyncStatus.SUCCESS,
                        stats_json='{"checked": [{}, {}, {}], "mismatched": 1}',
                    ),
                    SyncRun(
                        tenant_id=tenant.id,
                        kind="orders",
                        status=SyncStatus.FAILED,
                        error="AdapterError: square is down",
                    ),
                ]
            )
            db.commit()
        page = client.get("/app/connection/history").text
    assert "4 order(s) read, 3 recorded (hourly re-read)" in page
    assert "3 day(s) checked, 1 differed" in page
    assert "Failed" in page and "square is down" in page
