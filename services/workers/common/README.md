# Common Worker Area

Celery is only a durable-job wake-up mechanism: PostgreSQL remains the source of
truth for job state, attempts, retries, and dead-letter handling. The process-local
composition root is `services/api/app/worker/composition.py`; it owns one engine
per worker process and opens a fresh session for every drain iteration.

This slice provides DB-polling drain tasks for ingest, technical analysis,
scene/speech, video understanding, and stale-job recovery. Beat scheduling and a
concrete outbox-to-Celery publisher are intentionally not configured yet.
