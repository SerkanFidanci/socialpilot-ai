# ADR-005: Transactional Outbox

**Status:** Accepted for Phase 0 planning
**Date:** 2026-07-27

## Context

Domain changes such as business creation and media upload completion must lead to background processing or external orchestration. A database commit followed by a direct message publish can lose an event on process failure; publishing first can announce work that later rolls back.

## Decision

Write the domain state change and an `outbox_events` record in the same PostgreSQL transaction. A background publisher claims unpublished records, publishes to Celery or a future n8n transport, tracks attempts/errors, and marks events published only after a successful handoff. Consumers are idempotent and may use inbox/idempotency records.

## Consequences

- Event delivery is at least once, not exactly once; each consumer must handle duplicates safely.
- Outbox rows need locking/claiming, retries, observability, retention, and dead-letter escalation.
- User-visible mutations gain durable causal records and can be replayed operationally.
- There is modest database and publisher complexity, accepted in exchange for no lost committed-domain events.

## Rejected alternatives

- Publish directly after commit: rejected because crashes create lost work.
- Publish before commit: rejected because consumers can observe rolled-back state.
- Distributed database/message transactions: rejected for Phase 0 operational complexity and ecosystem constraints.
