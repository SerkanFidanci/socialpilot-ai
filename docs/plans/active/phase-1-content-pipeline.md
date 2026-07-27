# Phase 1 — Content Pipeline Plan

**Status:** Active
**Scope owner:** Backend media-processing foundation
**Prerequisite:** Completed Phase 0 at migration head `0004_operational_reliability`

## 1. Objective

Turn an already-authorized, direct-to-storage uploaded `media_asset` into tenant-safe, reusable analysis data. Phase 1 begins at the durable `media.ingest.requested` outbox event and ends when a valid asset has technically verified metadata, safe derived assets, scenes, transcript segments, and normalized video-understanding results. PostgreSQL remains the source of truth; Celery delivery is only a trigger.

## 2. Scope

- Ingest commands that reload the tenant-scoped asset and inspect object metadata through the storage port.
- File-signature/MIME inspection, SHA-256 verification, policy checks, and a provider-neutral malware-scanning port.
- FFprobe-based technical analysis, thumbnail/proxy/audio derivatives, and scene-detection infrastructure in isolated workers.
- Provider-neutral ASR and video-understanding ports, normalized results, route selection, and provider-usage/cost records.
- Durable job state/retry/dead-letter handling, transactional outbox handoff, audit-safe observability, authorized reprocessing, and tenant isolation.

## 3. Out of scope

- Ready-to-publish Reels, final render, timeline, TTS, publishing, advertising, n8n workflows, mobile UI, payment, and automatic publication.
- A production storage-provider selection or credentials. Slice 1A defines the production-adapter readiness contract; local tests use a fake or MinIO-compatible adapter.
- A mandatory production malware, ASR, or VLM provider. Ports, adapters/fakes, contracts, routing policy, and safe failure behaviour are in scope; provider onboarding is separately approved.
- Cross-tenant deduplication, embeddings/retrieval, brand/product modelling, consent workflow UI, and human moderation workflow.

## 4. Architectural decisions

1. The API remains a control plane: it never proxies original media, proxy media, audio, or thumbnails; n8n never receives those bytes.
2. `business_id` is required on every Phase 1 record, job, event, object namespace, usage record, audit record, and repository query.
3. A completed upload is untrusted until server-side storage metadata, content signature, checksum, and malware policy checks complete. It cannot become `ready` or reach an AI provider before this gate.
4. FFprobe/FFmpeg and parsers run only in constrained media workers. Executables are invoked with argument arrays, fixed binaries, controlled work directories, bounded input, timeout, CPU/memory/disk limits, and no user-composed shell string.
5. Application services coordinate durable transitions; controllers do not manage transactions, retries, subprocesses, or provider calls.
6. Domain/application code sees capability ports and normalized value objects only. Adapters own provider SDKs, short-lived storage access, redaction, timeout, and error translation.
7. Each state-changing command writes domain state, durable job changes, audit record where applicable, and outbox rows in one PostgreSQL transaction. Consumers are idempotent and re-authorize/reload tenant state.

New ADRs record the ingestion security gate and the analysis-routing policy; ADR-004 remains the general provider-adapter decision.

## 5. Delivery slices and application order

### 1A — Storage and ingest foundation

Implement a production-ready `ObjectStoragePort` contract without selecting a production provider; inspect immutable object metadata; identify content by bytes rather than filename/client MIME; verify SHA-256; define a malware-scanning port; and move the durable ingest job through its first state transitions.

Acceptance criteria:

- [x] `media.ingest.requested` creates exactly one tenant-scoped ingest execution for an uploaded asset; duplicate completion/outbox delivery cannot create a second durable ingest job.
- [x] The worker reloads the asset through a tenant-scoped repository, compares server-side object size/content type/checksum/ETag metadata against the persisted completion contract, and does not log a signed URL.
- [x] The content-inspection port and Settings MIME policy reject a mismatched or disallowed type before later analysis; checksum and size mismatch are non-retryable rejection states.
- [x] The malware port distinguishes clean, infected, unavailable, and indeterminate outcomes. Clean assets become `ready_for_analysis`; infected/indeterminate assets are quarantined; scanner unavailability is retryable and never bypasses the gate.
- [x] PostgreSQL integration tests cover tenant isolation at the media read boundary, duplicate job prevention, metadata mismatch, clean/infected scan transitions, concurrent `SKIP LOCKED` claiming, and retry-to-dead handling.

**1A implementation record — 2026-07-28:** Migration `0005_media_ingest_foundation` adds verified-ingest inspection and malware-scan records, tenant-first indexes, a unique ingest-job resource constraint, and due-retry job scheduling. Upload completion continues to atomically create the queued `media.ingest` job and `media.ingest.requested` outbox event. The worker-side `MediaIngestService` uses provider-neutral storage-metadata, content-inspection, and malware-scan ports with development/test fakes; it never accepts media bytes through FastAPI. Container verification passed: `27 passed, 16 skipped` locally and `43 passed` with PostgreSQL integration tests.

