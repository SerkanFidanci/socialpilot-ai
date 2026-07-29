"""Read-only aggregate of one asset's durable processing state for client screens.

The mobile demo needs the whole pipeline in a single tenant-scoped read. This module
derives the current step and any terminal failure from durable records only; it never
exposes a storage object key, upload identifier, storage ETag, or credential, and it
performs no provider work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.media.models import (
    IngestStatus,
    MalwareScanStatus,
    MediaAsset,
    MediaAssetStatus,
    MediaScene,
    MediaSceneUnderstanding,
    MediaTechnicalMetadata,
    TechnicalAnalysisStatus,
    Transcript,
    TranscriptSegment,
    TranscriptStatus,
    UploadSessionStatus,
)
from app.modules.media.repository import MediaRepository
from app.modules.media.service import MediaService
from app.modules.media.video_understanding import (
    SceneAnalysisMode,
    SceneCoverageReport,
    build_scene_coverage_report,
)
from app.modules.operations.models import BackgroundJob, JobStatus
from app.modules.operations.repository import OperationsRepository

INGEST_JOB = "media.ingest"
TECHNICAL_JOB = "media.technical_analysis"
SCENE_SPEECH_JOB = "media.scene_speech_analysis"
VIDEO_UNDERSTANDING_JOB = "media.video_understanding"


class ProcessingStep(StrEnum):
    """The client-visible pipeline position, ordered as the screens present it."""

    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    SECURITY_CHECK = "security_check"
    TECHNICAL_ANALYSIS = "technical_analysis"
    SCENE_SPEECH_ANALYSIS = "scene_speech_analysis"
    VIDEO_UNDERSTANDING = "video_understanding"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class StageState:
    """One pipeline stage reduced to a safe status, error code, and durable job facts."""

    status: str
    safe_error_code: str | None
    job_status: JobStatus | None
    attempt_count: int
    max_attempts: int
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class ProcessingSummary:
    """Everything a result screen needs, already tenant-filtered."""

    asset: MediaAsset
    upload_session_status: UploadSessionStatus | None
    upload_expected_part_count: int | None
    upload_expires_at: datetime | None
    upload_completed_at: datetime | None
    detected_content_type: str | None
    malware_scan_status: MalwareScanStatus | None
    ingest: StageState
    technical: StageState
    technical_metadata: MediaTechnicalMetadata | None
    scene_speech: StageState
    video_understanding: StageState
    scenes: tuple[MediaScene, ...]
    scenes_truncated: bool
    transcript: Transcript | None
    transcript_segments: tuple[TranscriptSegment, ...]
    transcript_segments_truncated: bool
    understandings: tuple[MediaSceneUnderstanding, ...]
    understandings_truncated: bool
    coverage: SceneCoverageReport | None
    current_step: ProcessingStep
    terminal_failure_code: str | None


class ProcessingSummaryService:
    """Aggregate durable pipeline state for one asset under the caller's tenant scope."""

    def __init__(self, session: AsyncSession, settings: Settings, media: MediaService) -> None:
        self._settings = settings
        self._media_service = media
        self._media = MediaRepository(session)
        self._operations = OperationsRepository(session)

    async def build(self, *, user_id: UUID, business_id: UUID, asset_id: UUID) -> ProcessingSummary:
        """Authorize through the media service, then read every stage for this asset."""

        asset = await self._media_service.asset(
            user_id=user_id, business_id=business_id, asset_id=asset_id
        )
        limit = self._settings.processing_summary_max_items
        upload = await self._media.get_upload_session_for_asset(business_id, asset_id)
        inspection = await self._media.get_inspection(business_id, asset_id)
        scan = await self._media.get_malware_scan(business_id, asset_id)
        technical = await self._media.get_technical_analysis(business_id, asset_id)
        technical_metadata = await self._media.get_technical_metadata(business_id, asset_id)
        transcript = await self._media.get_transcript(business_id, asset_id)
        segments = (
            await self._media.list_transcript_segments(business_id, transcript.id)
            if transcript is not None
            else []
        )
        scenes = await self._media.list_scenes(business_id, asset_id)
        understandings = self._in_scene_order(
            scenes, await self._media.list_scene_understandings(business_id, asset_id)
        )
        jobs = {
            job.job_type: job
            for job in await self._operations.list_jobs_for_resource(business_id, asset_id)
        }

        # The scan verdict is reported as its own safe field, not folded into the stage.
        ingest = self._stage(
            status=asset.ingest_status, safe_error_code=None, job=jobs.get(INGEST_JOB)
        )
        technical_stage = self._stage(
            status=(technical.status if technical is not None else TechnicalAnalysisStatus.PENDING),
            safe_error_code=technical.safe_error_code if technical is not None else None,
            job=jobs.get(TECHNICAL_JOB),
        )
        scene_speech = self._stage(
            status=(transcript.status if transcript is not None else "pending"),
            safe_error_code=None,
            job=jobs.get(SCENE_SPEECH_JOB),
        )
        understanding_stage = self._understanding_stage(
            understandings, jobs.get(VIDEO_UNDERSTANDING_JOB)
        )
        coverage = self._coverage(total_scene_count=len(scenes), understandings=understandings)
        terminal_failure_code = self._terminal_failure_code(
            asset=asset,
            scan_status=scan.status if scan is not None else None,
            scan_error_code=scan.safe_error_code if scan is not None else None,
            technical_status=technical.status if technical is not None else None,
            technical_error_code=technical.safe_error_code if technical is not None else None,
            transcript_status=transcript.status if transcript is not None else None,
            jobs=jobs,
        )
        return ProcessingSummary(
            asset=asset,
            upload_session_status=upload.status if upload is not None else None,
            upload_expected_part_count=upload.expected_part_count if upload is not None else None,
            upload_expires_at=upload.expires_at if upload is not None else None,
            upload_completed_at=upload.completed_at if upload is not None else None,
            detected_content_type=(
                inspection.detected_content_type if inspection is not None else None
            ),
            malware_scan_status=scan.status if scan is not None else None,
            ingest=ingest,
            technical=technical_stage,
            technical_metadata=technical_metadata,
            scene_speech=scene_speech,
            video_understanding=understanding_stage,
            scenes=tuple(scenes[:limit]),
            scenes_truncated=len(scenes) > limit,
            transcript=transcript,
            transcript_segments=tuple(segments[:limit]),
            transcript_segments_truncated=len(segments) > limit,
            understandings=tuple(understandings[:limit]),
            understandings_truncated=len(understandings) > limit,
            coverage=coverage,
            current_step=self._current_step(
                asset=asset,
                technical_status=technical.status if technical is not None else None,
                transcript_status=transcript.status if transcript is not None else None,
                scene_count=len(scenes),
                understanding_count=len(understandings),
                understanding_job=jobs.get(VIDEO_UNDERSTANDING_JOB),
                terminal_failure_code=terminal_failure_code,
            ),
            terminal_failure_code=terminal_failure_code,
        )

    @staticmethod
    def _in_scene_order(
        scenes: list[MediaScene], understandings: list[MediaSceneUnderstanding]
    ) -> list[MediaSceneUnderstanding]:
        """Order results along the video timeline.

        Understandings written in one transaction share a `created_at`, so the stored
        tie-break is a random UUID. A result screen must list scenes in timeline order,
        so sort by scene index and keep any unmatched row last but deterministic.
        """

        index_by_scene = {scene.id: scene.scene_index for scene in scenes}
        return sorted(
            understandings,
            key=lambda value: (index_by_scene.get(value.scene_id, len(scenes)), str(value.id)),
        )

    @staticmethod
    def _stage(
        *, status: str, safe_error_code: str | None, job: BackgroundJob | None
    ) -> StageState:
        return StageState(
            status=str(status),
            safe_error_code=safe_error_code,
            job_status=job.status if job is not None else None,
            attempt_count=job.attempt_count if job is not None else 0,
            max_attempts=job.max_attempts if job is not None else 0,
            started_at=job.started_at if job is not None else None,
            finished_at=job.finished_at if job is not None else None,
        )

    def _understanding_stage(
        self, understandings: list[MediaSceneUnderstanding], job: BackgroundJob | None
    ) -> StageState:
        statuses = {value.status for value in understandings}
        if not statuses:
            status = "pending"
        elif len(statuses) == 1:
            status = str(next(iter(statuses)))
        else:
            status = "partial"
        return self._stage(status=status, safe_error_code=None, job=job)

    @staticmethod
    def _coverage(
        *, total_scene_count: int, understandings: list[MediaSceneUnderstanding]
    ) -> SceneCoverageReport | None:
        """Recompute coverage from the persisted service-authoritative analysis modes.

        The completion outbox event carries the same numbers, but a read API must not
        depend on transport state. An unparseable mode means coverage cannot be reported
        honestly, so it is omitted rather than guessed.
        """

        if not understandings or total_scene_count < len(understandings):
            return None
        modes: list[SceneAnalysisMode] = []
        for value in understandings:
            raw = value.quality_signals.get("analysis_mode")
            if not isinstance(raw, str) or raw not in set(SceneAnalysisMode):
                return None
            modes.append(SceneAnalysisMode(raw))
        return build_scene_coverage_report(total_scene_count=total_scene_count, modes=modes)

    @staticmethod
    def _terminal_failure_code(
        *,
        asset: MediaAsset,
        scan_status: MalwareScanStatus | None,
        scan_error_code: str | None,
        technical_status: TechnicalAnalysisStatus | None,
        technical_error_code: str | None,
        transcript_status: TranscriptStatus | None,
        jobs: dict[str, BackgroundJob],
    ) -> str | None:
        """Return a safe code only for states no retry can leave.

        A `failed` job keeps a due `next_attempt_at`, so only `dead`, a rejected or
        quarantined asset, and a `dead` stage record are terminal.
        """

        if asset.status == MediaAssetStatus.QUARANTINED:
            return scan_error_code or "MEDIA_ASSET_QUARANTINED"
        if asset.status == MediaAssetStatus.REJECTED:
            return scan_error_code or "MEDIA_ASSET_REJECTED"
        if asset.ingest_status == IngestStatus.REJECTED:
            return scan_error_code or "MEDIA_INGEST_REJECTED"
        if asset.ingest_status == IngestStatus.DEAD:
            return jobs[INGEST_JOB].last_error_code if INGEST_JOB in jobs else "MEDIA_INGEST_DEAD"
        if scan_status in {MalwareScanStatus.INFECTED, MalwareScanStatus.INDETERMINATE}:
            return scan_error_code or "MEDIA_MALWARE_SCAN_BLOCKED"
        if technical_status == TechnicalAnalysisStatus.DEAD:
            return technical_error_code or "MEDIA_TECHNICAL_ANALYSIS_DEAD"
        if transcript_status == TranscriptStatus.DEAD:
            return "MEDIA_SCENE_SPEECH_ANALYSIS_DEAD"
        for job_type in (INGEST_JOB, TECHNICAL_JOB, SCENE_SPEECH_JOB, VIDEO_UNDERSTANDING_JOB):
            job = jobs.get(job_type)
            if job is not None and job.status == JobStatus.DEAD:
                return job.last_error_code or "BACKGROUND_JOB_DEAD"
        return None

    @staticmethod
    def _current_step(
        *,
        asset: MediaAsset,
        technical_status: TechnicalAnalysisStatus | None,
        transcript_status: TranscriptStatus | None,
        scene_count: int,
        understanding_count: int,
        understanding_job: BackgroundJob | None,
        terminal_failure_code: str | None,
    ) -> ProcessingStep:
        """Derive the active step from durable state in strict pipeline order."""

        if terminal_failure_code is not None:
            return ProcessingStep.FAILED
        if asset.status == MediaAssetStatus.UPLOADING:
            return ProcessingStep.UPLOADING
        if asset.ingest_status == IngestStatus.PENDING:
            return ProcessingStep.UPLOADED
        if asset.ingest_status != IngestStatus.READY_FOR_ANALYSIS:
            return ProcessingStep.SECURITY_CHECK
        if technical_status != TechnicalAnalysisStatus.COMPLETED:
            return ProcessingStep.TECHNICAL_ANALYSIS
        if scene_count < 1 or transcript_status not in {
            TranscriptStatus.COMPLETED,
            TranscriptStatus.NO_SPEECH,
        }:
            return ProcessingStep.SCENE_SPEECH_ANALYSIS
        if understanding_count < 1 or (
            understanding_job is not None and understanding_job.status != JobStatus.SUCCEEDED
        ):
            return ProcessingStep.VIDEO_UNDERSTANDING
        return ProcessingStep.COMPLETED
