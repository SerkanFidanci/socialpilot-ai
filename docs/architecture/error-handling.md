# Error Handling and API Problem Contract

## Response format

Public API errors use `application/problem+json`, based on RFC 9457, with a stable product code and correlation ID.

```json
{
  "type": "https://errors.socialpilot.ai/tenant-resource-not-found",
  "title": "Resource not found",
  "status": 404,
  "code": "TENANT_RESOURCE_NOT_FOUND",
  "detail": "The requested resource is not available.",
  "correlation_id": "uuid",
  "meta": {}
}
```

`detail` is safe for a user/client and must not expose SQL, storage keys, signed URLs, secrets, credentials, internal stack traces, cross-tenant identifiers, or provider payloads. Diagnostic information belongs in redacted structured logs and, where appropriate, an internal error reference.

## Initial error catalogue

| HTTP | Code | Meaning |
|---:|---|---|
| 400 | `REQUEST_INVALID` | Schema or request semantics are invalid. |
| 401 | `AUTHENTICATION_REQUIRED` | Principal is absent or cannot be verified. |
| 403 | `AUTHORIZATION_DENIED` | Actor has tenant access but lacks this action. |
| 404 | `TENANT_RESOURCE_NOT_FOUND` | Resource does not exist in the authorized tenant, including non-disclosing cross-tenant access. |
| 409 | `IDEMPOTENCY_CONFLICT` | Key was reused with a different request fingerprint. |
| 409 | `RESOURCE_STATE_CONFLICT` | Command is illegal in the current resource state. |
| 409 | `UPLOAD_CHECKSUM_MISMATCH` | Completed object does not match the declared checksum. |
| 413 | `REQUEST_TOO_LARGE` | API payload exceeds a control-plane limit. |
| 422 | `UPLOAD_METADATA_INVALID` | Declared or verified upload metadata violates policy. |
| 429 | `RATE_LIMITED` | Client must retry after the supplied interval. |
| 503 | `DEPENDENCY_UNAVAILABLE` | Required readiness dependency is unavailable. |

## Mapping and logging

- Domain/application errors are typed and mapped in one HTTP exception boundary; controllers do not construct ad hoc error payloads.
- Unexpected exceptions return a generic `500 INTERNAL_ERROR` with correlation ID, are logged once with redacted context, and are observable through metrics/traces.
- Dependency errors are classified as transient or permanent before jobs retry. API clients receive stable product codes, not raw dependency exceptions.
- Each request accepts or generates `X-Correlation-ID`; it is validated as a bounded opaque identifier and propagated to job/event/log contexts.
- Validation errors use the common shape with field-safe metadata. Never return rejected secrets, tokens, signed URLs, or full untrusted media metadata.