### 1B — Technical media analysis

Add a hardened FFprobe adapter, persist technical audio/video metadata, and generate thumbnail, 720p proxy, and extracted-audio derivatives in isolated workers.

Acceptance criteria:

- FFprobe records duration, stream presence, codec, container, width, height, frame rate, audio characteristics, and a normalized rotation/aspect representation using integer/rational-safe fields.
- Unsupported, corrupt, oversized, duration-exceeding, or parser-failing input is rejected or quarantined with a stable safe error; no raw tool output reaches an API response.
- Every subprocess uses a fixed executable plus an argument list, per-job restricted directory, timeout, output-size bound, and cleanup policy; tests prove user-controlled filename/path text is never interpolated into a shell command.
- Generated variants are stored under the existing tenant/asset namespace, are recorded with checksum/size/content type, and the immutable original remains unchanged.
- Technical metadata, derivative ownership, timeout handling, and tenant isolation have unit, contract, and PostgreSQL integration tests.

### 1C — Scene and speech analysis

Create scene segmentation, audio extraction, ASR port/adapter contract, transcript and timecode persistence, and a scene/keyframe model.

Acceptance criteria:

- Scene boundaries are ordered, non-overlapping, within verified duration, and persist millisecond offsets with deterministic source/version metadata.
- Audio extraction consumes only the verified original or trusted derivative and produces a bounded derivative record; no audio bytes pass through FastAPI.
- ASR results are normalized to language, transcript, segment start/end milliseconds, bounded text, confidence, and optional speaker label; malformed provider responses are rejected before persistence.
- Retryable ASR/scene failures preserve correlation ID and attempt history; permanent media and schema failures do not retry; exhausted work is dead-lettered with a safe error summary.
- Tenant-scoped scene/transcript retrieval rules, duplicate task delivery, timecode validation, and provider contract fixtures are tested.

### 1D — Video understanding

Define the `VideoUnderstandingPort`, route capabilities by policy, persist normalized scene descriptions/tags/classifications, and record provider usage without provider payload leakage.

Acceptance criteria:

- Requests use an adapter-controlled short-lived resource reference for the approved proxy or selected scene, never a client URL or the original provider credential.
- A route decision is deterministic for the capability/policy snapshot and records provider, model alias, route revision, timeout, and maximum approved cost before the call.
- JSON-schema and semantic validation constrain response fields, enum values, confidence range, array/text limits, and referenced scene IDs. Media-embedded text is data, never instruction.
- Provider failures are classified as transient, permanent, throttled, budget-exceeded, or invalid-response; only safe transient failures retry/fallback within the approved policy.
- Provider usage has tenant, job, capability, provider/model, estimated and actual integer-minor-unit cost, currency, and correlation fields, with no token, signed URL, raw prompt, or full raw response stored in audit/logs.

### 1E — Analysis orchestration and quality control

Make the event-driven chain observable and safe: dependency-aware jobs, aggregation of validated results, reprocessing controls, usage/cost accounting, and operations signals.

Acceptance criteria:

- The chain advances only after prerequisite durable jobs succeed: validate/scan → technical analysis/derivatives → scene/audio → ASR/VLM → aggregate readiness.
- A domain mutation and its next outbox event commit together; outbox dispatch is at least once and handlers use a job/resource idempotency scope.
- Retry uses bounded exponential backoff with jitter, per-step timeout/max-attempt policy, and terminal `dead` state; policy, validation, checksum, malware, and malformed-response failures do not auto-retry.
- Authorized reprocessing creates a new processing run and preserves prior immutable analysis provenance; it is idempotency-protected, audited, rate/quotas checked, and cannot overwrite an active incompatible run.
- Asset reaches `ready` only after required enabled analysis steps have valid normalized results. It exposes safe aggregate status, not provider diagnostics.
- Metrics/logs/traces include business ID, asset ID, job ID, correlation ID, capability, duration, status, attempt, queue delay, and cost aggregate, with redaction tests.

## 6. Database changes planned per implementation slice

All new identifiers are UUIDs; timestamps are UTC; all money is integer minor units plus ISO currency; every business-owned query begins with `business_id`.

| Slice | Tables or changes | Purpose |
|---|---|---|
| 1A | Extend `media_assets`/`jobs`; `media_ingest_inspections`; `media_malware_scans` | Implemented in `0005_media_ingest_foundation`: verified server metadata, detected type, checksum, policy/scan outcome and provenance. |
| 1B | `media_variants`; `media_technical_metadata`; `media_processing_runs` | Immutable derivative records, FFprobe facts, and repeatable execution provenance. |
| 1C | `media_scenes`; `media_keyframes`; `transcripts`; `transcript_segments` | Time-bounded scene/keyframe/transcript data. |
| 1D | `media_analysis_results`; `media_tags`; `provider_usage` | Normalized VLM output and attributable provider usage. |
| 1E | Extend existing `jobs`, `job_attempts`, `outbox_events`, `audit_logs`, `idempotency_keys` only where a migration proves a required field/index is absent | Dependencies/reprocessing, safe status visibility, retention and operational indexes. |

