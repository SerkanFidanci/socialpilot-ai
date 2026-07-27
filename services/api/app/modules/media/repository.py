"""Tenant-scoped media persistence operations."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.media.models import MediaAsset, MediaUploadSession


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

    def add(self, value: MediaAsset | MediaUploadSession) -> None:
        self._session.add(value)
