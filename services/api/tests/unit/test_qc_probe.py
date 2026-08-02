"""The deterministic measurement, driven against media that is genuinely broken.

The work order's second acceptance criterion is the reason this file exists: "check present" is
not the claim, "check actually catches it" is. So nothing here is stubbed. Every fixture is
encoded by FFmpeg on the spot — a wholly black video, a silent one, a held frame, one with no
audio stream at all, one that is not a container — and the adapter is asked what it sees.

The failure paths get the same treatment. A missing binary, a symlinked source and a source
outside the run's own directory are all driven through the real adapter, because those are the
paths that only run when something has already gone wrong.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.infrastructure.render.qc_probe import FFmpegQcProbe
from app.modules.content.qc import (
    MediaQcProbePermanentError,
    MediaQcProbeTransientError,
    QcProbeRequest,
)

FFMPEG = "/usr/bin/ffmpeg"
requires_ffmpeg = pytest.mark.skipif(not Path(FFMPEG).exists(), reason="requires the ffmpeg binary")


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
    }
    return Settings(**(base | overrides))


def encode(path: Path, *video: str, audio: tuple[str, ...] | None, seconds: int = 3) -> None:
    command = [
        FFMPEG,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        *video,
    ]
    if audio is not None:
        command += ["-f", "lavfi", "-i", *audio]
    command += [
        "-t",
        str(seconds),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
    ]
    if audio is not None:
        command += ["-c:a", "aac", "-shortest"]
    else:
        command += ["-an"]
    command.append(str(path))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def request(path: Path, **overrides: Any) -> QcProbeRequest:
    base: dict[str, Any] = {
        "path": path,
        "workdir": path.parent,
        "frame_sample_count": 3,
        "frame_max_width": 320,
        "timeout_seconds": 120,
    }
    return QcProbeRequest(**(base | overrides))


# --- what the measurement sees ------------------------------------------------------------------


@requires_ffmpeg
@pytest.mark.asyncio
async def test_a_healthy_output_measures_clean(tmp_path: Path) -> None:
    source = tmp_path / "good.mp4"
    encode(
        source,
        "testsrc2=size=320x568:rate=15",
        audio=("sine=frequency=440:sample_rate=48000",),
    )
    result = await FFmpegQcProbe(settings()).measure(request=request(source))

    assert abs(result.duration_ms - 3_000) < 500
    assert (result.width, result.height) == (320, 568)
    assert result.has_audio_stream is True
    assert result.integrated_loudness_lufs is not None
    assert result.black_ratio == 0.0
    assert result.static_ratio == 0.0
    # Frames are sampled for the model checks and live inside the run's own directory.
    assert result.frames
    assert all(frame.parent == tmp_path and frame.is_file() for frame in result.frames)
    # Paths are debris, not record: the stored document counts them and keeps none.
    assert "frames" not in result.as_document()
    assert result.as_document()["frame_sample_count"] == len(result.frames)


@requires_ffmpeg
@pytest.mark.asyncio
async def test_a_wholly_black_output_is_caught_end_to_end(tmp_path: Path) -> None:
    """The interval never closes — the file ends while still black — and it still measures 1.0."""

    source = tmp_path / "black.mp4"
    encode(
        source,
        "color=c=black:size=320x568:rate=15",
        audio=("anullsrc=channel_layout=stereo:sample_rate=48000",),
    )
    result = await FFmpegQcProbe(settings()).measure(request=request(source))

    assert result.black_ratio >= 0.95
    assert result.longest_black_ms >= 2_500
    # A black picture is also a still one; both checks are entitled to say so.
    assert result.static_ratio >= 0.95


@requires_ffmpeg
@pytest.mark.asyncio
async def test_a_silent_track_measures_far_below_the_silence_floor(tmp_path: Path) -> None:
    source = tmp_path / "silent.mp4"
    encode(
        source,
        "testsrc2=size=320x568:rate=15",
        audio=("anullsrc=channel_layout=stereo:sample_rate=48000",),
    )
    result = await FFmpegQcProbe(settings()).measure(request=request(source))

    assert result.has_audio_stream is True
    assert result.integrated_loudness_lufs is not None
    assert result.integrated_loudness_lufs <= -60.0


@requires_ffmpeg
@pytest.mark.asyncio
async def test_a_held_frame_is_caught_without_being_black(tmp_path: Path) -> None:
    source = tmp_path / "frozen.mp4"
    encode(
        source,
        "color=c=blue:size=320x568:rate=15",
        audio=("sine=frequency=440:sample_rate=48000",),
    )
    result = await FFmpegQcProbe(settings()).measure(request=request(source))

    assert result.static_ratio >= 0.95
    assert result.longest_static_ms >= 2_500
    # Blue is not black; the two checks must not be the same check wearing two names.
    assert result.black_ratio == 0.0


@requires_ffmpeg
@pytest.mark.asyncio
async def test_an_output_with_no_audio_stream_reports_no_loudness(tmp_path: Path) -> None:
    """`None`, not zero and not a floor value: nothing was integrated, so nothing is claimed."""

    source = tmp_path / "mute.mp4"
    encode(source, "testsrc2=size=320x568:rate=15", audio=None)
    result = await FFmpegQcProbe(settings()).measure(request=request(source))

    assert result.has_audio_stream is False
    assert result.audio_codec is None
    assert result.integrated_loudness_lufs is None


@requires_ffmpeg
@pytest.mark.asyncio
async def test_a_short_output_is_measured_at_its_real_length(tmp_path: Path) -> None:
    source = tmp_path / "short.mp4"
    encode(
        source,
        "testsrc2=size=320x568:rate=15",
        audio=("sine=frequency=440:sample_rate=48000",),
        seconds=1,
    )
    result = await FFmpegQcProbe(settings()).measure(request=request(source))
    assert abs(result.duration_ms - 1_000) < 400


# --- the paths that only run when something is wrong ---------------------------------------------


@pytest.mark.asyncio
async def test_a_broken_container_is_permanent_and_says_nothing_about_the_file(
    tmp_path: Path,
) -> None:
    """A file that does not open is an answer about the output, not an outage — and it is quiet.

    FFmpeg echoes input paths and container metadata to stderr. None of it may surface in the
    error, because the error travels into a report, a log line and a span.
    """

    source = tmp_path / "secret-tenant-name.mp4"
    source.write_bytes(b"definitely not a container")
    with pytest.raises(MediaQcProbePermanentError) as error:
        await FFmpegQcProbe(settings()).measure(request=request(source))
    assert str(error.value) == "QC_CONTAINER_UNREADABLE"
    assert "secret-tenant-name" not in str(error.value)


@pytest.mark.asyncio
async def test_a_missing_binary_is_transient_rather_than_a_verdict(tmp_path: Path) -> None:
    """Nothing was learned about the video, so nothing may be concluded about it."""

    source = tmp_path / "any.mp4"
    source.write_bytes(b"x")
    probe = FFmpegQcProbe(settings(ffprobe_binary="/nonexistent/ffprobe"))
    with pytest.raises(MediaQcProbeTransientError, match="QC_PROBE_UNAVAILABLE"):
        await probe.measure(request=request(source))


@pytest.mark.asyncio
async def test_a_timeout_is_transient(tmp_path: Path) -> None:
    from unittest.mock import patch

    source = tmp_path / "any.mp4"
    source.write_bytes(b"x")

    def timeout(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=1)

    with patch("app.infrastructure.render.ffmpeg.subprocess.run", timeout):
        with pytest.raises(MediaQcProbeTransientError, match="QC_PROBE_TIMEOUT"):
            await FFmpegQcProbe(settings()).measure(request=request(source))


@pytest.mark.asyncio
async def test_a_symlinked_source_is_refused_before_any_process_starts(tmp_path: Path) -> None:
    """A symlink is how a job would reach a file outside its own scratch directory."""

    real = tmp_path / "real.mp4"
    real.write_bytes(b"x")
    link = tmp_path / "link.mp4"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):  # pragma: no cover - platform without symlinks
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(MediaQcProbePermanentError, match="QC_PROBE_SOURCE_INVALID"):
        await FFmpegQcProbe(settings()).measure(request=request(link))


@pytest.mark.asyncio
async def test_a_source_outside_the_run_directory_is_refused(tmp_path: Path) -> None:
    """Commands name the input by file name relative to the workdir; this keeps that true."""

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    source = elsewhere / "video.mp4"
    source.write_bytes(b"x")
    with pytest.raises(MediaQcProbePermanentError, match="QC_PROBE_SOURCE_OUTSIDE_WORKDIR"):
        await FFmpegQcProbe(settings()).measure(request=request(source, workdir=tmp_path))


@pytest.mark.asyncio
async def test_a_workdir_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    not_a_directory = tmp_path / "file"
    not_a_directory.write_bytes(b"x")
    with pytest.raises(MediaQcProbePermanentError, match="QC_PROBE_WORKDIR_INVALID"):
        await FFmpegQcProbe(settings()).measure(
            request=request(not_a_directory, workdir=not_a_directory)
        )


@requires_ffmpeg
@pytest.mark.asyncio
async def test_frame_sampling_failing_does_not_lose_the_deterministic_measurement(
    tmp_path: Path,
) -> None:
    """The vision half is optional; losing it must not cost the checks that actually work today."""

    source = tmp_path / "good.mp4"
    encode(
        source,
        "testsrc2=size=320x568:rate=15",
        audio=("sine=frequency=440:sample_rate=48000",),
    )
    result = await FFmpegQcProbe(settings(qc_frame_max_bytes=1_024)).measure(
        request=request(source)
    )
    # Every sampled frame exceeded the byte ceiling, so none survive — and the measurement is
    # still complete.
    assert result.frames == ()
    assert result.duration_ms > 0
    assert result.integrated_loudness_lufs is not None