`media_processing_runs` is the analysis-run aggregate; it gives reprocessing a distinct provenance boundary rather than overwriting an earlier result. Unique constraints and indexes must be tenant-first and include the applicable asset/run/capability identity. No migration is created by this planning task.

## 7. State transitions and durable commands

### Media asset state

```text
uploaded → validating → processing → ready
uploaded|validating → quarantined      (malware or unsafe/indeterminate security policy)
validating|processing → rejected       (unsupported, corrupt, checksum or permanent policy failure)
ready|rejected|quarantined → purging → deleted
```

An ordinary retry does not fabricate a separate asset state: the asset remains `validating` or `processing`, while the durable job carries failure/attempt/next-attempt state. Transition ownership belongs to media application services.

### Job state

The existing job states remain authoritative: `queued → running → succeeded`; retryable execution becomes `failed` with `next_attempt_at` and returns to `queued`; cancelled work becomes `cancelled`; exhausted retry becomes `dead`. Invalid transitions return `JOB_STATE_CONFLICT`. A worker rechecks asset, business membership/state policy, and processing-run state before each side effect.

### Internal command/event boundary

| Trigger | Internal command | Resulting durable event |
|---|---|---|
| `media.ingest.requested` | `ValidateMediaIngest` | `media.ingest.validated` or terminal state event |
| validated + clean | `AnalyzeTechnicalMedia` | `media.technical_analysis.completed` |
| verified derivatives | `AnalyzeScenes` / `TranscribeMedia` | `media.scenes.detected` / `media.transcript.completed` |
| selected scenes | `UnderstandMediaScenes` | `media.analysis.completed` |
| prerequisites complete | `FinalizeMediaAnalysis` | `media.ready` |
| authorized retry | `ReprocessMediaAnalysis` | `media.reprocessing.requested` |

Commands are application-service contracts, not HTTP handlers. A future read API may project safe job/asset status, but Phase 1 should not promise an endpoint until its authorization, cursor pagination, and OpenAPI contract are implemented.

## 8. Provider ports

- `ObjectStoragePort`: immutable-object metadata inspection and controlled short-lived worker access.
- `ContentInspectionPort`: magic-byte/container type identification and policy-safe result.
- `MalwareScanPort`: scan submission/result with `clean`, `infected`, `unavailable`, and `indeterminate` outcomes.
- `MediaProbePort`: FFprobe facts only; no provider/domain types.
- `MediaDerivativePort`: proxy, thumbnail, waveform, keyframe, and audio output contracts.
- `SceneDetectionPort`: time-bound scene/keyframe candidate output.
- `SpeechToTextPort`: normalized transcript segment output.
- `VideoUnderstandingPort`: schema-constrained scene analysis output.
- `AnalysisRoutePolicyPort`: capability route decision, timeout, budget cap, fallback, and route revision.

Each adapter has explicit connect/read/total timeouts where applicable, bounded payloads, redaction, contract tests, and error classification. Provider selection and production credentials are configuration/operations work, never entity or controller state.

## 9. Security and tenant-isolation checklist

- [ ] Authorize every user command against active membership and the media-write/reprocess permission; inaccessible tenant resources return non-disclosing `404`.
- [ ] Require `business_id` in every new media, scene, transcript, usage, job, outbox, audit, and repository operation; reject unscoped repository access.
- [ ] Generate object paths server-side; validate object identity, expected tenant, size, and checksum from trusted storage metadata.
- [ ] Treat filename, declared MIME, container metadata, transcript text, OCR/text frames, and provider output as untrusted data.
- [ ] Quarantine infected or security-indeterminate media; prevent proxy/AI/export/public delivery before gate success.
- [ ] Use no shell interpolation; pin executable paths; restrict filesystem, network, CPU, memory, process count, duration, and output size for worker tools.
- [ ] Do not log or audit raw provider prompts/responses, tokens, signed URLs, object keys beyond safe opaque references, or sensitive media metadata.
- [ ] Re-authorize and reload asset/run state at worker execution; cancellation, deletion, suspension, or tenant removal stops later side effects.
- [ ] Test cross-tenant read/write/reprocess/job/event/usage isolation plus duplicate outbox/Celery delivery.

## 10. Retry, timeout, dead-letter, and cost policy

