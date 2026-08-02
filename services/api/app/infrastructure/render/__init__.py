"""Render adapter selection, mirroring `create_storage` and `create_materializer`."""

from __future__ import annotations

from app.core.config import Settings
from app.infrastructure.render.fake import FakeRenderAdapter
from app.infrastructure.render.ffmpeg import FFmpegRenderAdapter
from app.infrastructure.render.qc_probe import FFmpegQcProbe
from app.modules.content.qc import MediaQcProbePort
from app.modules.content.render import RenderPort

__all__ = [
    "FFmpegQcProbe",
    "FFmpegRenderAdapter",
    "FakeRenderAdapter",
    "create_qc_probe",
    "create_render",
]


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


def create_qc_probe(settings: Settings) -> MediaQcProbePort:
    """Build the QC measurement adapter. There is no fixture, deliberately.

    `create_audio_probe` made the same call in slice 2C and the reasoning carries over exactly:
    this port *is* the check that nobody's account of the output is taken at face value, so a
    fixture probe would be a fixture verifying a fixture and every quality claim in the system
    would rest on it. It runs in development and in production alike, and when it cannot run the
    checks that depend on it are `unknown` rather than absent.

    There is a visible consequence in the test suite, and it is the right one: the placeholder
    render adapter writes a file that is not video, so measuring its output fails and the report
    says so. A fake probe would have made that combination look healthy.
    """

    return FFmpegQcProbe(settings)
