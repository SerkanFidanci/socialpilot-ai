# ADR-009: Real Media Materializer and `.mov`/HEVC Analysis Gate

**Status:** Accepted for Phase 1G
**Date:** 2026-07-30
**Builds on [ADR-008](ADR-008-s3-compatible-storage-adapter.md) (the byte path) and
[ADR-006](ADR-006-media-ingest-security-gate.md) (the ingest gate).**

## Context

ADR-008 made the *upload* byte path real: the client PUTs parts straight to storage and
completion is verified against what storage holds. But the worker still turned a completed
upload into a local file through the fixture-backed `FakeMediaMaterializer`, which writes
`b"test-only-media"`. So a real video uploaded to MinIO passed ingest and then handed garbage
to ffprobe — the Phase 1 exit criterion (*"10 videos upload; scenes, transcript, and tags
appear"*) could not be demonstrated end to end.

Two gaps blocked it:

1. **No real materializer.** Technical analysis, scene/speech, and video understanding all
   materialize an object (the original or the proxy) to worker scratch before running
   FFprobe/FFmpeg. Without a real one, none of them saw real bytes.
2. **`.mov`/HEVC admitted but never analyzed.** ADR-008 added `video/quicktime` to the upload
   allowlist for iOS, but `ingest.py::_complete_clean` only scheduled technical analysis for
   `video/mp4`. An iPhone's default `.mov` therefore stopped silently after ingest (STATUS K6).

## Decision

### A streaming materializer that reuses the storage adapter

`S3MediaMaterializer` (`infrastructure/media/s3_materializer.py`) implements
`MediaMaterializerPort` by streaming the object from storage to the worker's scratch directory
in 1 MiB chunks. It does **not** re-implement SigV4: it calls a new `download_to_path` on
W01's `S3MultipartStorage`, so the system owns one signing/error-translation path, not two.

- **Size is checked before the download starts.** `download_to_path` issues `HeadObject`
  first; an object larger than the system-wide ceiling
  (`max(media_max_bytes, media_max_derivative_bytes, media_max_extracted_audio_bytes)` — the
  same ceiling the adapter already uses for streamed verification) never pulls a body byte.
  The stream also enforces the head-reported size as a running ceiling, so an object that grows
  mid-download is rejected.
- **Scratch cleanup is mandatory.** The materializer removes its partial destination file on
  any error, cancellation, or timeout (PRD §19.3); the enclosing `TemporaryDirectory` removes
  the rest.
- **Selection mirrors the storage adapter.** A new `materializer_adapter` setting (`fake|s3`)
  selects the implementation through a `create_materializer` factory, exactly as
  `create_storage` selects storage. `production` refuses `fake`, and the `s3` materializer
  requires the same `S3_*` configuration as the storage adapter (it reuses it). The fixture
  fake stays the default for the byte-free control-plane and unit suites.

The `MediaMaterializerPort` signature is unchanged (`materialize(object_key, workdir)`), so the
three call sites (technical, scene/speech, video understanding) were not touched.

### The analysis gate widens to supported video containers; the codec decides at ffprobe

`ingest.py::_complete_clean` now schedules technical analysis for the configured
`media_analyzable_video_types` (`video/mp4`, `video/quicktime`) instead of only `video/mp4`.
Admission of a *container* is not a claim about its *codec*: the codec is validated inside
technical analysis against `media_supported_video_codecs` (`h264`, `hevc`) from what ffprobe
actually resolved. An unsupported codec raises `TechnicalUnsupportedMediaError`
(`TECHNICAL_VIDEO_CODEC_UNSUPPORTED`), which rejects the asset (`rejected`, per
media-ingest-pipeline.md's "unsupported media" rule) with a documented code — never a silent
technical stage that appears to hang. HEVC input is transcoded to an H.264 proxy by the
existing derivative adapter (`-c:v libx264`), so §15.5's proxy profile still holds.

### HEIC/HEIF are declined explicitly, not silently

HEIC/HEIF photos are admitted at upload (ADR-008, iOS) but have no analysis pipeline yet — the
current pipeline is video-focused (scenes, transcript), and photo analysis (technical metadata
+ VLM tagging, no scene/ASR) is undefined (STATUS K6, second half). Rather than leave them in
the accepted-but-never-analyzed limbo K6 warns is *worse than rejecting*, the ingest gate
declines them explicitly after the security gate: `INGEST_ANALYSIS_UNSUPPORTED_MEDIA_TYPE`,
asset `rejected`. Unlike JPEG/PNG (directly usable as-is) and MPEG audio, HEIC/HEIF are not
web-compatible and need a not-yet-built transcode to be usable at all, so an honest decline is
the right interim behavior. JPEG/PNG/audio keep their existing accepted-no-video-analysis
contract. When the photo-analysis slice lands, the declined set empties.

## Consequences

- The Phase 1 exit criterion is demonstrable end to end: a real `.mov`/HEVC, a real vertical
  clip, and a real clip with audio each flow upload → ingest → technical → scene/speech →
  video understanding, producing scenes, a transcript, and scene understandings, verified
  against real MinIO (`tests/integration/test_real_media_pipeline.py`).
- Materializer memory is bounded by the 1 MiB chunk size regardless of file size.
- One `httpx.AsyncClient` is opened per download (the adapter's existing per-operation client);
  materialization is per-job, not per-byte, so this is acceptable. The pooled-client upgrade
  noted in ADR-008 applies here too.
- HEIC/HEIF now reject at ingest instead of stopping silently; this is intentionally interim
  and reverses when photo analysis is built (needs a migration slot for a dedicated state).

## Rejected alternatives

- **A second SigV4 implementation in the materializer.** Rejected: two signing paths drift.
  The materializer reuses `S3MultipartStorage.download_to_path`.
- **Passing per-asset expected size/type through the port.** Would touch three call sites for
  a marginal gain; the system-wide ceiling plus the head-reported running ceiling is enough.
- **Buffering the object in memory before writing.** Rejected: violates the streaming
  requirement (criterion 4) for large originals.
- **Removing HEIC/HEIF from the upload allowlist.** Rejected: re-breaks the iOS main flow
  ADR-008 fixed; the byte path must keep working. An explicit ingest-stage decline keeps upload
  working while being honest about analysis.
- **Rejecting the codec at the ingest gate.** Rejected: ffprobe has not run at ingest; the
  container is all that is known there. The codec is decided where its facts exist.
