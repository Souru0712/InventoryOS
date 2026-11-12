# ADR-0044: The job queue and scheduled evaluation for every business

- **Status:** Accepted, 2026-11-12 (owner: "Yes, merge it"; PR #42)
- **Decision ID:** none (implements T1.20 under ID-06 / ADR-0006; REL-07, SCL-03, SAAS-T-06)
- **Tasks:** T1.20; T1.21 and later background work queue through it

## Context

ADR-0006 chose a Postgres `jobs` table drained by the leader-locked scheduler process. Until now:
- stock and expiry were evaluated only inside the Square sync, so a business without a POS got no
  exceptions (G2);
- invoice photos were read inside the upload request, which waited on a slow model call (C8);
- the alert digest and the weekly report were sent once, and a failed send was only logged.

## Decision

1. **`jobs` table** (migration `cb043c5cd27a`):
   - **What:** each job always names its organization (SAAS-T-06), with a kind (named CHECK),
     a JSON payload and an optional dedupe key;
   - **Progress:** status (`pending` → `running` → `done` / `dead`), attempts and maximum, run
     time, lock time and worker, last error, created and finished times;
   - **Dedupe:** a partial unique index keeps one *pending* copy per (organization, kind,
     dedupe key), so a slow worker never piles up duplicate work.
2. **Rules** (`app/transform/jobs.py`, pure):
   - **Kinds:** four, each with a label and a maximum number of attempts;
   - **Back-off:** 1, 2, 4, 8 … minutes, at most an hour;
   - **Abandoned jobs:** a job left running for 15 minutes was abandoned.
3. **Service** (`app/jobs.py`):
   - **Queueing:** `enqueue()` adds a job. With a dedupe key, a copy already waiting is the job.
   - **Draining:** `drain()` claims one due job at a time. It uses `FOR UPDATE SKIP LOCKED`
     through `next_due_job()` in `app/access/system.py`, the one cross-organization claim, and
     serves organizations round-robin so a backlog cannot starve another business (SCL-03).
   - **Running:** a handler's work and the job's completion commit together. A failure rolls
     the work back and is retried after the back-off.
   - **Dead letters:** after the last attempt the job is `dead` and a `job_failed` exception
     opens, one per kind with an occurrence count. The kind's next success resolves it as
     system. The exception's title never carries the error text; that stays on the job, for
     the founder console (T4.07).
   - **Abandoned jobs** go back on the queue, so execution is at least once (REL-07), and every
     handler is safe to repeat.
4. **Kinds now:**
   - `evaluate_exceptions` runs the stock and expiry evaluation, then queues the digest;
   - `send_alert_digest`;
   - `send_weekly_report` stops if this week's report is already in the email log;
   - `extract_document` reads an invoice photo.
5. **The scheduler process:**
   - **Worker:** it runs the worker every `JOB_POLL_SECONDS` (5).
   - **Evaluation:** it queues an evaluation for *every* subscribed business every
     `EVALUATION_INTERVAL_MINUTES` (60), POS or not.
   - **Weekly report:** it queues each business's report once a week; the dedupe key names the
     ISO week.
   - **Sync:** the Square sync queues the digest instead of sending it inline.
   - **Development:** the scheduler runs in the web process, so the worker does too.
6. **Uploads queue the reading:**
   - the request returns at once, and the review page says the invoice is being read and
     refreshes itself;
   - "Read it again" queues another read;
   - a photo the model cannot read marks the document failed and is not retried; an API or
     network error is retried.

## Alternatives considered

- **Redis with RQ or Celery:** rejected by ID-06 at this scale.
- **APScheduler jobs per business:** they cannot retry, dead-letter or survive a restart, and
  they need a code change per business.
- **Retrying inside the request:** it holds the request, and a crash loses the work.
- **Dead letters only in the log:** the business never learns that its email stopped; an
  exception is the one place things that need a person go (ADR-0043).

## Consequences

- New background work is a kind in `app/transform/jobs.py` with its CHECK (a migration) and a
  handler in `app/jobs.py`, and it must be safe to run twice.
- Exceptions now reach Standalone businesses on schedule, closing the gap ADR-0043 noted.
- Invoice reading in development and production happens in the scheduler process. Tests drain
  the queue explicitly with `drain()`.
- Built in T1.20 with migration `cb043c5cd27a`.
