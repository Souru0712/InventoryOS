"""Background jobs. APScheduler is enough at this size; no orchestrator needed yet.

In development the jobs run inside the web process. Deployed, they run only in a dedicated
`python -m app.cli scheduler` process holding a Postgres advisory lock, so extra web workers, a
second scheduler, or old and new containers overlapping during a redeploy never run a job twice.
"""

from __future__ import annotations

import functools
import logging
import signal
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from app.config import get_settings
from app.db import SCHEDULER_LOCK, LeaderLock, session_scope
from app.entitlements import owns_snack_box
from app.models import BoxSettings, Plan, ProviderConnection, Tenant
from app.observability import log_context, new_request_id
from app.orchestration.boxes import expire_unpaid, reconcile
from app.orchestration.health import record_snapshot
from app.orchestration.reporting import report_sent_recently, send_weekly_report
from app.orchestration.subscriptions import expire_due_trials
from app.orchestration.sync import run_sync

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

LOCK_CHECK_SECONDS = 60
STANDBY_POLL_SECONDS = 30


def sync_all_tenants() -> None:
    with session_scope() as db:
        tenant_ids = [
            connection.tenant_id
            for connection in db.scalars(
                select(ProviderConnection).where(ProviderConnection.status == "active")
            )
        ]
    for tenant_id in tenant_ids:
        with log_context(tenant_id=tenant_id):
            try:
                with session_scope() as db:
                    stats = run_sync(db, tenant_id)
                log.info("synced tenant %s: %s", tenant_id, stats)
            except Exception:
                log.exception("scheduled sync failed for tenant %s", tenant_id)


def send_due_reports(now: datetime | None = None) -> None:
    """Send each tenant its weekly report at its configured day and hour, once."""
    now = now or datetime.now(UTC)
    with session_scope() as db:
        due = [
            tenant.id
            for tenant in db.scalars(select(Tenant).where(Tenant.plan != Plan.CANCELED))
            if tenant.report_day == now.weekday() and tenant.report_hour == now.hour
        ]
    for tenant_id in due:
        with log_context(tenant_id=tenant_id):
            try:
                with session_scope() as db:
                    # A retried or restarted job lands in the same hour; the email log says if
                    # it went.
                    if report_sent_recently(db, tenant_id, now):
                        log.info(
                            "weekly report for tenant %s already went out this week", tenant_id
                        )
                        continue
                    send_weekly_report(db, tenant_id)
                log.info("weekly report sent for tenant %s", tenant_id)
            except Exception:
                log.exception("weekly report failed for tenant %s", tenant_id)


def close_finished_trials() -> None:
    """A trial ends on its date, not when someone next opens the app."""
    try:
        with session_scope() as db:
            expired = expire_due_trials(db)
        if expired:
            log.info("trials ended for tenants %s", expired)
    except Exception:
        log.exception("could not close finished trials")


def record_health_snapshots() -> None:
    """Keep each tenant's health for the night, so tomorrow can say why it moved."""
    with session_scope() as db:
        tenant_ids = [t.id for t in db.scalars(select(Tenant).where(Tenant.plan != Plan.CANCELED))]
    for tenant_id in tenant_ids:
        with log_context(tenant_id=tenant_id):
            try:
                with session_scope() as db:
                    row = record_snapshot(db, tenant_id)
                log.info("health snapshot for tenant %s: %s", tenant_id, row.score)
            except Exception:
                log.exception("health snapshot failed for tenant %s", tenant_id)


def box_housekeeping() -> None:
    """Cancel unpaid box orders, then bring box allocations back into step with Square."""
    with session_scope() as db:
        tenant_ids = [
            row.tenant_id
            for row in db.scalars(select(BoxSettings))
            if owns_snack_box(row.tenant_id)
        ]
    for tenant_id in tenant_ids:
        try:
            with session_scope() as db:
                expired = expire_unpaid(db, tenant_id)
                open_issues = sum(1 for row in reconcile(db, tenant_id) if row.issues)
            log.info(
                "box housekeeping for tenant %s: %s expired, %s items need a look",
                tenant_id,
                expired,
                open_issues,
            )
        except Exception:
            log.exception("box housekeeping failed for tenant %s", tenant_id)


def _job(func: Callable[[], None]) -> Callable[[], None]:
    """Each run of a job gets its own id on every line it logs, as a web request does."""

    @functools.wraps(func)
    def run() -> None:
        with log_context(request_id=f"job-{new_request_id()}", job=func.__name__):
            func()

    return run


def _add_jobs(scheduler: BaseScheduler) -> None:
    settings = get_settings()
    scheduler.add_job(
        _job(sync_all_tenants),
        "interval",
        minutes=settings.sync_interval_minutes,
        id="sync_all_tenants",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _job(send_due_reports),
        "cron",
        minute=5,
        id="send_due_reports",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _job(close_finished_trials),
        "cron",
        hour=1,
        minute=5,
        id="close_finished_trials",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _job(record_health_snapshots),
        "cron",
        hour=2,
        minute=15,
        id="record_health_snapshots",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _job(box_housekeeping),
        "interval",
        minutes=10,
        id="box_housekeeping",
        max_instances=1,
        coalesce=True,
    )


def start() -> BackgroundScheduler:
    """Run the jobs on a background thread of this process: development only."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    _add_jobs(scheduler)
    scheduler.start()
    _scheduler = scheduler
    log.info(
        "scheduler started in-process: sync every %s minutes", get_settings().sync_interval_minutes
    )
    return scheduler


def run_forever() -> int:
    """Run the jobs in this process until stopped, and only while it holds the scheduler lock.

    A second copy stands by and takes over when the first dies. Returns a non-zero exit code
    when the lock was lost mid-run, so the platform restarts the process.
    """
    lock = LeaderLock(SCHEDULER_LOCK)
    while not lock.acquire():
        log.info("another scheduler holds the lock; standing by")
        time.sleep(STANDBY_POLL_SECONDS)

    scheduler = BlockingScheduler(timezone="UTC")
    _add_jobs(scheduler)
    lost = threading.Event()

    def check_lock() -> None:
        if not lock.alive():
            log.error("scheduler lost its lock; stopping so a restart can take it back")
            lost.set()
            scheduler.shutdown(wait=False)

    scheduler.add_job(
        check_lock,
        "interval",
        seconds=LOCK_CHECK_SECONDS,
        id="scheduler_lock_check",
        max_instances=1,
        coalesce=True,
    )

    def stop(signum, _frame) -> None:
        log.info("scheduler stopping on signal %s", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log.info(
        "scheduler holds the lock: sync every %s minutes", get_settings().sync_interval_minutes
    )
    try:
        scheduler.start()
    finally:
        lock.release()
    return 1 if lost.is_set() else 0


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
