"""Provider-neutral multipart storage contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class UploadPartInstruction:
    part_number: int
    upload_url: str


@dataclass(frozen=True)
class CompletedPart:
    part_number: int
    etag: str


@dataclass(frozen=True)
class StoredObjectMetadata:
    byte_size: int
    content_type: str
    sha256_checksum: str


class StorageUnavailableError(RuntimeError):
    """Adapter failure without provider details."""


class MultipartStoragePort(Protocol):
    async def create_upload(
        self,
        *,
        storage_upload_id: str,
        object_key: str,
        expires_at: datetime,
        part_numbers: tuple[int, ...],
    ) -> tuple[UploadPartInstruction, ...]: ...
    async def create_part_urls(
        self, *, storage_upload_id: str, expires_at: datetime, part_numbers: tuple[int, ...]
    ) -> tuple[UploadPartInstruction, ...]: ...
    async def complete_upload(
        self, *, storage_upload_id: str, parts: tuple[CompletedPart, ...]
    ) -> StoredObjectMetadata: ...
    async def cancel_upload(self, *, storage_upload_id: str) -> None: ...
