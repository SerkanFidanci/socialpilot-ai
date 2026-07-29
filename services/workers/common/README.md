# Common Worker Area

Celery is only a durable-job wake-up mechanism: PostgreSQL remains the source of
truth for job state, attempts, retries, and dead-letter handling. The process-local
composition root is `services/api/app/worker/composition.py`; it owns one engine
per worker process and opens a fresh session for every drain iteration.

## Tasks

DB-polling drain tasks exist for ingest, technical analysis, scene/speech, video
understanding, stale-job recovery, and outbox dispatch. Every task re-derives its
work from PostgreSQL with `SKIP LOCKED` claims and stops on an empty queue or its
configured batch limit, so a duplicate or out-of-order message cannot duplicate
work or advance an asset.

## Outbox publisher

`CeleryOutboxPublisher` maps each `*.requested` event to its drain task and sends a
message with no arguments — no object key, signed URL, tenant identity, credential,
or media byte reaches the broker. An event is marked `published` only after the
enqueue succeeds; a broker outage is transient and leaves it unpublished for bounded
retry. Completion events are notification-only in this phase, and an unregistered
event type is dead-lettered rather than silently dropped.

## Event loop

An asyncpg connection belongs to the loop that opened it, so each worker process
owns exactly one event loop and every task runs on it through `WorkerContext.run`.
Using `asyncio.run` per task would break pooled connections on the second task in
the same process. Shutdown disposes the engine, shuts down async generators, and
closes the loop.

## Beat

Beat runs as its own read-only `celery-beat` Compose service — never a worker `-B`
flag — and schedules outbox dispatch, a fallback drain per media step, and stale-job
recovery. It is a safety net for lost messages, never a correctness requirement.
Development Compose runs exactly one beat process; production HA/leader election is
deferred to Phase 1E.

See `docs/architecture/background-jobs.md` for the routing table, schedule entries,
and interval settings.
