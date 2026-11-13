"""T1.20: background jobs and scheduled evaluation for every tenant (ID-06, REL-07, SCL-03)."""

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.access import OrgContext
from app.config import get_settings
from app.db import SessionLocal, is_postgres
from app.extract.invoice import StubExtractor
from app.jobs import drain, enqueue
from app.main import app
from app.models import (
    Document,
    DocumentStatus,
    ExceptionRecord,
    Job,
    StockItem,
    Tenant,
    TrackMode,
)
from app.orchestration import receiving as receiving_mod
from app.orchestration import scheduler
from app.transform.jobs import JobRuleError, after_failure, backoff, check_kind
from app.units import Dimension
from tests.ledger_helpers import opened
from tests.sample_files import jpeg
from tests.test_email import FakeSMTP

D = Decimal
NOW = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)  # a Monday, 07:00: the default report time
postgres_only = pytest.mark.skipif(
    not is_postgres(),
    reason="two workers locking rows at once is a Postgres property (ADR-0018)",
)


class Clock:
    """Time the worker sees, moved by the test."""

    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def jobs() -> list[Job]:
    with SessionLocal() as db:
        return list(db.scalars(select(Job).order_by(Job.id)))


def second_tenant(db) -> Tenant:
    other = Tenant(name="Harbor Cafe")
    db.add(other)
    db.commit()
    return other


# --- the rules (pure) --------------------------------------------------------------------------


def test_waits_double_after_each_failure_up_to_an_hour():
    assert [backoff(n) for n in (1, 2, 3, 4)] == [timedelta(minutes=m) for m in (1, 2, 4, 8)]
    assert backoff(20) == timedelta(hours=1)
    assert after_failure(2, 5, NOW).run_at == NOW + timedelta(minutes=2)
    assert after_failure(5, 5, NOW).dead is True
    with pytest.raises(JobRuleError):
        check_kind("mine_bitcoin")


# --- queueing ----------------------------------------------------------------------------------


def test_asking_twice_for_the_same_waiting_work_queues_it_once(db, tenant):
    org = OrgContext.system(tenant.id)
    first = enqueue(db, org, "evaluate_exceptions", dedupe_key="evaluate")
    again = enqueue(db, org, "evaluate_exceptions", dedupe_key="evaluate")
    other = enqueue(
        db, OrgContext.system(second_tenant(db).id), "evaluate_exceptions", dedupe_key="evaluate"
    )
    db.commit()
    assert first.id == again.id != other.id
    assert (first.tenant_id, first.status, first.attempts, first.max_attempts) == (
        tenant.id,
        "pending",
        0,
        5,
    )
    db.add(Job(tenant_id=tenant.id, kind="evaluate_exceptions", dedupe_key="evaluate"))
    with pytest.raises(IntegrityError):
        db.commit()  # the database holds the line too


def test_once_done_the_same_work_can_be_queued_again(db, tenant):
    org = OrgContext.system(tenant.id)
    first = enqueue(db, org, "send_alert_digest", dedupe_key="digest")
    db.commit()
    drain(handlers={"send_alert_digest": lambda *_: None})
    second = enqueue(db, org, "send_alert_digest", dedupe_key="digest")
    db.commit()
    assert second.id != first.id


# --- running, retrying, dead-lettering ---------------------------------------------------------


def test_a_job_runs_and_its_work_commits_with_it(db, tenant):
    enqueue(db, OrgContext.system(tenant.id), "evaluate_exceptions", {"note": "x"})
    db.commit()
    seen = []

    def handler(session, org, payload):
        seen.append((org.tenant_id, payload))
        session.get(Tenant, org.tenant_id).name = "Renamed by the job"

    assert drain(handlers={"evaluate_exceptions": handler})["done"] == 1
    assert seen == [(tenant.id, {"note": "x"})]
    [job] = jobs()
    assert (job.status, job.attempts, job.finished_at is not None) == ("done", 1, True)
    db.expire_all()
    assert db.get(Tenant, tenant.id).name == "Renamed by the job"


