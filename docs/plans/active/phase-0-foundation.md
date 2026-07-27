# Phase 0 — Foundation Plan

**Status:** Active planning — implementation has not started.
**Scope owner:** Backend platform foundation
**Source requirements:** `docs/product/product-requirements.md`

## 1. Objective

Create the documented, incrementally deliverable foundation for SocialPilot AI: a modular-monolith backend platform with tenant-safe identity and business boundaries, direct multipart media-upload orchestration, durable events, and operational primitives. Phase 0 establishes contracts and infrastructure seams; it does not implement media analysis, AI generation, publishing, advertising, billing, or mobile UI.

## 2. Scope

- Monorepo baseline and Python backend project structure.
- FastAPI application shell, Pydantic Settings, OpenAPI baseline, structured JSON logs, health and readiness endpoints.
- PostgreSQL system of record, SQLAlchemy 2 async access, Alembic migrations, Redis, and Celery worker skeleton.
- Slice 0B user, external-identity, business, membership, RBAC, authorization, tenant-scoped repository boundaries, identity-provider port, and test adapter.
- Slice 0C provider-neutral direct-upload multipart session contract, completion validation contract, media asset record, ingest-job creation, and a local fake or MinIO-compatible adapter.
- Transactional outbox, job state/attempt model, idempotency records, audit-log seed, and RFC 9457-style problem response.
- Docker Compose and CI verification command design.

## 3. Out of scope

- Flutter screens, AI/provider calls, video analysis, FFmpeg, n8n workflows, social/ad connectors, advertising, subscriptions, payments, and a production object-storage connection.
- The admin web panel is not part of Slice 0A and has no implementation work in this planning task.
- Actual Firebase, OIDC, or another production identity-provider integration. Slice 0B defines only the identity-provider port and test adapter; a real provider integration follows Slice 0B.
- Production secrets, API keys, and any committed credentials.

## 4. Architectural decisions

1. One deployable FastAPI modular monolith owns domain state; Celery workers are separate processes, not microservices.
2. PostgreSQL is authoritative. Redis is a broker/cache and must not become a source of truth.
3. `business_id` is the tenant boundary for business-owned data. Repository APIs require it and authorization resolves it from a verified membership, never only from a route parameter.
4. Controllers translate HTTP only; use cases own business rules and repositories own persistence.
5. Browser/mobile clients upload large media directly to an object-storage adapter using short-lived multipart URLs. The API never proxies media bytes.
6. Every state-changing public request evaluates authorization and idempotency. Durable domain-event publication uses the same database transaction as the state change.
7. External identity, storage, n8n, and future AI/social/ads integrations remain behind adapter interfaces.

## 4.1 Recorded scope decisions

- **ADR catalogue:** `docs/adr/` and its actual filenames are the sole source of ADR numbering. Accepted ADRs are never renumbered. The product requirements ADR list is a non-canonical proposal and does not require reconciliation or a product-document edit.
- **Slice 0A boundary:** Slice 0A is development and backend infrastructure only. It excludes the admin web panel and all identity-provider integration.
- **Identity sequence:** Slice 0B introduces the provider-neutral identity port and a test adapter. A real Firebase, OIDC, or other production provider integration is deferred until after Slice 0B.
- **Storage sequence:** Slice 0C introduces the provider-neutral storage port and may use a local fake or MinIO-compatible adapter. Selecting or connecting a production object-storage provider is deferred beyond Slice 0C.

## 5. Delivery order and measurable acceptance criteria

### Slice 0A — Development foundation

Slice 0A is limited to development and backend infrastructure. It does not include the admin web panel, an identity-provider port, a test identity adapter, or a real authentication-provider integration.

Order: project layout → settings → FastAPI lifecycle → async database/Redis/Celery wiring → observability → health endpoints → test tooling → local Compose and CI commands.

Acceptance criteria:

- A configuration validation test rejects a missing required non-secret setting and never prints secret values.
- `/health/live` returns `200` without dependency checks; `/health/ready` returns `200` only when PostgreSQL and Redis checks succeed, otherwise `503` with the documented error format.
- A JSON log for a request includes timestamp, level, event, correlation ID, environment, and redacts authorization and signed-URL values.
- A worker task records correlation ID, attempt, timeout, status, and dead-letter destination in its job contract.
- `make lint`, `make test-backend`, and `make verify` have deterministic documented responsibilities; CI invokes the same verification entry points.

### Slice 0B — Identity and tenant foundation

Slice 0B defines the provider-neutral identity-provider port and a test adapter. It does not integrate Firebase, OIDC, or another production identity provider; that work begins only after Slice 0B.

