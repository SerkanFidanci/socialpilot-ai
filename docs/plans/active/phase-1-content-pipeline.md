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

Add a hardened FFprobe adapter, persist technical video metadata, and generate thumbnail and low-resolution proxy derivatives in isolated workers. This slice schedules technical analysis only for `video/mp4`; JPEG, PNG, and MPEG audio remain supported by the upload/ingest security gate but do not create a technical-analysis job. Audio extraction remains in 1C.

Acceptance criteria:

- [x] FFprobe records duration, stream presence, codec, container, width, height, frame rate, audio characteristics, and normalized rotation/aspect values using integer/rational-safe fields.
- [x] Corrupt/parser-failing input, trusted-size mismatch, and duration-limit violations produce stable safe errors; raw tool output is never returned.
- [x] FFprobe and FFmpeg use fixed trusted executable paths, argument arrays, `shell=False`, timeout, and bounded diagnostic output. Contract coverage proves a shell-metacharacter filename is data, not a command.
- [x] Thumbnail and proxy records use generated tenant/asset keys and persist checksum, size, content type, and immutable ownership metadata.
- [x] Unit and PostgreSQL integration tests cover real FFmpeg/FFprobe fixture processing, durable metadata/derivative records, technical job/outbox creation, and tenant-scoped job claims.

Technical admission uses orientation-independent long/short-edge and total-pixel Settings limits. Proxy (1280x720) and thumbnail (640x640) output targets are independent from source admission, preserve aspect ratio, never upscale, and normalize proxy dimensions for codec compatibility. The bounded worker `/tmp` remains sized consistently with the current materialization and derivative limits.

**1B implementation record — 2026-07-28:** Migration `0006_technical_media_analysis` adds tenant-scoped technical-analysis, technical-metadata, and derivative records plus a unique technical-analysis job constraint. A clean `video/mp4` ingest atomically schedules `media.technical_analysis` and its outbox event. The worker uses provider-neutral materialization/probe/derivative ports; generated local files are streamed into the storage port, whose persisted metadata must match before a derivative becomes `ready`. The test adapter stores files on disk without storage credentials. FFmpeg/FFprobe paths and duration, dimensions, pixel, and derivative-size limits are Settings-controlled. The worker profile is non-root, read-only, CPU/memory/PID-limited, and has a bounded writable `/tmp`. JPEG/PNG/audio uploads are intentionally not sent to FFmpeg in 1B. Container verification passed: `30 passed, 17 skipped` without PostgreSQL integration mode and `55 passed` with it enabled.

### 1C — Scene and speech analysis

Create scene segmentation, audio extraction, ASR port/adapter contract, transcript and timecode persistence, and a scene model.

Acceptance criteria:

- [x] Scene boundaries are ordered, non-overlapping, within verified duration, and persist millisecond offsets with deterministic provider-neutral candidates.
- [x] Audio extraction consumes the trusted proxy, uses fixed FFmpeg arguments/timeout/output-size limits, and persists a bounded audio derivative through the storage port.
- [x] ASR results normalize language, text, segment timecodes, confidence, and optional speaker label; malformed output is rejected before persistence.
- [x] Retryable dependency failures preserve correlation ID and attempt history; validation failures are terminal and exhausted retries become dead.
- [x] Tenant-scoped scene/transcript persistence, duplicate job protection, no-speech output, timecode validation, and deterministic fake contracts have PostgreSQL/unit coverage.

**1C implementation record — 2026-07-28:** Migration `0007_scene_speech_analysis` adds `media_scenes`, `transcripts`, and `transcript_segments` plus a unique scene/speech job constraint. A completed technical analysis atomically schedules `media.scene_speech_analysis`; the worker consumes the trusted proxy only. A local deterministic scene detector returns a whole-video fallback where no scene is found. No-audio videos create a successful `no_speech` transcript; audio-capable work uses a provider-neutral ASR port and a real FFmpeg extraction adapter without production ASR integration.

### 1D — Video understanding

Define the `VideoUnderstandingPort`, route capabilities by policy, persist normalized scene descriptions/tags/classifications, and record provider usage without provider payload leakage.

