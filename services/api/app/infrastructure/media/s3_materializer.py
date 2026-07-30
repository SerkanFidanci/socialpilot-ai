"""Real worker-input adapter: stream storage bytes to the worker scratch directory.

The materializer is the bridge between the direct-upload byte path (owned by the storage
adapter) and the FFprobe/FFmpeg workers. It reuses ``S3MultipartStorage`` for signing and
error mapping — the system must not carry a second SigV4 implementation — and only adds the
download-to-file streaming and mandatory scratch cleanup on top of it. It returns a local
``Path`` and never exposes a signed URL, credential, or provider type. See ADR-009.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings
from app.infrastructure.storage.s3 import S3MultipartStorage

# A short, lowercase, alphanumeric suffix copied from the object key only. FFprobe/FFmpeg
# identify inputs by content, so the extension is cosmetic; this keeps a hostile key from
# steering the destination name.
_SAFE_SUFFIX = re.compile(r"^\.[a-z0-9]{1,8}$")


def _destination_name(object_key: str) -> str:
    suffix = Path(object_key).suffix.lower()
    return f"materialized{suffix}" if _SAFE_SUFFIX.fullmatch(suffix) else "materialized"


class S3MediaMaterializer:
    """Stream a tenant object to worker scratch, cleaning up any partial file on failure."""

    def __init__(self, settings: Settings, *, storage: S3MultipartStorage | None = None) -> None:
        self._storage = storage or S3MultipartStorage(settings)
        # The ceiling must cover every object the worker materializes: originals plus the
        # proxies and audio the pipeline writes back. It mirrors the adapter's own streamed
        # verification ceiling.
        self._max_bytes = max(
            settings.media_max_bytes,
            settings.media_max_derivative_bytes,
            settings.media_max_extracted_audio_bytes,
        )

    async def materialize(self, *, object_key: str, workdir: Path) -> Path:
        workdir.mkdir(parents=True, exist_ok=True)
        destination = workdir / _destination_name(object_key)
        try:
            await self._storage.download_to_path(
                object_key=object_key, destination=destination, max_bytes=self._max_bytes
            )
        except BaseException:
            # No partial file may survive an error, cancellation, or timeout (PRD §19.3).
            destination.unlink(missing_ok=True)
            raise
        return destination
