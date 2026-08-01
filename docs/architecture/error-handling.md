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

## Timeline, parametric editing and render (PRD §18.2, §18.3, §19 — slice 2A)

Top-level codes. A timeline is rejected in two stages, and the split is deliberate: a document
that is not a timeline at all fails to *parse* and names one location, while a document that
parses but could not be rendered fails *validation* and names every violation at once, so a
client fixes one round of problems rather than discovering them one at a time.

| HTTP | Code | Meaning |
|---:|---|---|
| 404 | `TIMELINE_NOT_FOUND` | Timeline does not exist in the authorized tenant, including non-disclosing cross-tenant access. |
| 404 | `RENDER_NOT_FOUND` | Render output does not exist in the authorized tenant. |
| 422 | `TIMELINE_SCHEMA_INVALID` | The document does not match §18.2. `meta.issue` carries the code below and `meta.pointer` the location; the rejected value is never echoed, because a timeline can carry text lifted out of an uploaded video. |
| 422 | `TIMELINE_PATCH_INVALID` | The patch body does not match the closed operation set (K4). Same `meta.issue`/`meta.pointer` shape. |
| 422 | `TIMELINE_VALIDATION_FAILED` | §18.3 pre-render rules. `meta.issues[]` lists every violation at once, and **no render is scheduled** — the check runs before the job exists. |

`meta.issue` values (schema, §18.2):

| Code | What it catches |
|---|---|
| `TIMELINE_VERSION_UNSUPPORTED` | a document version this build does not implement |
| `TIMELINE_UNKNOWN_FIELD` | an extra key — a raw `x`/`y` coordinate lands here, which is how K4 is enforced structurally |
| `TIMELINE_FIELD_INVALID` / `TIMELINE_FIELD_OUT_OF_RANGE` | wrong type, or a value outside its bounds |
| `TIMELINE_TOO_MANY_ENTRIES` | more clips, overlays or tracks than one document may hold |
| `TIMELINE_NO_VIDEO_TRACK` / `TIMELINE_NO_CLIP` | nothing to render |
| `TIMELINE_DUPLICATE_TRACK` / `TIMELINE_DUPLICATE_AUDIO_TRACK` | the same track declared twice |
| `TIMELINE_CLIP_TOO_SHORT` | a cut below the minimum clip duration, including one that snapping pulled shut |
| `TIMELINE_LITERAL_WITH_REFERENCE` | literal text offered together with a record reference |
| `TIMELINE_VERIFIED_FIELD_NOT_LITERAL` | prose written into a verified slot — the shortest path around "a model never writes a price" |
| `TIMELINE_VERIFIED_REFERENCE_MISSING` | a verified slot with no record to resolve from |
| `TIMELINE_STYLE_TOKEN_UNKNOWN` | a style outside the closed token registry |

`meta.issue` values (patch, K4):

| Code | What it catches |
|---|---|
| `PATCH_EMPTY` / `PATCH_TOO_MANY_OPERATIONS` | no operations, or more than one request may carry |
| `PATCH_OPERATION_UNKNOWN` | an operation outside the closed set |
| `PATCH_UNKNOWN_FIELD` / `PATCH_FIELD_INVALID` / `PATCH_FIELD_OUT_OF_RANGE` | an extra key, a wrong type, or a value outside its bounds |
| `PATCH_TARGET_NOT_FOUND` / `PATCH_TARGET_NOT_TEXT` | an overlay, track or clip index past the end, or a text edit aimed at a logo. Out-of-range edits are refused rather than ignored, so a client cannot believe a change landed when it did not |

The schema codes above also apply to a patch, because a patch is parsed under the same rules.

`meta.issues[].code` values (pre-render validation, §18.3):

| Code | What it catches |
|---|---|
| `TIMELINE_DURATION_OVERFLOW` | a cut past the canvas duration, or a canvas past the adapter's ceiling |
| `TIMELINE_ASSET_NOT_ACCESSIBLE` | an asset that does not exist **or belongs to another tenant** — the query is tenant-scoped, so the two are indistinguishable by construction rather than by comparison |
| `TIMELINE_ASSET_NOT_RENDERABLE` | an asset whose ingest or technical analysis has not finished |
| `TIMELINE_CLIP_RANGE_INVALID` | a cut beyond the source duration |
| `TIMELINE_CLIP_OVERLAP` / `TIMELINE_DUPLICATE_CLIP` | overlapping cuts on one track, or the same (asset, start, end) twice |
| `TIMELINE_ASPECT_RATIO_MISMATCH` / `TIMELINE_RESOLUTION_TOO_LOW` | canvas ratio against the target profile; a source that cannot fill the target without visible upscaling |
| `TIMELINE_TEXT_OUTSIDE_SAFE_AREA` | text that still does not fit the safe area after line wrapping |
| `TIMELINE_FORBIDDEN_TERM` | a term on the brand's forbidden claim/topic list, matched at word boundaries |
| `TIMELINE_VERIFIED_FIELD_NOT_FOUND` | a reference that resolves to nothing in this tenant |
| `TIMELINE_CAMPAIGN_WINDOW_INVALID` | a campaign outside its window |
| `TIMELINE_LOGO_ASSET_INVALID` | an image the brand never registered as a logo |
| `TIMELINE_OVERLAY_WINDOW_INVALID` / `TIMELINE_AUDIO_TRACK_INVALID` | an overlay window or audio track that cannot be placed |
| `TIMELINE_UNSUPPORTED_TRANSITION` / `_CROP_MODE` / `_AUDIO_SOURCE` / `_CAPTION_SOURCE` | outside the render adapter's declared capabilities — refused cleanly rather than failing halfway through an encode |
| `TIMELINE_TOO_MANY_VIDEO_TRACKS` / `RENDER_PROFILE_UNSUPPORTED` | more tracks or a target profile than this adapter declares |

