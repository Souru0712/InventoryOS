"""Background jobs: queueing, claiming and running them (T1.20; ID-06, REL-07, SCL-03, ADR-0044).

Work that should not hold up a request, or that runs on a clock, is a job: one row per piece of
work for one organization. The scheduler process's worker drains the queue with drain(): it
claims each job with FOR UPDATE SKIP LOCKED, so two workers never run the same one, and serves
organizations round-robin, so one business's backlog cannot starve the others. A handler runs at
least once and must be safe to repeat. A failure is retried after a growing wait; after the last
attempt the job is dead-lettered into a `job_failed` exception, which the kind's next success
clears.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.access.system import abandoned_jobs, next_due_job
from app.db import session_scope
from app.exceptions import apply_findings, clear, record_exception
from app.models import Job
from app.observability import log_context
from app.transform.exceptions import Finding
from app.transform.jobs import ABANDONED_AFTER, KINDS, after_failure, check_kind

log = logging.getLogger(__name__)

WORKER = f"{socket.gethostname()}:{os.getpid()}"[:120]
Handler = Callable[[Session, OrgContext, dict], None]


def enqueue(
    db: Session,
    org: OrgContext,
    kind: str,
    payload: dict | None = None,
    *,
    dedupe_key: str | None = None,
    run_at: datetime | None = None,
) -> Job:
    """Queue work for org's organization. With a dedupe key, a copy already waiting is the job:
    asking twice queues it once."""
    found = check_kind(kind)
    if dedupe_key is not None:
        waiting = _waiting(db, org, kind, dedupe_key)
        if waiting is not None:
            return waiting
    try:
        with db.begin_nested():
            job = Job(
                tenant_id=org.tenant_id,
                kind=kind,
                payload=payload or {},
                dedupe_key=dedupe_key,
                max_attempts=found.max_attempts,
                run_at=run_at or datetime.now(UTC),
            )
            db.add(job)
            db.flush()
        return job
    except IntegrityError:
        # Queued by someone else a moment ago.
        waiting = _waiting(db, org, kind, dedupe_key)
        if waiting is None:
            raise
        return waiting


def _waiting(db: Session, org: OrgContext, kind: str, dedupe_key: str | None) -> Job | None:
    return db.scalars(
        select_scoped(org, Job).where(
            Job.kind == kind, Job.dedupe_key == dedupe_key, Job.status == "pending"
        )
    ).first()


def _handlers() -> dict[str, Handler]:
    # Imported here: these modules queue follow-up work through this one.
    from app.extract.invoice import ExtractionError
    from app.ledger.verify import drifts, findings
    from app.orchestration.alerts import evaluate_and_store, send_alert_digest
    from app.orchestration.receiving import parse_document
    from app.orchestration.reporting import report_sent_recently, send_weekly_report

    def evaluate(db: Session, org: OrgContext, payload: dict) -> None:
        stats = evaluate_and_store(db, org)
        log.info("evaluated exceptions for tenant %s: %s", org.tenant_id, stats)
        enqueue(db, org, "send_alert_digest", dedupe_key="digest")

    def digest(db: Session, org: OrgContext, payload: dict) -> None:
        send_alert_digest(db, org)

    def weekly(db: Session, org: OrgContext, payload: dict) -> None:
        # A repeat in the same week finds it sent (the email log) and stops.
        if report_sent_recently(db, org, datetime.now(UTC)):
            return
        send_weekly_report(db, org)

    def extract(db: Session, org: OrgContext, payload: dict) -> None:
        try:
            parse_document(db, org, int(payload["document_id"]))
        except ExtractionError:
            # The photo cannot be read: the document now says so and offers to try again.
            log.info("document %s could not be read", payload["document_id"])
        except ValueError:
            # Applied or gone since it was queued: there is nothing left to read.
            log.info("document %s no longer needs reading", payload["document_id"])

    def verify(db: Session, org: OrgContext, payload: dict) -> None:
        found = drifts(db, org)
        # Opens, refreshes, and once a drift is gone resolves, critical balance_drift exceptions.
        apply_findings(db, org, findings(db, org, found), frozenset({"balance_drift"}))
        if found:
            # The founder's alert (OBS-06, drift > 0): an error reaches error reporting.
            log.error(
                "ledger drift at %d item location(s) for tenant %s: %s",
                len(found),
                org.tenant_id,
                ", ".join(f"{d.inventory_item_id}:{d.location_id}" for d in found),
            )

    return {
        "evaluate_exceptions": evaluate,
        "verify_ledger": verify,
        "send_alert_digest": digest,
        "send_weekly_report": weekly,
        "extract_document": extract,
    }


@dataclass(frozen=True)
class Claimed:
    id: int
    tenant_id: int
    kind: str
    payload: dict
    attempts: int


def _claim(now: datetime, served: set[int], worker: str) -> Claimed | None:
    with session_scope() as db:
        job = next_due_job(db, now, served)
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_at = now
        job.locked_by = worker
        return Claimed(job.id, job.tenant_id, job.kind, dict(job.payload or {}), job.attempts)


def _fail(db: Session, job: Job, error: str, now: datetime) -> str:
    """Retry later, or dead-letter into an exception after the last attempt."""
    job.last_error = error[:2000]
    job.locked_at = None
    job.locked_by = None
    step = after_failure(job.attempts, job.max_attempts, now)
    if not step.dead:
        job.status = "pending"
        job.run_at = step.run_at or now
        return "retried"
    job.status = "dead"
    job.finished_at = now
    label = KINDS[job.kind].label
    record_exception(
        db,
        OrgContext.system(job.tenant_id),
        Finding(
            type="job_failed",
            subject_type="job_kind",
            subject_id=job.kind,
            title=f"{label} failed {job.attempts} times in a row and has stopped.",
        ),
        now=now,
    )
    return "dead"


def run_claimed(
    claimed: Claimed,
    handlers: dict[str, Handler] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    """Run one claimed job; its work and its completion commit together. Returns what became
    of it: done, retried or dead."""
    handler = (handlers or _handlers())[claimed.kind]
    org = OrgContext.system(claimed.tenant_id)
    with log_context(tenant_id=claimed.tenant_id, job=claimed.kind, job_id=claimed.id):
        try:
            with session_scope() as db:
                handler(db, org, claimed.payload)
                job = db.get(Job, claimed.id)
                if job is None:
                    raise LookupError(f"job {claimed.id} disappeared while it ran")
                job.status = "done"
                job.finished_at = clock()
                job.locked_at = None
                job.last_error = None
                clear(db, org, ("job_failed", "job_kind", claimed.kind), clock())
            return "done"
        except Exception as exc:
            log.exception(
                "job %s (%s) failed on attempt %s", claimed.id, claimed.kind, claimed.attempts
            )
            with session_scope() as db:
                job = db.get(Job, claimed.id)
                if job is None:
                    raise
                return _fail(db, job, f"{type(exc).__name__}: {exc}", clock())


def requeue_abandoned(now: datetime) -> int:
    """Jobs a dead worker left running go back on the queue, or dead-letter if that was their
    last attempt (at-least-once, REL-07)."""
    with session_scope() as db:
        jobs = abandoned_jobs(db, now - ABANDONED_AFTER)
        for job in jobs:
            with log_context(tenant_id=job.tenant_id, job=job.kind, job_id=job.id):
                log.warning("job %s was abandoned by %s; queued again", job.id, job.locked_by)
                _fail(db, job, "abandoned: the worker stopped while running it", now)
        return len(jobs)


def drain(
    *,
    limit: int = 100,
    worker: str = WORKER,
    handlers: dict[str, Handler] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, int]:
    """Run due jobs, one organization at a time round-robin, until none is due or `limit`
    have run. Returns how many finished each way."""
    stats = {"done": 0, "retried": 0, "dead": 0, "requeued": requeue_abandoned(clock())}
    table = handlers or _handlers()
    served: set[int] = set()
    ran = 0
    while ran < limit:
        claimed = _claim(clock(), served, worker)
        if claimed is None:
            if not served:
                break
            served = set()  # a new round: every organization may go again
            continue
        served.add(claimed.tenant_id)
        stats[run_claimed(claimed, table, clock)] += 1
        ran += 1
    return stats