def test_a_failing_job_retries_with_back_off_then_dead_letters_into_an_exception(db, tenant):
    """Acceptance: a failing job retries then dead-letters into an exception."""
    enqueue(db, OrgContext.system(tenant.id), "send_alert_digest")
    db.commit()
    clock = Clock(NOW)

    def broken(session, org, payload):
        session.get(Tenant, org.tenant_id).name = "half done"
        raise ConnectionError("mail server unreachable")

    handlers = {"send_alert_digest": broken}
    assert drain(handlers=handlers, clock=clock)["retried"] == 1
    [job] = jobs()
    assert (job.status, job.attempts) == ("pending", 1)
    assert job.run_at == NOW + timedelta(minutes=1)
    assert job.last_error == "ConnectionError: mail server unreachable"
    db.expire_all()
    assert db.get(Tenant, tenant.id).name == "Rosewood Coffee"  # the failed work rolled back

    assert drain(handlers=handlers, clock=clock)["done"] == 0  # not due yet: nothing runs
    for attempt in range(2, 6):
        clock.now += timedelta(hours=1)
        outcome = drain(handlers=handlers, clock=clock)
        assert outcome["dead" if attempt == 5 else "retried"] == 1
    [job] = jobs()
    assert (job.status, job.attempts) == ("dead", 5)

    exception = db.scalars(select(ExceptionRecord)).one()
    assert (exception.type, exception.subject_type, exception.subject_id, exception.status) == (
        "job_failed",
        "job_kind",
        "send_alert_digest",
        "open",
    )
    assert exception.title == "Sending the alert email failed 5 times in a row and has stopped."
    assert "unreachable" not in exception.title  # the error itself stays with the job

    # The kind's next success clears it (REC-FR-14).
    enqueue(db, OrgContext.system(tenant.id), "send_alert_digest")
    db.commit()
    drain(handlers={"send_alert_digest": lambda *_: None}, clock=clock)
    db.expire_all()
    exception = db.get(ExceptionRecord, exception.id)
    assert (exception.status, exception.resolution) == ("resolved", "system")


def test_a_job_its_worker_abandoned_runs_again(db, tenant):
    job = enqueue(db, OrgContext.system(tenant.id), "evaluate_exceptions")
    job.status, job.attempts = "running", 1
    job.locked_at, job.locked_by = NOW - timedelta(minutes=20), "dead-worker:1"
    db.commit()
    handlers = {"evaluate_exceptions": lambda *_: None}
    stats = drain(handlers=handlers, clock=Clock(NOW))
    assert (stats["requeued"], stats["done"]) == (1, 0)  # back on the queue, after a wait
    assert drain(handlers=handlers, clock=Clock(NOW + timedelta(minutes=2)))["done"] == 1
    [job] = jobs()
    assert (job.status, job.attempts) == ("done", 2)


def test_one_business_backlog_does_not_starve_another(db, tenant):
    """SCL-03: organizations take turns."""
    other = second_tenant(db)
    for _ in range(4):
        enqueue(db, OrgContext.system(tenant.id), "evaluate_exceptions")
    enqueue(db, OrgContext.system(other.id), "evaluate_exceptions")
    db.commit()
    order = []
    drain(handlers={"evaluate_exceptions": lambda _db, org, _p: order.append(org.tenant_id)})
    assert order[:2] == [tenant.id, other.id]
    assert sorted(order) == sorted([tenant.id] * 4 + [other.id])


@postgres_only
def test_two_workers_never_run_the_same_job(db, tenant):
    tenants = [tenant] + [second_tenant(db) for _ in range(2)]
    for business in tenants:
        for _ in range(15):
            enqueue(db, OrgContext.system(business.id), "evaluate_exceptions")
    db.commit()
    ran: list[int] = []
    lock = threading.Lock()

    def handler(session, org, payload):
        with lock:
            ran.append(org.tenant_id)

    start = threading.Barrier(2)
    errors: list[Exception] = []

    def work(name):
        try:
            start.wait(timeout=10)
            drain(handlers={"evaluate_exceptions": handler}, worker=name)
        except Exception as exc:  # noqa: BLE001 (reported by the assertion below)
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not errors, errors
    assert len(ran) == 45
    assert {job.status for job in jobs()} == {"done"}
    assert all(job.attempts == 1 for job in jobs())


