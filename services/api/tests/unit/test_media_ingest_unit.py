"""Fast unit coverage for Phase 1A metadata and safe fake adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.infrastructure.media.fake_ingest import FakeContentInspector, FakeMalwareScanner
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.modules.media.ingest import IngestValidationError, MediaIngestService
from app.modules.media.models import MalwareScanStatus
from app.modules.media.storage import StoredObjectMetadata


def service() -> MediaIngestService:
    return MediaIngestService(
        AsyncMock(),
        Settings(
            database_url="postgresql+asyncpg://test:test@localhost:5432/test",
            redis_url="redis://localhost:6379/0",
            celery_broker_url="redis://localhost:6379/1",
            celery_result_backend="redis://localhost:6379/2",
        ),
        FakeMultipartStorage(),
        FakeContentInspector(),
        FakeMalwareScanner(),
    )


def test_ingest_metadata_validation_rejects_size_checksum_and_content_type_mismatches() -> None:
    expected = StoredObjectMetadata(128, "video/mp4", "a" * 64, "etag-a")
    for actual, code in (
        (StoredObjectMetadata(129, "video/mp4", "a" * 64, "etag-b"), "INGEST_SIZE_MISMATCH"),
        (StoredObjectMetadata(128, "video/mp4", "b" * 64, "etag-b"), "UPLOAD_CHECKSUM_MISMATCH"),
        (
            StoredObjectMetadata(128, "image/jpeg", "a" * 64, "etag-b"),
            "INGEST_CONTENT_TYPE_MISMATCH",
        ),
    ):
        with pytest.raises(IngestValidationError) as error:
            service()._validate_metadata(expected, actual)
        assert error.value.code == code


@pytest.mark.asyncio
async def test_fake_ingest_adapters_are_safe_and_configurable() -> None:
    inspector, scanner = FakeContentInspector(), FakeMalwareScanner()
    object_key = "tenant/opaque/media/opaque/original/opaque"
    assert (
        await inspector.inspect(object_key=object_key, timeout_seconds=1)
    ).detected_content_type == "video/mp4"
    scanner.set_result_for_testing(object_key=object_key, status=MalwareScanStatus.INFECTED)
    assert (
        await scanner.scan(object_key=object_key, timeout_seconds=1) == MalwareScanStatus.INFECTED
    )
