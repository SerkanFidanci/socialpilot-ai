"""Bounded FFmpeg frame extraction behind the provider-neutral port."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from app.core.config import Settings
from app.modules.media.video_understanding import (
    FrameExtractionPermanentError,
    FrameExtractionPort,
    FrameExtractionTransientError,
    FrameReference,
    VideoUnderstandingRequest,
)

_STDERR_LIMIT = 16_384


def select_frame_timestamps(
    *,
    scene_start_ms: int,
    scene_end_ms: int,
    frames_per_scene: int,
    maximum_frames: int,
    boundary_offset_ms: int,
) -> tuple[int, ...]:
    """Return deterministic, strictly ordered timestamps inside one scene."""

    if scene_start_ms < 0 or scene_end_ms <= scene_start_ms:
        raise FrameExtractionPermanentError("FRAME_SCENE_RANGE_INVALID")
    if frames_per_scene < 1 or maximum_frames < 1 or boundary_offset_ms < 0:
        raise FrameExtractionPermanentError("FRAME_EXTRACTION_CONFIGURATION_INVALID")
    count = min(frames_per_scene, maximum_frames)
    duration = scene_end_ms - scene_start_ms
    if duration <= boundary_offset_ms * 2 + 1:
        lower, upper = scene_start_ms, scene_end_ms - 1
    else:
        lower = scene_start_ms + boundary_offset_ms
        upper = scene_end_ms - 1 - boundary_offset_ms
    count = min(count, upper - lower + 1)
    if count == 1:
        return (lower + (upper - lower) // 2,)
    return tuple(lower + ((upper - lower) * index) // (count - 1) for index in range(count))


class FFmpegFrameExtractionAdapter(FrameExtractionPort):
    """Extract validated JPEG frames without accepting caller-controlled commands or paths."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def extract(
        self,
        *,
        request: VideoUnderstandingRequest,
        source_path: Path,
        workdir: Path,
        timeout_seconds: int,
        maximum_frames: int,
    ) -> tuple[FrameReference, ...]:
        if timeout_seconds < 1:
            raise FrameExtractionPermanentError("FRAME_EXTRACTION_TIMEOUT_INVALID")
        controlled_workdir = _controlled_directory(workdir)
        source = _controlled_file(source_path, controlled_workdir, expected_suffixes={".mp4"})
        timestamps = select_frame_timestamps(
            scene_start_ms=request.scene_start_ms,
            scene_end_ms=request.scene_end_ms,
            frames_per_scene=self._settings.video_understanding_frames_per_scene,
            maximum_frames=maximum_frames,
            boundary_offset_ms=self._settings.video_understanding_frame_boundary_offset_ms,
        )
        created: list[Path] = []
        try:
            frames: list[FrameReference] = []
            for index, timestamp in enumerate(timestamps):
                output = controlled_workdir / f"frame-{index:03d}-{timestamp}.jpg"
                created.append(output)
                await self._run_ffmpeg(
                    source, output, timestamp, controlled_workdir, timeout_seconds
                )
                width, height = await self._probe_dimensions(
                    output, controlled_workdir, timeout_seconds
                )
                stat = output.stat()
                _validate_frame(
                    output, controlled_workdir, stat.st_size, width, height, self._settings
                )
                frames.append(
                    FrameReference(
                        scene_id=request.scene_id,
                        timestamp_ms=timestamp,
                        local_path=output,
                        width=width,
                        height=height,
                        byte_size=stat.st_size,
                        content_type="image/jpeg",
                    )
                )
            return tuple(frames)
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise

    async def _run_ffmpeg(
        self, source: Path, output: Path, timestamp_ms: int, workdir: Path, timeout_seconds: int
    ) -> None:
        command = [
            self._settings.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{timestamp_ms / 1000:.3f}",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-an",
            "-c:v",
            "mjpeg",
            "-q:v",
            "3",
            str(output),
        ]
        await self._run(command, workdir, timeout_seconds, "FRAME_EXTRACTION_FAILED")

    async def _probe_dimensions(
        self, output: Path, workdir: Path, timeout_seconds: int
    ) -> tuple[int, int]:
        result = await self._run(
            [
                self._settings.ffprobe_binary,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(output),
            ],
            workdir,
            timeout_seconds,
            "FRAME_OUTPUT_INVALID",
        )
        try:
            stream = json.loads(result.stdout).get("streams", [])[0]
            width, height = int(stream["width"]), int(stream["height"])
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise FrameExtractionPermanentError("FRAME_OUTPUT_INVALID") from error
        return width, height

    async def _run(
        self, command: list[str], workdir: Path, timeout_seconds: int, error_code: str
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                cwd=workdir,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise FrameExtractionTransientError("FRAME_EXTRACTION_TIMEOUT") from error
        except OSError as error:
            raise FrameExtractionTransientError("FRAME_EXTRACTION_UNAVAILABLE") from error
        _ = result.stderr[:_STDERR_LIMIT]
        if result.returncode != 0:
            raise FrameExtractionPermanentError(error_code)
        return result


def _controlled_directory(workdir: Path) -> Path:
    if workdir.is_symlink() or not workdir.is_dir():
        raise FrameExtractionPermanentError("FRAME_WORKDIR_INVALID")
    return workdir.resolve(strict=True)


def _controlled_file(path: Path, workdir: Path, *, expected_suffixes: set[str]) -> Path:
    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in expected_suffixes:
        raise FrameExtractionPermanentError("FRAME_SOURCE_INVALID")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(workdir):
        raise FrameExtractionPermanentError("FRAME_PATH_INVALID")
    return resolved


def _validate_frame(
    path: Path, workdir: Path, size: int, width: int, height: int, settings: Settings
) -> None:
    _controlled_file(path, workdir, expected_suffixes={".jpg", ".jpeg"})
    if size < 1 or size > settings.video_understanding_max_frame_bytes:
        raise FrameExtractionPermanentError("FRAME_OUTPUT_INVALID")
    if (
        width < 1
        or height < 1
        or width > settings.video_understanding_max_frame_width
        or height > settings.video_understanding_max_frame_height
    ):
        raise FrameExtractionPermanentError("FRAME_OUTPUT_INVALID")
