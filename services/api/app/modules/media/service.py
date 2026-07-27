"""Authorization, state rules, and transactions for direct upload control plane."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.modules.businesses.models import BusinessStatus
from app.modules.businesses.policy import Permission, permits
from app.modules.businesses.repository import BusinessRepository
from app.modules.media.models import (
    MediaAsset,
    MediaAssetStatus,
    MediaUploadSession,
    UploadSessionStatus,
)
from app.modules.media.repository import MediaRepository
from app.modules.media.storage import (
    CompletedPart,
    MultipartStoragePort,
    StorageUnavailableError,
    UploadPartInstruction,
)

_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_EXTENSIONS = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "video/mp4": {".mp4"},
    "audio/mpeg": {".mp3"},
}


class MediaService:
    def __init__(
        self, session: AsyncSession, settings: Settings, storage: MultipartStoragePort
    ) -> None:
        self._session, self._settings, self._storage = session, settings, storage
        self._repo, self._businesses = MediaRepository(session), BusinessRepository(session)

    async def create(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        filename: str,
        content_type: str,
        byte_size: int,
        checksum: str,
        part_count: int,
    ) -> tuple[MediaUploadSession, tuple[UploadPartInstruction, ...]]:
        content_type = self._validate(filename, content_type, byte_size, checksum, part_count)
        async with self._session.begin():
            await self._authorize(user_id, business_id, Permission.MEDIA_UPLOAD)
            asset_id, expires_at = (
                uuid4(),
                datetime.now(UTC)
                + timedelta(seconds=self._settings.media_upload_session_ttl_seconds),
            )
            asset = MediaAsset(
                id=asset_id,
                business_id=business_id,
                created_by_user_id=user_id,
                storage_object_key=f"tenant/{business_id}/media/{asset_id}/original/{uuid4().hex}",
                content_type=content_type,
                byte_size=byte_size,
                sha256_checksum=checksum.lower(),
            )
            upload = MediaUploadSession(
                business_id=business_id,
                asset_id=asset_id,
                storage_upload_id=uuid4().hex,
                expected_part_count=part_count,
                expires_at=expires_at,
            )
            self._repo.add(asset)
            self._repo.add(upload)
            try:
                instructions = await self._storage.create_upload(
                    storage_upload_id=upload.storage_upload_id,
                    object_key=asset.storage_object_key,
                    expires_at=expires_at,
                    part_numbers=tuple(range(1, part_count + 1)),
                )
            except StorageUnavailableError as error:
                raise self._storage_error() from error
            await self._session.flush()
            return upload, instructions

    async def parts(
        self, *, user_id: UUID, business_id: UUID, session_id: UUID, numbers: tuple[int, ...]
    ) -> tuple[MediaUploadSession, tuple[UploadPartInstruction, ...]]:
        async with self._session.begin():
            await self._authorize(user_id, business_id, Permission.MEDIA_UPLOAD)
            upload = await self._active_session(business_id, session_id)
            self._valid_numbers(numbers, upload.expected_part_count, exact=False)
            try:
                urls = await self._storage.create_part_urls(
                    storage_upload_id=upload.storage_upload_id,
                    expires_at=upload.expires_at,
                    part_numbers=numbers,
                )
            except StorageUnavailableError as error:
                raise self._storage_error() from error
            return upload, urls

    async def complete(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        session_id: UUID,
        checksum: str,
        parts: tuple[CompletedPart, ...],
    ) -> MediaAsset:
        if not _CHECKSUM.fullmatch(checksum.lower()):
            raise self._invalid()
        async with self._session.begin():
            await self._authorize(user_id, business_id, Permission.MEDIA_UPLOAD)
            upload = await self._active_session(business_id, session_id)
            self._valid_numbers(
                tuple(part.part_number for part in parts), upload.expected_part_count, exact=True
            )
            if any(not part.etag or len(part.etag) > 512 for part in parts):
                raise self._invalid()
            asset = await self._repo.get_asset(business_id, upload.asset_id, lock=True)
            if asset is None:
                raise self._not_found("MEDIA_ASSET_NOT_FOUND", "Media asset not found")
            if asset.sha256_checksum != checksum.lower():
                raise self._checksum_error()
            try:
                metadata = await self._storage.complete_upload(
                    storage_upload_id=upload.storage_upload_id, parts=parts
                )
            except StorageUnavailableError as error:
                raise self._storage_error() from error
            if (
                metadata.byte_size != asset.byte_size
                or metadata.content_type.lower() != asset.content_type
                or metadata.sha256_checksum.lower() != asset.sha256_checksum
            ):
                raise self._checksum_error()
            now = datetime.now(UTC)
            upload.status, upload.completed_at = UploadSessionStatus.COMPLETED, now
            asset.status, asset.uploaded_at = MediaAssetStatus.UPLOADED, now
            return asset

    async def cancel(self, *, user_id: UUID, business_id: UUID, session_id: UUID) -> None:
        async with self._session.begin():
            await self._authorize(user_id, business_id, Permission.MEDIA_UPLOAD)
            upload = await self._active_session(business_id, session_id)
            try:
                await self._storage.cancel_upload(storage_upload_id=upload.storage_upload_id)
            except StorageUnavailableError as error:
                raise self._storage_error() from error
            upload.status, upload.cancelled_at = UploadSessionStatus.CANCELLED, datetime.now(UTC)

    async def asset(self, *, user_id: UUID, business_id: UUID, asset_id: UUID) -> MediaAsset:
        await self._authorize(user_id, business_id, Permission.MEDIA_READ)
        asset = await self._repo.get_asset(business_id, asset_id)
        if asset is None:
            raise self._not_found("MEDIA_ASSET_NOT_FOUND", "Media asset not found")
        return asset

    async def _authorize(self, user_id: UUID, business_id: UUID, permission: Permission) -> None:
        membership = await self._businesses.get_active_membership(business_id, user_id)
        if membership is None:
            raise self._not_found("BUSINESS_NOT_FOUND", "Business not found")
        if not permits(membership.role, permission):
            raise ProblemException(
                status=403,
                code="AUTHORIZATION_DENIED",
                title="Forbidden",
                detail="You do not have this permission.",
            )
        business = await self._businesses.get_business(business_id)
        if business is None:
            raise self._not_found("BUSINESS_NOT_FOUND", "Business not found")
        if business.status != BusinessStatus.ACTIVE:
            raise ProblemException(
                status=409,
                code="BUSINESS_NOT_MUTABLE",
                title="Business is not mutable",
                detail="Suspended or archived businesses cannot be changed.",
            )

    async def _active_session(self, business_id: UUID, session_id: UUID) -> MediaUploadSession:
        upload = await self._repo.get_session(business_id, session_id, lock=True)
        if upload is None:
            raise self._not_found("UPLOAD_SESSION_NOT_FOUND", "Upload session not found")
        if datetime.now(UTC) >= upload.expires_at:
            raise ProblemException(
                status=409,
                code="UPLOAD_SESSION_EXPIRED",
                title="Upload session expired",
                detail="The upload session is no longer available.",
            )
        if upload.status != UploadSessionStatus.CREATED:
            raise ProblemException(
                status=409,
                code="RESOURCE_STATE_CONFLICT",
                title="Invalid upload session state",
                detail="The upload session is no longer available.",
            )
        return upload

    def _validate(
        self, filename: str, content_type: str, byte_size: int, checksum: str, part_count: int
    ) -> str:
        mime, extension = content_type.strip().lower(), PurePath(filename.strip()).suffix.lower()
        if (
            mime not in self._settings.media_allowed_mime_types
            or extension not in _EXTENSIONS.get(mime, set())
            or byte_size <= 0
            or byte_size > self._settings.media_max_bytes
            or part_count < 1
            or part_count > self._settings.media_max_parts
            or not _CHECKSUM.fullmatch(checksum.lower())
        ):
            raise self._invalid()
        return mime

    @staticmethod
    def _valid_numbers(numbers: tuple[int, ...], count: int, *, exact: bool) -> None:
        if (
            not numbers
            or len(set(numbers)) != len(numbers)
            or any(number < 1 or number > count for number in numbers)
            or (exact and set(numbers) != set(range(1, count + 1)))
        ):
            raise MediaService._invalid()

    @staticmethod
    def _invalid() -> ProblemException:
        return ProblemException(
            status=422,
            code="UPLOAD_METADATA_INVALID",
            title="Invalid upload metadata",
            detail="The upload metadata is not allowed.",
        )

    @staticmethod
    def _storage_error() -> ProblemException:
        return ProblemException(
            status=503,
            code="STORAGE_UNAVAILABLE",
            title="Storage unavailable",
            detail="The upload storage is temporarily unavailable.",
        )

    @staticmethod
    def _checksum_error() -> ProblemException:
        return ProblemException(
            status=409,
            code="UPLOAD_CHECKSUM_MISMATCH",
            title="Upload verification failed",
            detail="The uploaded object could not be verified.",
        )

    @staticmethod
    def _not_found(code: str, title: str) -> ProblemException:
        return ProblemException(
            status=404, code=code, title=title, detail="The requested resource is not available."
        )
