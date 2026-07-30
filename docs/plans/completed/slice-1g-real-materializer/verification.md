# Slice 1G — Verification record

**Date:** 2026-07-30 · **Branch:** `slice/1g-real-materializer` · **Base:** `main` (`82eb4dc`)

Full record lives in the work order's Rapor section:
[W09](../../../handoffs/W09-real-media-materializer.md). Summary below.

## Environment

The long-running dev image had drifted (unpinned dev tools) and mounted pre-`82eb4dc` code, so
the api image was rebuilt from this worktree (`--build-arg INSTALL_DEV=true`) and every check
ran against it, with `postgres`/`redis`/`minio` from the standard Compose stack. Real-S3 tests
need a container on both the `backend` (postgres/redis) and `edge` (minio) networks.

## Results

| Check | Result |
|---|---|
| `ruff check app tests migrations` | pass |
| `ruff format --check app tests migrations` | pass (101 files) |
| `mypy .` (strict) | pass (105 files) |
| `pytest` with `RUN_INTEGRATION_TESTS=1` (real PostgreSQL + MinIO) | **264 passed** (was 244) |
| OpenAPI contract (semantic diff) | unchanged |
| Alembic migration up → down → up | head `0009_video_understanding` (unchanged) |

## Acceptance criteria (W09 §Kabul kriterleri)

All 11 met. Highlights:

- **Real `.mp4` + `.mov`/HEVC full chain** on real MinIO — scenes, transcript, scene
  understanding, coverage; no fixture bytes
  (`tests/integration/test_real_media_pipeline.py`, 3 real videos: `.mov`/HEVC, vertical,
  voiced).
- **Unsupported codec** → asset `rejected`, documented `TECHNICAL_VIDEO_CODEC_UNSUPPORTED`, no
  retry, no silent stop.
- **Streaming** (1 MiB chunks) with pre-download `HeadObject` size guard; **scratch cleanup**
  on success/error/timeout (unit tests).
- **`production` + `fake` materializer** refused at settings validation.
- **HEIC** declined explicitly (`INGEST_ANALYSIS_UNSUPPORTED_MEDIA_TYPE`), not silent.
- **No signed-URL/credential leak** in logs or exception bodies.

## Notes for PM / follow-up

1. `mypy .` surfaced a pre-existing error in `scripts/generate_openapi.py` (bare intra-package
   import resolved only at runtime via `sys.path`); fixed with a correct `# type: ignore`
   annotation. Proper fix (absolute import + W02 dev-tool pinning) belongs with W02.
2. ADR-009 written but **not** linked in `docs/index.md` / `docs/adr/README.md` (W03's domain).
3. Toolchain drift in the dev image motivates W02's lockfile work.
