# Phase 1 — Content Pipeline Plan

**Status:** Completed 2026-07-30 — `main` `28e356a`, Alembic head `0010_brand_catalog`, 392 test. Çıkış kriteri mekanik olarak karşılandı; ASR/VLM hâlâ fake (gerçek sağlayıcı W08 sonrası).
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


## 5. Open slice

Slices 1A–1C are complete and 1D is delivered pending final acceptance; their scope,
acceptance criteria, implementation records and established design live in
[completed/phase-1-content-pipeline/verification.md](../completed/phase-1-content-pipeline/verification.md)
— together with §7 (state transitions) and §12 (expected files), which now describe built
reality. Section numbers are preserved, so the gaps below are intentional.

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
| 1E | Extend existing `jobs`, `job_attempts`, `outbox_events`, `audit_logs`, `idempotency_keys` only where a migration proves a required field/index is absent | Dependencies/reprocessing, safe status visibility, retention and operational indexes. |


`media_processing_runs` is the analysis-run aggregate; it gives reprocessing a distinct provenance boundary rather than overwriting an earlier result. Unique constraints and indexes must be tenant-first and include the applicable asset/run/capability identity. No migration is created by this planning task.


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

- [ ] 1D: Video-understanding contracts, durable scheduling, tenant-scoped persistence, and
  bounded FFmpeg JPEG frame extraction are implemented. Real VLM routing, worker composition,
  provider usage/cost controls, and final slice acceptance remain pending.
- [ ] 1D: Step timeout separation, scene-count-based whole-job VLM budgets, recovery grace,
  global stale-job scanning, stale-worker ownership checks, and bounded Celery drain-task
  composition are implemented. Beat scheduling and concrete outbox publishing remain deferred.
- [ ] 1E: Add analysis orchestration, reprocessing, cost limits, observability, and end-to-end quality checks.
- [ ] Run slice-specific migrations, unit/contract/PostgreSQL tests, OpenAPI verification, Compose worker validation, lint, format, mypy, and security regression checks.
- [ ] Move this plan to `docs/plans/completed/` only after every acceptance criterion is verified.
