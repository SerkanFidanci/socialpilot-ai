"""Deterministic local adapters for scene and speech worker tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.modules.media.scene_speech import (
    AudioExtractionPort,
    AudioOutput,
    SceneCandidate,
    SceneDetectionPort,
    SceneSpeechTransientError,
    SpeechResult,
    SpeechToTextPort,
    TranscriptCandidate,
)


class FakeSceneDetector(SceneDetectionPort):
    async def detect(self, *, proxy_path: Path, duration_ms: int) -> tuple[SceneCandidate, ...]:
        del proxy_path
        return (SceneCandidate(0, duration_ms, 1.0),)


class FakeAudioExtractor(AudioExtractionPort):
    async def extract(
        self, *, input_path: Path, output_dir: Path, timeout_seconds: int
    ) -> AudioOutput:
        del input_path, timeout_seconds
        path = output_dir / "audio.wav"
        contents = b"fake-audio"
        path.write_bytes(contents)
        return AudioOutput(
            path=path, byte_size=len(contents), sha256_checksum=hashlib.sha256(contents).hexdigest()
        )


class FakeSpeechToText(SpeechToTextPort):
    def __init__(self) -> None:
        self._unavailable = False
        self._result = SpeechResult(
            language="tr",
            provider="fake-asr",
            segments=(TranscriptCandidate(0, 500, "Merhaba", 0.9),),
        )

    async def transcribe(self, *, audio_path: Path, timeout_seconds: int) -> SpeechResult:
        del audio_path, timeout_seconds
        if self._unavailable:
            raise SceneSpeechTransientError("ASR_UNAVAILABLE")
        return self._result

    def set_result_for_testing(self, result: SpeechResult) -> None:
        self._result = result

    def fail_for_testing(self) -> None:
        self._unavailable = True
