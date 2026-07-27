# ADR-003: n8n Orchestration Boundary

**Status:** Accepted for Phase 0 planning
**Date:** 2026-07-27

## Context

n8n can coordinate external schedules, notifications, and webhooks, but it is not a transactional domain runtime or a secure binary-processing platform. Critical platform rules need tested backend ownership and PostgreSQL durability.

## Decision

n8n may later trigger or coordinate external workflows through authenticated, idempotent backend contracts and durable event envelopes. The FastAPI modular monolith and PostgreSQL retain ownership of authorization, tenant state, business rules, entitlement/budget decisions, idempotency, and auditability.

## Consequences

- Workflows consume and emit narrow event/API contracts rather than owning domain tables.
- n8n must not receive or move large media files, store OAuth tokens in plaintext, make authorization decisions, calculate entitlements, or determine advertising budgets.
- Backend services must tolerate at-least-once workflow deliveries using idempotency keys and signed/private ingress.
- Phase 0 creates no n8n workflow; it only preserves a future adapter/event boundary.

## Rejected alternatives

- Putting business processes in n8n: rejected because logic becomes hard to test, version, secure, and transactionally coordinate.
- Eliminating orchestration entirely: rejected because external scheduling/notification coordination is still useful behind this boundary.
