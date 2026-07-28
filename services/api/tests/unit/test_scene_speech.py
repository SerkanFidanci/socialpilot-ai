"""Contract coverage for scene/timecode normalization and audio extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.infrastructure.media.fake_scene_speech import FakeSceneDetector
from app.modules.media.scene_speech import (
    FFmpegAudioExtractionAdapter,
    SceneCandidate,
    SceneSpeechPermanentError,
    SceneSpeechTransientError,
    SpeechResult,
    TranscriptCandidate,
    normalize_scenes,
    normalize_transcript,
    transcript_full_text,
)


def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
    )


def test_scene_timecodes_are_validated_and_empty_results_use_whole_video() -> None:
    resolved = settings()
    assert normalize_scenes(settings=resolved, duration_ms=1_000, scenes=()) == (
        SceneCandidate(0, 1_000, 1.0),
    )
    with pytest.raises(SceneSpeechPermanentError, match="SCENE_TIMECODE_INVALID"):
        normalize_scenes(
            settings=resolved,
            duration_ms=1_000,
            scenes=(SceneCandidate(600, 500, 0.9),),
        )
    with pytest.raises(SceneSpeechPermanentError, match="SCENE_TIMECODE_INVALID"):
        normalize_scenes(
            settings=resolved,
            duration_ms=1_000,
            scenes=(SceneCandidate(0, 600, 0.9), SceneCandidate(500, 1_000, 0.9)),
        )


def test_transcript_segments_must_be_ordered_and_bounded() -> None:
    resolved = settings()
    result = SpeechResult("tr", "fake", (TranscriptCandidate(0, 500, "Merhaba", 0.8),))
    assert (
        normalize_transcript(settings=resolved, duration_ms=1_000, result=result) == result.segments
    )
    with pytest.raises(SceneSpeechPermanentError, match="TRANSCRIPT_TIMECODE_INVALID"):
        normalize_transcript(
            settings=resolved,
            duration_ms=1_000,
            result=SpeechResult("tr", "fake", (TranscriptCandidate(-1, 500, "bad", 0.8),)),
        )


@pytest.mark.parametrize("text", ["unsafe\x00text", "unsafe\x01text", "\x02"])
def test_transcript_text_rejects_postgresql_unsafe_control_characters(text: str) -> None:
    with pytest.raises(SceneSpeechPermanentError, match="TRANSCRIPT_INVALID"):
        normalize_transcript(
            settings=settings(),
            duration_ms=1_000,
            result=SpeechResult("tr", "fake", (TranscriptCandidate(0, 500, text, 0.8),)),
        )


def test_transcript_text_normalizes_line_endings_and_enforces_total_length() -> None:
    resolved = settings().model_copy(
        update={"transcript_max_segment_chars": 10, "transcript_max_total_chars": 12}
    )
    valid = normalize_transcript(
        settings=resolved,
        duration_ms=1_000,
        result=SpeechResult(
            "tr",
            "fake",
            (
                TranscriptCandidate(0, 400, "  one\r\ntwo  ", 0.8),
                TranscriptCandidate(400, 800, "ok", 0.8),
            ),
        ),
    )
    assert transcript_full_text(valid) == "one\ntwo ok"
    with pytest.raises(SceneSpeechPermanentError, match="TRANSCRIPT_INVALID"):
        normalize_transcript(
            settings=resolved,
            duration_ms=1_000,
            result=SpeechResult(
                "tr",
                "fake",
                (
                    TranscriptCandidate(0, 400, "123456", 0.8),
                    TranscriptCandidate(400, 800, "abcdef", 0.8),
                ),
            ),
        )


def test_audio_size_limit_must_cover_configured_pcm_duration() -> None:
    assert (
        Settings(
            database_url="postgresql+asyncpg://test:test@localhost:5432/test",
            redis_url="redis://localhost:6379/0",
            celery_broker_url="redis://localhost:6379/1",
            celery_result_backend="redis://localhost:6379/2",
            media_max_duration_seconds=1,
            media_max_extracted_audio_bytes=32_044,
        ).media_max_extracted_audio_bytes
        == 32_044
    )
    with pytest.raises(ValidationError, match="MEDIA_MAX_EXTRACTED_AUDIO_BYTES"):
        Settings(
            database_url="postgresql+asyncpg://test:test@localhost:5432/test",
            redis_url="redis://localhost:6379/0",
            celery_broker_url="redis://localhost:6379/1",
            celery_result_backend="redis://localhost:6379/2",
            media_max_duration_seconds=1,
            media_max_extracted_audio_bytes=32_043,
        )


@pytest.mark.asyncio
async def test_ffmpeg_audio_extraction_uses_a_real_small_fixture(tmp_path: Path) -> None:
    source = tmp_path / "audio.mp4"
    resolved = settings()
    subprocess.run(
        [
            resolved.ffmpeg_binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x48:r=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=16000",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    audio = await FFmpegAudioExtractionAdapter(resolved).extract(
        input_path=source, output_dir=tmp_path, timeout_seconds=10
    )
    assert audio.path.is_file() and audio.byte_size > 0 and len(audio.sha256_checksum) == 64


@pytest.mark.asyncio
async def test_fake_scene_detector_classifies_timeout_as_transient(tmp_path: Path) -> None:
    detector = FakeSceneDetector()
    assert await detector.detect(
        proxy_path=tmp_path / "proxy.mp4", duration_ms=1_000, timeout_seconds=1
    )
    detector.fail_for_testing()
    with pytest.raises(SceneSpeechTransientError, match="SCENE_DETECTION_UNAVAILABLE"):
        await detector.detect(
            proxy_path=tmp_path / "proxy.mp4", duration_ms=1_000, timeout_seconds=1
        )
