"""Tenant-safe durable orchestration for normalized video understanding."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.modules.media.models import (
    MediaScene,
    MediaSceneUnderstanding,
    SceneUnderstandingStatus,
    TranscriptStatus,
)
from app.modules.media.repository import MediaRepository
from app.modules.media.scene_speech import TranscriptCandidate
from app.modules.media.technical import (
    MediaMaterializerPort,
    TechnicalPermanentError,
    TechnicalTransientError,
)
from app.modules.media.video_understanding import (
    FrameExtractionPermanentError,
    FrameExtractionPort,
    FrameExtractionTransientError,
    FrameReference,
    VideoUnderstandingPermanentError,
    VideoUnderstandingPort,
    VideoUnderstandingRequest,
    VideoUnderstandingResult,
    VideoUnderstandingTransientError,
    build_transcript_context,
    normalize_result,
)
from app.modules.operations.models import (
    BackgroundJob,
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import OperationsService


class VideoUnderstandingSchedulingService:
    """Schedule exactly one job only after durable scene/speech prerequisites exist."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._media = MediaRepository(session)
        self._operations = OperationsService(session, settings)
        self._settings = settings

    async def schedule_after_scene_speech(
        self, *, business_id: UUID, asset_id: UUID, correlation_id: str
    ) -> BackgroundJob | None:
        if not await self._media.has_completed_scene_speech(business_id, asset_id):
            return None
        scenes = await self._media.list_scenes(business_id, asset_id)
        supported_scenes = min(
            len(scenes), self._settings.video_understanding_supported_scene_count
        )
        if supported_scenes < 1:
            return None
        return await self._operations.record_video_understanding(
            business_id=business_id,
            asset_id=asset_id,
            correlation_id=correlation_id,
            scene_count=supported_scenes,
        )


