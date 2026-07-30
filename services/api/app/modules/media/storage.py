"""Provider-neutral multipart storage contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class UploadPartInstruction:
    part_number: int
    upload_url: str


@dataclass(frozen=True)
class CreatedUpload:
    """The result of opening a multipart upload.

    ``storage_upload_id`` is the provider's own multipart identifier; the caller persists it in
    ``media_upload_sessions.storage_upload_id`` and passes it back — together with the object key
    — on every later part/complete/cancel call. There is no server-side control object.
    """

    storage_upload_id: str
    instructions: tuple[UploadPartInstruction, ...]


@dataclass(frozen=True)
class CompletedPart:
    part_number: int
    etag: str


@dataclass(frozen=True)
class StoredObjectMetadata:
    byte_size: int
    content_type: str
    sha256_checksum: str
    etag: str = "fake-etag"


class StorageUnavailableError(RuntimeError):
    """Adapter failure without provider details."""


class StoragePermanentError(RuntimeError):
    """Adapter rejected an immutable object without provider details."""


class MultipartStoragePort(Protocol):
    async def create_upload(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_at: datetime,
        part_numbers: tuple[int, ...],
    ) -> CreatedUpload:
        """Open a multipart upload and return the provider's upload id with the part URLs.

        ``content_type`` is the server-validated declaration recorded on the asset. A real
        provider must stamp it on the object at creation time, because completion compares
        the stored content type against the asset instead of trusting the client again.
        """
        ...

    async def create_part_urls(
        self,
        *,
        object_key: str,
        storage_upload_id: str,
        expires_at: datetime,
        part_numbers: tuple[int, ...],
    ) -> tuple[UploadPartInstruction, ...]: ...
    async def complete_upload(
        self, *, object_key: str, storage_upload_id: str, parts: tuple[CompletedPart, ...]
    ) -> StoredObjectMetadata: ...
    async def get_object_metadata(self, *, object_key: str) -> StoredObjectMetadata: ...
    async def persist_file(
        self, *, object_key: str, source_path: Path, content_type: str
    ) -> StoredObjectMetadata: ...
    async def cancel_upload(self, *, object_key: str, storage_upload_id: str) -> None: ...
