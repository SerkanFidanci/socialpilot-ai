"""Composition root owned by each Celery worker process."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.infrastructure.database.session import Database, create_database
from app.infrastructure.media.fake_ingest import (
    FakeContentInspector,
    FakeMalwareScanner,
    FakeMediaMaterializer,
)
from app.infrastructure.media.fake_scene_speech import (
    FakeAudioExtractor,
    FakeSceneDetector,
    FakeSpeechToText,
)
from app.infrastructure.media.fake_video_understanding import (
    FakeFrameExtractionAdapter,
    FakeVideoUnderstandingAdapter,
)
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.modules.media.ingest import MediaIngestService
from app.modules.media.scene_speech import SceneSpeechAnalysisService
from app.modules.media.technical import (
    FFmpegDerivativeAdapter,
    FFprobeAdapter,
    TechnicalAnalysisService,
)
from app.modules.media.video_understanding_service import VideoUnderstandingService
from app.modules.operations.service import JobRecoveryService


@dataclass(frozen=True)
class WorkerContext:
    """One process-owned engine and adapter graph; sessions are never shared."""

    settings: Settings
    database: Database
    storage: FakeMultipartStorage
    materializer: FakeMediaMaterializer
    content_inspector: FakeContentInspector
    malware_scanner: FakeMalwareScanner
    scene_detector: FakeSceneDetector
    audio_extractor: FakeAudioExtractor
    speech_to_text: FakeSpeechToText
    frame_extractor: FakeFrameExtractionAdapter
    video_provider: FakeVideoUnderstandingAdapter

    def ingest_service(self, session: AsyncSession) -> MediaIngestService:
        return MediaIngestService(
            session, self.settings, self.storage, self.content_inspector, self.malware_scanner
        )

    def technical_service(self, session: AsyncSession) -> TechnicalAnalysisService:
        return TechnicalAnalysisService(
            session,
            self.settings,
            self.materializer,
            FFprobeAdapter(self.settings),
            FFmpegDerivativeAdapter(self.settings),
            self.storage,
        )

    def scene_speech_service(self, session: AsyncSession) -> SceneSpeechAnalysisService:
        return SceneSpeechAnalysisService(
            session,
            self.settings,
            self.materializer,
            self.scene_detector,
            self.audio_extractor,
            self.speech_to_text,
            self.storage,
        )

    def video_understanding_service(self, session: AsyncSession) -> VideoUnderstandingService:
        return VideoUnderstandingService(
            session,
            self.settings,
            self.frame_extractor,
            self.video_provider,
            self.materializer,
        )

    def recovery_service(self, session: AsyncSession) -> JobRecoveryService:
        return JobRecoveryService(session, self.settings)


_context: WorkerContext | None = None


def build_worker_context(settings: Settings) -> WorkerContext:
    """Build the process-local development/test graph; production must configure real adapters."""

    if settings.app_env == "production":
        raise RuntimeError("WORKER_PRODUCTION_ADAPTERS_NOT_CONFIGURED")
    Path(settings.worker_temp_root).mkdir(mode=0o700, parents=True, exist_ok=True)
    return WorkerContext(
        settings=settings,
        database=create_database(settings),
        storage=FakeMultipartStorage(),
        materializer=FakeMediaMaterializer(allow_missing_for_testing=True),
        content_inspector=FakeContentInspector(),
        malware_scanner=FakeMalwareScanner(),
        scene_detector=FakeSceneDetector(),
        audio_extractor=FakeAudioExtractor(),
        speech_to_text=FakeSpeechToText(),
        frame_extractor=FakeFrameExtractionAdapter(settings),
        video_provider=FakeVideoUnderstandingAdapter(settings),
    )


def get_worker_context() -> WorkerContext:
    global _context
    if _context is None:
        _context = build_worker_context(get_settings())
    return _context


def start_worker_process() -> None:
    """Initialize after Celery forks, avoiding inherited async-engine pools."""

    global _context
    _context = build_worker_context(get_settings())


def shutdown_worker_process() -> None:
    """Dispose the process-local pool on worker shutdown."""

    global _context
    if _context is not None:
        asyncio.run(_context.database.dispose())
        _context = None
