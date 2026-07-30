"""Object-storage adapter selection for the direct-upload byte path."""

from __future__ import annotations

from app.core.config import Settings
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.infrastructure.storage.s3 import S3MultipartStorage
from app.modules.media.storage import MultipartStoragePort

__all__ = ["FakeMultipartStorage", "S3MultipartStorage", "create_storage"]


def create_storage(settings: Settings) -> MultipartStoragePort:
    """Build the configured adapter; the byte-free fake never reaches production.

    ``Settings`` already rejects ``fake`` under ``production``. This second check keeps the
    guarantee at the composition root, matching the worker's adapter guard.
    """

    if settings.storage_adapter == "s3":
        return S3MultipartStorage(settings)
    if settings.app_env == "production":
        raise RuntimeError("STORAGE_PRODUCTION_ADAPTER_NOT_CONFIGURED")
    return FakeMultipartStorage()
