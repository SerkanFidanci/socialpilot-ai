"""Deterministic non-production adapters for the `tts` capability.

`FakeTTSAdapter` writes a **real audio file** — a quiet, obviously synthetic tone in a WAV
container whose length is derived from the text and the voice profile's speaking rate. That is
the difference between this fixture and a stub that returns a number: the whole point of slice
2C is that durations are *measured* with ffprobe rather than believed, and a fake that produces
no bytes would leave every alignment test asserting against the fixture's own arithmetic. Here
the file goes to disk, ffprobe reads it back, and the pipeline is exercised end to end with zero
provider spend.

The tone is deliberately not speech. A fixture that sounded like a voice actor could be mistaken
for output worth shipping; a 220 Hz beep cannot.

`DisabledTTSAdapter` is the production answer. Synthesized speech reading real, human-approved
marketing copy is publishable in a way a placeholder video file is not, so production gets an
adapter that declines with a documented code rather than an application that refuses to start
(the rule W13 settled and PM generalized). It refuses on call rather than at import, which is
why a deployment without a real provider still boots and still serves every other endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import wave
from array import array
from pathlib import Path
from typing import Final, Literal

from app.core.config import Settings
from app.modules.content.script import ProviderDescriptor
from app.modules.content.tts import (
    MAX_SEGMENT_AUDIO_MS,
    MIN_SEGMENT_AUDIO_MS,
    AudioResult,
    SynthesisRequest,
    TTSDisabledError,
    TTSPermanentError,
    TTSPort,
    TTSTransientError,
    VoiceProfile,
)

FAKE_PROVIDER: Final = "fake"
FAKE_MODEL: Final = "fake-tts-v1"
DISABLED_PROVIDER: Final = "disabled"
DISABLED_MODEL: Final = "none"

# Fixture audio parameters. Low rate and mono keep a full eight-line voiceover inside a couple
# of megabytes of worker scratch, which matters on the single server of ADR-013.
SAMPLE_RATE_HZ: Final = 22_050
CHANNELS: Final = 1
SAMPLE_WIDTH_BYTES: Final = 2
TONE_HZ: Final = 220.0
# Quiet enough to be unmistakably a placeholder if anyone ever plays it.
TONE_AMPLITUDE: Final = 0.08

# Turkish read aloud at a conversational pace sits near this rate including spaces. It is a
# fixture constant, not a platform fact: nothing downstream depends on the number being right,
# only on it being deterministic and on the resulting file being measurable.
CHARS_PER_SECOND: Final = 13.0


def fixture_duration_ms(text: str, profile: VoiceProfile) -> int:
    """The length the fixture will produce for one line. Pure, so a test can predict it."""

    rate = profile.speaking_rate if profile.speaking_rate > 0 else 1.0
    estimate = round(len(text) / (CHARS_PER_SECOND * rate) * 1_000)
    return max(MIN_SEGMENT_AUDIO_MS, min(MAX_SEGMENT_AUDIO_MS, estimate))


class FakeTTSAdapter(TTSPort):
    """Fixture-only provider covering the success path and every explicit failure path.

    The test hooks are all about *disagreement*: `declared_duration_ms` makes the provider lie
    about the length of the file it just wrote, and `duration_ms` makes it write a file of a
    length the text does not imply. Both exist because the guarantee under test is that the
    measurement wins.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        failure: Literal["transient", "permanent"] | None = None,
        declared_duration_ms: int | None = None,
        duration_ms: int | None = None,
        estimated_cost_minor: int = 0,
        actual_cost_minor: int = 0,
        currency: str = "TRY",
        provider: str = FAKE_PROVIDER,
        model: str = FAKE_MODEL,
    ) -> None:
        _reject_production(settings)
        # Public and mutable: a test seeds its tenant and script first, then decides how the
        # provider should misbehave. Rebuilding the adapter afterwards would mean rebuilding
        # the application.
        self.failure = failure
        self.declared_duration_ms = declared_duration_ms
        self.duration_ms = duration_ms
        self.fail_after_calls: int | None = None
        self._descriptor = ProviderDescriptor(
            provider=provider,
            model=model,
            currency=currency,
            estimated_cost_minor=estimated_cost_minor,
            enabled=True,
        )
        self._actual_cost_minor = actual_cost_minor
        # Tests assert on these: "the ceiling stopped the run" is only meaningful if the calls
        # provably did not happen.
        self.calls = 0
        self.last_request: SynthesisRequest | None = None

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    async def synthesize(self, *, request: SynthesisRequest, timeout_seconds: int) -> AudioResult:
        if timeout_seconds < 1:
            raise TTSPermanentError("TTS_TIMEOUT_INVALID")
        self.calls += 1
        self.last_request = request
        if self.failure == "transient" and self._should_fail():
            raise TTSTransientError("TTS_PROVIDER_UNAVAILABLE")
        if self.failure == "permanent" and self._should_fail():
            raise TTSPermanentError("TTS_PROVIDER_REJECTED_REQUEST")

        duration_ms = self.duration_ms or fixture_duration_ms(request.text, request.voice_profile)
        expected_bytes = _wav_byte_size(duration_ms)
        if expected_bytes > request.max_output_bytes:
            raise TTSPermanentError("TTS_OUTPUT_TOO_LARGE")
        await asyncio.to_thread(_write_tone, request.destination, duration_ms)
        payload = request.destination.read_bytes()
        return AudioResult(
            provider=self._descriptor.provider,
            model=self._descriptor.model,
            path=request.destination,
            content_type=request.output_format.content_type,
            byte_size=len(payload),
            sha256_checksum=hashlib.sha256(payload).hexdigest(),
            # What the provider *claims*. `declared_duration_ms` lets a test make this disagree
            # with the file on disk; the service is required to prefer the file.
            declared_duration_ms=(
                self.declared_duration_ms if self.declared_duration_ms is not None else duration_ms
            ),
            actual_cost_minor=self._actual_cost_minor,
            currency=self._descriptor.currency,
        )

    def _should_fail(self) -> bool:
        """Fail every call, or only from the nth onward when `fail_after_calls` is set.

        Partial output is the interesting failure for a multi-line voiceover: two lines stored,
        the third refused. A test can only produce that if the fixture can succeed and then stop.
        """

        return self.fail_after_calls is None or self.calls > self.fail_after_calls


