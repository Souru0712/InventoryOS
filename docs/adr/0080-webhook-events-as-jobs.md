# ADR-0080: Webhook events stored once and processed as jobs

- **Status:** Proposed, 2026-11-20 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T3.06: POS-FR-11, DR-21, SEC-11, PERF-07, AC-POS-03,
  AC-POS-10)
- **Tasks:** T3.06; T3.08 reconciles, T3.09 shows unprocessed events

## Context

The Square webhook acted inside the request: a catalog event re-read the catalog before answering,
and nothing recorded an event, so Square's retries were acted on again. `webhook_events` existed
but nothing wrote to it.

## Decision

1. **A verified event is stored before anything acts on it.** `webhook_events` (unique per
   provider and event id) gains a status (`received`, `processed`, `ignored`), the kind of job
   that acts on it (`handled_by`), the entities it names and when it was processed.
   `app/webhook_events.py` is its only writer (a test enforces it). A repeated delivery finds the
   id taken and is answered `duplicate` with nothing done.
2. **The answer comes at once (PERF-07).** An order event (`order.created`, `order.updated`,
   `order.fulfillment.updated`) queues `ingest_orders`. A catalog event queues the new
   `refresh_catalog`. Both are deduplicated per business, so a burst is one waiting job, and the
   request reads nothing from Square.
3. **An event is processed when its job succeeds.** The job marks the events of its kind that were
   received for the business before it started. An event arriving mid-run waits for the next run.
   A failed job leaves its events `received` and is retried.
4. **Revocation and Square's counts are acted on as they arrive**, since they touch only our rows.
   An unknown merchant or a type nothing reads is stored as `ignored`, so its retries are no-ops
   too.
5. **The catalog read is provider-neutral** (`refresh_catalog()` in `app/pos_catalog.py`, through
   the adapter). It skips when a catalog read ran within the last minute, and refreshes the
   external catalog. The legacy rows follow with the hourly sync until T3.12.
6. **A bad signature is refused before parsing** and logged without its payload (SEC-11,
   AC-POS-10). An event with no id is refused.

## Alternatives considered

- **Acting in the request after storing the event:** reading Square takes seconds, and the
  acknowledgement must take two at most.
- **One job per event:** the order read already takes everything changed since its cursor, so one
  waiting read covers every event of a burst.

## Consequences

- A catalog change reaches the legacy `sold_items` rows only with the hourly sync, until T3.12.
- Events whose job keeps failing stay `received`, where T3.09 counts them.
