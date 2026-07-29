"""Provider-neutral video-understanding contracts and pure safety rules.

This module intentionally contains no persistence, job, or FFmpeg orchestration;
`video_understanding_service` owns those responsibilities.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from app.core.config import Settings
from app.modules.media.scene_speech import TranscriptCandidate, normalize_safe_text


class VideoUnderstandingPermanentError(RuntimeError):
    """Raised for output that cannot safely enter the domain model."""


class VideoUnderstandingTransientError(RuntimeError):
    """Raised for a retryable provider outage."""


class FrameExtractionPermanentError(RuntimeError):
    """Raised when an extracted frame is unsafe or cannot be validated."""


class FrameExtractionTransientError(RuntimeError):
    """Raised when bounded frame extraction can safely be retried."""


type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]

SERVICE_AUTHORITATIVE_QUALITY_SIGNALS: frozenset[str] = frozenset(
    {
        "visual_input_available",
        "analysis_mode",
        "coverage",
        "total_scene_count",
        "analyzed_scene_count",
        "skipped_scene_count",
        "frame_backed_scene_count",
        "transcript_only_scene_count",
        "no_context_scene_count",
    }
)
"""Quality-signal keys only this service may assert; provider copies are discarded."""


class SceneAnalysisMode(StrEnum):
    """Service-decided analysis mode; a provider can neither select nor report it."""

    VISUAL = "visual"
    VISUAL_AND_TRANSCRIPT = "visual_and_transcript"
    TRANSCRIPT_ONLY = "transcript_only"
    NO_CONTEXT = "no_context"

    @classmethod
    def decide(cls, *, has_frames: bool, has_transcript_context: bool) -> SceneAnalysisMode:
        if has_frames:
            return cls.VISUAL_AND_TRANSCRIPT if has_transcript_context else cls.VISUAL
        return cls.TRANSCRIPT_ONLY if has_transcript_context else cls.NO_CONTEXT

    @property
    def visual_input_available(self) -> bool:
        return self in {SceneAnalysisMode.VISUAL, SceneAnalysisMode.VISUAL_AND_TRANSCRIPT}


@dataclass(frozen=True)
class SceneCoverageReport:
    """Server-calculated completion coverage; carries counts only, never analysis text."""

    total_scene_count: int
    analyzed_scene_count: int
    skipped_scene_count: int
    coverage: Literal["full", "partial"]
    frame_backed_scene_count: int
    transcript_only_scene_count: int
    no_context_scene_count: int

    def as_event_payload(self) -> dict[str, JsonValue]:
        return {
            "total_scene_count": self.total_scene_count,
            "analyzed_scene_count": self.analyzed_scene_count,
            "skipped_scene_count": self.skipped_scene_count,
            "coverage": self.coverage,
            "frame_backed_scene_count": self.frame_backed_scene_count,
            "transcript_only_scene_count": self.transcript_only_scene_count,
            "no_context_scene_count": self.no_context_scene_count,
        }


def build_scene_coverage_report(
    *, total_scene_count: int, modes: Sequence[SceneAnalysisMode]
) -> SceneCoverageReport:
    """Derive coverage from service-decided modes so provider output cannot influence it."""

    analyzed_scene_count = len(modes)
    if analyzed_scene_count < 1 or total_scene_count < analyzed_scene_count:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_COVERAGE_INVALID")
    return SceneCoverageReport(
        total_scene_count=total_scene_count,
        analyzed_scene_count=analyzed_scene_count,
        skipped_scene_count=total_scene_count - analyzed_scene_count,
        coverage="full" if analyzed_scene_count == total_scene_count else "partial",
        frame_backed_scene_count=sum(mode.visual_input_available for mode in modes),
        transcript_only_scene_count=sum(
            mode is SceneAnalysisMode.TRANSCRIPT_ONLY for mode in modes
        ),
        no_context_scene_count=sum(mode is SceneAnalysisMode.NO_CONTEXT for mode in modes),
    )


@dataclass(frozen=True)
class FrameReference:
    """A controlled local frame reference supplied by a future extractor."""

    scene_id: UUID
    timestamp_ms: int
    local_path: Path
    width: int
    height: int
    byte_size: int
    content_type: str


@dataclass(frozen=True)
class VideoUnderstandingRequest:
    """Provider-neutral input; frames may be empty for transcript-only degradation."""

    asset_id: UUID
    scene_id: UUID
    scene_start_ms: int
    scene_end_ms: int
    transcript_context: str
    frames: tuple[FrameReference, ...]


@dataclass(frozen=True)
class VideoUnderstandingResult:
    """Normalized provider result that can later be persisted safely."""

    provider: str
    model_name: str
    summary: str
    visual_description: str
    confidence: float
    labels: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    visible_text: tuple[str, ...] = ()
    dominant_topics: tuple[str, ...] = ()
    safety_flags: tuple[str, ...] = ()
    quality_signals: dict[str, JsonValue] | None = None


class FrameExtractionPort(Protocol):
    """Future frame extraction boundary, kept separate from AI providers."""

    async def extract(
        self,
        *,
        request: VideoUnderstandingRequest,
        source_path: Path,
        workdir: Path,
        timeout_seconds: int,
        maximum_frames: int,
    ) -> tuple[FrameReference, ...]: ...


class VideoUnderstandingPort(Protocol):
    """External provider boundary; adapters must accept an empty frame tuple safely."""

    async def understand(
        self, *, request: VideoUnderstandingRequest, timeout_seconds: int
    ) -> VideoUnderstandingResult: ...


def build_transcript_context(
    *,
    scene_start_ms: int,
    scene_end_ms: int,
    segments: tuple[TranscriptCandidate, ...] | None,
    maximum_chars: int,
) -> str:
    """Return complete overlapping transcript segments in deterministic order.

    Boundary-only contact is not an overlap.  The limit is applied before a
    segment is appended, so a selected transcript segment is never truncated.
    """

    if maximum_chars < 1:
        raise ValueError("maximum_chars must be positive")
    if scene_start_ms < 0 or scene_end_ms <= scene_start_ms or not segments:
        return ""

    selected = sorted(
        (
            segment
            for segment in segments
            if segment.start_ms < scene_end_ms and segment.end_ms > scene_start_ms
        ),
        key=lambda segment: (segment.start_ms, segment.end_ms),
    )
    output: list[str] = []
    used = 0
    for segment in selected:
        separator_length = 1 if output else 0
        next_length = used + separator_length + len(segment.text)
        if next_length > maximum_chars:
            break
        output.append(segment.text)
        used = next_length
    return " ".join(output)


def normalize_provider_output(
    payload: Mapping[str, object], settings: Settings
) -> VideoUnderstandingResult:
    """Discard unknown provider fields before creating a domain DTO."""

    required = ("provider", "model_name", "summary", "visual_description", "confidence")
    try:
        values = {name: payload[name] for name in required}
    except KeyError as error:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID") from error

    try:
        result = VideoUnderstandingResult(
            provider=_required_string(values["provider"]),
            model_name=_required_string(values["model_name"]),
            summary=_required_string(values["summary"]),
            visual_description=_required_string(values["visual_description"]),
            confidence=_required_confidence(values["confidence"]),
            labels=_string_tuple(payload.get("labels", ())),
            objects=_string_tuple(payload.get("objects", ())),
            actions=_string_tuple(payload.get("actions", ())),
            visible_text=_string_tuple(payload.get("visible_text", ())),
            dominant_topics=_string_tuple(payload.get("dominant_topics", ())),
            safety_flags=_string_tuple(payload.get("safety_flags", ())),
            quality_signals=_quality_signals(payload.get("quality_signals", {})),
        )
    except (TypeError, ValueError) as error:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID") from error
    return normalize_result(result, settings)


def normalize_result(
    result: VideoUnderstandingResult, settings: Settings
) -> VideoUnderstandingResult:
    """Validate a provider DTO without exposing provider text in errors or logs."""

    if not settings.video_understanding_min_confidence <= result.confidence <= 1.0:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID")

    provider = _normalized_text(result.provider, maximum_chars=64)
    model_name = _normalized_text(result.model_name, maximum_chars=128)
    summary = _normalized_text(result.summary, settings.video_understanding_max_summary_chars)
    visual_description = _normalized_text(
        result.visual_description,
        settings.video_understanding_max_visual_description_chars,
    )
    labels = _normalize_string_list(result.labels, settings.video_understanding_max_labels, 500)
    objects = _normalize_string_list(result.objects, settings.video_understanding_max_objects, 500)
    actions = _normalize_string_list(result.actions, settings.video_understanding_max_actions, 500)
    visible_text = _normalize_string_list(
        result.visible_text,
        settings.video_understanding_max_visible_text_items,
        settings.video_understanding_max_visible_text_item_chars,
    )
    dominant_topics = _normalize_string_list(
        result.dominant_topics,
        settings.video_understanding_max_labels,
        500,
    )
    safety_flags = _normalize_string_list(
        result.safety_flags,
        settings.video_understanding_max_labels,
        500,
    )
    quality_signals = _normalize_quality_signals(result.quality_signals or {}, settings)

    structured_values: dict[str, JsonValue] = {
        "labels": list(labels),
        "objects": list(objects),
        "actions": list(actions),
        "visible_text": list(visible_text),
        "dominant_topics": list(dominant_topics),
        "safety_flags": list(safety_flags),
        "quality_signals": quality_signals,
    }
    _validate_json_limits(structured_values, settings)
    return VideoUnderstandingResult(
        provider=provider,
        model_name=model_name,
        summary=summary,
        visual_description=visual_description,
        confidence=result.confidence,
        labels=labels,
        objects=objects,
        actions=actions,
        visible_text=visible_text,
        dominant_topics=dominant_topics,
        safety_flags=safety_flags,
        quality_signals=quality_signals,
    )


def apply_service_analysis_signals(
    result: VideoUnderstandingResult, *, mode: SceneAnalysisMode, settings: Settings
) -> VideoUnderstandingResult:
    """Stamp the authoritative mode and cap confidence when no visual input was used.

    Call this only on a `normalize_result` output, which has already discarded any
    provider-supplied copy of these keys.
    """

    signals: dict[str, JsonValue] = dict(result.quality_signals or {})
    signals["visual_input_available"] = mode.visual_input_available
    signals["analysis_mode"] = mode.value
    confidence = result.confidence
    if not mode.visual_input_available:
        confidence = min(confidence, settings.video_understanding_nonvisual_max_confidence)
    return replace(result, confidence=confidence, quality_signals=signals)


def _required_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("provider field is not a string")
    return value


def _required_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, float | int):
        raise TypeError("confidence is not numeric")
    return float(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple | list) or not all(isinstance(item, str) for item in value):
        raise TypeError("provider list is invalid")
    return tuple(value)


def _quality_signals(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError("quality_signals is invalid")
    return value


def _normalized_text(value: str, maximum_chars: int) -> str:
    try:
        normalized = normalize_safe_text(value)
    except RuntimeError as error:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID") from error
    if len(normalized) > maximum_chars:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID")
    return normalized


def _normalize_string_list(
    values: tuple[str, ...], maximum_items: int, maximum_chars: int
) -> tuple[str, ...]:
    if len(values) > maximum_items:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID")
    return tuple(_normalized_text(value, maximum_chars) for value in values)


def _normalize_quality_signals(
    value: dict[str, JsonValue], settings: Settings
) -> dict[str, JsonValue]:
    """Discard service-authoritative keys so a provider cannot assert coverage or visual input.

    Filtering happens after key normalization, so a provider cannot smuggle a reserved
    key past the check by encoding it differently.
    """

    normalized = _normalize_json_value(value)
    if not isinstance(normalized, dict):  # Defensive guard for the recursive normalizer.
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID")
    retained = {
        key: item
        for key, item in normalized.items()
        if key not in SERVICE_AUTHORITATIVE_QUALITY_SIGNALS
    }
    _validate_json_limits(retained, settings)
    return retained


def _normalize_json_value(value: JsonValue) -> JsonValue:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID")
        return value
    if isinstance(value, str):
        return _normalized_text(value, maximum_chars=20_000)
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            normalized_key = _normalized_text(key, maximum_chars=500)
            if normalized_key in normalized:
                raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID")
            normalized[normalized_key] = _normalize_json_value(item)
        return normalized
    raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID")


def _validate_json_limits(value: object, settings: Settings) -> None:
    if _json_depth(value) > settings.video_understanding_max_json_depth:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID")
    try:
        serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID") from error
    if len(serialized) > settings.video_understanding_max_json_bytes:
        raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_INVALID")


def _json_depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 0
