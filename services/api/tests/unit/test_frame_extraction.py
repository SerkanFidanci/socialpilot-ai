"""Contract and real-FFmpeg tests for bounded frame extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.infrastructure.media.frame_extraction import (
    FFmpegFrameExtractionAdapter,
    select_frame_timestamps,
)
from app.modules.media.video_understanding import (
    FrameExtractionPermanentError,
    FrameExtractionTransientError,
    VideoUnderstandingRequest,
)


def settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def request() -> VideoUnderstandingRequest:
    return VideoUnderstandingRequest(
        asset_id=uuid4(),
        scene_id=uuid4(),
        scene_start_ms=0,
        scene_end_ms=1_000,
        transcript_context="",
        frames=(),
    )


def make_video(path: Path, *, size: str, duration: float = 1.0, audio: bool = False) -> None:
    command = [
        "/usr/bin/ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate=24",
        "-t",
        str(duration),
    ]
    if audio:
        command.extend(["-f", "lavfi", "-i", "sine=frequency=800:sample_rate=16000", "-shortest"])
    command.extend(["-pix_fmt", "yuv420p", str(path)])
    subprocess.run(command, check=True, capture_output=True)


def test_timestamp_selection_is_bounded_deterministic_and_nonduplicating() -> None:
    timestamps = select_frame_timestamps(
        scene_start_ms=1_000,
        scene_end_ms=2_000,
        frames_per_scene=3,
        maximum_frames=3,
        boundary_offset_ms=100,
    )
    assert timestamps == (1_100, 1_499, 1_899)
    short = select_frame_timestamps(
        scene_start_ms=0,
        scene_end_ms=2,
        frames_per_scene=5,
        maximum_frames=5,
        boundary_offset_ms=100,
    )
    assert short == (0, 1)
    assert timestamps == select_frame_timestamps(
        scene_start_ms=1_000,
        scene_end_ms=2_000,
        frames_per_scene=3,
        maximum_frames=3,
        boundary_offset_ms=100,
    )


def test_frame_limits_are_configuration_bounded() -> None:
    with pytest.raises(ValidationError, match="FRAMES_PER_SCENE"):
        settings(video_understanding_frames_per_scene=2, video_understanding_max_frames_per_asset=1)
    with pytest.raises(ValidationError, match="VIDEO_UNDERSTANDING_JOB_PER_SCENE_TIMEOUT_SECONDS"):
        settings(
            frame_extraction_timeout_seconds=120,
            video_understanding_job_per_scene_timeout_seconds=120,
        )


@pytest.mark.asyncio
async def test_real_landscape_and_portrait_frames_are_bounded_and_cleaned(tmp_path: Path) -> None:
    adapter = FFmpegFrameExtractionAdapter(settings())
    for name, size in (("landscape", "320x180"), ("portrait", "180x320")):
        workdir = tmp_path / name
        workdir.mkdir()
        source = workdir / "proxy.mp4"
        make_video(source, size=size, audio=name == "portrait")
        frames = await adapter.extract(
            request=request(),
            source_path=source,
            workdir=workdir,
            timeout_seconds=5,
            maximum_frames=3,
        )
        assert len(frames) == 3
        assert all(frame.local_path.is_relative_to(workdir) for frame in frames)
        assert all(
            frame.width <= 1280 and frame.height <= 720 and frame.byte_size > 0 for frame in frames
        )
        for frame in frames:
            frame.local_path.unlink()
        assert not list(workdir.glob("frame-*.jpg"))


@pytest.mark.asyncio
async def test_rejects_uncontrolled_symlink_and_limit_outputs(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    outside = tmp_path / "outside.mp4"
    make_video(outside, size="320x180")
    link = workdir / "proxy.mp4"
    link.symlink_to(outside)
    adapter = FFmpegFrameExtractionAdapter(settings())
    with pytest.raises(FrameExtractionPermanentError, match="FRAME_SOURCE_INVALID"):
        await adapter.extract(
            request=request(),
            source_path=link,
            workdir=workdir,
            timeout_seconds=5,
            maximum_frames=1,
        )
    link.unlink()
    source = workdir / "proxy.mp4"
    make_video(source, size="320x180")
    with pytest.raises(FrameExtractionPermanentError, match="FRAME_OUTPUT_INVALID"):
        await FFmpegFrameExtractionAdapter(settings(video_understanding_max_frame_bytes=1)).extract(
            request=request(),
            source_path=source,
            workdir=workdir,
            timeout_seconds=5,
            maximum_frames=1,
        )
    with pytest.raises(FrameExtractionPermanentError, match="FRAME_OUTPUT_INVALID"):
        await FFmpegFrameExtractionAdapter(
            settings(video_understanding_max_frame_width=100)
        ).extract(
            request=request(),
            source_path=source,
            workdir=workdir,
            timeout_seconds=5,
            maximum_frames=1,
        )


@pytest.mark.asyncio
async def test_timeout_and_nonzero_failures_are_classified(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    source = workdir / "proxy.mp4"
    make_video(source, size="320x180")
    sleeper = tmp_path / "sleeper"
    sleeper.write_text("#!/bin/sh\nsleep 2\n", encoding="utf-8")
    sleeper.chmod(0o755)
    with pytest.raises(FrameExtractionTransientError, match="FRAME_EXTRACTION_TIMEOUT"):
        await FFmpegFrameExtractionAdapter(settings(ffmpeg_binary=str(sleeper))).extract(
            request=request(),
            source_path=source,
            workdir=workdir,
            timeout_seconds=1,
            maximum_frames=1,
        )
    with pytest.raises(FrameExtractionPermanentError, match="FRAME_EXTRACTION_FAILED"):
        await FFmpegFrameExtractionAdapter(settings(ffmpeg_binary="/bin/false")).extract(
            request=request(),
            source_path=source,
            workdir=workdir,
            timeout_seconds=5,
            maximum_frames=1,
        )


@pytest.mark.asyncio
async def test_excessive_stderr_is_rejected_without_leaking_diagnostics(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    source = workdir / "proxy.mp4"
    make_video(source, size="320x180")
    noisy = tmp_path / "noisy"
    secret = "private-frame-diagnostic"
    noisy.write_text(f"#!/bin/sh\nyes '{secret}' | head -c 20000 >&2\nexit 1\n", encoding="utf-8")
    noisy.chmod(0o755)
    with pytest.raises(
        FrameExtractionPermanentError, match="FRAME_DIAGNOSTIC_LIMIT_EXCEEDED"
    ) as error:
        await FFmpegFrameExtractionAdapter(settings(ffmpeg_binary=str(noisy))).extract(
            request=request(),
            source_path=source,
            workdir=workdir,
            timeout_seconds=5,
            maximum_frames=1,
        )
    assert secret not in str(error.value)
