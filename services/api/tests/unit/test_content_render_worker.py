"""Worker isolation for the render job: timeouts, partial output, scratch, leak-freedom.

These are the failure paths — the ones that only run when something has already gone wrong,
and therefore the ones most likely to be broken without anyone noticing. Each is driven
through the real `FFmpegRenderAdapter` or the real scratch guard rather than a stub of them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.infrastructure.render.ffmpeg import FFmpegRenderAdapter
from app.modules.content.render import (
    PREVIEW_PROFILE,
    AiDisclosureState,
    PlannedAudio,
    PlannedSegment,
    RenderPermanentError,
    RenderPlan,
    RenderProfile,
    RenderRequest,
    RenderTransientError,
)
from app.modules.content.timeline import (
    TEXT_STYLES,
    AudioTrackKind,
    Canvas,
    CropMode,
    TransitionKind,
)
from app.worker.scratch import WorkerScratchExhausted, WorkerScratchGuard


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
        "render_adapter": "ffmpeg",
    }
    return Settings(**(base | overrides))


def plan(source: Path) -> RenderPlan:
    return RenderPlan(
        profile=RenderProfile.INSTAGRAM_REELS_1080X1920,
        canvas=Canvas(width=1080, height=1920, fps=30, duration_ms=1_000),
        segments=(
            PlannedSegment(
                asset_id=uuid4(),
                source_path=source,
                source_start_ms=0,
                source_end_ms=1_000,
                crop_mode=CropMode.SMART_COVER,
                transition_out=TransitionKind.CUT,
                has_audio=False,
            ),
        ),
        texts=(),
        logos=(),
        captions=(),
        caption_style=TEXT_STYLES["brand-caption-v1"],
        audio=PlannedAudio(source=AudioTrackKind.ORIGINAL, gain_db=0),
        ai_disclosure=AiDisclosureState.NONE,
    )


def request(source: Path, workdir: Path) -> RenderRequest:
    return RenderRequest(
        plan=plan(source),
        workdir=workdir,
        preview_profile=PREVIEW_PROFILE,
        timeout_seconds=120,
    )


@pytest.mark.asyncio
async def test_a_timeout_is_transient_and_leaves_no_partial_output(tmp_path: Path) -> None:
    """A killed encode must be retryable and must not leave half a video behind."""

    source = tmp_path / "source.mp4"
    source.write_bytes(b"not really a video, the subprocess is patched")
    adapter = FFmpegRenderAdapter(settings())

    def timeout(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    with patch("app.infrastructure.render.ffmpeg.subprocess.run", timeout):
        with pytest.raises(RenderTransientError, match="RENDER_TIMEOUT"):
            await adapter.render(request=request(source, tmp_path))
    # Only the input survives: every file the run created was removed on the way out.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source.mp4"]


@pytest.mark.asyncio
async def test_a_failed_encode_is_permanent_and_cleans_up(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"definitely not a video")
    adapter = FFmpegRenderAdapter(settings())
    with pytest.raises(RenderPermanentError, match="RENDER_SEGMENT_FAILED"):
        await adapter.render(request=request(source, tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source.mp4"]


@pytest.mark.asyncio
async def test_a_missing_source_is_refused_before_any_process_starts(tmp_path: Path) -> None:
    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no subprocess may start for an invalid source")

    with patch("app.infrastructure.render.ffmpeg.subprocess.run", explode):
        with pytest.raises(RenderPermanentError, match="RENDER_SOURCE_INVALID"):
            await FFmpegRenderAdapter(settings()).render(
                request=request(tmp_path / "absent.mp4", tmp_path)
            )


@pytest.mark.asyncio
async def test_a_symlinked_source_is_refused(tmp_path: Path) -> None:
    """A symlink is how a job would reach a file outside its own scratch directory."""

    real = tmp_path / "real.mp4"
    real.write_bytes(b"x")
    link = tmp_path / "link.mp4"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):  # pragma: no cover - platform without symlinks
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(RenderPermanentError, match="RENDER_SOURCE_INVALID"):
        await FFmpegRenderAdapter(settings()).render(request=request(link, tmp_path))


@pytest.mark.asyncio
async def test_a_workdir_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    not_a_directory = tmp_path / "file"
    not_a_directory.write_bytes(b"x")
    with pytest.raises(RenderPermanentError, match="RENDER_WORKDIR_INVALID"):
        await FFmpegRenderAdapter(settings()).render(
            request=request(not_a_directory, not_a_directory)
        )


@pytest.mark.asyncio
async def test_diagnostics_never_reach_the_error(tmp_path: Path) -> None:
    """FFmpeg echoes paths and metadata to stderr; none of it may surface."""

    source = tmp_path / "secret-tenant-name.mp4"
    source.write_bytes(b"definitely not a video")
    with pytest.raises(RenderPermanentError) as error:
        await FFmpegRenderAdapter(settings()).render(request=request(source, tmp_path))
    message = str(error.value)
    assert "secret-tenant-name" not in message
    assert message == "RENDER_SEGMENT_FAILED"


@pytest.mark.asyncio
async def test_an_oversized_output_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    make = subprocess.run(
        [
            "/usr/bin/ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=320x568:rate=15",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    if make.returncode != 0:  # pragma: no cover - ffmpeg absent
        pytest.skip("ffmpeg is unavailable")
    adapter = FFmpegRenderAdapter(settings(render_max_output_bytes=1))
    with pytest.raises(RenderPermanentError, match="RENDER_OUTPUT_SIZE_EXCEEDED"):
        await adapter.render(request=request(source, tmp_path))


def test_the_scratch_guard_still_stops_a_drain_over_budget(tmp_path: Path) -> None:
    """W07's guard must remain effective now that renders share the same scratch root."""

    guard = WorkerScratchGuard(tmp_path, max_bytes=16)
    (tmp_path / "leftover.bin").write_bytes(b"x" * 64)
    with pytest.raises(WorkerScratchExhausted) as error:
        guard.ensure_within_budget()
    assert error.value.error_code == "WORKER_SCRATCH_BUDGET_EXCEEDED"


def test_the_render_drain_checks_scratch_before_each_job() -> None:
    """The render drain goes through the same guarded `_drain` helper as media analysis."""

    import inspect

    from app.worker import tasks

    source = inspect.getsource(tasks.drain_content_render)
    assert "needs_workdir=True" in source
    drain = inspect.getsource(tasks._drain)
    assert "ensure_within_budget" in drain
    assert "reclaim_stale" in drain
