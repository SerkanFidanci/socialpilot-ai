"""Render adapter selection, mirroring `create_storage` and `create_materializer`."""

from __future__ import annotations

from app.core.config import Settings
from app.infrastructure.render.fake import FakeRenderAdapter
from app.infrastructure.render.ffmpeg import FFmpegRenderAdapter
from app.modules.content.render import RenderPort

__all__ = ["FFmpegRenderAdapter", "FakeRenderAdapter", "create_render"]


def create_render(settings: Settings) -> RenderPort:
    """Build the configured render adapter; the placeholder fake never reaches production.

    ``Settings`` already refuses ``fake`` under ``production``. This second check keeps the
    guarantee at the composition root, matching the storage and materializer factories: a
    deployment that renders placeholder files instead of video would look healthy in every
    metric while shipping nothing, so it has to be impossible rather than unlikely.
    """

    if settings.render_adapter == "ffmpeg":
        return FFmpegRenderAdapter(settings)
    if settings.app_env == "production":
        raise RuntimeError("RENDER_PRODUCTION_ADAPTER_NOT_CONFIGURED")
    return FakeRenderAdapter()
