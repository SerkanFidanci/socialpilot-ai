"""The deterministic half of §19.4's QC, measured with FFmpeg. One adapter, not the port.

`MediaQcProbePort` describes *what* has to be established about a rendered file; this file is
the only place that knows *how*. The domain never learns that black frames are found with a
filter graph, and a managed render service that returns its own measurements stays a viable
second adapter.

Four bounded passes, each with its own timeout:

1. **Container** — one `ffprobe` JSON read: duration, geometry, codecs, whether an audio stream
   exists at all. A file this pass cannot parse is the answer to "video açılıyor mu", so it
   raises `MediaQcProbePermanentError` and the caller records a *failed* check rather than an
   unknown one.
2. **Picture** — `blackdetect` and `freezedetect` in one graph, writing their findings through
   the `metadata` filter into a private file. This is the load-bearing safety choice in the
   module: both filters normally announce themselves on stderr, and this pipeline's rule is that
   FFmpeg's stderr is written to a private file whose *size* is inspected and whose contents are
   never read, because FFmpeg echoes input paths and container metadata into it. Routing the
   measurement through `metadata=mode=print:file=` keeps that rule intact — the file this module
   parses contains frame numbers, timestamps and `lavfi.*` values, and nothing else. Nothing is
   learned from stderr here either.
3. **Loudness** — `ebur128` in metadata mode, through the same mechanism. Only the integrated
   value is printed, and the parse keeps a single scalar, so a three-minute render costs one
   bounded file and constant memory.
4. **Frames** — a small, evenly spaced JPEG sample for the model checks. Bounded in count, in
   width and in bytes, following the frame budget `media/frame_extraction.py` set.

Every command runs with a fixed binary from `Settings`, `shell=False`, a hard timeout, and a
working directory it cannot escape. Inputs are referenced by file *name* relative to that
directory, so no tenant path is ever interpolated into a filter string.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.infrastructure.render.ffmpeg import _run_with_bounded_diagnostics
from app.modules.content.qc import (
    MediaQcProbePermanentError,
    MediaQcProbePort,
    MediaQcProbeTransientError,
    QcMeasurement,
    QcProbeRequest,
)

# ffprobe's JSON for one short output is a few kilobytes; this ceiling is far above that and
# exists so a pathological file cannot become an unbounded read into memory.
_PROBE_STDOUT_LIMIT = 65_536
_STDERR_LIMIT = 16_384
_MAX_STREAMS = 8
# The metadata files hold two short lines per detected event (picture) or per audio frame
# (loudness). A three-minute render lands well under this; exceeding it means something is
# emitting far more than the filters should, and an unbounded parse is not the answer.
_METADATA_LIMIT_BYTES = 4 * 1024 * 1024

# `blackdetect`'s own sensitivity: a frame counts as black when at least 98% of its pixels are
# below 10% luminance. These are the filter's defaults, restated so a future change to them is a
# change to this file rather than a silent change of meaning under it. They are detector tuning,
# not policy — how much black is *acceptable* is `QC_BLACK_RATIO_LIMIT`, and that is config.
_BLACK_PICTURE_RATIO = 0.98
_BLACK_PIXEL_THRESHOLD = 0.10
# `freezedetect`'s noise tolerance. Encoded video is never bit-identical frame to frame, so a
# held shot still differs slightly; this is the difference below which two frames count as the
# same picture.
_FREEZE_NOISE = 0.003

_METADATA_LINE = re.compile(r"^lavfi\.([A-Za-z0-9_.]+)=(.*)$")


@dataclass(frozen=True, slots=True)
class _Interval:
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


class FFmpegQcProbe(MediaQcProbePort):
    """Measure one rendered file with a fixed FFmpeg/ffprobe pair and no shell interpolation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def measure(self, *, request: QcProbeRequest) -> QcMeasurement:
        workdir = _controlled_directory(request.workdir)
        source = _controlled_source(request.path)
        if source.parent != workdir:
            # Every command below names the input by file name relative to `workdir`, which is
            # what keeps a tenant-influenced path out of a filter string. A source outside that
            # directory would silently break the property, so it is refused instead.
            raise MediaQcProbePermanentError("QC_PROBE_SOURCE_OUTSIDE_WORKDIR")

        container = await self._container(source, workdir, request.timeout_seconds)
        picture = await self._picture(
            source, workdir, request.timeout_seconds, container.duration_ms
        )
        loudness = (
            await self._loudness(source, workdir, request.timeout_seconds)
            if container.has_audio_stream
            else None
        )
        frames = await self._frames(source, workdir, request)
        return QcMeasurement(
            duration_ms=container.duration_ms,
            width=container.width,
            height=container.height,
            video_codec=container.video_codec,
            audio_codec=container.audio_codec,
            has_audio_stream=container.has_audio_stream,
            integrated_loudness_lufs=loudness,
            black_ratio=_ratio(picture.black, container.duration_ms),
            longest_black_ms=_longest(picture.black),
            static_ratio=_ratio(picture.frozen, container.duration_ms),
            longest_static_ms=_longest(picture.frozen),
            frames=frames,
        )

    # --- pass 1: the container ---------------------------------------------------------------

    async def _container(self, source: Path, workdir: Path, timeout: int) -> _Container:
        result = await self._run(
            [
                self._settings.ffprobe_binary,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                source.name,
            ],
            workdir,
            timeout,
            capture_stdout=True,
        )
        stdout = result.stdout or ""
        if result.returncode != 0 or len(stdout) > _PROBE_STDOUT_LIMIT:
            raise MediaQcProbePermanentError("QC_CONTAINER_UNREADABLE")
        try:
            payload = json.loads(stdout)
            streams = list(payload["streams"])[:_MAX_STREAMS]
            video = next(item for item in streams if item.get("codec_type") == "video")
            audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
            duration_ms = round(float(payload["format"]["duration"]) * 1000)
            width, height = int(video["width"]), int(video["height"])
        except (KeyError, IndexError, StopIteration, TypeError, ValueError) as error:
            raise MediaQcProbePermanentError("QC_CONTAINER_UNREADABLE") from error
        if duration_ms < 1 or width < 1 or height < 1:
            raise MediaQcProbePermanentError("QC_CONTAINER_UNREADABLE")
        return _Container(
            duration_ms=duration_ms,
            width=width,
            height=height,
            video_codec=str(video.get("codec_name", ""))[:32],
            audio_codec=None if audio is None else str(audio.get("codec_name", ""))[:32],
            has_audio_stream=audio is not None,
        )

    # --- pass 2: black and frozen picture -----------------------------------------------------

    async def _picture(
        self, source: Path, workdir: Path, timeout: int, duration_ms: int
    ) -> _Picture:
        report = workdir / "qc-picture.txt"
        result = await self._run(
            [
                self._settings.ffmpeg_binary,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                source.name,
                "-an",
                "-vf",
                (
                    f"blackdetect=d={_seconds(self._settings.qc_black_min_ms)}:"
                    f"pic_th={_BLACK_PICTURE_RATIO}:pix_th={_BLACK_PIXEL_THRESHOLD},"
                    f"freezedetect=n={_FREEZE_NOISE}:"
                    f"d={_seconds(self._settings.qc_freeze_min_ms)},"
                    f"metadata=mode=print:file={report.name}"
                ),
                "-f",
                "null",
                "-",
            ],
            workdir,
            timeout,
        )
        if result.returncode != 0:
            # The file opened for `ffprobe`, so an analysis pass that cannot run is an
            # environment problem rather than a verdict about the picture.
            raise MediaQcProbeTransientError("QC_PICTURE_ANALYSIS_FAILED")
        return _parse_picture(_read_metadata(report), duration_ms=duration_ms)

    # --- pass 3: loudness ---------------------------------------------------------------------

    async def _loudness(self, source: Path, workdir: Path, timeout: int) -> float | None:
        report = workdir / "qc-loudness.txt"
        result = await self._run(
            [
                self._settings.ffmpeg_binary,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                source.name,
                "-vn",
                "-af",
                (f"ebur128=metadata=1,ametadata=mode=print:file={report.name}:key=lavfi.r128.I"),
                "-f",
                "null",
                "-",
            ],
            workdir,
            timeout,
        )
        if result.returncode != 0:
            # No loudness measurement is `None`, which the caller turns into `unknown` rather
            # than into a passing check. A number nobody measured must never look like one.
            return None
        # `lavfi.r128.I` is cumulative: the last value printed is the integrated loudness of the
        # whole programme, which is what EBU R128 defines. Only that scalar is kept, so file
        # length never becomes memory use.
        integrated: float | None = None
        for key, value in _read_metadata(report):
            if key == "r128.I":
                try:
                    integrated = float(value)
                except ValueError:
                    continue
        return integrated

    # --- pass 4: sample frames ----------------------------------------------------------------

    async def _frames(
        self, source: Path, workdir: Path, request: QcProbeRequest
    ) -> tuple[Path, ...]:
        """Evenly spaced JPEGs for the model checks. A failure here costs frames, not the run.

        The vision adapter is what turns frames into answers, and it is `disabled` in every
        deployment today. Failing the whole measurement because a sampling pass did not run
        would throw away the deterministic checks — which are the ones that work — so this
        returns an empty tuple and lets the model checks go `unknown` on their own.
        """

        if request.frame_sample_count < 1:
            return ()
        result = await self._run(
            [
                self._settings.ffmpeg_binary,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                source.name,
                "-vf",
                f"thumbnail,scale={request.frame_max_width}:-2",
                "-frames:v",
                str(request.frame_sample_count),
                "-q:v",
                "5",
                "qc-frame-%03d.jpg",
            ],
            workdir,
            request.timeout_seconds,
        )
        if result.returncode != 0:
            return ()
        frames: list[Path] = []
        for index in range(1, request.frame_sample_count + 1):
            candidate = workdir / f"qc-frame-{index:03d}.jpg"
            if not candidate.is_file():
                break
            if candidate.stat().st_size > self._settings.qc_frame_max_bytes:
                candidate.unlink(missing_ok=True)
                break
            frames.append(candidate)
        return tuple(frames)

    # --- process plumbing ---------------------------------------------------------------------

    async def _run(
        self,
        command: list[str],
        workdir: Path,
        timeout: int,
        *,
        capture_stdout: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run one bounded, quiet command. Diagnostics are sized, never read.

        `_run_with_bounded_diagnostics` is imported from the render adapter rather than copied:
        "run with no shell, a timeout, and stderr into a private file nobody reads" is one rule,
        and two implementations of it would eventually be two rules.
        """

        try:
            result, stderr_size = await asyncio.to_thread(
                _run_with_bounded_diagnostics, command, workdir, timeout, capture_stdout
            )
        except subprocess.TimeoutExpired as error:
            raise MediaQcProbeTransientError("QC_PROBE_TIMEOUT") from error
        except OSError as error:
            # A missing or unusable binary is an environment problem, not a claim about the
            # file, so the job may retry somewhere the binary exists.
            raise MediaQcProbeTransientError("QC_PROBE_UNAVAILABLE") from error
        if stderr_size > _STDERR_LIMIT:
            raise MediaQcProbeTransientError("QC_PROBE_DIAGNOSTIC_LIMIT_EXCEEDED")
        return result


@dataclass(frozen=True, slots=True)
class _Container:
    duration_ms: int
    width: int
    height: int
    video_codec: str
    audio_codec: str | None
    has_audio_stream: bool


@dataclass(frozen=True, slots=True)
class _Picture:
    black: tuple[_Interval, ...]
    frozen: tuple[_Interval, ...]


def _read_metadata(path: Path) -> list[tuple[str, str]]:
    """Read `lavfi.*` key/value pairs out of a metadata dump, bounded in size.

    The file was written by the `metadata` filter, so it holds frame numbers, timestamps and
    filter values — never an input path. Anything past the ceiling is dropped rather than parsed:
    a run that produced megabytes of events is not one to reason about line by line.
    """

    try:
        if not path.is_file() or path.stat().st_size > _METADATA_LIMIT_BYTES:
            return []
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    pairs: list[tuple[str, str]] = []
    for line in raw.splitlines():
        matched = _METADATA_LINE.match(line.strip())
        if matched is not None:
            pairs.append((matched.group(1), matched.group(2).strip()))
    return pairs


def _parse_picture(pairs: list[tuple[str, str]], *, duration_ms: int) -> _Picture:
    """Turn the filters' event stream into closed intervals.

    An interval left open at the end of the file is closed at the end of the video rather than
    discarded. That case is not an edge case here — it is exactly what a completely black or
    completely frozen render looks like, which is the failure this check exists to catch.
    """

    black: list[_Interval] = []
    frozen: list[_Interval] = []
    black_start: int | None = None
    freeze_start: int | None = None
    for key, value in pairs:
        seconds = _float(value)
        if seconds is None:
            continue
        milliseconds = round(seconds * 1000)
        if key == "black_start":
            black_start = milliseconds
        elif key == "black_end" and black_start is not None:
            black.append(_Interval(black_start, milliseconds))
            black_start = None
        elif key == "freezedetect.freeze_start":
            freeze_start = milliseconds
        elif key == "freezedetect.freeze_end" and freeze_start is not None:
            frozen.append(_Interval(freeze_start, milliseconds))
            freeze_start = None
    if black_start is not None:
        black.append(_Interval(black_start, duration_ms))
    if freeze_start is not None:
        frozen.append(_Interval(freeze_start, duration_ms))
    return _Picture(black=tuple(black), frozen=tuple(frozen))


def _ratio(intervals: tuple[_Interval, ...], duration_ms: int) -> float:
    if duration_ms <= 0:
        return 0.0
    total = sum(interval.duration_ms for interval in intervals)
    return min(1.0, round(total / duration_ms, 4))


def _longest(intervals: tuple[_Interval, ...]) -> int:
    return max((interval.duration_ms for interval in intervals), default=0)


def _float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.3f}"


def _controlled_directory(workdir: Path) -> Path:
    if workdir.is_symlink() or not workdir.is_dir():
        raise MediaQcProbePermanentError("QC_PROBE_WORKDIR_INVALID")
    return workdir.resolve(strict=True)


def _controlled_source(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise MediaQcProbePermanentError("QC_PROBE_SOURCE_INVALID")
    return path.resolve(strict=True)