**1D-A1 implementation record — 2026-07-29:** Migration `0009_video_understanding`
adds the tenant-scoped `media_scene_understandings` record with a
`(business_id, scene_id)` uniqueness constraint and tenant-first asset lookup index.
The current slice defines provider-neutral frame/request/result DTOs, deterministic
test fakes, strict transcript overlap context, and bounded safe output
normalization. It intentionally does not yet create a job, outbox event, worker
claim, provider route decision, persistence service, or provider-usage record;
those remain in the next 1D slice.

**1D-A2 implementation record — 2026-07-29:** A completed scene/speech
transaction now idempotently creates one `media.video_understanding` job and
one `media.video_understanding.requested` outbox event. The durable service
claims that job with PostgreSQL locking, builds tenant-scoped per-scene
transcript context, invokes only the deterministic frame/VLM fakes with
separate step timeouts, atomically writes all normalized scene results and the
completion outbox event, and finalizes every attempt on success, retry, or
failure. The real FFmpeg frame extractor, worker/Celery composition, and
provider routing/usage accounting remain deferred within 1D.

**1D hardening record — 2026-07-29:** Frame extraction uses one bounded FFmpeg
subprocess per selected frame, so the per-scene wall-clock budget is
`frames_per_scene × frame_extraction_timeout + provider_timeout` (150 seconds by
default), plus job-level persistence margin. Frames are allocated deterministically
in scene order and never exceed the per-asset limit. Once exhausted, remaining
scenes invoke the provider with an empty frame tuple: providers must support this
transcript-only/no-context request and return a safe deterministic quality signal,
not a retryable failure. FFmpeg diagnostics are bounded and never exposed.

**1D ownership and timeout hardening record — 2026-07-29:** The VLM job stores
only the deterministic prefix of scenes that fits its configured ceiling; all
scene and transcript records remain durable when the detector produces more.
The default ceiling supports five scenes and job timeout calculation cannot exceed
its 900-second maximum. Technical and scene/speech job budgets are validated
against their complete step budgets plus persistence margin. Every technical,
scene/speech, and VLM worker claim carries its attempt number; persistence and
failure transactions require the same `RUNNING` job, attempt number, and
`STARTED` attempt, making reaped workers safe no-ops.

**1D worker composition record — 2026-07-29:** Celery now runs bounded durable
drain tasks through a process-local composition context. PostgreSQL remains the
only job truth and each drain iteration uses a fresh session; task payloads carry
no trusted tenant/job identity. Beat and a concrete outbox-to-Celery publisher
remain deferred. Framesless analysis writes service-authoritative quality signals
and a capped non-visual confidence.

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

**1C hardening record:** Migration `0008_scene_speech_hardening` changes transcript full text to PostgreSQL `TEXT`. Application Settings bound transcript segment, count, and total length; untrusted ASR text rejects PostgreSQL-unsafe controls and normalizes line endings before persistence. The technical proxy preserves an audio stream for scene/speech processing; WAV extraction remains 16 kHz mono signed 16-bit PCM with a byte limit mathematically consistent with the maximum supported duration. Completion persistence is transaction-safe and finalizes job attempts on classified failure. A tenant-scoped stale-running recovery service locks only expired jobs, records `JOB_TIMEOUT`, and schedules retry or dead-letter state without reclaiming active work.

## 6. Database changes planned per implementation slice

All new identifiers are UUIDs; timestamps are UTC; all money is integer minor units plus ISO currency; every business-owned query begins with `business_id`.

| Slice | Tables or changes | Purpose |
|---|---|---|
| 1A | Extend `media_assets`/`jobs`; `media_ingest_inspections`; `media_malware_scans` | Implemented in `0005_media_ingest_foundation`: verified server metadata, detected type, checksum, policy/scan outcome and provenance. |
| 1B | `media_derivatives`; `media_technical_metadata`; `media_technical_analyses` | Implemented in `0006_technical_media_analysis`: immutable derivative records, FFprobe facts, and technical execution provenance. |
| 1C | `media_scenes`; `transcripts`; `transcript_segments` | Implemented in `0007_scene_speech_analysis`: time-bounded scenes and normalized transcript data. |
| 1D | `media_scene_understandings`; later `provider_usage` and any separately justified tag/result projections | `0009_video_understanding` establishes normalized tenant-scoped scene output; routing, job/outbox composition, persistence flow, and attributable usage remain pending. |
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