Order: identity boundary → users/identities → businesses/memberships/roles → authorization policy → tenant-scoped repositories → membership and isolation tests.

Acceptance criteria:

- A verified principal can create a business and becomes its `owner` in one database transaction.
- `GET /api/v1/businesses` returns only businesses in which the principal has an active membership.
- A member from business A receives `404` for a business-B resource, without existence disclosure.
- Role-policy tests prove that `viewer` cannot mutate, `editor` cannot manage memberships, and only `owner` can delete a business or change billing-scoped settings.
- Every business-owned repository query requires `business_id`; tests fail when an unscoped query path is attempted.

### Slice 0C — Media upload start

Slice 0C may use a local fake or MinIO-compatible storage adapter to exercise the port. It does not select, provision, credential, or connect a production object-storage provider.

Order: media states → storage port/fake → upload session → multipart instructions → completion verification → media asset persistence → ingest job/outbox event.

Acceptance criteria:

- `POST /api/v1/businesses/{business_id}/media/uploads` returns an upload-session ID, opaque object key, part instructions, expiry, and checksum requirements; it does not return a long-lived provider credential.
- A caller without upload permission or outside the tenant receives `403`/`404` before an adapter call is made.
- Completion accepts only the expected session state and declared parts, verifies checksum and object metadata through the storage port, and is idempotent for the same request key.
- Completion creates one `media_asset`, one ingest job, and one `media.upload_completed` outbox event atomically.
- Actual MIME/content inspection, malware scan, ffprobe, proxies, and analysis are explicitly deferred to later slices; no bytes pass through FastAPI or n8n.

### Slice 0D — Events and operational foundation

Order: job model → outbox publisher → idempotency store → audit-log writer → error catalogue → operational dashboards/metrics contracts.

Acceptance criteria:

- A successful mutation and its outbox event commit or roll back together.
- The publisher can retry an unpublished event without producing duplicate consumer-visible effects; attempts and last error are retained.
- Repeating a completed mutation with the same tenant, actor, operation, and idempotency key returns the stored response; a payload mismatch returns `409`.
- Security-relevant mutations produce immutable audit records without credentials, signed URLs, or raw media metadata.
- Every API failure uses the documented problem shape and includes a correlation ID and stable machine code.

## 6. Files to create or change

### This planning task

- `docs/plans/active/phase-0-foundation.md`
- `docs/architecture/overview.md`
- `docs/architecture/backend-modules.md`
- `docs/architecture/tenant-isolation.md`
- `docs/architecture/media-upload.md`
- `docs/architecture/background-jobs.md`
- `docs/architecture/error-handling.md`
- `docs/adr/ADR-001-modular-monolith.md`
- `docs/adr/ADR-002-direct-object-storage-upload.md`
- `docs/adr/ADR-003-n8n-orchestration-boundary.md`
- `docs/adr/ADR-004-provider-adapter-pattern.md`
- `docs/adr/ADR-005-transactional-outbox.md`
- `docs/adr/README.md`
- `docs/index.md`

### Expected implementation files, later and only slice by slice

- Root workspace/Make/Compose/CI configuration; `services/api/` application, migration, and test files.
- API core modules for settings, logging, errors, database, Redis/Celery, health, and observability.
- Identity, businesses, media, jobs, outbox, idempotency, and audit-log modules with migrations and tests.
- Storage and identity adapter ports plus non-production fakes.

## 7. Initial database table list

| Area | Initial tables | Notes |
|---|---|---|
| Identity | `users`, `external_identities` | User is global; external provider subject is unique per provider. |
| Tenant/RBAC | `businesses`, `business_members`, `roles`, `member_roles` | `businesses.id` is the tenant key. Membership is the authorization anchor. |
| Media intake | `media_assets`, `media_upload_sessions` | Asset and session both carry `business_id`; object key is opaque. |
| Operations | `jobs`, `job_attempts`, `outbox_events`, `idempotency_keys`, `audit_logs` | Durable statuses, correlation IDs, attempts, and error references. |

All identifiers are UUIDs; timestamps are UTC; business-owned records include `business_id`; money fields introduced later use integer minor units plus currency.

## 8. First API endpoints

