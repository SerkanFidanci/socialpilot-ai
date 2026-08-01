"""FFprobe implementation of `AudioProbePort`: what the file says, not what a provider said.

This adapter exists because a text-to-speech provider reporting the length of its own audio is
reporting an unverified number, and every downstream decision in slice 2C rests on that number —
whether the voiceover fits the canvas, how far it drifted from the script's target, how long a
cut may be. Re-deriving it from the container costs one bounded subprocess per line and removes
the provider from the trusted set for the one fact that matters most.

It is a separate adapter rather than a call into `media/technical.py`'s `FFprobeAdapter` because
that one is shaped for source video: it requires a video stream and returns raster dimensions, so
a WAV file is a `TECHNICAL_VIDEO_STREAM_REQUIRED` failure there. The shared thing between them is
the discipline, not the code: a fixed binary path, no shell, a hard timeout, and bounded output.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Final

from app.core.config import Settings
from app.modules.content.tts import (
    AudioProbePermanentError,
    AudioProbePort,
    AudioProbeTransientError,
    MeasuredAudio,
)

# FFprobe's JSON for a single-stream audio file is a few hundred bytes. These ceilings are three
# orders of magnitude above that and exist so a pathological file cannot turn into an unbounded
# read into memory.
MAX_STDOUT_BYTES: Final = 65_536
MAX_STDERR_BYTES: Final = 16_384
MAX_STREAMS: Final = 8


class FFprobeAudioProbe(AudioProbePort):
    """Measure one local audio file with a fixed ffprobe binary and no shell interpolation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def measure(self, *, path: Path, timeout_seconds: int) -> MeasuredAudio:
        command = [
            self._settings.ffprobe_binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=False,
                cwd=path.parent,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise AudioProbeTransientError("AUDIO_PROBE_TIMEOUT") from error
        except OSError as error:
            # A missing or unusable binary is an environment problem, not a claim about the
            # file, so the job may retry somewhere the binary exists.
            raise AudioProbeTransientError("AUDIO_PROBE_UNAVAILABLE") from error
        if (
            result.returncode != 0
            or len(result.stdout) > MAX_STDOUT_BYTES
            or len(result.stderr) > MAX_STDERR_BYTES
        ):
            raise AudioProbePermanentError("AUDIO_PROBE_INVALID_OUTPUT")
        try:
            return _normalize(json.loads(result.stdout))
        except (TypeError, ValueError, KeyError) as error:
            raise AudioProbePermanentError("AUDIO_PROBE_INVALID_OUTPUT") from error


def _normalize(payload: object) -> MeasuredAudio:
    if not isinstance(payload, dict):
        raise ValueError
    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams or len(streams) > MAX_STREAMS:
        raise ValueError
    audio = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
        None,
    )
    if audio is None:
        raise AudioProbePermanentError("AUDIO_PROBE_NO_AUDIO_STREAM")
    # The stream's own duration is preferred; some containers report only a format duration, and
    # for a voiceover object the two are the same file either way.
    seconds = _duration_seconds(audio) or _duration_seconds(payload.get("format"))
    if seconds is None or seconds <= 0:
        raise ValueError
    return MeasuredAudio(
        duration_ms=round(seconds * 1_000),
        sample_rate_hz=int(audio["sample_rate"]),
        channels=int(audio["channels"]),
        codec=str(audio["codec_name"]),
    )


def _duration_seconds(section: Any) -> float | None:
    if not isinstance(section, dict):
        return None
    raw = section.get("duration")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
