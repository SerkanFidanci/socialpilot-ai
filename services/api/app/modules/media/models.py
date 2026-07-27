"""Media upload persistence models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, func
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
