"""A render adapter that writes placeholder files instead of encoding video.

This exists so unit tests can exercise the render *service* — job claiming, storage
persistence, failure classification, disclosure and provenance recording — without spending
CPU on FFmpeg or requiring the binary to be present. It is refused in production by both
`Settings` and `create_render`.

It is deliberately not a silent stand-in: it declares the same capabilities as the real
adapter, so a timeline that this fake accepts is one the real adapter would also accept, and a
test cannot pass against a capability set the production path does not have.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.modules.content.render import (
    ProvenanceState,
    RenderCapabilities,
    RenderedArtifact,
    RenderPermanentError,
    RenderPort,
    RenderProfile,
    RenderRequest,
    RenderResult,
    RenderSummary,
    profile_spec,
)
from app.modules.content.timeline import AudioTrackKind, CaptionSource, CropMode, TransitionKind

_MAX_DURATION_MS = 10 * 60 * 1000


class FakeRenderAdapter(RenderPort):
    """Produce byte-bearing placeholder artifacts with a truthful technical summary."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self._fail_with = fail_with

    @property
    def capabilities(self) -> RenderCapabilities:
        return RenderCapabilities(
            profiles=frozenset(RenderProfile),
            crop_modes=frozenset(CropMode),
            transitions=frozenset({TransitionKind.CUT}),
            # `voiceover` joins `original` in slice 2E, matching the FFmpeg adapter exactly. The
            # point of this fake is that a timeline it accepts is one the real adapter accepts;
            # a capability set that drifted would make every test here a test of nothing.
            audio_sources=frozenset({AudioTrackKind.ORIGINAL, AudioTrackKind.VOICEOVER}),
            caption_sources=frozenset({CaptionSource.TRANSCRIPT}),
            max_duration_ms=_MAX_DURATION_MS,
            max_video_tracks=1,
            supports_provenance_manifest=False,
        )

    async def render(self, *, request: RenderRequest) -> RenderResult:
        if self._fail_with is not None:
            raise self._fail_with
        workdir = request.workdir
        if not workdir.is_dir():
            raise RenderPermanentError("RENDER_WORKDIR_INVALID")
        spec = profile_spec(request.plan.profile)
        return RenderResult(
            master=_write(workdir / "master.mp4", "master", "video/mp4"),
            preview=_write(workdir / "preview.mp4", "preview", "video/mp4"),
            thumbnail=_write(workdir / "thumbnail.jpg", "thumbnail", "image/jpeg"),
            summary=RenderSummary(
                # The plan's own duration, so a test asserting "the output is as long as the
                # cuts asked for" is testing the service's arithmetic, not this fake's.
                duration_ms=max(1, request.plan.duration_ms),
                width=spec.width,
                height=spec.height,
                video_codec="h264",
                audio_codec="aac",
            ),
            provenance=ProvenanceState.STRIPPED_PENDING_REATTACH,
        )


def _write(path: Path, kind: str, content_type: str) -> RenderedArtifact:
    payload = f"fake-render:{kind}".encode()
    path.write_bytes(payload)
    return RenderedArtifact(
        kind=kind,
        path=path,
        content_type=content_type,
        byte_size=len(payload),
        sha256_checksum=hashlib.sha256(payload).hexdigest(),
    )
