# System Overview

## Purpose

SocialPilot AI starts as a modular monolith: one FastAPI codebase and one PostgreSQL system of record, with isolated worker processes for asynchronous work. This preserves transactional domain boundaries while allowing expensive and failure-prone work to run outside request handling.

## Phase 0 topology

```mermaid
flowchart LR
    Client["Mobile / Admin clients"] --> API["FastAPI modular monolith"]
    API --> DB[("PostgreSQL")]
    API --> Redis[("Redis")]
    Redis --> Worker["Celery workers"]
    Worker --> DB
    API --> StoragePort["Object-storage adapter"]
    Client --> StoragePort
    Worker --> StoragePort
    DB --> Outbox["Transactional outbox"]
    Outbox --> Worker
```

The client requests a constrained upload session from the API, transfers media directly to object storage, then sends a completion command. FastAPI and n8n never receive large media bytes. Slice 0C defines the provider-neutral storage port and may use a local fake or MinIO-compatible adapter; production provider selection, credentials, and connection are outside Slice 0C.

## Runtime responsibilities

| Runtime | Responsibility | Must not own |
|---|---|---|
| FastAPI API | Authentication boundary, authorization, validation, use-case invocation, OpenAPI, dependency readiness | Business rules in controllers or media-byte proxying |
| PostgreSQL | Authoritative domain state, durable jobs, audit records, idempotency state, outbox rows | Queue-only transient state |
| Redis/Celery | Dispatch and execute bounded background work | Source-of-truth domain state |
| Worker | Execute use-case commands, track attempts/timeout/correlation/dead-letter status | Bypass tenant or authorization checks |
| Object-storage adapter | Create multipart instructions and verify completed-object metadata | Domain policy or credential exposure |
| n8n (later) | External workflow coordination and notifications | Domain state, authorization, money decisions, binary media transfer |

## Core boundaries

- **Identity:** Slice 0B introduces a provider-neutral port and test adapter that converts a verified subject into an internal principal. Actual Firebase/OIDC or other production-provider wiring is deferred until after Slice 0B.
- **Tenant:** a business is the operational tenant. Membership and permission checks authorize access to every business resource.
- **Domain modules:** identity, businesses, media, jobs, outbox, idempotency, and audit each own use cases and persistence mappings.
- **Adapters:** storage, identity, provider, and n8n adapters are replaceable infrastructure implementations. Domain services never import a provider SDK.

## Data and event consistency

Use cases update PostgreSQL state and insert an outbox event in one transaction. A worker publishes unprocessed events after commit with retries and an inbox/idempotency discipline at consumers. Direct synchronous calls are reserved for bounded dependency checks and adapter commands that do not replace the outbox guarantee.

## Non-goals for this phase

The diagram deliberately excludes AI, FFmpeg, social/ad platforms, billing, the Slice 0A admin web panel, and production storage/identity providers. These are later adapters or separately scoped work that must conform to the boundaries established here.
