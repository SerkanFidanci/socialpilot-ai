"""Composition root owned by each Celery worker process."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.infrastructure.celery_app import celery_app
from app.infrastructure.celery_publisher import CeleryOutboxPublisher
from app.infrastructure.database.metadata import verify_mapping_is_complete
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
from app.infrastructure.storage import create_storage
from app.modules.media.ingest import MediaIngestService
from app.modules.media.scene_speech import SceneSpeechAnalysisService
from app.modules.media.storage import MultipartStoragePort
from app.modules.media.technical import (
    FFmpegDerivativeAdapter,
    FFprobeAdapter,
    TechnicalAnalysisService,
)
from app.modules.media.video_understanding_service import VideoUnderstandingService
from app.modules.operations.service import JobRecoveryService, OutboxDispatchService


@dataclass(frozen=True)
class WorkerContext:
    """One process-owned engine and adapter graph; sessions are never shared."""

    settings: Settings
    database: Database
    loop: asyncio.AbstractEventLoop
    outbox_publisher: CeleryOutboxPublisher
    storage: MultipartStoragePort
    materializer: FakeMediaMaterializer
    content_inspector: FakeContentInspector
    malware_scanner: FakeMalwareScanner
    scene_detector: FakeSceneDetector
    audio_extractor: FakeAudioExtractor
    speech_to_text: FakeSpeechToText
    frame_extractor: FakeFrameExtractionAdapter
    video_provider: FakeVideoUnderstandingAdapter

    def run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        """Run every task on this process's one loop so pooled asyncpg connections stay local.

        Consecutive Celery tasks must reuse this loop: an asyncpg connection is bound to
        the loop that opened it, so a per-task `asyncio.run` would hand pooled connections
        to a foreign loop and fail with "attached to a different loop".
        """

        if self.loop.is_closed():
            raise RuntimeError("WORKER_EVENT_LOOP_CLOSED")
        if _running_loop() is not None:
            raise RuntimeError("WORKER_EVENT_LOOP_REENTRANT")
        asyncio.set_event_loop(self.loop)
        return self.loop.run_until_complete(coroutine)

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

    def outbox_dispatcher(self, session: AsyncSession) -> OutboxDispatchService:
        return OutboxDispatchService(session, self.outbox_publisher)


_context: WorkerContext | None = None


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def build_worker_context(settings: Settings) -> WorkerContext:
    """Build the process-local development/test graph; production must configure real adapters."""

    if settings.app_env == "production":
        raise RuntimeError("WORKER_PRODUCTION_ADAPTERS_NOT_CONFIGURED")
    # The worker entry point imports fewer model modules than the API, so resolve every
    # cross-module foreign key here rather than on the first task that loads a row.
    verify_mapping_is_complete()
    Path(settings.worker_temp_root).mkdir(mode=0o700, parents=True, exist_ok=True)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return WorkerContext(
        settings=settings,
        database=create_database(settings),
        loop=loop,
        outbox_publisher=CeleryOutboxPublisher(celery_app),
        storage=create_storage(settings),
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
    shutdown_worker_process()
    _context = build_worker_context(get_settings())


def shutdown_worker_process() -> None:
    """Release the process-local pool, async generators, and event loop exactly once."""

    global _context
    context, _context = _context, None
    if context is None:
        return
    loop = context.loop
    try:
        if not loop.is_closed() and _running_loop() is None:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(context.database.dispose())
            loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        if not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(None)