| Method | Path | Slice | Contract |
|---|---|---|---|
| GET | `/health/live` | 0A | Process liveness, no dependency check. |
| GET | `/health/ready` | 0A | PostgreSQL and Redis readiness. |
| GET | `/api/v1/me` | 0B | Current principal only. |
| GET, POST | `/api/v1/businesses` | 0B | List authorized businesses; create business and owner membership. |
| GET, PATCH, DELETE | `/api/v1/businesses/{business_id}` | 0B | Tenant-authorized business resource. |
| GET, POST | `/api/v1/businesses/{business_id}/members` | 0B | Role-gated membership management. |
| POST | `/api/v1/businesses/{business_id}/media/uploads` | 0C | Create multipart upload session. |
| POST | `/api/v1/businesses/{business_id}/media/uploads/{session_id}/complete` | 0C | Validate completion and create ingest work. |
| GET | `/api/v1/jobs/{job_id}` | 0D | Authorized tenant job status. |

## 9. Security risks

- IDOR/tenant escape through route IDs, background jobs, or event consumers.
- Forged identity claims or a provider subject associated with the wrong local user.
- Reused, overly broad, logged, or leaked multipart signed URLs.
- Content-type spoofing, decompression/parser abuse, poisoned metadata, checksum mismatch, and malicious media.
- Duplicate completion/mutation requests and duplicate message delivery.
- Secrets and personal data leaking through configuration, error details, structured logs, audit records, or traces.
- Queue poisoning, unbounded retries, and jobs executing after authorization/state changes.

## 10. Tenant-isolation checklist

- [ ] Resolve the authenticated actor before using any route `business_id`.
- [ ] Verify an active membership and permission for every tenant mutation/read.
- [ ] Add `business_id` to every business-owned model, index, event, job, audit record, and idempotency scope.
- [ ] Require `business_id` in repository constructors or query methods; never provide a generic unscoped list method.
- [ ] Include tenant ID in object key namespace, job payload, event envelope, and log context; do not trust client-supplied values without authorization.
- [ ] Re-check tenant and resource state in worker entry points.
- [ ] Test cross-tenant read, write, upload-completion, job-status, outbox, and audit scenarios.
- [ ] Evaluate PostgreSQL RLS as a second boundary after application enforcement; do not rely on it alone.

## 11. Test strategy

- Unit: settings redaction, policy matrix, repository scope guards, state transitions, error mapping, idempotency hashing, and outbox selection.
- Integration: PostgreSQL migrations, transaction rollbacks, Redis/Celery configuration, membership authorization, multipart storage fake, and atomic completion/outbox behavior.
- Contract: OpenAPI problem schema, storage/identity adapter interfaces, job/event envelope, and HTTP idempotency behavior.
- Security/regression: cross-tenant IDOR matrix, unauthorized mutation, signed URL redaction, duplicate request, stale session, and retry/dead-letter cases.

## 12. Docker and local-development validations

- Compose starts API, worker, PostgreSQL, and Redis using environment references only; no production credentials are included.
- API liveness and readiness checks pass after dependency startup; readiness fails predictably when a dependency is stopped.
- Alembic upgrade and downgrade run against an ephemeral local database when migrations begin.
- Backend test suite runs against isolated test services; no tests call production providers or real object storage.
- CI runs formatting/lint/type checks, unit tests, migration checks, OpenAPI validation, and the Compose smoke test before later contract tests are enabled.

## 13. Phase 0 completion criteria

Phase 0 is complete only when slices 0A–0D meet their acceptance criteria, every added table has a reversible migration, all public mutations are authorized and idempotency-reviewed, tenant-isolation tests pass, OpenAPI/error contracts are generated and checked, background jobs have status/timeout/attempt/correlation/dead-letter handling, and local/CI verification commands succeed. No Phase 1 work begins automatically.

## 14. Task checklist

- [ ] 0A: Create workspace, FastAPI, settings, async PostgreSQL, Redis/Celery, logging, health endpoints, Compose, and test foundation; exclude admin web and identity-provider work.
- [ ] 0A: Add deterministic lint/test/verification commands and CI baseline.
- [ ] 0B: Implement identity-provider port and test adapter with the principal-verification contract; defer Firebase/OIDC/production provider integration.
- [ ] 0B: Implement user, identity, business, membership, role, policy, and tenant-scoped repository vertical slice.
- [ ] 0B: Add authorization and cross-tenant isolation tests.
- [ ] 0C: Implement storage port plus local fake or MinIO-compatible adapter, media asset/session lifecycle, multipart instructions, completion, and ingest-job creation; defer production storage integration.
- [ ] 0C: Add checksum/metadata/idempotency/tenant tests without proxying media bytes.
- [ ] 0D: Implement job, outbox, idempotency, audit, and structured-error vertical slice.
- [ ] 0D: Add atomicity, retry/dead-letter, duplicate-delivery, and redaction tests.
- [ ] Verify migrations, OpenAPI, local Compose smoke checks, and CI commands.
- [ ] Move this plan to `docs/plans/completed/` only after all completion criteria pass.
