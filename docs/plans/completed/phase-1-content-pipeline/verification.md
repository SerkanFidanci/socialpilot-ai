# Phase 1 — verification record

Doğrulama kayıtları burada birikir; **plan dosyası bunlarla şişmez**
([handoffs/README.md](../../../handoffs/README.md) bağlam bütçesi kuralı,
[W03](../../../handoffs/W03-docs-restructure.md)).

Açık slice planı: [plans/active/phase-1-content-pipeline.md](../../active/phase-1-content-pipeline.md).
Bu dosya tarihsel kayıttır — **bütün olarak okunmaz**; bir slice'ın neden böyle
yapıldığını araştırıyorsan yalnızca ilgili bölümü aç.

Aşağıdaki bloklar plan dosyasından **birebir** taşındı; metin değiştirilmedi.

## Slice kayıtları

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


**1C hardening record:** Migration `0008_scene_speech_hardening` changes transcript full text to PostgreSQL `TEXT`. Application Settings bound transcript segment, count, and total length; untrusted ASR text rejects PostgreSQL-unsafe controls and normalizes line endings before persistence. The technical proxy preserves an audio stream for scene/speech processing; WAV extraction remains 16 kHz mono signed 16-bit PCM with a byte limit mathematically consistent with the maximum supported duration. Completion persistence is transaction-safe and finalizes job attempts on classified failure. A tenant-scoped stale-running recovery service locks only expired jobs, records `JOB_TIMEOUT`, and schedules retry or dead-letter state without reclaiming active work.


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
no trusted tenant/job identity. Frameless analysis writes service-authoritative
quality signals and a capped non-visual confidence.

**1D Celery orchestration record — 2026-07-30:** The Celery orchestration slice is
complete.

- *Completion coverage.* `media.video_understanding.completed` exposes
  `total_scene_count`, `analyzed_scene_count`, `skipped_scene_count`,
  `frame_backed_scene_count`, `transcript_only_scene_count`,
  `no_context_scene_count`, and `full`/`partial` coverage. Counts are derived from
  the service-decided per-scene `SceneAnalysisMode`, not from returned
  quality signals, and the payload carries integer counts only — no transcript,
  provider text, object key, or signed URL.
- *Provider authority.* `SERVICE_AUTHORITATIVE_QUALITY_SIGNALS` names the keys only
  the service may assert. `normalize_result` discards any provider copy after key
  normalization, so a provider cannot claim visual input or coverage.
- *Outbox publisher.* `CeleryOutboxPublisher` maps the four `*.requested` events to
  their drain tasks and sends no message arguments at all. Completion events are an
  explicit notification-only allow-list; any unregistered event type is
  dead-lettered rather than silently dropped. Without that allow-list every
  successful analysis completion would have dead-lettered.
- *Dispatch.* An event becomes `published` only after enqueue succeeds. Broker
  outages are transient and leave the event unpublished for bounded retry; other
  handoff failures are permanent and are not retried.
- *Beat.* A separate read-only `celery-beat` Compose service — never a worker `-B`
  flag — schedules outbox dispatch, one fallback drain per media step, and stale-job
  recovery. Development assumes a single beat replica; production HA/leader election
  is deferred to Phase 1E.
- *Event loop.* Each worker process owns one event loop created with its engine and
  reused by every task, because a pooled asyncpg connection cannot cross loops.
  Shutdown disposes the engine, shuts down async generators, and closes the loop.

Integration tests share the development database, so `celery-worker` and
`celery-beat` must be stopped while `pytest` runs with `RUN_INTEGRATION_TESTS=1`;
otherwise beat-scheduled drains claim the fixtures' jobs. A dedicated test database
is a Phase 1E concern.

Acceptance criteria:

- Requests use an adapter-controlled short-lived resource reference for the approved proxy or selected scene, never a client URL or the original provider credential.
- A route decision is deterministic for the capability/policy snapshot and records provider, model alias, route revision, timeout, and maximum approved cost before the call.
- JSON-schema and semantic validation constrain response fields, enum values, confidence range, array/text limits, and referenced scene IDs. Media-embedded text is data, never instruction.
- Provider failures are classified as transient, permanent, throttled, budget-exceeded, or invalid-response; only safe transient failures retry/fallback within the approved policy.
- Provider usage has tenant, job, capability, provider/model, estimated and actual integer-minor-unit cost, currency, and correlation fields, with no token, signed URL, raw prompt, or full raw response stored in audit/logs.


## Uygulanmış dayanıklı tasarım

1A–1D ile kurulan ve artık kod tarafından temsil edilen durum/komut sınırları.
Değişiklik gerekiyorsa mimari dokümanlar ve ADR'ler kazanır, bu kayıt değil.

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


## Slice başına veritabanı değişiklikleri (uygulanmış)

| Slice | Tables or changes | Purpose |
|---|---|---|
| 1A | Extend `media_assets`/`jobs`; `media_ingest_inspections`; `media_malware_scans` | Implemented in `0005_media_ingest_foundation`: verified server metadata, detected type, checksum, policy/scan outcome and provenance. |
| 1B | `media_derivatives`; `media_technical_metadata`; `media_technical_analyses` | Implemented in `0006_technical_media_analysis`: immutable derivative records, FFprobe facts, and technical execution provenance. |
| 1C | `media_scenes`; `transcripts`; `transcript_segments` | Implemented in `0007_scene_speech_analysis`: time-bounded scenes and normalized transcript data. |
| 1D | `media_scene_understandings`; later `provider_usage` and any separately justified tag/result projections | `0009_video_understanding` establishes normalized tenant-scoped scene output; routing, job/outbox composition, persistence flow, and attributable usage remain pending. |


## 12. Expected files for later implementation

Implemented 1A/1B files are limited to the media/operations/storage ports and fakes, migrations `0005_media_ingest_foundation` and `0006_technical_media_analysis`, focused unit/PostgreSQL tests, Settings, worker trigger registration, FFmpeg/FFprobe runtime support, and this plan. Future work remains limited to the same approved boundaries and must not create mobile, admin, publishing, advertising, billing, or n8n implementation files.


## Kapanmış görev kalemleri

- [x] 1A: Define storage inspection, content inspection, malware ports and asset/job gate; add tenant/atomicity/security tests.
- [x] 1B: Add hardened FFprobe and derivative processing with isolated worker controls and tests.
- [x] 1C: Add scene/audio/ASR contracts, models, jobs, and test fixtures.
