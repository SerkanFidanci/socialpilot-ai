"""The `tts` capability port (PRD §17.3) and the voiceover contract around it.

Slice 2B produced a script; this module turns one into sound. Three properties are load-bearing
and none of them are stylistic.

**Nothing is spoken that a record did not vouch for.** A synthesis request names a stored
`content_scripts` row and nothing else. The text handed to the provider is the script's
**resolved** document — the one where `{{price:…}}` was already substituted by code from
`product_prices` — so a figure that reaches a listener is a figure a verified record held. There
is no field anywhere in this module that carries free text in from a caller, which is a stronger
promise than validating one: prose the API cannot express cannot be smuggled past a check.

**Duration is measured, never believed.** A provider that reports the length of its own audio is
reporting a number nobody verified, and every downstream decision — how long a cut may be,
whether the voiceover fits the canvas, how far the result drifted from the script's target —
rests on that number. `AudioProbePort` re-derives it from the file itself. `AudioResult` still
carries the provider's claim, so a disagreement stays visible in the record rather than being
quietly overwritten.

**A voice is versioned.** `VoiceProfile` is a closed registry with an explicit version, and the
exact profile document handed to the provider is stored beside the audio (PRD §17.6's pattern
applied to speech). Audio whose voice, language and speaking rate cannot be named later is audio
nobody can reproduce or defend.

Nothing here touches a database, a clock, a subprocess or a provider SDK. Parsing, planning and
alignment are pure, so the same code runs at the API boundary and in any later re-check.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

# `ProviderDescriptor` and `RouteSnapshot` are deliberately reused rather than redefined. They
# describe a routing decision, not a script: one definition keeps every capability's route
# snapshot and `provider_usage` row the same shape, so cost across capabilities can be summed
# without reconciling two records that drifted apart.
from app.modules.content.script import ProviderDescriptor, RouteSnapshot

__all__ = [
    "AudioFormat",
    "AudioProbePermanentError",
    "AudioProbePort",
    "AudioProbeTransientError",
    "AudioResult",
    "MAX_VOICEOVER_LINES",
    "MAX_VOICEOVER_TEXT_CHARS",
    "MeasuredAudio",
    "ProviderDescriptor",
    "RouteSnapshot",
    "SynthesisRequest",
    "TTSDisabledError",
    "TTSPermanentError",
    "TTSPort",
    "TTSTransientError",
    "TTS_CAPABILITY",
    "VOICE_PROFILES",
    "VoiceProfile",
    "VoiceoverLine",
    "VoiceoverSegment",
    "VoiceoverSourceError",
    "VoiceoverStatus",
    "resolve_voice_profile",
    "script_lines",
    "segment_object_key",
    "serialize_segments",
    "total_drift_ms",
    "total_duration_ms",
]

# PRD §17.1's capability name, verbatim. It is what `provider_usage.capability` records, so the
# cost of every voiceover in the system sums with one equality test.
TTS_CAPABILITY: Final = "tts"

# Bounds. A script is already bounded by §18.1's parser, but this module is the last place
# before money is spent per line, so it re-states the ceilings rather than inheriting them by
# coincidence: a future scenario with more segments must widen this deliberately.
MAX_VOICEOVER_LINES: Final = 8
MAX_VOICEOVER_TEXT_CHARS: Final = 900
MIN_VOICEOVER_TEXT_CHARS: Final = 1
# What a single synthesized line may measure. The floor catches an adapter that produced an
# empty container; the ceiling catches one that produced a file unrelated to the request.
MIN_SEGMENT_AUDIO_MS: Final = 100
MAX_SEGMENT_AUDIO_MS: Final = 60_000
MAX_TOTAL_AUDIO_MS: Final = 180_000

_CONTROL_CHARACTERS: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class VoiceoverStatus(StrEnum):
    """The lifecycle of one voiceover production.

    `pending` is written and committed *before* the first provider call, together with the route
    snapshot (ADR-007). It is therefore also the honest crash state: a row stuck in `pending`
    means calls may have been billed and never settled — exactly what a cost-attribution record
    has to be able to say.
    """

    PENDING = "pending"
    GENERATED = "generated"
    FAILED = "failed"


class AudioFormat(StrEnum):
    """Container the provider is asked for. Closed, because storage, the probe and the render
    adapter each have to know what a voiceover object is without opening it."""

    WAV = "wav"

    @property
    def content_type(self) -> str:
        return _AUDIO_CONTENT_TYPES[self]

    @property
    def extension(self) -> str:
        return f".{self.value}"


_AUDIO_CONTENT_TYPES: Final[Mapping[AudioFormat, str]] = {AudioFormat.WAV: "audio/wav"}


# --- the voice ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    """One named, versioned voice (PRD §17.6's pattern applied to speech).

    A closed registry rather than caller-supplied parameters, for the same reason overlay styles
    are a closed registry: an arbitrary speaking rate or an arbitrary voice id is an arbitrary
    bill and an arbitrary brand voice, and neither is a caller's to choose. `as_document` is what
    gets stored next to the audio, so the profile that produced a file is recoverable even if
    this table is edited later.
    """

    code: str
    version: int
    language: str
    voice: str
    style: str
    # Relative to the voice's natural pace: 1.0 is unmodified. Bounded by the registry rather
    # than by validation, because there is no path for a caller to supply one.
    speaking_rate: float

    def as_document(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "version": self.version,
            "language": self.language,
            "voice": self.voice,
            "style": self.style,
            "speaking_rate": self.speaking_rate,
        }


VOICE_PROFILES: Final[Mapping[str, VoiceProfile]] = {
    "tr-warm-v1": VoiceProfile(
        code="tr-warm-v1",
        version=1,
        language="tr",
        voice="tr-female-warm",
        style="conversational",
        speaking_rate=1.0,
    ),
    "tr-neutral-v1": VoiceProfile(
        code="tr-neutral-v1",
        version=1,
        language="tr",
        voice="tr-male-neutral",
        style="informative",
        speaking_rate=1.0,
    ),
}


def resolve_voice_profile(code: str) -> VoiceProfile | None:
    return VOICE_PROFILES.get(code)


# --- what gets spoken -----------------------------------------------------------------------


class VoiceoverSourceError(ValueError):
    """The script cannot be voiced. Carries a documented code, never the rejected text."""

    def __init__(self, code: str, pointer: str) -> None:
        super().__init__(f"{code} at {pointer}")
        self.code = code
        self.pointer = pointer


SOURCE_DOCUMENT_MISSING: Final = "VOICEOVER_SCRIPT_DOCUMENT_MISSING"
SOURCE_SEGMENTS_INVALID: Final = "VOICEOVER_SCRIPT_SEGMENTS_INVALID"
SOURCE_TEXT_INVALID: Final = "VOICEOVER_SCRIPT_TEXT_INVALID"
SOURCE_TOO_MANY_LINES: Final = "VOICEOVER_SCRIPT_TOO_MANY_LINES"


@dataclass(frozen=True, slots=True)
class VoiceoverLine:
    """One line to synthesize, lifted from a resolved script segment.

    `text` has already been through §18.1's parser, the fabrication detector and slot
    resolution — it is the string the script *settled on*, not anything a caller typed. It is
    re-bounded here anyway, because this is where it turns into a billable call.
    """

    index: int
    purpose: str
    text: str
    target_duration_ms: int


def script_lines(document: Mapping[str, Any] | None) -> tuple[VoiceoverLine, ...]:
    """Read the lines to voice out of a stored `content_scripts.document`.

    The *resolved* document is the source, not the template: `{{price:…}}` is a reference, and a
    listener must hear the value the record held. The document was produced by `resolve_script`,
    so this is a read of our own output — but it is still parsed defensively, because a row can
    be older than the code reading it.
    """

    if not isinstance(document, Mapping):
        raise VoiceoverSourceError(SOURCE_DOCUMENT_MISSING, "$")
    segments = document.get("segments")
    if not isinstance(segments, list) or not segments:
        raise VoiceoverSourceError(SOURCE_SEGMENTS_INVALID, "$.segments")
    if len(segments) > MAX_VOICEOVER_LINES:
        raise VoiceoverSourceError(SOURCE_TOO_MANY_LINES, "$.segments")

    lines: list[VoiceoverLine] = []
    for index, raw in enumerate(segments):
        pointer = f"$.segments[{index}]"
        if not isinstance(raw, Mapping):
            raise VoiceoverSourceError(SOURCE_SEGMENTS_INVALID, pointer)
        text = raw.get("voice_text")
        purpose = raw.get("purpose")
        target = raw.get("target_duration_ms")
        if not isinstance(text, str) or not isinstance(purpose, str):
            raise VoiceoverSourceError(SOURCE_SEGMENTS_INVALID, pointer)
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            raise VoiceoverSourceError(SOURCE_SEGMENTS_INVALID, f"{pointer}.target_duration_ms")
        cleaned = text.strip()
        if not MIN_VOICEOVER_TEXT_CHARS <= len(
            cleaned
        ) <= MAX_VOICEOVER_TEXT_CHARS or _CONTROL_CHARACTERS.search(cleaned):
            raise VoiceoverSourceError(SOURCE_TEXT_INVALID, f"{pointer}.voice_text")
        lines.append(
            VoiceoverLine(index=index, purpose=purpose, text=cleaned, target_duration_ms=target)
        )
    return tuple(lines)


# --- what came back -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VoiceoverSegment:
    """One stored audio object and the two durations that describe it.

    `duration_ms` is the probe's answer and the only one anything downstream may use.
    `declared_duration_ms` is what the provider said, kept so a disagreement is a fact in the
    record rather than something that was silently discarded. `drift_ms` is measured minus the
    script's target: slice 2D decides what an unacceptable drift is, this slice only measures it.
    """

    index: int
    purpose: str
    object_key: str
    content_type: str
    byte_size: int
    sha256_checksum: str
    duration_ms: int
    declared_duration_ms: int | None
    target_duration_ms: int

    @property
    def drift_ms(self) -> int:
        return self.duration_ms - self.target_duration_ms

    def as_document(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "purpose": self.purpose,
            "object_key": self.object_key,
            "content_type": self.content_type,
            "byte_size": self.byte_size,
            "sha256_checksum": self.sha256_checksum,
            "duration_ms": self.duration_ms,
            "declared_duration_ms": self.declared_duration_ms,
            "target_duration_ms": self.target_duration_ms,
            "drift_ms": self.drift_ms,
        }


def serialize_segments(segments: Sequence[VoiceoverSegment]) -> list[dict[str, Any]]:
    return [segment.as_document() for segment in segments]


def total_duration_ms(segments: Sequence[VoiceoverSegment]) -> int:
    """Voiceover length as the sum of measured lines.

    Lines are spoken one after another; there is no crossfade and no gap model in this slice, so
    the sum is the honest total. Slice 2E, which places lines against cuts, is what will make
    inter-line silence a thing worth modelling.
    """

    return sum(segment.duration_ms for segment in segments)


def total_drift_ms(segments: Sequence[VoiceoverSegment]) -> int:
    return sum(segment.drift_ms for segment in segments)


def segment_object_key(
    business_id: object, voiceover_id: object, index: int, *, suffix: str
) -> str:
    """Where one line's audio lives. Deterministic, tenant-prefixed, never a URL."""

    return f"tenant/{business_id}/voiceovers/{voiceover_id}/segment-{index:03d}{suffix}"


# --- the capability port --------------------------------------------------------------------


class TTSTransientError(RuntimeError):
    """The provider failed for a reason that may not recur."""


class TTSPermanentError(RuntimeError):
    """The provider failed for a reason retrying cannot fix."""


class TTSDisabledError(RuntimeError):
    """No adapter may produce real speech in this environment.

    Raised on call rather than at startup, following the rule W13 settled and PM generalized:
    a capability whose output a human could approve and publish falls back to a `disabled`
    adapter with a documented error, while infrastructure adapters (storage, identity,
    materializer, render) keep being refused in `Settings` validation. Synthesized speech is
    squarely in the first class — a fixture voice reading real marketing copy is publishable —
    and taking a whole deployment down over one capability is the wrong trade.
    """


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    """Provider-neutral input, shaped after PRD §17.3's `TTSProvider.synthesize`.

    §17.3 writes `(text, voice_profile, output_format)`. Two additions are ours: the caller names
    the `destination` file, so an adapter never picks a path inside the worker's scratch budget,
    and `max_output_bytes` bounds what a hostile or broken provider may write to that path.
    """

    text: str
    voice_profile: VoiceProfile
    output_format: AudioFormat
    destination: Path
    max_output_bytes: int


@dataclass(frozen=True, slots=True)
class AudioResult:
    """What one synthesis call produced, described by the adapter.

    Every field here is the *adapter's* account of the file, including `declared_duration_ms`.
    None of it is trusted for duration: the service probes the file. Byte size and checksum are
    re-observed by storage on upload and compared, so a mismatch is a rejection rather than a
    record that agrees with itself and with nothing else.
    """

    provider: str
    model: str
    path: Path
    content_type: str
    byte_size: int
    sha256_checksum: str
    declared_duration_ms: int | None
    actual_cost_minor: int
    currency: str


class TTSPort(Protocol):
    """PRD §17.1's `tts` capability, behind ADR-004's adapter boundary."""

    @property
    def descriptor(self) -> ProviderDescriptor: ...

    async def synthesize(
        self, *, request: SynthesisRequest, timeout_seconds: int
    ) -> AudioResult: ...


# --- measurement ----------------------------------------------------------------------------


class AudioProbeTransientError(RuntimeError):
    """The probe could not run; the file may still be fine."""


class AudioProbePermanentError(RuntimeError):
    """The file is not audio this pipeline can measure."""


@dataclass(frozen=True, slots=True)
class MeasuredAudio:
    """What the file itself says, independent of whoever produced it."""

    duration_ms: int
    sample_rate_hz: int
    channels: int
    codec: str


class AudioProbePort(Protocol):
    """Measure a local audio file. Separate from `TTSPort` on purpose: a provider measuring its
    own output is the provider's claim again, wearing a different name."""

    async def measure(self, *, path: Path, timeout_seconds: int) -> MeasuredAudio: ...