- Policy/validation/checksum/type/malware-infected/malformed-provider-response errors are terminal and non-retryable.
- Storage, scanner, subprocess infrastructure, provider timeout/5xx, and rate limit errors retry only after classification, with bounded exponential backoff plus jitter, max attempts, and a job deadline.
- A provider fallback is allowed only when the route policy permits it, the data-region policy is compatible, and the cumulative approved cost cap remains available.
- A dead job retains safe code, summary, timestamps, attempt history, correlation ID, and tenant context; it emits a durable operational event but no secret/provider payload.
- Record estimated cost before provider dispatch and actual/reconciled cost after it; use integer minor units. Cost thresholds, concurrent analysis caps, and reprocess quota are checked before expensive work.

## 11. Test strategy

- Unit: state machines, MIME/signature policy, checksum, scan classification, subprocess argument builder, scene/timecode validation, provider response normalization, routing/cost rules, and error mapping.
- PostgreSQL integration: tenant isolation, transaction/outbox atomicity, duplicate delivery, concurrent job claim, retry/dead-letter, asset/run transition integrity, immutable provenance, and usage/audit redaction.
- Adapter contract: fake storage/scanner/FFprobe/ASR/VLM fixtures, timeout and malformed response cases, never production services or credentials.
- Media security/golden: benign supported files, spoofed extension/MIME, corrupt container, oversize/duration boundary, parser bombs, infected scan fixture, audio/video absent, rotation/FPS edge cases, Turkish ASR timecodes, and adversarial embedded prompt text.
- End-to-end local environment: direct upload-control-plane completion through ingest to safe aggregate `ready` using local fakes/sample media; assert no media bytes are sent through API/n8n.

## 12. Expected files for later implementation

Implemented 1A files are limited to the media/operations/storage ports and fakes, migration `0005_media_ingest_foundation`, focused unit/PostgreSQL tests, Settings, worker trigger registration, Compose cache environment, and this plan. Future work remains limited to the same approved boundaries and must not create mobile, admin, publishing, advertising, billing, or n8n implementation files.

## 13. Phase 1 completion criteria

Phase 1 is complete when an authorized uploaded media asset reliably reaches `ready` only after server-side content/checksum/scan validation, technical analysis, required safe derivatives, scenes, transcript, and normalized analysis results; every durable transition is tenant-scoped, observable, retry-safe, and test-covered; all migrations upgrade/downgrade; API contracts and error codes are documented; and no original or derived bytes pass through FastAPI/n8n.

## 14. Open questions and source alignment

- Product requirements section 44 names brand profiles and products alongside media in "Phase 1 — Brand and media". This task's approved scope is the media-processing pipeline only. Brand/product modelling is therefore deferred here; confirm whether it is a separately sequenced Phase 1 slice or a later phase before implementation begins.
- The first supported asset matrix is unresolved: Phase 1 must explicitly decide whether image/audio-only assets receive the same ingest gate but a reduced analysis chain, or whether the initial vertical slice accepts video only. MIME/container, size, duration, and per-plan limits are policy inputs still to be approved.
- Production malware, storage, ASR, and VLM providers, regional data-processing policy, retention/deletion obligations, and provider benchmark thresholds are intentionally unselected.
- The route policy must decide which analysis stages are mandatory for each supported media type and quality tier, and whether a VLM/ASR failure leaves an asset non-ready, operator-reviewable, or eligible for a policy-approved degraded result. The default in this plan is fail closed: no `ready` transition without all enabled mandatory stages.
- Reprocessing quotas, authorization permission name, retention period for prior runs, and the relationship to future entitlement consumption are not yet product decisions.

## 15. Deferred work

- Brand profiles, products, retrieval embeddings/pgvector, final content/timeline/render, human moderation UI, consent workflow UI, and usage entitlement consumption.
- Production storage, malware, ASR, and VLM provider onboarding; provider credentials, data-processing agreements, regional routing approval, and benchmark selection.
- PostgreSQL RLS, cross-tenant deduplication, object lifecycle/purge execution, user-facing media/job read APIs with cursor pagination, and operational dashboards/alerts.

## 16. Task checklist

- [x] 1A: Define storage inspection, content inspection, malware ports and asset/job gate; add tenant/atomicity/security tests.
- [ ] 1B: Add hardened FFprobe and derivative processing with isolated worker controls and tests.
- [ ] 1C: Add scene/audio/ASR contracts, models, jobs, and test fixtures.
- [ ] 1D: Add video-understanding routing, normalization, provenance, and provider-usage controls.
- [ ] 1E: Add analysis orchestration, reprocessing, cost limits, observability, and end-to-end quality checks.
- [ ] Run slice-specific migrations, unit/contract/PostgreSQL tests, OpenAPI verification, Compose worker validation, lint, format, mypy, and security regression checks.
- [ ] Move this plan to `docs/plans/completed/` only after every acceptance criterion is verified.