Worker-side failure codes. These are not HTTP responses: they are written to
`render_outputs.failure_code` and read back through `GET .../renders/{id}`, so a caller learns
why an encode stopped without the worker inventing an API error.

| Code | Meaning |
|---|---|
| `RENDER_RECORD_MISSING` / `RENDER_TIMELINE_MISSING` | the claimed job's render or timeline row is gone |
| `RENDER_TIMELINE_INVALID` | the stored document no longer parses under §18.2 |
| `RENDER_TIMELINE_VALIDATION_FAILED` | §18.3 re-run immediately before rendering now refuses. Not redundant with the API check: a campaign can expire or a price row can be superseded between request and render |
| `RENDER_SOURCE_UNAVAILABLE` | a source object could not be materialized for the encode |
| `RENDER_STORAGE_UNAVAILABLE` / `RENDER_STORAGE_METADATA_INVALID` | the output could not be stored, or what storage holds does not match what was written |

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

## Voiceover (PRD §14.8, §17.3 — slice 2C)

Request-level rejections. All of these happen **before** a provider is called, so a refused
request costs nothing and leaves no row:

| HTTP | Code | Meaning |
|---:|---|---|
| 404 | `VOICEOVER_SCRIPT_NOT_FOUND` | The script does not exist in the authorized tenant. Another tenant's real id answers exactly like a made-up one — the query is tenant-scoped, so the two are indistinguishable by construction. |
| 404 | `VOICEOVER_NOT_FOUND` | The voiceover does not exist in the authorized tenant. |
| 409 | `VOICEOVER_SCRIPT_NOT_USABLE` | The script is `pending` or `failed`. Only a script that settled successfully carries a resolved document, and only a resolved document contains values a record vouched for. |
| 422 | `VOICEOVER_SCRIPT_NOT_VOICEABLE` | The stored document has no synthesizable lines, or a line is empty, over-long, or carries control characters. `meta.issue`/`meta.pointer` name the location; the text is never echoed. |
| 422 | `VOICEOVER_VOICE_PROFILE_UNKNOWN` | The requested voice is not in the closed `VOICE_PROFILES` registry. |
| 409 | `VOICEOVER_VOICE_PROFILE_NOT_CONFIGURED` | The deployment's default voice is not in the registry. Kept apart from the code above on purpose: a `422` there would blame the caller for a configuration error. |
| 409 | `TTS_COST_LIMIT_EXCEEDED` | The estimated cost — per call **and** for the whole run — exceeds `TTS_MAX_COST_MINOR`. Also raised mid-run when settled cost passes the ceiling, which stops the remaining lines. |
| 503 | `TTS_NOT_CONFIGURED` | No speech provider is configured for this environment. Production never serves fixture audio as real content, and declining here is why the application still boots without a provider. |

Run-level failures. Calls happened, so every one of them has a `provider_usage` row and any
audio already stored is recorded on the `failed` row rather than orphaned in the bucket:

| HTTP | Code | Meaning |
|---:|---|---|
| 502 | `TTS_GENERATION_FAILED` | The provider rejected a line permanently. |
| 503 | `TTS_PROVIDER_UNAVAILABLE` | A line's provider call was unavailable, or the call or the whole run exceeded its timeout. |
| 502 | `TTS_ROUTE_MISMATCH` | The provider answered as a different provider/model than the persisted route snapshot named. |
| 503 | `VOICEOVER_AUDIO_UNMEASURABLE` | The probe could not run. The file may be fine; nothing may assume a duration it did not measure. |
| 502 | `VOICEOVER_AUDIO_INVALID` | The produced file is not measurable audio, or its measured length is outside the permitted per-line or per-run bounds. |
| 503 | `VOICEOVER_STORAGE_UNAVAILABLE` | The audio could not be stored. |
| 502 | `VOICEOVER_STORAGE_METADATA_INVALID` | What storage observed differs from what the adapter said it wrote. The row must not describe one file while the bucket holds another. |

## Mapping and logging

- Domain/application errors are typed and mapped in one HTTP exception boundary; controllers do not construct ad hoc error payloads.
- Unexpected exceptions return a generic `500 INTERNAL_ERROR` with correlation ID, are logged once with redacted context, and are observable through metrics/traces.
- Dependency errors are classified as transient or permanent before jobs retry. API clients receive stable product codes, not raw dependency exceptions.
- Each request accepts or generates `X-Correlation-ID`; it is validated as a bounded opaque identifier and propagated to job/event/log contexts.
- Validation errors use the common shape with field-safe metadata. Never return rejected secrets, tokens, signed URLs, or full untrusted media metadata.
