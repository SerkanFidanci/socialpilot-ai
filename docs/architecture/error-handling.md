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
| 409 | `IDEMPOTENCY_IN_PROGRESS` | An equivalent request is still being processed. |
| 400 | `IDEMPOTENCY_KEY_INVALID` | The supplied idempotency key is empty or exceeds the public limit. |
| 409 | `RESOURCE_STATE_CONFLICT` | Command is illegal in the current resource state. |
| 409 | `JOB_STATE_CONFLICT` | A requested durable job state transition is not allowed. |
| 409 | `UPLOAD_CHECKSUM_MISMATCH` | Completed object does not match the declared checksum. |
| 413 | `REQUEST_TOO_LARGE` | API payload exceeds a control-plane limit. |
| 422 | `UPLOAD_METADATA_INVALID` | Declared or verified upload metadata violates policy. |
| 409 | `INGEST_SIZE_MISMATCH` | Storage object size differs from the completed upload contract. |
| 409 | `INGEST_CONTENT_TYPE_MISMATCH` | Server-observed or inspected content type differs from the accepted contract. |
| 422 | `INGEST_CONTENT_TYPE_REJECTED` | Inspected content type is not allowed by the media policy. |
| 409 | `INGEST_STORAGE_METADATA_INVALID` | Immutable object metadata cannot be safely verified. |
| 409 | `MALWARE_SCAN_NOT_CLEAN` | The asset cannot enter analysis because its scan result is not clean. |
| 400 | `PAGINATION_CURSOR_INVALID` | The supplied cursor is malformed, oversized, or not decodable. The list is not silently restarted. |
| 404 | `BRAND_PROFILE_NOT_FOUND` | The authorized business has no brand profile yet. |
| 404 | `PRODUCT_NOT_FOUND` | Product does not exist in the authorized tenant, including non-disclosing cross-tenant access. |
| 404 | `CAMPAIGN_OFFER_NOT_FOUND` | Campaign offer does not exist in the authorized tenant. |
| 409 | `PRODUCT_NAME_CONFLICT` | Another product in this business already uses this name. |
| 409 | `CURRENCY_MISMATCH` | A price, discount, or brand currency of record disagrees with the record it must match. |
| 422 | `CAMPAIGN_WINDOW_INVALID` | The campaign end is not after its start, so the window could never be active. |
| 422 | `CAMPAIGN_PRODUCT_UNKNOWN` | A cited product is not available in this business; the unknown identifier is not echoed. |
| 422 | `BRAND_ASSET_INVALID` | A brand asset does not reference an uploaded media asset of this business. |
| 429 | `RATE_LIMITED` | Client must retry after the supplied interval. |
| 503 | `DEPENDENCY_UNAVAILABLE` | Required readiness dependency is unavailable. |
| 503 | `STORAGE_UNAVAILABLE` | The upload-storage control-plane adapter is temporarily unavailable. |

## Script generation (PRD §17.5, §18.1 — slice 2B)

Request-level rejections. All of these happen **before** a provider is called, so a refused
request costs nothing:

| HTTP | Code | Meaning |
|---:|---|---|
| 404 | `SCRIPT_INPUT_NOT_FOUND` | A referenced product, campaign, CTA or asset is not available in this tenant. The identifier is never echoed, so the endpoint cannot be used to probe another tenant. |
| 404 | `SCRIPT_NOT_FOUND` | Script does not exist in the authorized tenant. |
| 409 | `SCRIPT_CAMPAIGN_NOT_ACTIVE` | The named campaign is outside its window or not approved; generating copy for it would produce something unpublishable. |
| 409 | `SCRIPT_PROMPT_TEMPLATE_MISSING` | The scenario has no active prompt version. A script with unknown provenance cannot exist, so generation is refused rather than run with a built-in prompt. |
| 409 | `SCRIPT_COST_LIMIT_EXCEEDED` | The route's estimated (or settled) cost exceeds `SCRIPT_GENERATION_MAX_COST_MINOR`. |
| 422 | `SCRIPT_TOO_MANY_SOURCE_ASSETS` | More source assets than one script may draw on. |
| 503 | `SCRIPT_GENERATION_NOT_CONFIGURED` | No script-generation provider is configured for this environment. Production never serves fixture output as real content, and declining here is why the application still boots without a provider. |
| 502 | `SCRIPT_GENERATION_FAILED` | The provider rejected the request permanently. |
| 503 | `SCRIPT_PROVIDER_UNAVAILABLE` | The provider was unavailable or exceeded its timeout. |
| 502 | `SCRIPT_ROUTE_MISMATCH` | The provider answered as a different provider/model than the persisted route snapshot named. |

