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

from app.access import OrgContext
from app.access.system import connected_tenant_ids, subscribed_tenants
from app.config import get_settings
from app.db import SCHEDULER_LOCK, LeaderLock, session_scope
from app.jobs import drain, enqueue
from app.observability import log_context, new_request_id
from app.orchestration.health import record_snapshot
from app.orchestration.subscriptions import expire_due_trials
from app.orchestration.sync import run_sync
from app.throttle import prune

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

LOCK_CHECK_SECONDS = 60
STANDBY_POLL_SECONDS = 30


def sync_all_tenants() -> None:
    with session_scope() as db:
        tenant_ids = connected_tenant_ids(db)
    for tenant_id in tenant_ids:
        with log_context(tenant_id=tenant_id):
            try:
                with session_scope() as db:
                    stats = run_sync(db, OrgContext.system(tenant_id))
                log.info("synced tenant %s: %s", tenant_id, stats)
            except Exception:
                log.exception("scheduled sync failed for tenant %s", tenant_id)


def send_due_reports(now: datetime | None = None) -> None:
    """Queue each tenant's weekly report at its configured day and hour, once a week; the job
    retries a failed send (T1.20)."""
    now = now or datetime.now(UTC)
    with session_scope() as db:
        due = [
            tenant.id
            for tenant in subscribed_tenants(db)
            if tenant.report_day == now.weekday() and tenant.report_hour == now.hour
        ]
    year, week, _ = now.isocalendar()
    for tenant_id in due:
        with log_context(tenant_id=tenant_id):
            try:
                with session_scope() as db:
                    enqueue(
                        db,
                        OrgContext.system(tenant_id),
                        "send_weekly_report",
                        dedupe_key=f"week:{year}-{week:02d}",
                    )
                log.info("weekly report queued for tenant %s", tenant_id)
            except Exception:
                log.exception("could not queue the weekly report for tenant %s", tenant_id)


def queue_evaluations() -> None:
    """Check every business's stock and expiry on a clock, POS or not (T1.20, G2)."""
    with session_scope() as db:
        tenant_ids = [tenant.id for tenant in subscribed_tenants(db)]
    for tenant_id in tenant_ids:
        with log_context(tenant_id=tenant_id):
            try:
                with session_scope() as db:
                    enqueue(
                        db,
                        OrgContext.system(tenant_id),
                        "evaluate_exceptions",
                        dedupe_key="evaluate",
                    )
            except Exception:
                log.exception("could not queue evaluation for tenant %s", tenant_id)


def queue_ledger_verification(now: datetime | None = None) -> None:
    """Check every business's ledger each night (DR-03, REL-08): once per date."""
    now = now or datetime.now(UTC)
    with session_scope() as db:
        tenant_ids = [tenant.id for tenant in subscribed_tenants(db)]
    for tenant_id in tenant_ids:
        with log_context(tenant_id=tenant_id):
            try:
                with session_scope() as db:
                    enqueue(
                        db,
                        OrgContext.system(tenant_id),
                        "verify_ledger",
                        dedupe_key=f"night:{now.date().isoformat()}",
                    )
            except Exception:
                log.exception("could not queue ledger verification for tenant %s", tenant_id)


def run_job_queue() -> None:
    """The worker: run whatever background jobs are due (ID-06)."""
    try:
        stats = drain()
        if any(stats.values()):
            log.info("ran background jobs: %s", stats)
    except Exception:
        log.exception("the job worker failed")


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
        tenant_ids = [tenant.id for tenant in subscribed_tenants(db)]
    for tenant_id in tenant_ids:
        with log_context(tenant_id=tenant_id):
            try:
                with session_scope() as db:
                    row = record_snapshot(db, OrgContext.system(tenant_id))
                log.info("health snapshot for tenant %s: %s", tenant_id, row.score)
            except Exception:
                log.exception("health snapshot failed for tenant %s", tenant_id)


def prune_throttles() -> None:
    """Throttle counters whose windows and locks are long over are only history."""
    try:
        with session_scope() as db:
            removed = prune(db)
        if removed:
            log.info("pruned %s throttle counters", removed)
    except Exception:
        log.exception("could not prune throttle counters")


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
        _job(run_job_queue),
        "interval",
        seconds=settings.job_poll_seconds,
        id="run_job_queue",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _job(queue_evaluations),
        "interval",
        minutes=settings.evaluation_interval_minutes,
        id="queue_evaluations",
        max_instances=1,
        coalesce=True,
    )
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
        _job(prune_throttles),
        "cron",
        hour=3,
        minute=25,
        id="prune_throttles",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _job(queue_ledger_verification),
        "cron",
        hour=2,
        minute=45,
        id="queue_ledger_verification",
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