Implemented 1A/1B files are limited to the media/operations/storage ports and fakes, migrations `0005_media_ingest_foundation` and `0006_technical_media_analysis`, focused unit/PostgreSQL tests, Settings, worker trigger registration, FFmpeg/FFprobe runtime support, and this plan. Future work remains limited to the same approved boundaries and must not create mobile, admin, publishing, advertising, billing, or n8n implementation files.

## 13. Phase 1 completion criteria

Phase 1 is complete when an authorized uploaded media asset reliably reaches `ready` only after server-side content/checksum/scan validation, technical analysis, required safe derivatives, scenes, transcript, and normalized analysis results; every durable transition is tenant-scoped, observable, retry-safe, and test-covered; all migrations upgrade/downgrade; API contracts and error codes are documented; and no original or derived bytes pass through FastAPI/n8n.

## 14. Open questions and source alignment

- Product requirements section 44 names brand profiles and products alongside media in "Phase 1 — Brand and media". This task's approved scope is the media-processing pipeline only. Brand/product modelling is therefore deferred here; confirm whether it is a separately sequenced Phase 1 slice or a later phase before implementation begins.
- Phase 1B resolves the immediate technical-analysis boundary: only `video/mp4` produces FFprobe/FFmpeg work. JPEG, PNG, and MPEG audio still use the ingest gate but await their explicit reduced analysis chains in later slices; no silent FFmpeg failure is permitted.
- Production malware, storage, ASR, and VLM providers, regional data-processing policy, retention/deletion obligations, and provider benchmark thresholds are intentionally unselected.
- The route policy must decide which analysis stages are mandatory for each supported media type and quality tier, and whether a VLM/ASR failure leaves an asset non-ready, operator-reviewable, or eligible for a policy-approved degraded result. The default in this plan is fail closed: no `ready` transition without all enabled mandatory stages.
- Reprocessing quotas, authorization permission name, retention period for prior runs, and the relationship to future entitlement consumption are not yet product decisions.

## 15. Deferred work

- Brand profiles, products, retrieval embeddings/pgvector, final content/timeline/render, human moderation UI, consent workflow UI, and usage entitlement consumption.
- Production storage, malware, ASR, and VLM provider onboarding; provider credentials, data-processing agreements, regional routing approval, and benchmark selection.
- PostgreSQL RLS, cross-tenant deduplication, object lifecycle/purge execution, user-facing media/job read APIs with cursor pagination, and operational dashboards/alerts.

## 16. Task checklist

- [x] 1A: Define storage inspection, content inspection, malware ports and asset/job gate; add tenant/atomicity/security tests.
- [x] 1B: Add hardened FFprobe and derivative processing with isolated worker controls and tests.
- [x] 1C: Add scene/audio/ASR contracts, models, jobs, and test fixtures.
- [ ] 1D: Video-understanding contracts, durable scheduling, tenant-scoped persistence, and
  bounded FFmpeg JPEG frame extraction are implemented. Real VLM routing, worker composition,
  provider usage/cost controls, and final slice acceptance remain pending.
- [ ] 1D: Step timeout separation, scene-count-based whole-job VLM budgets, recovery grace,
  global stale-job scanning, stale-worker ownership checks, and bounded Celery drain-task
  composition are implemented. Beat scheduling and concrete outbox publishing remain deferred.
- [ ] 1E: Add analysis orchestration, reprocessing, cost limits, observability, and end-to-end quality checks.
- [ ] Run slice-specific migrations, unit/contract/PostgreSQL tests, OpenAPI verification, Compose worker validation, lint, format, mypy, and security regression checks.
- [ ] Move this plan to `docs/plans/completed/` only after every acceptance criterion is verified.