Output-level rejections. The generation happened, the answer is unusable, and **no fallback
provider is tried** — an invented price is a policy failure, not a transient one. The
`content_scripts` row is kept as `failed` with the specific code below in `failure_code`; the
rejected text itself is never stored:

| HTTP | Code | Meaning |
|---:|---|---|
| 422 | `SCRIPT_PROVIDER_OUTPUT_INVALID` | Strict schema rejection. `meta.issue` carries the specific code and `meta.pointer` the location; the rejected value is never echoed. |
| 422 | `SCRIPT_VALIDATION_FAILED` | Content rules. `meta.issues[]` lists every violation at once. |

`meta.issue` values (schema):

| Kod | Ne yakalar |
|---|---|
| `SCRIPT_MALFORMED_JSON` | the response is not JSON |
| `SCRIPT_NOT_AN_OBJECT` | a required object arrived as something else |
| `SCRIPT_REQUIRED_FIELD_MISSING` | a §18.1 field is absent |
| `SCRIPT_UNKNOWN_FIELD` | an extra field — a `tool_calls` object lands here |
| `SCRIPT_FIELD_TYPE_INVALID` | wrong type for a contract field |
| `SCRIPT_ENUM_INVALID` | a purpose or `cta.source` outside its closed set |
| `SCRIPT_TEXT_TOO_LONG` / `SCRIPT_TEXT_EMPTY` | text outside its bounds, or an oversized response |
| `SCRIPT_DURATION_OUT_OF_RANGE` | segment, hook or total duration outside its bounds |
| `SCRIPT_SEGMENT_COUNT_INVALID` / `SCRIPT_SEGMENT_ORDER_INVALID` | too few/many segments, or a first segment that is not the hook |
| `SCRIPT_SCENE_TAG_INVALID` | a scene tag outside the permitted character set or count |
| `SCRIPT_SLOT_MALFORMED` / `SCRIPT_SLOT_KIND_UNKNOWN` / `SCRIPT_SLOT_LIMIT_EXCEEDED` | a broken `{{kind:id}}` reference |
| `SCRIPT_CONTROL_CHARACTER` | control characters in generated text |

`meta.issues[].code` values (content):

| Kod | Ne yakalar |
|---|---|
| `SCRIPT_FABRICATED_PRICE` | a money amount or percentage the model wrote itself — digits, symbols, or written-out amounts, and any percentage figure |
| `SCRIPT_FABRICATED_DATE` | a date the model wrote itself (`31.08.2026`, `1 Ağustos`, ISO) |
| `SCRIPT_FORBIDDEN_TERM` | a term on the brand's forbidden claim/topic list, matched at word boundaries and case-insensitively including Turkish `İ`/`I` |
| `SCRIPT_LITERAL_URL_REJECTED` | a link in generated text. §17.5 forbids acting on a model-produced URL; it is not stored either |
| `SCRIPT_VERIFIED_FIELD_NOT_FOUND` | a slot naming a record that does not exist, belongs to another tenant, or was not among the request's declared inputs |
| `SCRIPT_CAMPAIGN_WINDOW_INVALID` | a campaign that expired between the request and settlement |
| `SCRIPT_CTA_NOT_APPROVED` | a CTA the request did not name. Free CTA text cannot be expressed at all — the contract has no field for it |
| `SCRIPT_RESOLVED_TEXT_TOO_LONG` | the line exceeded its ceiling after substitution |

## Mapping and logging

- Domain/application errors are typed and mapped in one HTTP exception boundary; controllers do not construct ad hoc error payloads.
- Unexpected exceptions return a generic `500 INTERNAL_ERROR` with correlation ID, are logged once with redacted context, and are observable through metrics/traces.
- Dependency errors are classified as transient or permanent before jobs retry. API clients receive stable product codes, not raw dependency exceptions.
- Each request accepts or generates `X-Correlation-ID`; it is validated as a bounded opaque identifier and propagated to job/event/log contexts.
- Validation errors use the common shape with field-safe metadata. Never return rejected secrets, tokens, signed URLs, or full untrusted media metadata.
