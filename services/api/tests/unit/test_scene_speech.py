"""Contract coverage for scene/timecode normalization and audio extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.modules.media.scene_speech import (
    FFmpegAudioExtractionAdapter,
    SceneCandidate,
    SceneSpeechPermanentError,
    SpeechResult,
    TranscriptCandidate,
    normalize_scenes,
    normalize_transcript,
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
