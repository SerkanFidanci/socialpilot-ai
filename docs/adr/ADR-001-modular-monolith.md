# ADR-001: Modular Monolith

**Status:** Accepted for Phase 0 planning
**Date:** 2026-07-27

## Context

The system has tightly connected tenant, entitlement, media, content, publication, and advertising concerns. At the start, the team needs fast schema/domain evolution and strong transactional consistency without distributed-transaction complexity. Heavy work still requires isolation from the HTTP request process.

## Decision

Build one FastAPI modular monolith backed by PostgreSQL. Organize code into domain modules with explicit interfaces and repository boundaries. Run Celery workers as separately deployable processes that execute commands against the same modular-monolith domain contracts and system of record.

## Consequences

- Business transactions, outbox writes, and tenant enforcement remain local to one database boundary.
- Modules must not bypass one another through direct table access; later extraction requires a deliberate ADR and contract.
- Heavy media/AI/render work can scale in dedicated workers without introducing microservice ownership now.
- Deployment remains simpler initially, but the codebase needs disciplined module boundaries to avoid a monolith without structure.

## Rejected alternatives

- Microservices from Phase 0: rejected due to premature distributed consistency, operational complexity, and a small initial team.
- A single synchronous API process for all work: rejected because jobs require timeout, retries, isolation, and dead-letter handling.