class DisabledTTSAdapter(TTSPort):
    """Declines every call with a documented reason. Production's adapter until a real one lands."""

    def __init__(self, *, reason: str) -> None:
        self._reason = reason

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider=DISABLED_PROVIDER,
            model=DISABLED_MODEL,
            currency="TRY",
            estimated_cost_minor=0,
            enabled=False,
        )

    async def synthesize(self, *, request: SynthesisRequest, timeout_seconds: int) -> AudioResult:
        del request, timeout_seconds
        raise TTSDisabledError(self._reason)


def _wav_byte_size(duration_ms: int) -> int:
    return 44 + _frame_count(duration_ms) * CHANNELS * SAMPLE_WIDTH_BYTES


def _frame_count(duration_ms: int) -> int:
    return max(1, round(SAMPLE_RATE_HZ * duration_ms / 1_000))


def _write_tone(destination: Path, duration_ms: int) -> None:
    """Write a fixed-frequency 16-bit mono tone of the requested length."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    frames = _frame_count(duration_ms)
    peak = int(TONE_AMPLITUDE * 32_767)
    step = 2.0 * math.pi * TONE_HZ / SAMPLE_RATE_HZ
    samples = array("h", (int(peak * math.sin(step * index)) for index in range(frames)))
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH_BYTES)
        handle.setframerate(SAMPLE_RATE_HZ)
        handle.writeframes(samples.tobytes())


def _reject_production(settings: Settings) -> None:
    if settings.app_env == "production":
        raise RuntimeError("TTS_FAKE_ADAPTER_NOT_ALLOWED_IN_PRODUCTION")
