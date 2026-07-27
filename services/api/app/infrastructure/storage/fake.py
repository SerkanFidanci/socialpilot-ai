"""Byte-free in-memory fake multipart storage adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.modules.media.storage import (
    CompletedPart,
    StorageUnavailableError,
    StoredObjectMetadata,
    UploadPartInstruction,
)


@dataclass
class _Upload:
    object_key: str
    expires_at: datetime
    parts: tuple[int, ...]
    uploaded: dict[int, str] = field(default_factory=dict)
    metadata: StoredObjectMetadata | None = None
    cancelled: bool = False
    unavailable: bool = False


class FakeMultipartStorage:
    def __init__(self) -> None:
        self._uploads: dict[str, _Upload] = {}
        self._objects: dict[str, StoredObjectMetadata] = {}
        self._unavailable_objects: set[str] = set()

    async def create_upload(
        self,
        *,
        storage_upload_id: str,
        object_key: str,
        expires_at: datetime,
        part_numbers: tuple[int, ...],
    ) -> tuple[UploadPartInstruction, ...]:
        self._uploads[storage_upload_id] = _Upload(object_key, expires_at, part_numbers)
        return await self.create_part_urls(
            storage_upload_id=storage_upload_id, expires_at=expires_at, part_numbers=part_numbers
        )

    async def create_part_urls(
        self, *, storage_upload_id: str, expires_at: datetime, part_numbers: tuple[int, ...]
    ) -> tuple[UploadPartInstruction, ...]:
        upload = self._get(storage_upload_id)
        if (
            upload.unavailable
            or upload.cancelled
            or datetime.now(UTC) >= expires_at
            or datetime.now(UTC) >= upload.expires_at
            or any(part not in upload.parts for part in part_numbers)
        ):
            raise StorageUnavailableError("upload unavailable")
        return tuple(
            UploadPartInstruction(
                part, f"https://fake-storage.invalid/upload/{storage_upload_id}/part/{part}"
            )
            for part in part_numbers
        )

    async def complete_upload(
        self, *, storage_upload_id: str, parts: tuple[CompletedPart, ...]
    ) -> StoredObjectMetadata:
        upload = self._get(storage_upload_id)
        if (
            upload.unavailable
            or upload.cancelled
            or datetime.now(UTC) >= upload.expires_at
            or {part.part_number: part.etag for part in parts} != upload.uploaded
            or upload.metadata is None
        ):
            raise StorageUnavailableError("completion unavailable")
        self._objects[upload.object_key] = upload.metadata
        return upload.metadata

    async def get_object_metadata(self, *, object_key: str) -> StoredObjectMetadata:
        if object_key in self._unavailable_objects:
            raise StorageUnavailableError("storage object unavailable")
        try:
            return self._objects[object_key]
        except KeyError as error:
            raise StorageUnavailableError("storage object unavailable") from error

    async def cancel_upload(self, *, storage_upload_id: str) -> None:
        self._get(storage_upload_id).cancelled = True

    def mark_uploaded_for_testing(
        self, *, storage_upload_id: str, parts: dict[int, str], metadata: StoredObjectMetadata
    ) -> None:
        upload = self._get(storage_upload_id)
        upload.uploaded, upload.metadata = parts, metadata

    def fail_for_testing(self, storage_upload_id: str) -> None:
        self._get(storage_upload_id).unavailable = True

    def set_object_metadata_for_testing(
        self, *, object_key: str, metadata: StoredObjectMetadata
    ) -> None:
        self._objects[object_key] = metadata

    def fail_object_for_testing(self, object_key: str) -> None:
        self._unavailable_objects.add(object_key)

    def _get(self, storage_upload_id: str) -> _Upload:
        try:
            return self._uploads[storage_upload_id]
        except KeyError as error:
            raise StorageUnavailableError("storage upload unavailable") from error
