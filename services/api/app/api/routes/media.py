"""Direct-upload control plane routes; no media bytes are accepted."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.infrastructure.database.session import get_session
from app.modules.identity.models import User
from app.modules.media.models import (
    IngestStatus,
    MalwareScanStatus,
    MediaAssetStatus,
    MediaSceneUnderstanding,
    UploadSessionStatus,
)
from app.modules.media.processing_summary import (
    ProcessingStep,
    ProcessingSummary,
    ProcessingSummaryService,
    StageState,
)
from app.modules.media.service import MediaService
from app.modules.media.storage import CompletedPart, MultipartStoragePort, UploadPartInstruction
from app.modules.operations.models import JobStatus

router = APIRouter(prefix="/v1", tags=["media"])


class PartResponse(BaseModel):
    part_number: int
    upload_url: str

    @classmethod
    def make(cls, instruction: UploadPartInstruction) -> PartResponse:
        return cls(part_number=instruction.part_number, upload_url=instruction.upload_url)


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    business_id: UUID
    content_type: str
    byte_size: int
    sha256_checksum: str
    status: MediaAssetStatus
    ingest_status: IngestStatus
    created_at: datetime
    uploaded_at: datetime | None


class SessionResponse(BaseModel):
    id: UUID
    asset_id: UUID
    status: UploadSessionStatus
    expires_at: datetime
    parts: list[PartResponse]


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=127)
    byte_size: int = Field(gt=0)
    sha256_checksum: str = Field(min_length=64, max_length=64)
    part_count: int = Field(ge=1, le=1000)


class PartsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_numbers: list[int] = Field(min_length=1, max_length=1000)


class CompletedPartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_number: int = Field(ge=1)
    etag: str = Field(min_length=1, max_length=512)


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256_checksum: str = Field(min_length=64, max_length=64)
    parts: list[CompletedPartRequest] = Field(min_length=1, max_length=1000)


def service(session: AsyncSession, request: Request) -> MediaService:
    return MediaService(
        session,
        cast(Settings, request.app.state.settings),
        cast(MultipartStoragePort, request.app.state.storage),
    )


def response(
    upload_id: UUID,
    asset_id: UUID,
    status: UploadSessionStatus,
    expires_at: datetime,
    instructions: tuple[UploadPartInstruction, ...],
) -> SessionResponse:
    return SessionResponse(
        id=upload_id,
        asset_id=asset_id,
        status=status,
        expires_at=expires_at,
        parts=[PartResponse.make(value) for value in instructions],
    )


@router.post(
    "/businesses/{business_id}/media/uploads", response_model=SessionResponse, status_code=201
)
async def create(
    business_id: UUID,
    payload: CreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionResponse:
    upload, instructions = await service(session, request).create(
        user_id=user.id,
        business_id=business_id,
        filename=payload.filename,
        content_type=payload.content_type,
        byte_size=payload.byte_size,
        checksum=payload.sha256_checksum,
        part_count=payload.part_count,
    )
    return response(upload.id, upload.asset_id, upload.status, upload.expires_at, instructions)


@router.post(
    "/businesses/{business_id}/media/uploads/{upload_session_id}/parts",
    response_model=SessionResponse,
)
async def parts(
    business_id: UUID,
    upload_session_id: UUID,
    payload: PartsRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionResponse:
    upload, instructions = await service(session, request).parts(
        user_id=user.id,
        business_id=business_id,
        session_id=upload_session_id,
        numbers=tuple(payload.part_numbers),
    )
    return response(upload.id, upload.asset_id, upload.status, upload.expires_at, instructions)


@router.post(
    "/businesses/{business_id}/media/uploads/{upload_session_id}/complete",
    response_model=AssetResponse,
)
async def complete(
    business_id: UUID,
    upload_session_id: UUID,
    payload: CompleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=255),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AssetResponse:
    asset = await service(session, request).complete(
        user_id=user.id,
        business_id=business_id,
        session_id=upload_session_id,
        checksum=payload.sha256_checksum,
        parts=tuple(CompletedPart(part.part_number, part.etag) for part in payload.parts),
        idempotency_key=idempotency_key,
        correlation_id=get_correlation_id() or "unknown",
    )
    return AssetResponse.model_validate(asset)


@router.post("/businesses/{business_id}/media/uploads/{upload_session_id}/cancel", status_code=204)
async def cancel(
    business_id: UUID,
    upload_session_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await service(session, request).cancel(
        user_id=user.id, business_id=business_id, session_id=upload_session_id
    )
    return Response(status_code=204)


@router.get("/businesses/{business_id}/media/{asset_id}", response_model=AssetResponse)
async def asset(
    business_id: UUID,
    asset_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AssetResponse:
    return AssetResponse.model_validate(
        await service(session, request).asset(
            user_id=user.id, business_id=business_id, asset_id=asset_id
        )
    )


class StageResponse(BaseModel):
    status: str
    safe_error_code: str | None
    job_status: JobStatus | None
    attempt_count: int
    max_attempts: int
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def make(cls, value: StageState) -> StageResponse:
        return cls(
            status=value.status,
            safe_error_code=value.safe_error_code,
            job_status=value.job_status,
            attempt_count=value.attempt_count,
            max_attempts=value.max_attempts,
            started_at=value.started_at,
            finished_at=value.finished_at,
        )


class UploadSummaryResponse(BaseModel):
    """Upload session state only; the storage upload identifier is never exposed."""

    status: UploadSessionStatus | None
    expected_part_count: int | None
    expires_at: datetime | None
    completed_at: datetime | None


class TechnicalMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    container_format: str
    duration_ms: int
    file_size: int
    video_codec: str | None
    width: int | None
    height: int | None
    display_aspect_ratio: str | None
    frame_rate_numerator: int | None
    frame_rate_denominator: int | None
    bit_rate: int | None
    rotation_degrees: int
    has_audio: bool
    audio_codec: str | None
    audio_sample_rate: int | None
    audio_channel_count: int | None
    stream_count: int


class SceneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    scene_index: int
    start_ms: int
    end_ms: int
    duration_ms: int
    confidence: float


class TranscriptSegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    segment_index: int
    start_ms: int
    end_ms: int
    text: str
    confidence: float
    speaker_label: str | None


class TranscriptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    language: str
    duration_ms: int
    full_text: str
    provider: str
    status: str


class UnderstandingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    scene_id: UUID
    status: str
    provider: str
    model_name: str
    summary: str
    visual_description: str
    transcript_context: str
    confidence: float
    labels: list[str]
    objects: list[str]
    actions: list[str]
    visible_text: list[str]
    dominant_topics: list[str]
    safety_flags: list[str]
    analysis_mode: str | None
    visual_input_available: bool | None

    @classmethod
    def make(cls, value: MediaSceneUnderstanding) -> UnderstandingResponse:
        """Surface only the service-authoritative signals, not the raw provider dictionary."""

        mode = value.quality_signals.get("analysis_mode")
        visual = value.quality_signals.get("visual_input_available")
        return cls(
            id=value.id,
            scene_id=value.scene_id,
            status=str(value.status),
            provider=value.provider,
            model_name=value.model_name,
            summary=value.summary,
            visual_description=value.visual_description,
            transcript_context=value.transcript_context,
            confidence=value.confidence,
            labels=list(value.labels),
            objects=list(value.objects),
            actions=list(value.actions),
            visible_text=list(value.visible_text),
            dominant_topics=list(value.dominant_topics),
            safety_flags=list(value.safety_flags),
            analysis_mode=mode if isinstance(mode, str) else None,
            visual_input_available=visual if isinstance(visual, bool) else None,
        )


class CoverageResponse(BaseModel):
    total_scene_count: int
    analyzed_scene_count: int
    skipped_scene_count: int
    coverage: str
    frame_backed_scene_count: int
    transcript_only_scene_count: int
    no_context_scene_count: int


class ProcessingSummaryResponse(BaseModel):
    asset: AssetResponse
    upload: UploadSummaryResponse
    detected_content_type: str | None
    malware_scan_status: MalwareScanStatus | None
    ingest: StageResponse
    technical: StageResponse
    technical_metadata: TechnicalMetadataResponse | None
    scene_speech: StageResponse
    video_understanding: StageResponse
    scenes: list[SceneResponse]
    scenes_truncated: bool
    transcript: TranscriptResponse | None
    transcript_segments: list[TranscriptSegmentResponse]
    transcript_segments_truncated: bool
    understandings: list[UnderstandingResponse]
    understandings_truncated: bool
    coverage: CoverageResponse | None
    current_step: ProcessingStep
    terminal_failure_code: str | None

    @classmethod
    def make(cls, value: ProcessingSummary) -> ProcessingSummaryResponse:
        return cls(
            asset=AssetResponse.model_validate(value.asset),
            upload=UploadSummaryResponse(
                status=value.upload_session_status,
                expected_part_count=value.upload_expected_part_count,
                expires_at=value.upload_expires_at,
                completed_at=value.upload_completed_at,
            ),
            detected_content_type=value.detected_content_type,
            malware_scan_status=value.malware_scan_status,
            ingest=StageResponse.make(value.ingest),
            technical=StageResponse.make(value.technical),
            technical_metadata=(
                TechnicalMetadataResponse.model_validate(value.technical_metadata)
                if value.technical_metadata is not None
                else None
            ),
            scene_speech=StageResponse.make(value.scene_speech),
            video_understanding=StageResponse.make(value.video_understanding),
            scenes=[SceneResponse.model_validate(scene) for scene in value.scenes],
            scenes_truncated=value.scenes_truncated,
            transcript=(
                TranscriptResponse.model_validate(value.transcript)
                if value.transcript is not None
                else None
            ),
            transcript_segments=[
                TranscriptSegmentResponse.model_validate(segment)
                for segment in value.transcript_segments
            ],
            transcript_segments_truncated=value.transcript_segments_truncated,
            understandings=[
                UnderstandingResponse.make(understanding) for understanding in value.understandings
            ],
            understandings_truncated=value.understandings_truncated,
            coverage=(
                CoverageResponse(**value.coverage.as_event_payload())  # type: ignore[arg-type]
                if value.coverage is not None
                else None
            ),
            current_step=value.current_step,
            terminal_failure_code=value.terminal_failure_code,
        )


@router.get(
    "/businesses/{business_id}/media/{asset_id}/processing-summary",
    response_model=ProcessingSummaryResponse,
)
async def processing_summary(
    business_id: UUID,
    asset_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProcessingSummaryResponse:
    """Return one tenant-scoped read of the whole analysis pipeline for a client screen."""

    summary = await ProcessingSummaryService(
        session, cast(Settings, request.app.state.settings), service(session, request)
    ).build(user_id=user.id, business_id=business_id, asset_id=asset_id)
    return ProcessingSummaryResponse.make(summary)