# --- scheduled evaluation for every tenant (G2) ------------------------------------------------


def test_a_business_with_no_pos_gets_low_stock_exceptions_on_schedule(db, tenant, location):
    """Acceptance: no connection, and still checked on a clock."""
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="oat-milk",
        name="Oat milk",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
        qty_base=D(2),
        reorder_point_base=D(6),
    )
    db.add(item)
    opened(db, item)
    db.commit()

    scheduler.queue_evaluations()
    scheduler.queue_evaluations()  # a slow worker does not pile them up
    assert [(j.kind, j.status) for j in jobs()] == [("evaluate_exceptions", "pending")]
    assert drain()["done"] == 2  # the evaluation, then the digest it queued

    low = db.scalars(select(ExceptionRecord)).one()
    assert (low.type, low.subject_id, low.status) == ("low_stock", str(item.id), "open")
    assert [(j.kind, j.status) for j in jobs()] == [
        ("evaluate_exceptions", "done"),
        ("send_alert_digest", "done"),
    ]


def test_the_weekly_report_is_queued_once_a_week_and_sent_once(db, tenant, monkeypatch):
    FakeSMTP.outbox = []
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.test", raising=False)
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    scheduler.send_due_reports(NOW)
    scheduler.send_due_reports(NOW)  # a retried scheduler run in the same hour
    assert [(j.kind, j.dedupe_key) for j in jobs()] == [("send_weekly_report", "week:2026-47")]
    drain()
    enqueue(db, OrgContext.system(tenant.id), "send_weekly_report")  # asked again regardless
    db.commit()
    drain()
    assert len(FakeSMTP.outbox) == 1  # the second run found it sent this week
    assert [j.status for j in jobs()] == ["done", "done"]


def test_the_scheduler_runs_the_worker_and_the_evaluations():
    class Recorder:
        def __init__(self):
            self.ids = []

        def add_job(self, func, *args, id, **kwargs):
            self.ids.append(id)

    recorder = Recorder()
    scheduler._add_jobs(recorder)
    assert {"run_job_queue", "queue_evaluations"} <= set(recorder.ids)


# --- invoice reading is a job (SCL-03) ---------------------------------------------------------


INVOICE = {
    "supplier": "Northside",
    "invoice_number": "NS-9",
    "lines": [
        {
            "raw_text": "OAT",
            "product_name": "Oat milk",
            "quantity": 6,
            "unit": "each",
            "pack_size": None,
            "unit_price_cents": 300,
            "line_total_cents": 1800,
        }
    ],
}


def test_an_uploaded_invoice_is_read_by_a_job_not_the_request(db, tenant, location, monkeypatch):
    monkeypatch.setattr(receiving_mod, "get_extractor", lambda: StubExtractor(INVOICE))
    client = TestClient(app)
    client.__enter__()
    try:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        response = client.post(
            "/app/receiving",
            data={"location_id": location.id},
            files={"photo": ("delivery.jpg", jpeg("oat delivery"), "image/jpeg")},
            follow_redirects=False,
        )
        page = client.get(response.headers["location"]).text
        assert "Reading the invoice" in page
        [job] = jobs()
        assert (job.kind, job.status) == ("extract_document", "pending")

        assert drain()["done"] == 1
        with SessionLocal() as check:
            document = check.scalars(select(Document)).one()
            assert document.supplier == "Northside"
            assert document.status is not DocumentStatus.UPLOADED
        assert "Reading the invoice" not in client.get(response.headers["location"]).text

        client.post(f"/app/receiving/{job.payload['document_id']}/reparse")
        assert [j.status for j in jobs()] == ["done", "pending"]  # read again, by a job
    finally:
        client.__exit__(None, None, None)
