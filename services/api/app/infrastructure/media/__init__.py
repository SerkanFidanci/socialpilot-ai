"""Media-processing adapters and worker-input selection."""

from __future__ import annotations

from app.core.config import Settings
from app.infrastructure.media.fake_ingest import FakeMediaMaterializer
from app.infrastructure.media.s3_materializer import S3MediaMaterializer
from app.modules.media.technical import MediaMaterializerPort

__all__ = ["FakeMediaMaterializer", "S3MediaMaterializer", "create_materializer"]


def create_materializer(settings: Settings) -> MediaMaterializerPort:
    """Build the configured worker-input adapter; the fixture fake never reaches production.

    ``Settings`` already refuses ``fake`` under ``production``. This second check keeps the
    guarantee at the composition root, matching ``create_storage``.
    """

    if settings.materializer_adapter == "s3":
        return S3MediaMaterializer(settings)
    if settings.app_env == "production":
        raise RuntimeError("MATERIALIZER_PRODUCTION_ADAPTER_NOT_CONFIGURED")
    return FakeMediaMaterializer(allow_missing_for_testing=True)
