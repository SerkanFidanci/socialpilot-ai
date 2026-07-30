# Slice 1G — Real media materializer + `.mov`/HEVC analysis gate

**Status:** Active · **Work order:** [W09](../../handoffs/W09-real-media-materializer.md) ·
**Branch:** `slice/1g-real-materializer` · **Migration slot:** none (Alembic head stays
`0009_video_understanding`).

## 1. Objective

Close the remaining half of the Phase 1 exit criterion. W01 made the *upload* byte path real
(multipart PUT → MinIO → complete). The worker still "materializes" media through the
fixture-backed `FakeMediaMaterializer` (writes `b"test-only-media"`), so ffprobe never sees
real bytes, and `.mov`/HEVC is admitted at upload but never analyzed. This slice makes the
worker stream real bytes from storage and lets QuickTime/HEVC enter the analysis pipeline.

## 2. Scope

1. **Real `MediaMaterializerPort`.** `S3MediaMaterializer` streams the object from S3-compatible
   storage to the worker's scratch directory in bounded chunks. It reuses W01's `S3MultipartStorage`
   signing/error mapping (no second SigV4). Size is checked with `HeadObject` **before** the
   download starts. Scratch cleanup is mandatory on success, error, and timeout — no partial file.
   The `fake` materializer stays for tests; selection mirrors `STORAGE_ADAPTER`, and `production`
   refuses `fake`.
2. **`.mov`/HEVC analysis gate (K6).** `ingest.py::_complete_clean` schedules technical analysis
   for the supported video *container* set (`video/mp4`, `video/quicktime`) instead of only
   `video/mp4`. Actual codec support is decided by ffprobe in technical analysis: an unsupported
   codec becomes a documented `rejected` outcome, never a silent stop.
3. **HEIC/HEIF:** not brought into the (video-focused) analysis pipeline. This slice only ensures
   HEIC does not silently die — explicit decline with a documented code (rationale in report).
4. **End-to-end proof** with real `.mp4` and `.mov` files (not fixtures): upload → ingest →
   technical → scene/speech → video understanding → processing-summary coverage.

## 3. Out of scope (stop and report if needed)

- Migrations / schema changes (`storage_upload_id` widening is W04's slot).
- Real `ContentInspectionPort` / `MalwareScanPort` (stay fake, gate order preserved — ADR-006).
- Real AI providers (ASR/VLM stay fake).
- `pyproject.toml`, `Dockerfile`, `Makefile`, `.github/workflows/**`, runbook → W02.
- `docs/index.md`, `docs/adr/README.md` → W03 (report the ADR link, PM wires it).
- HEIC photo-analysis pipeline (K6 second half, separate slice).

## 4. Design decisions

- **Reuse, don't re-sign.** Add `download_to_path` to `S3MultipartStorage` (HeadObject size guard
  + streamed `GetObject` to a file). `S3MediaMaterializer` depends on the concrete adapter — this
  is infrastructure→infrastructure reuse the WO explicitly asks for, not an SDK leak.
- **Port signature unchanged.** `materialize(object_key, workdir)` stays as-is so the three call
  sites (technical/scene-speech/video-understanding) are untouched. The download ceiling is the
  system-wide `max(media_max_bytes, media_max_derivative_bytes, media_max_extracted_audio_bytes)`
  — the same ceiling the adapter already uses for streamed verification.
- **Codec gate lives where ffprobe output exists** — `validate_technical_metadata` in
  `technical.py` (not in the declared touch list but owned by no other WO; justified in report).
- **HEIC/HEIF → explicit decline** at the ingest gate (`INGEST_ANALYSIS_UNSUPPORTED_MEDIA_TYPE`,
  asset `rejected`). Rationale: HEIC/HEIF need a not-yet-built transcode to be usable, so an
  explicit decline beats the silent limbo K6 warns against; jpeg/png/audio keep their existing
  accepted-no-video-analysis contract (tested).

## 5. Files (declared touch list from W09 + justified additions)

```
services/api/app/infrastructure/media/s3_materializer.py   (new)
services/api/app/infrastructure/media/__init__.py          (create_materializer factory — justified)
services/api/app/infrastructure/storage/s3.py              (download_to_path)
services/api/app/modules/media/ingest.py                   (analysis gate + HEIC decline)
services/api/app/modules/media/technical.py                (codec gate — justified, unowned)
services/api/app/worker/composition.py                     (materializer selection)
services/api/app/core/config.py                            (materializer_adapter + policy)
services/api/tests/unit/test_s3_materializer.py            (new)
services/api/tests/unit/test_config.py                     (materializer guard)
services/api/tests/integration/test_media_ingest.py        (.mov gate, HEIC, codec reject)
services/api/tests/integration/test_real_media_pipeline.py (new — real MinIO E2E)
docs/architecture/media-ingest-pipeline.md
docs/adr/ADR-009-real-media-materializer.md                (new — 009 unused on disk)
```

## 6. Acceptance criteria

The 11 criteria in [W09](../../handoffs/W09-real-media-materializer.md#kabul-kriterleri):
real `.mp4` and `.mov` full chain, codec reject non-silent, streamed (bounded memory), scratch
cleanup on success/error/timeout, `production`+`fake` refused at startup, HEIC not silent, tenant
isolation, no signed-URL/credential leak, `make verify` green, Alembic head unchanged, Phase 1
exit criterion met with ≥3 real videos.

## 7. Verification

`make verify` + `RUN_INTEGRATION_TESTS=1` (real PostgreSQL + MinIO). Migration up/down/up
confirms head is `0009_video_understanding`. Records go to
`docs/plans/completed/slice-1g-real-materializer/verification.md` on close.
