"""Deterministic non-production adapters for video-understanding tests."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from app.core.config import Settings
from app.modules.media.video_understanding import (
    FrameExtractionPermanentError,
    FrameExtractionPort,
    FrameReference,
    VideoUnderstandingPermanentError,
    VideoUnderstandingPort,
    VideoUnderstandingRequest,
    VideoUnderstandingResult,
    VideoUnderstandingTransientError,
)


class FakeFrameExtractionAdapter(FrameExtractionPort):
    """Fixture-only adapter that never opens media or invokes FFmpeg."""

    def __init__(self, settings: Settings, frames: tuple[FrameReference, ...] = ()) -> None:
        _reject_production(settings)
        self._frames = frames

    async def extract(
        self,
        *,
        request: VideoUnderstandingRequest,
        source_path: Path,
        workdir: Path,
        timeout_seconds: int,
        maximum_frames: int,
    ) -> tuple[FrameReference, ...]:
        del source_path, workdir
        if timeout_seconds < 1 or maximum_frames < 1:
            raise FrameExtractionPermanentError("FRAME_EXTRACTION_TIMEOUT_INVALID")
        return (self._frames or request.frames)[:maximum_frames]


class FakeVideoUnderstandingAdapter(VideoUnderstandingPort):
    """Fixture-only provider fake for success and explicit error paths."""

    def __init__(
        self,
        settings: Settings,
        mode: Literal["success", "transient", "permanent", "invalid"] = "success",
    ) -> None:
        _reject_production(settings)
        self._mode = mode

    async def understand(
        self, *, request: VideoUnderstandingRequest, timeout_seconds: int
    ) -> VideoUnderstandingResult:
        if timeout_seconds < 1:
            raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_TIMEOUT_INVALID")
        if self._mode == "transient":
            raise VideoUnderstandingTransientError("VLM_UNAVAILABLE")
        if self._mode == "permanent":
            raise VideoUnderstandingPermanentError("VLM_REJECTED")
        if self._mode == "invalid":
            return VideoUnderstandingResult(
                provider="fake-vlm",
                model_name="deterministic",
                summary="\x00",
                visual_description="invalid output",
                confidence=2.0,
            )
        return VideoUnderstandingResult(
            provider="fake-vlm",
            model_name="deterministic",
            summary="Scene analyzed",
            visual_description="Deterministic visual scene",
            confidence=0.9,
            labels=("scene",),
            quality_signals={"frame_count": len(request.frames)},
        )


def _reject_production(settings: Settings) -> None:
    if settings.app_env == "production":
        raise RuntimeError("fake video-understanding adapters are not allowed in production")
