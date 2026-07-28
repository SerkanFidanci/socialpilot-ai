"""Media upload persistence models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.identity.models import Base


class MediaAssetStatus(StrEnum):
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class IngestStatus(StrEnum):
    PENDING = "pending"
    VALIDATING = "validating"
    SCANNING = "scanning"
    READY_FOR_ANALYSIS = "ready_for_analysis"
    REJECTED = "rejected"
    FAILED = "failed"
    DEAD = "dead"


class MalwareScanStatus(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"
    UNAVAILABLE = "unavailable"
    INDETERMINATE = "indeterminate"


class TechnicalAnalysisStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"


class MediaDerivativeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class TranscriptStatus(StrEnum):
    COMPLETED = "completed"
    NO_SPEECH = "no_speech"
    FAILED = "failed"
    DEAD = "dead"


class UploadSessionStatus(StrEnum):
    CREATED = "created"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (Index("ix_media_assets_business_status", "business_id", "status"),)
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    storage_object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[MediaAssetStatus] = mapped_column(
        Enum(
            MediaAssetStatus,
            name="media_asset_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        default=MediaAssetStatus.UPLOADING,
        nullable=False,
    )
    ingest_status: Mapped[IngestStatus] = mapped_column(
        Enum(
            IngestStatus,
            name="media_ingest_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        default=IngestStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MediaUploadSession(Base):
    __tablename__ = "media_upload_sessions"
    __table_args__ = (
        UniqueConstraint("business_id", "asset_id", name="uq_media_upload_session_asset"),
        Index("ix_media_upload_sessions_business_status", "business_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    storage_upload_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expected_part_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[UploadSessionStatus] = mapped_column(
        Enum(
            UploadSessionStatus,
            name="media_upload_session_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        default=UploadSessionStatus.CREATED,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MediaIngestInspection(Base):
    __tablename__ = "media_ingest_inspections"
    __table_args__ = (
        UniqueConstraint("business_id", "asset_id", name="uq_media_ingest_inspection_asset"),
        Index("ix_media_ingest_inspections_business_asset", "business_id", "asset_id"),
    )
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    storage_byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    storage_sha256_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_etag: Mapped[str] = mapped_column(String(512), nullable=False)
    detected_content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    inspected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MediaMalwareScan(Base):
    __tablename__ = "media_malware_scans"
    __table_args__ = (
        UniqueConstraint("business_id", "asset_id", name="uq_media_malware_scan_asset"),
        Index("ix_media_malware_scans_business_asset", "business_id", "asset_id"),
    )
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[MalwareScanStatus] = mapped_column(
        Enum(
            MalwareScanStatus,
            name="media_malware_scan_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
    )
    scanner_name: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MediaTechnicalMetadata(Base):
    __tablename__ = "media_technical_metadata"
    __table_args__ = (
        UniqueConstraint("business_id", "asset_id", name="uq_media_technical_metadata_asset"),
        Index("ix_media_technical_metadata_business_asset", "business_id", "asset_id"),
    )
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    container_format: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    video_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    display_aspect_ratio: Mapped[str | None] = mapped_column(String(32), nullable=True)
    frame_rate_numerator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_rate_denominator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bit_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rotation_degrees: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_audio: Mapped[bool] = mapped_column(nullable=False)
    audio_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_channel_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stream_count: Mapped[int] = mapped_column(Integer, nullable=False)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MediaTechnicalAnalysis(Base):
    __tablename__ = "media_technical_analyses"
    __table_args__ = (
        UniqueConstraint("business_id", "asset_id", name="uq_media_technical_analysis_asset"),
        Index("ix_media_technical_analyses_business_status", "business_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[TechnicalAnalysisStatus] = mapped_column(
        Enum(
            TechnicalAnalysisStatus,
            name="technical_analysis_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
    )
    safe_error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MediaDerivative(Base):
    __tablename__ = "media_derivatives"
    __table_args__ = (
        UniqueConstraint("business_id", "asset_id", "kind", name="uq_media_derivative_kind"),
        Index("ix_media_derivatives_business_asset", "business_id", "asset_id"),
    )
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[MediaDerivativeStatus] = mapped_column(
        Enum(
            MediaDerivativeStatus,
            name="media_derivative_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
    )
    safe_error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MediaScene(Base):
    __tablename__ = "media_scenes"
    __table_args__ = (
        UniqueConstraint("business_id", "asset_id", "scene_index", name="uq_media_scene_index"),
        Index("ix_media_scenes_business_asset", "business_id", "asset_id"),
    )
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    scene_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("business_id", "asset_id", name="uq_transcript_asset"),
        Index("ix_transcripts_business_asset", "business_id", "asset_id"),
    )
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("media_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TranscriptStatus] = mapped_column(
        Enum(
            TranscriptStatus,
            name="transcript_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcript_id", "segment_index", name="uq_transcript_segment_index"),
        Index("ix_transcript_segments_transcript_index", "transcript_id", "segment_index"),
    )
    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    transcript_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("transcripts.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(4_000), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    speaker_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
