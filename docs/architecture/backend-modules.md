# Backend Modules

## Module structure

The backend is a modular monolith organized by domain, with a thin HTTP layer and shared technical primitives. A proposed shape for later implementation is:

```text
services/api/app/
  core/                 # settings, logging, errors, DB, observability
  modules/
    identity/           # principals, users, external identities
    businesses/         # businesses, memberships, roles, authorization
    media/              # media assets and upload sessions
    jobs/               # job state and worker contracts
    outbox/             # durable event publication
    idempotency/        # request replay protection
    audit/              # immutable security/domain audit trail
  adapters/
    identity/           # future Firebase/OIDC implementations
    storage/            # fake, then object-storage implementations
    messaging/          # Celery/n8n transport implementations
```

## Layering rules

| Layer | May do | May not do |
|---|---|---|
| API/controller | Parse HTTP, inject principal/use case, serialize response | Query ORM directly for business rules, call providers directly |
| Application/use case | Coordinate authorization, domain rules, transaction, idempotency, outbox | Depend on FastAPI request/response or provider SDKs |
| Domain | Express entities, policies, state transitions, ports/events | Depend on database, Celery, or external SDKs |
| Repository | Persist/query one module with mandatory tenant scope | Decide permissions or return cross-tenant collections |
| Adapter | Implement a port with timeout/redaction/error translation | Contain domain decisions or own data truth |

## Module contracts

### Identity

Slice 0B provides `Principal`, a verified identity-provider port, a test adapter, and internal `User`/`ExternalIdentity` mappings. It has no dependency on a particular OIDC/Firebase SDK in its domain or application layers; a production provider adapter is deferred until after Slice 0B.

### Businesses

Owns `Business`, `BusinessMember`, role assignments, and permission policy. This module is the only authority that turns an actor and business ID into an authorized tenant context.

### Media

Slice 0C owns `MediaAsset` and `MediaUploadSession` state transitions. It uses a `MultipartStoragePort` for multipart instructions and metadata verification, then emits a durable completion event. A local fake or MinIO-compatible adapter may implement the port; production storage integration, content inspection, and processing are future responsibilities.

### Jobs and operations

Owns job status, attempts, deadline/timeout, correlation, retry classification, and dead-letter state. Worker handlers invoke application use cases; they do not mutate module tables ad hoc.

### Outbox, idempotency, audit

These are shared operational modules with explicit interfaces. A mutation requests an idempotency decision, executes in a transaction, records audit data where required, and appends an outbox event before commit.

## Dependency direction

`api → application → domain ← adapters` and `application → repository ports ← persistence adapters`. Shared core code must remain technical; it cannot become a hidden domain-service layer. Cross-module collaboration uses explicit application interfaces or domain events, not direct table access.
