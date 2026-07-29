"""Unit coverage for the Phase 1D-A1 video-understanding contracts."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint

from app.core.config import Settings
from app.infrastructure.media.fake_video_understanding import (
    FakeFrameExtractionAdapter,
    FakeVideoUnderstandingAdapter,
)
from app.modules.media.models import MediaSceneUnderstanding, SceneUnderstandingStatus
from app.modules.media.scene_speech import TranscriptCandidate
from app.modules.media.video_understanding import (
    SERVICE_AUTHORITATIVE_QUALITY_SIGNALS,
    FrameExtractionPermanentError,
    FrameReference,
    SceneAnalysisMode,
    VideoUnderstandingPermanentError,
    VideoUnderstandingRequest,
    VideoUnderstandingResult,
    VideoUnderstandingTransientError,
    apply_service_analysis_signals,
    build_scene_coverage_report,
    build_transcript_context,
    normalize_provider_output,
    normalize_result,
)


def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
    )


def request() -> VideoUnderstandingRequest:
    return VideoUnderstandingRequest(
        asset_id=uuid4(),
        scene_id=uuid4(),
        scene_start_ms=100,
        scene_end_ms=200,
        transcript_context="",
        frames=(),
    )


def valid_result(**changes: object) -> VideoUnderstandingResult:
    values: dict[str, object] = {
        "provider": "fake-vlm",
        "model_name": "deterministic",
        "summary": "Scene analyzed",
        "visual_description": "A deterministic visual scene",
        "confidence": 0.9,
        "labels": ("scene",),
        "quality_signals": {"frame_count": 1},
    }
    values.update(changes)
    return VideoUnderstandingResult(**values)  # type: ignore[arg-type]


def test_model_has_tenant_constraint_indexes_and_statuses() -> None:
    table = MediaSceneUnderstanding.metadata.tables["media_scene_understandings"]
    assert {status.value for status in SceneUnderstandingStatus} == {
        "pending",
        "completed",
        "failed",
        "dead",
    }
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns) == ("business_id", "scene_id")
        for constraint in table.constraints
    )
    assert {index.name for index in table.indexes} >= {
        "ix_scene_understandings_business_asset",
        "ix_scene_understandings_asset",
    }


def test_transcript_context_uses_strict_overlap_in_time_order() -> None:
    segments = (
        TranscriptCandidate(200, 300, "right boundary", 0.9),
        TranscriptCandidate(120, 180, "inside", 0.9),
        TranscriptCandidate(50, 110, "partial", 0.9),
        TranscriptCandidate(0, 100, "left boundary", 0.9),
        TranscriptCandidate(300, 400, "outside", 0.9),
    )
    assert (
        build_transcript_context(
            scene_start_ms=100,
            scene_end_ms=200,
            segments=segments,
            maximum_chars=100,
        )
        == "partial inside"
    )


def test_transcript_context_handles_no_speech_absence_and_full_segment_limit() -> None:
    segments = (
        TranscriptCandidate(100, 150, "first", 0.9),
        TranscriptCandidate(150, 190, "second", 0.9),
    )
    assert (
        build_transcript_context(
            scene_start_ms=100,
            scene_end_ms=200,
            segments=segments,
            maximum_chars=7,
        )
        == "first"
    )
    assert (
        build_transcript_context(
            scene_start_ms=100,
            scene_end_ms=200,
            segments=(),
            maximum_chars=10,
        )
        == ""
    )
    assert (
        build_transcript_context(
            scene_start_ms=100,
            scene_end_ms=200,
            segments=None,
            maximum_chars=10,
        )
        == ""
    )


@pytest.mark.asyncio
async def test_fake_adapters_are_deterministic_and_do_not_process_media() -> None:
    resolved_request = request()
    frame = FrameReference(
        scene_id=resolved_request.scene_id,
        timestamp_ms=150,
        local_path=Path("/controlled-test-frame.jpg"),
        width=64,
        height=64,
        byte_size=128,
        content_type="image/jpeg",
    )
    assert await FakeFrameExtractionAdapter(settings(), (frame,)).extract(
        request=resolved_request,
        source_path=Path("/tmp/proxy.mp4"),
        workdir=Path("/tmp"),
        timeout_seconds=1,
        maximum_frames=1,
    ) == (frame,)
    provider = FakeVideoUnderstandingAdapter(settings())
    assert await provider.understand(
        request=resolved_request, timeout_seconds=1
    ) == await provider.understand(request=resolved_request, timeout_seconds=1)


@pytest.mark.asyncio
async def test_fake_provider_exposes_transient_permanent_and_invalid_cases() -> None:
    resolved_request = request()
    with pytest.raises(VideoUnderstandingTransientError, match="VLM_UNAVAILABLE"):
        await FakeVideoUnderstandingAdapter(settings(), "transient").understand(
            request=resolved_request, timeout_seconds=1
        )
    with pytest.raises(VideoUnderstandingPermanentError, match="VLM_REJECTED"):
        await FakeVideoUnderstandingAdapter(settings(), "permanent").understand(
            request=resolved_request, timeout_seconds=1
        )
    invalid = await FakeVideoUnderstandingAdapter(settings(), "invalid").understand(
        request=resolved_request, timeout_seconds=1
    )
    with pytest.raises(VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_INVALID"):
        normalize_result(invalid, settings())


@pytest.mark.asyncio
async def test_fake_adapters_require_positive_step_timeouts() -> None:
    resolved_request = request()
    with pytest.raises(FrameExtractionPermanentError, match="FRAME_EXTRACTION_TIMEOUT_INVALID"):
        await FakeFrameExtractionAdapter(settings()).extract(
            request=resolved_request,
            source_path=Path("/tmp/proxy.mp4"),
            workdir=Path("/tmp"),
            timeout_seconds=0,
            maximum_frames=1,
        )
    with pytest.raises(
        VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_TIMEOUT_INVALID"
    ):
        await FakeVideoUnderstandingAdapter(settings()).understand(
            request=resolved_request, timeout_seconds=0
        )


def test_fake_adapters_are_rejected_in_production() -> None:
    production = settings().model_copy(update={"app_env": "production"})
    with pytest.raises(RuntimeError, match="not allowed in production"):
        FakeFrameExtractionAdapter(production)
    with pytest.raises(RuntimeError, match="not allowed in production"):
        FakeVideoUnderstandingAdapter(production)


def test_job_timeout_covers_combined_frame_and_provider_steps() -> None:
    with pytest.raises(ValidationError, match="VIDEO_UNDERSTANDING_JOB_PER_SCENE_TIMEOUT_SECONDS"):
        Settings(
            database_url="postgresql+asyncpg://test:test@localhost:5432/test",
            redis_url="redis://localhost:6379/0",
            celery_broker_url="redis://localhost:6379/1",
            celery_result_backend="redis://localhost:6379/2",
            frame_extraction_timeout_seconds=30,
            video_understanding_timeout_seconds=60,
            video_understanding_job_per_scene_timeout_seconds=89,
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_normalization_rejects_confidence_outside_the_closed_range(confidence: float) -> None:
    with pytest.raises(VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_INVALID"):
        normalize_result(valid_result(confidence=confidence), settings())


@pytest.mark.parametrize(
    ("field", "settings_field"),
    [
        ("labels", "video_understanding_max_labels"),
        ("objects", "video_understanding_max_objects"),
        ("actions", "video_understanding_max_actions"),
    ],
)
def test_normalization_rejects_oversized_classification_lists(
    field: str, settings_field: str
) -> None:
    resolved_settings = settings().model_copy(update={settings_field: 1})
    with pytest.raises(VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_INVALID"):
        normalize_result(valid_result(**{field: ("one", "two")}), resolved_settings)


def test_normalization_rejects_too_many_or_too_long_visible_text_items() -> None:
    with pytest.raises(VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_INVALID"):
        normalize_result(
            valid_result(visible_text=("one", "two")),
            settings().model_copy(update={"video_understanding_max_visible_text_items": 1}),
        )
    with pytest.raises(VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_INVALID"):
        normalize_result(
            valid_result(visible_text=("too long",)),
            settings().model_copy(update={"video_understanding_max_visible_text_item_chars": 3}),
        )


def test_normalization_rejects_excessive_json_depth_and_size() -> None:
    nested: dict[str, object] = {"a": {"b": {"c": {"d": {"e": "value"}}}}}
    with pytest.raises(VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_INVALID"):
        normalize_result(
            valid_result(quality_signals=nested),
            settings().model_copy(update={"video_understanding_max_json_depth": 5}),
        )
    with pytest.raises(VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_INVALID"):
        normalize_result(
            valid_result(quality_signals={"large": "x" * 300}),
            settings().model_copy(update={"video_understanding_max_json_bytes": 256}),
        )


@pytest.mark.parametrize("field", ["summary", "visual_description"])
@pytest.mark.parametrize("text", ["unsafe\x00text", "unsafe\x01text", "   "])
def test_normalization_rejects_unsafe_or_empty_required_text(field: str, text: str) -> None:
    with pytest.raises(VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_INVALID"):
        normalize_result(valid_result(**{field: text}), settings())


def test_normalization_discards_unknown_fields_and_normalizes_valid_output() -> None:
    result = normalize_provider_output(
        {
            "provider": " fake-vlm ",
            "model_name": " deterministic ",
            "summary": "  clear\r\nsummary ",
            "visual_description": "  visual description ",
            "confidence": 1,
            "labels": [" label "],
            "visible_text": [" sign text "],
            "quality_signals": {"frames": 2, "note": "  normalized\r\nvalue  "},
            "raw_provider_response": {"secret": "must not enter the DTO"},
        },
        settings(),
    )
    assert result.provider == "fake-vlm"
    assert result.summary == "clear\nsummary"
    assert result.labels == ("label",)
    assert result.visible_text == ("sign text",)
    assert result.quality_signals == {"frames": 2, "note": "normalized\nvalue"}
    assert not hasattr(result, "raw_provider_response")


@pytest.mark.parametrize("key", sorted(SERVICE_AUTHORITATIVE_QUALITY_SIGNALS))
def test_provider_cannot_assert_service_authoritative_quality_signals(key: str) -> None:
    """A provider claiming coverage or visual input must not reach the domain model."""

    result = normalize_result(
        valid_result(quality_signals={key: "provider claim", "frame_count": 1}), settings()
    )
    assert result.quality_signals == {"frame_count": 1}


def test_reserved_keys_are_discarded_even_when_encoded_differently() -> None:
    result = normalize_result(
        valid_result(quality_signals={" analysis_mode ": "visual", "note": "kept"}), settings()
    )
    assert result.quality_signals == {"note": "kept"}


@pytest.mark.parametrize(
    ("has_frames", "has_context", "expected"),
    [
        (True, True, SceneAnalysisMode.VISUAL_AND_TRANSCRIPT),
        (True, False, SceneAnalysisMode.VISUAL),
        (False, True, SceneAnalysisMode.TRANSCRIPT_ONLY),
        (False, False, SceneAnalysisMode.NO_CONTEXT),
    ],
)
def test_analysis_mode_is_decided_from_service_inputs(
    has_frames: bool, has_context: bool, expected: SceneAnalysisMode
) -> None:
    mode = SceneAnalysisMode.decide(has_frames=has_frames, has_transcript_context=has_context)
    assert mode is expected
    assert mode.visual_input_available is has_frames


@pytest.mark.parametrize(
    ("mode", "expected_confidence"),
    [
        (SceneAnalysisMode.VISUAL_AND_TRANSCRIPT, 0.9),
        (SceneAnalysisMode.VISUAL, 0.9),
        (SceneAnalysisMode.TRANSCRIPT_ONLY, 0.5),
        (SceneAnalysisMode.NO_CONTEXT, 0.5),
    ],
)
def test_service_signals_stamp_mode_and_cap_non_visual_confidence(
    mode: SceneAnalysisMode, expected_confidence: float
) -> None:
    resolved = settings()
    stamped = apply_service_analysis_signals(
        normalize_result(valid_result(quality_signals={"frame_count": 3}), resolved),
        mode=mode,
        settings=resolved,
    )
    assert stamped.confidence == expected_confidence
    assert stamped.quality_signals == {
        "frame_count": 3,
        "visual_input_available": mode.visual_input_available,
        "analysis_mode": mode.value,
    }


def test_coverage_report_is_full_only_when_every_scene_was_analyzed() -> None:
    modes = [SceneAnalysisMode.VISUAL, SceneAnalysisMode.TRANSCRIPT_ONLY]
    full = build_scene_coverage_report(total_scene_count=2, modes=modes)
    assert full.as_event_payload() == {
        "total_scene_count": 2,
        "analyzed_scene_count": 2,
        "skipped_scene_count": 0,
        "coverage": "full",
        "frame_backed_scene_count": 1,
        "transcript_only_scene_count": 1,
        "no_context_scene_count": 0,
    }
    partial = build_scene_coverage_report(
        total_scene_count=5,
        modes=[*modes, SceneAnalysisMode.NO_CONTEXT, SceneAnalysisMode.VISUAL_AND_TRANSCRIPT],
    )
    assert partial.as_event_payload() == {
        "total_scene_count": 5,
        "analyzed_scene_count": 4,
        "skipped_scene_count": 1,
        "coverage": "partial",
        "frame_backed_scene_count": 2,
        "transcript_only_scene_count": 1,
        "no_context_scene_count": 1,
    }


def test_coverage_payload_carries_only_integer_counts_and_a_coverage_label() -> None:
    payload = build_scene_coverage_report(
        total_scene_count=1, modes=[SceneAnalysisMode.NO_CONTEXT]
    ).as_event_payload()
    assert payload.pop("coverage") in {"full", "partial"}
    assert all(isinstance(value, int) for value in payload.values())


@pytest.mark.parametrize(
    ("total", "modes"), [(0, []), (1, []), (1, [SceneAnalysisMode.VISUAL] * 2)]
)
def test_coverage_report_rejects_impossible_scene_counts(
    total: int, modes: list[SceneAnalysisMode]
) -> None:
    with pytest.raises(
        VideoUnderstandingPermanentError, match="VIDEO_UNDERSTANDING_COVERAGE_INVALID"
    ):
        build_scene_coverage_report(total_scene_count=total, modes=modes)
