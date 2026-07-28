"""Tenant-scoped media persistence operations."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.media.models import (
    MediaAsset,
    MediaDerivative,
    MediaIngestInspection,
    MediaMalwareScan,
    MediaScene,
    MediaTechnicalAnalysis,
    MediaTechnicalMetadata,
    MediaUploadSession,
    Transcript,
)


class MediaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_asset(
        self, business_id: UUID, asset_id: UUID, *, lock: bool = False
    ) -> MediaAsset | None:
        statement = select(MediaAsset).where(
            MediaAsset.business_id == business_id, MediaAsset.id == asset_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(MediaAsset | None, await self._session.scalar(statement))

    async def get_session(
        self, business_id: UUID, session_id: UUID, *, lock: bool = False
    ) -> MediaUploadSession | None:
        statement = select(MediaUploadSession).where(
            MediaUploadSession.business_id == business_id, MediaUploadSession.id == session_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(MediaUploadSession | None, await self._session.scalar(statement))

    async def get_inspection(
        self, business_id: UUID, asset_id: UUID, *, lock: bool = False
    ) -> MediaIngestInspection | None:
        statement = select(MediaIngestInspection).where(
            MediaIngestInspection.business_id == business_id,
            MediaIngestInspection.asset_id == asset_id,
        )
        if lock:
            statement = statement.with_for_update()
        return cast(MediaIngestInspection | None, await self._session.scalar(statement))

    async def get_malware_scan(
        self, business_id: UUID, asset_id: UUID, *, lock: bool = False
    ) -> MediaMalwareScan | None:
        statement = select(MediaMalwareScan).where(
            MediaMalwareScan.business_id == business_id,
            MediaMalwareScan.asset_id == asset_id,
        )
        if lock:
            statement = statement.with_for_update()
        return cast(MediaMalwareScan | None, await self._session.scalar(statement))

    async def get_technical_analysis(
        self, business_id: UUID, asset_id: UUID, *, lock: bool = False
    ) -> MediaTechnicalAnalysis | None:
        statement = select(MediaTechnicalAnalysis).where(
            MediaTechnicalAnalysis.business_id == business_id,
            MediaTechnicalAnalysis.asset_id == asset_id,
        )
        if lock:
            statement = statement.with_for_update()
        return cast(MediaTechnicalAnalysis | None, await self._session.scalar(statement))

    async def get_technical_metadata(
        self, business_id: UUID, asset_id: UUID
    ) -> MediaTechnicalMetadata | None:
        return cast(
            MediaTechnicalMetadata | None,
            await self._session.scalar(
                select(MediaTechnicalMetadata).where(
                    MediaTechnicalMetadata.business_id == business_id,
                    MediaTechnicalMetadata.asset_id == asset_id,
                )
            ),
        )

    async def list_derivatives(self, business_id: UUID, asset_id: UUID) -> list[MediaDerivative]:
        statement = select(MediaDerivative).where(
            MediaDerivative.business_id == business_id,
            MediaDerivative.asset_id == asset_id,
        )
        return list((await self._session.scalars(statement)).all())

    async def get_derivative(
        self, business_id: UUID, asset_id: UUID, kind: str, *, lock: bool = False
    ) -> MediaDerivative | None:
        statement = select(MediaDerivative).where(
            MediaDerivative.business_id == business_id,
            MediaDerivative.asset_id == asset_id,
            MediaDerivative.kind == kind,
        )
        if lock:
            statement = statement.with_for_update()
        return cast(MediaDerivative | None, await self._session.scalar(statement))

    async def get_transcript(
        self, business_id: UUID, asset_id: UUID, *, lock: bool = False
    ) -> Transcript | None:
        statement = select(Transcript).where(
            Transcript.business_id == business_id, Transcript.asset_id == asset_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(Transcript | None, await self._session.scalar(statement))

    async def list_scenes(self, business_id: UUID, asset_id: UUID) -> list[MediaScene]:
        statement = (
            select(MediaScene)
            .where(MediaScene.business_id == business_id, MediaScene.asset_id == asset_id)
            .order_by(MediaScene.scene_index)
        )
        return list((await self._session.scalars(statement)).all())

    def add(
        self, value: MediaAsset | MediaUploadSession | MediaIngestInspection | MediaMalwareScan
    ) -> None:
        self._session.add(value)