class VideoUnderstandingService:
    """Run one durable asset job using provider-neutral, tenant-scoped inputs."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        frame_extractor: FrameExtractionPort,
        provider: VideoUnderstandingPort,
        materializer: MediaMaterializerPort | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._materializer = materializer
        self._frame_extractor = frame_extractor
        self._provider = provider
        self._media = MediaRepository(session)
        self._operations = OperationsRepository(session)
        self._claimed_attempts: dict[UUID, int] = {}

    async def claim_next(self) -> BackgroundJob | None:
        """Claim exactly one due job and begin its matching durable attempt."""

        async with self._session.begin():
            job = await self._operations.claim_next_video_understanding_job()
            if job is None:
                return None
            now = datetime.now(UTC)
            job.status = JobStatus.RUNNING
            job.attempt_count += 1
            job.started_at = now
            job.finished_at = None
            job.next_attempt_at = None
            job.last_error_code = None
            job.last_error_summary = None
            self._operations.add(
                JobAttempt(
                    job_id=job.id,
                    attempt_number=job.attempt_count,
                    status=JobAttemptStatus.STARTED,
                    correlation_id=job.correlation_id,
                )
            )
            self._claimed_attempts[job.id] = job.attempt_count
            return job

    async def process_next(self, *, workdir: Path) -> BackgroundJob | None:
        """Claim a job and keep materialized proxy/frames private to one temp directory."""

        job = await self.claim_next()
        if job is None:
            return None
        return await self.process_claimed(
            business_id=job.business_id,
            job_id=job.id,
            workdir=workdir,
            attempt_number=job.attempt_count,
        )

    async def process_claimed(
        self,
        *,
        business_id: UUID,
        job_id: UUID,
        workdir: Path | None = None,
        attempt_number: int | None = None,
    ) -> BackgroundJob:
        """Complete the claimed asset job, leaving no attempt in STARTED state."""

        try:
            expected_attempt_number = attempt_number or self._claimed_attempts.get(job_id)
            (
                scenes,
                transcript_segments,
                proxy_object_key,
                expected_attempt_number,
            ) = await self._load_inputs(
                business_id=business_id,
                job_id=job_id,
                expected_attempt_number=expected_attempt_number,
            )
            if self._materializer is None:
                raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_MATERIALIZER_REQUIRED")
            with TemporaryDirectory(
                prefix="video-understanding-", dir=workdir or Path(gettempdir())
            ) as temporary:
                temporary_path = Path(temporary)
                proxy_path = await self._materializer.materialize(
                    object_key=proxy_object_key, workdir=temporary_path
                )
                results: list[tuple[MediaScene, VideoUnderstandingResult, str]] = []
                remaining_frames = self._settings.video_understanding_max_frames_per_asset
                for scene in scenes:
                    context = build_transcript_context(
                        scene_start_ms=scene.start_ms,
                        scene_end_ms=scene.end_ms,
                        segments=transcript_segments,
                        maximum_chars=self._settings.video_understanding_max_transcript_context_chars,
                    )
                    request = VideoUnderstandingRequest(
                        asset_id=scene.asset_id,
                        scene_id=scene.id,
                        scene_start_ms=scene.start_ms,
                        scene_end_ms=scene.end_ms,
                        transcript_context=context,
                        frames=(),
                    )
                    frames: tuple[FrameReference, ...] = ()
                    if remaining_frames > 0:
                        frames = await self._frame_extractor.extract(
                            request=request,
                            source_path=proxy_path,
                            workdir=temporary_path,
                            timeout_seconds=self._settings.frame_extraction_timeout_seconds,
                            maximum_frames=remaining_frames,
                        )
                    remaining_frames -= len(frames)
                    result = await self._provider.understand(
                        request=VideoUnderstandingRequest(
                            asset_id=request.asset_id,
                            scene_id=request.scene_id,
                            scene_start_ms=request.scene_start_ms,
                            scene_end_ms=request.scene_end_ms,
                            transcript_context=request.transcript_context,
                            frames=frames,
                        ),
                        timeout_seconds=self._settings.video_understanding_timeout_seconds,
                    )
                    results.append((scene, normalize_result(result, self._settings), context))
            return await self._persist_results(
                business_id=business_id,
                job_id=job_id,
                results=tuple(results),
                expected_attempt_number=expected_attempt_number,
            )
        except (
            VideoUnderstandingTransientError,
            FrameExtractionTransientError,
            TechnicalTransientError,
        ):
            return await self._fail(
                business_id,
                job_id,
                expected_attempt_number,
                "VIDEO_UNDERSTANDING_PROVIDER_UNAVAILABLE",
                transient=True,
            )
        except (
            VideoUnderstandingPermanentError,
            FrameExtractionPermanentError,
            TechnicalPermanentError,
            ProblemException,
        ):
            return await self._fail(
                business_id,
                job_id,
                expected_attempt_number,
                "VIDEO_UNDERSTANDING_VALIDATION_FAILED",
                transient=False,
            )
        except IntegrityError:
            return await self._recover_duplicate_or_fail(
                business_id, job_id, expected_attempt_number
            )
        except SQLAlchemyError:
            return await self._fail(
                business_id,
                job_id,
                expected_attempt_number,
                "VIDEO_UNDERSTANDING_PERSISTENCE_UNAVAILABLE",
                transient=True,
            )

    async def _load_inputs(
        self, *, business_id: UUID, job_id: UUID, expected_attempt_number: int | None
    ) -> tuple[list[MediaScene], tuple[TranscriptCandidate, ...] | None, str, int]:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None or job.job_type != "media.video_understanding":
                raise self._not_found()
            expected_attempt_number = expected_attempt_number or job.attempt_count
            if (
                await self._operations.get_active_attempt_for_update(job, expected_attempt_number)
                is None
            ):
                return [], None, "", expected_attempt_number
            if job.status == JobStatus.SUCCEEDED:
                return [], None, "", expected_attempt_number
            if job.status != JobStatus.RUNNING:
                raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_JOB_STATE_INVALID")
            asset = await self._media.get_asset(business_id, job.resource_id, lock=True)
            proxy = await self._media.get_ready_proxy(business_id, job.resource_id)
            transcript = await self._media.get_transcript(business_id, job.resource_id, lock=True)
            scenes = (await self._media.list_scenes(business_id, job.resource_id))[
                : self._settings.video_understanding_supported_scene_count
            ]
            if (
                asset is None
                or proxy is None
                or transcript is None
                or transcript.status not in {TranscriptStatus.COMPLETED, TranscriptStatus.NO_SPEECH}
                or not scenes
            ):
                raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_RESOURCE_STATE_INVALID")
            if transcript.status == TranscriptStatus.NO_SPEECH:
                return scenes, (), proxy.storage_object_key, expected_attempt_number
            segments = await self._media.list_transcript_segments(business_id, transcript.id)
            return (
                scenes,
                tuple(
                    TranscriptCandidate(
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        text=segment.text,
                        confidence=segment.confidence,
                        speaker_label=segment.speaker_label,
                    )
                    for segment in segments
                ),
                proxy.storage_object_key,
                expected_attempt_number,
            )

    async def _persist_results(
        self,
        *,
        business_id: UUID,
        job_id: UUID,
        results: tuple[tuple[MediaScene, VideoUnderstandingResult, str], ...],
        expected_attempt_number: int,
    ) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None or job.job_type != "media.video_understanding":
                raise self._not_found()
            if job.status == JobStatus.SUCCEEDED:
                return job
            attempt = await self._operations.get_active_attempt_for_update(
                job, expected_attempt_number
            )
            if attempt is None:
                return job
            if not results:
                raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_RESOURCE_STATE_INVALID")
            expected_scenes = (await self._media.list_scenes(business_id, job.resource_id))[
                : self._settings.video_understanding_supported_scene_count
            ]
            if [scene.id for scene in expected_scenes] != [scene.id for scene, _, _ in results]:
                raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_RESOURCE_STATE_INVALID")
            existing = await self._media.list_scene_understandings(business_id, job.resource_id)
            if existing:
                if {value.scene_id for value in existing} == {scene.id for scene, _, _ in results}:
                    return await self._mark_succeeded(job, expected_attempt_number)
                raise VideoUnderstandingPermanentError("VIDEO_UNDERSTANDING_DUPLICATE_CONFLICT")
            for scene, result, context in results:
                self._media.add(
                    MediaSceneUnderstanding(
                        business_id=business_id,
                        asset_id=job.resource_id,
                        scene_id=scene.id,
                        status=SceneUnderstandingStatus.COMPLETED,
                        provider=result.provider,
                        model_name=result.model_name,
                        summary=result.summary,
                        visual_description=result.visual_description,
                        transcript_context=context,
                        confidence=result.confidence,
                        labels=list(result.labels),
                        objects=list(result.objects),
                        actions=list(result.actions),
                        visible_text=list(result.visible_text),
                        dominant_topics=list(result.dominant_topics),
                        safety_flags=list(result.safety_flags),
                        quality_signals=cast(dict[str, object], result.quality_signals or {}),
                    )
                )
            await self._session.flush()
            await self._mark_succeeded(job, expected_attempt_number)
            self._operations.add(
                OutboxEvent(
                    business_id=business_id,
                    event_type="media.video_understanding.completed",
                    aggregate_type="media_asset",
                    aggregate_id=job.resource_id,
                    payload={"job_id": str(job.id), "asset_id": str(job.resource_id)},
                    correlation_id=job.correlation_id,
                    status=OutboxStatus.PENDING,
                    max_attempts=job.max_attempts,
                    next_attempt_at=datetime.now(UTC),
                )
            )
            return job

    async def _recover_duplicate_or_fail(
        self, business_id: UUID, job_id: UUID, expected_attempt_number: int | None
    ) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("video understanding job disappeared")
            if (
                expected_attempt_number is None
                or await self._operations.get_active_attempt_for_update(
                    job, expected_attempt_number
                )
                is None
            ):
                return job
            existing = await self._media.list_scene_understandings(business_id, job.resource_id)
            scenes = (await self._media.list_scenes(business_id, job.resource_id))[
                : self._settings.video_understanding_supported_scene_count
            ]
            if existing and {value.scene_id for value in existing} == {
                scene.id for scene in scenes
            }:
                return await self._mark_succeeded(job, expected_attempt_number)
        return await self._fail(
            business_id,
            job_id,
            expected_attempt_number,
            "VIDEO_UNDERSTANDING_PERSISTENCE_FAILED",
            transient=False,
        )

    async def _mark_succeeded(
        self, job: BackgroundJob, expected_attempt_number: int
    ) -> BackgroundJob:
        attempt = await self._operations.get_active_attempt_for_update(job, expected_attempt_number)
        if attempt is None:
            return job
        attempt.status = JobAttemptStatus.SUCCEEDED
        attempt.finished_at = datetime.now(UTC)
        attempt.error_code = None
        attempt.error_summary = None
        job.status = JobStatus.SUCCEEDED
        job.finished_at = datetime.now(UTC)
        job.next_attempt_at = None
        job.last_error_code = None
        job.last_error_summary = None
        return job

    async def _fail(
        self,
        business_id: UUID,
        job_id: UUID,
        expected_attempt_number: int | None,
        code: str,
        *,
        transient: bool,
    ) -> BackgroundJob:
        async with self._session.begin():
            job = await self._operations.get_job_for_update(business_id, job_id)
            if job is None:
                raise RuntimeError("video understanding job disappeared")
            if (
                expected_attempt_number is None
                or await self._operations.get_active_attempt_for_update(
                    job, expected_attempt_number
                )
                is None
            ):
                return job
            attempt = await self._operations.get_active_attempt_for_update(
                job, expected_attempt_number
            )
            if attempt is not None:
                attempt.status = JobAttemptStatus.FAILED
                attempt.finished_at = datetime.now(UTC)
                attempt.error_code = code
                attempt.error_summary = code
            job.last_error_code = code
            job.last_error_summary = code
            job.finished_at = datetime.now(UTC)
            if transient and job.attempt_count < job.max_attempts:
                job.status = JobStatus.FAILED
                job.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=min(2**job.attempt_count, 60)
                )
            else:
                job.status = JobStatus.DEAD if transient else JobStatus.FAILED
                job.next_attempt_at = None
            return job

    @staticmethod
    def _not_found() -> ProblemException:
        return ProblemException(
            status=404,
            code="TENANT_RESOURCE_NOT_FOUND",
            title="Resource not found",
            detail="The requested resource is not available.",
        )
