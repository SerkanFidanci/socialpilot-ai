"""Composition root owned by each Celery worker process."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import install_signature_redaction
from app.core.telemetry import (
    TelemetryHandle,
    instrument_database,
    setup_worker_telemetry,
)
from app.infrastructure.ai import (
    create_audio_probe,
    create_script_generator,
    create_tts,
    create_visual_qc,
)
from app.infrastructure.celery_app import celery_app
from app.infrastructure.celery_publisher import CeleryOutboxPublisher
from app.infrastructure.database.metadata import verify_mapping_is_complete
from app.infrastructure.database.session import Database, create_database
from app.infrastructure.media import create_materializer
from app.infrastructure.media.fake_ingest import (
    FakeContentInspector,
    FakeMalwareScanner,
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
from app.infrastructure.render import create_qc_probe, create_render
from app.infrastructure.storage import create_storage
from app.modules.content.project_service import (
    AbandonedProjectSweeper,
    AbandonedRunSweeper,
    ContentProjectAdvanceService,
    ContentProjectReservationProbe,
)
from app.modules.content.qc import MediaQcProbePort, VisualQcPort
from app.modules.content.qc_service import ContentQcService
from app.modules.content.render import RenderPort
from app.modules.content.render_service import ContentRenderService
from app.modules.content.script import ScriptGenerationPort
from app.modules.content.tts import AudioProbePort, TTSPort
from app.modules.entitlement.service import AbandonedReservationSweeper
from app.modules.media.ingest import MediaIngestService
from app.modules.media.scene_speech import SceneSpeechAnalysisService
from app.modules.media.storage import MultipartStoragePort
from app.modules.media.technical import (
    FFmpegDerivativeAdapter,
    FFprobeAdapter,
    MediaMaterializerPort,
    TechnicalAnalysisService,
)
from app.modules.media.video_understanding_service import VideoUnderstandingService
from app.modules.operations.service import JobRecoveryService, OutboxDispatchService
from app.worker.scratch import WorkerScratchGuard


@dataclass(frozen=True)
class WorkerContext:
    """One process-owned engine and adapter graph; sessions are never shared."""

    settings: Settings
    database: Database
    loop: asyncio.AbstractEventLoop
    outbox_publisher: CeleryOutboxPublisher
    storage: MultipartStoragePort
    materializer: MediaMaterializerPort
    render: RenderPort
    # QC measurement has no fixture in any environment (see `create_qc_probe`), so this is the
    # real adapter here exactly as it is in production. The vision adapter beside it is the
    # opposite case: `disabled` in production, which is what keeps a fixture from approving a
    # customer's video.
    qc_probe: MediaQcProbePort
    visual_qc: VisualQcPort
    # The sequencer calls the script and speech services, so the worker process needs the same
    # two capability ports the API has. They follow the same rule: `disabled` in production until
    # a real provider is connected, which makes a project stop at `SCRIPTING` with a documented
    # code rather than produce fixture prose somebody could publish.
    script_generator: ScriptGenerationPort
    tts: TTSPort
    audio_probe: AudioProbePort
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

    def content_render_service(self, session: AsyncSession) -> ContentRenderService:
        return ContentRenderService(
            session, self.settings, self.materializer, self.render, self.storage
        )

    def content_qc_service(self, session: AsyncSession) -> ContentQcService:
        """Build the QC drain's service. No storage port: QC reads output, it never writes it.

        The render port is absent for a stronger reason than tidiness — a QC run that could
        reach it could re-render, and the attempt limit that would bound such a loop belongs to
        slice 2E. The constructor is the enforcement.
        """

        return ContentQcService(
            session, self.settings, self.materializer, self.qc_probe, self.visual_qc
        )

    def content_project_service(self, session: AsyncSession) -> ContentProjectAdvanceService:
        """Build the sequencer. Every capability arrives as the service that owns it.

        There is no provider port on this object that the individual services do not already
        hold — the sequencer orders them and owns none of them, which is the constructor saying
        the same thing slice 2E's work order does.
        """

        return ContentProjectAdvanceService(
            session,
            self.settings,
            render=self.render,
            script_generator=self.script_generator,
            tts=self.tts,
            audio_probe=self.audio_probe,
            storage=self.storage,
        )

    def abandoned_run_sweeper(self, session: AsyncSession) -> AbandonedRunSweeper:
        """Settle `pending` provider runs nobody will ever come back to. No ports at all."""

        return AbandonedRunSweeper(session, self.settings)

    def abandoned_project_sweeper(self, session: AsyncSession) -> AbandonedProjectSweeper:
        """Withdraw projects nobody ever came back to, and hand the credit back (PRD §21, §12.7).

        No capability port either. It cancels and refunds, which are both things this codebase
        already knows how to do; what the sweep adds is noticing that nobody is coming.
        """

        return AbandonedProjectSweeper(session, self.settings)

    def entitlement_sweeper(self, session: AsyncSession) -> AbandonedReservationSweeper:
        """Release credit holds whose work can no longer settle them.

        The probe is where the module boundary is kept: entitlement never queries
        `content_projects`, it asks the module that owns them. Wiring the two together is this
        composition root's job, exactly as it is for every capability port.
        """

        return AbandonedReservationSweeper(
            session, self.settings, ContentProjectReservationProbe(session)
        )

    def recovery_service(self, session: AsyncSession) -> JobRecoveryService:
        return JobRecoveryService(session, self.settings)

    def outbox_dispatcher(self, session: AsyncSession) -> OutboxDispatchService:
        return OutboxDispatchService(session, self.outbox_publisher)


_context: WorkerContext | None = None
_telemetry: TelemetryHandle | None = None


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
        materializer=create_materializer(settings),
        render=create_render(settings),
        qc_probe=create_qc_probe(settings),
        visual_qc=create_visual_qc(settings),
        script_generator=create_script_generator(settings),
        tts=create_tts(settings),
        audio_probe=create_audio_probe(settings),
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


# On a single server the API must never lose the CPU to a render/analysis burst. Renicing the
# worker process down (+10) makes FFmpeg and every other subprocess it spawns inherit a lower
# CPU priority than PostgreSQL and the API, so heavy jobs yield first under contention. This is
# the process-wide complement to compose.yaml's cpu_shares budget; ionice is left to the host.
_WORKER_NICE_INCREMENT = 10


def start_worker_process() -> None:
    """Initialize after Celery forks, avoiding inherited async-engine pools."""

    global _context, _telemetry
    shutdown_worker_process()
    # The worker never calls `configure_logging` — Celery owns its own handlers here — so the
    # signature scrubber has to be installed explicitly. Without this the worker would be the
    # one process where a materializer's HTTP client could log a presigned URL unmasked.
    install_signature_redaction()
    _lower_worker_cpu_priority()
    _context = build_worker_context(get_settings())
    # Wire telemetry in the forked worker process (default OFF). CeleryInstrumentor must patch
    # inside the worker, and the SQLAlchemy engine only exists now. No-op when disabled.
    _telemetry = setup_worker_telemetry(_context.settings)
    instrument_database(_telemetry, _context.database)
    # Sweep scratch orphaned by a previous worker generation killed mid-job (OOM, SIGKILL)
    # before this process starts draining, so a single server does not accumulate dead
    # scratch across restarts.
    WorkerScratchGuard(Path(_context.settings.worker_temp_root)).reclaim_stale()


def _lower_worker_cpu_priority() -> None:
    """Renice this worker process so its FFmpeg children cannot starve the API.

    ``os.nice`` is POSIX-only and absent on Windows; on a platform without it the compose
    ``cpu_shares`` budget still enforces the priority order, so this is a best-effort tightening
    rather than a hard requirement.
    """

    if not hasattr(os, "nice"):
        return
    try:
        os.nice(_WORKER_NICE_INCREMENT)
    except OSError:
        # Renicing can fail under a restrictive policy; the cpu_shares budget still applies.
        pass


def shutdown_worker_process() -> None:
    """Release the process-local pool, async generators, and event loop exactly once."""

    global _context, _telemetry
    if _telemetry is not None:
        _telemetry.shutdown()
        _telemetry = None
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
