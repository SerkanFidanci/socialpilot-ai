"""The render capability port (K5) and the provider-neutral plan it consumes.

`RenderPort` is a first-class capability port, not a wrapper around FFmpeg. That distinction
is the whole point of this module: rendering is the one part of the pipeline whose deployment
shape is still open (single-server FFmpeg today, a managed render service or burst compute at
volume — STATUS K5, ADR-013). If FFmpeg calls were reachable from the domain, that choice
would be made permanently and by accident.

So the boundary is drawn where it can be defended:

- The port receives a `RenderPlan`: fully resolved, provider-neutral, and already validated.
  Every verified value has been read from a tenant record, every source is a local file the
  worker materialized, and no signed URL, credential, or storage key crosses the boundary.
- The port declares `RenderCapabilities`. Validation checks the timeline against them *before*
  a render starts, so an unsupported transition is a documented rejection rather than a job
  that fails halfway through. PRD §19.2 asks for exactly this ("platform limitleri adapter
  capability endpoint'inden kontrol edilmelidir").
- The port reports what it did to provenance. Re-encoding strips a C2PA manifest, and an
  adapter that cannot preserve one has to say so rather than leave the caller guessing.

No executable code in `app/modules/content/` names a render provider, imports from
`app/infrastructure/`, or spawns a process. A unit test tokenizes the module and enforces
exactly that — prose explaining the boundary is fine, coupling to it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol
from uuid import UUID

from app.modules.content.timeline import (
    AudioTrackKind,
    Canvas,
    CaptionSource,
    CropMode,
    OverlayAnchor,
    TextStyle,
    TransitionKind,
)


class RenderTransientError(RuntimeError):
    """A render failed for a reason that may not recur; the job may retry."""


class RenderPermanentError(RuntimeError):
    """A render failed for a reason retrying cannot fix; the job must not retry."""


class RenderProfile(StrEnum):
    """PRD §19.2's target profiles, verbatim."""

    INSTAGRAM_REELS_1080X1920 = "instagram_reels_1080x1920"
    INSTAGRAM_STORY_1080X1920 = "instagram_story_1080x1920"
    INSTAGRAM_FEED_1080X1350 = "instagram_feed_1080x1350"
    INSTAGRAM_SQUARE_1080X1080 = "instagram_square_1080x1080"
    X_VIDEO_1280X720 = "x_video_1280x720"
    X_VERTICAL_1080X1920 = "x_vertical_1080x1920"
    PREVIEW_540X960 = "preview_540x960"


class AiDisclosureState(StrEnum):
    """Whether a render's output contains AI-generated or AI-modified material.

    Meta has required AI disclosure on FB/IG advertising since July 2026 and treats undeclared
    AI content as grounds for rejecting an ad — a platform policy, so it binds in Türkiye too
    (99-external-platform-facts.md). The field therefore exists from the first render rather
    than arriving with the first model call: a record written today as `none` is trustworthy,
    while a column back-filled later is not.
    """

    NONE = "none"
    AI_GENERATED = "ai_generated"
    AI_MODIFIED = "ai_modified"
    UNKNOWN = "unknown"


class ProvenanceState(StrEnum):
    """What happened to the output's C2PA provenance manifest during rendering.

    A C2PA manifest does not survive re-encoding, and every output of this pipeline is
    re-encoded. An adapter that strips provenance reports `stripped_pending_reattach`, which
    leaves a queryable set of outputs awaiting a signing step. Writing and signing the manifest
    is a separate job (it needs a certificate) and is out of this slice; the hook is not.
    """

    ABSENT = "absent"
    STRIPPED_PENDING_REATTACH = "stripped_pending_reattach"
    ATTACHED = "attached"


@dataclass(frozen=True, slots=True)
class SafeArea:
    """Fractional insets marking where text may not be placed.

    These are **our** conservative margins, not published platform geometry: vertical feeds
    cover the frame's edges with their own interface, and the numbers below keep overlays
    clear of that region with room to spare. They are product-tunable; treating them as
    platform facts would be a claim this repository is not allowed to make from memory.
    """

    top: float
    bottom: float
    left: float
    right: float

    def box(self, *, width: int, height: int) -> tuple[int, int, int, int]:
        """Return the permitted `(x0, y0, x1, y1)` rectangle in pixels."""

        return (
            round(width * self.left),
            round(height * self.top),
            round(width * (1.0 - self.right)),
            round(height * (1.0 - self.bottom)),
        )


@dataclass(frozen=True, slots=True)
class RenderProfileSpec:
    width: int
    height: int
    fps: int
    safe_area: SafeArea


# Vertical feeds hide the most frame behind their interface, so they carry the largest insets.
_VERTICAL_SAFE_AREA: Final = SafeArea(top=0.14, bottom=0.20, left=0.06, right=0.06)
_SQUARE_SAFE_AREA: Final = SafeArea(top=0.08, bottom=0.12, left=0.06, right=0.06)
_LANDSCAPE_SAFE_AREA: Final = SafeArea(top=0.06, bottom=0.10, left=0.05, right=0.05)

RENDER_PROFILES: Final[dict[RenderProfile, RenderProfileSpec]] = {
    RenderProfile.INSTAGRAM_REELS_1080X1920: RenderProfileSpec(1080, 1920, 30, _VERTICAL_SAFE_AREA),
    RenderProfile.INSTAGRAM_STORY_1080X1920: RenderProfileSpec(1080, 1920, 30, _VERTICAL_SAFE_AREA),
    RenderProfile.INSTAGRAM_FEED_1080X1350: RenderProfileSpec(1080, 1350, 30, _SQUARE_SAFE_AREA),
    RenderProfile.INSTAGRAM_SQUARE_1080X1080: RenderProfileSpec(1080, 1080, 30, _SQUARE_SAFE_AREA),
    RenderProfile.X_VIDEO_1280X720: RenderProfileSpec(1280, 720, 30, _LANDSCAPE_SAFE_AREA),
    RenderProfile.X_VERTICAL_1080X1920: RenderProfileSpec(1080, 1920, 30, _VERTICAL_SAFE_AREA),
    # The preview profile is PRD §15.5's proxy logic applied to output: small enough to review
    # on a phone over mobile data, same geometry as the vertical master.
    RenderProfile.PREVIEW_540X960: RenderProfileSpec(540, 960, 30, _VERTICAL_SAFE_AREA),
}

PREVIEW_PROFILE: Final = RenderProfile.PREVIEW_540X960


def profile_spec(profile: RenderProfile) -> RenderProfileSpec:
    return RENDER_PROFILES[profile]


@dataclass(frozen=True, slots=True)
class RenderCapabilities:
    """What one adapter can actually do. Validation rejects anything outside this set."""

    profiles: frozenset[RenderProfile]
    crop_modes: frozenset[CropMode]
    transitions: frozenset[TransitionKind]
    audio_sources: frozenset[AudioTrackKind]
    caption_sources: frozenset[CaptionSource]
    max_duration_ms: int
    max_video_tracks: int
    supports_provenance_manifest: bool


# --- the plan ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlannedSegment:
    """One clip, resolved to a local file and an exact source window."""

    asset_id: UUID
    source_path: Path
    source_start_ms: int
    source_end_ms: int
    crop_mode: CropMode
    transition_out: TransitionKind
    # Whether the source carries an audio stream. Known from technical analysis, so an adapter
    # never has to probe for it — and an adapter that concatenates cuts has to know, because a
    # set of clips where only some have audio cannot be joined without deciding what to do.
    has_audio: bool

    @property
    def duration_ms(self) -> int:
        return self.source_end_ms - self.source_start_ms


@dataclass(frozen=True, slots=True)
class PlannedText:
    """Text whose content is already final — resolved, checked, and safe to draw.

    `text` may still be hostile: it can be a tenant's literal string or a value copied out of a
    campaign record. Adapters must never interpolate it into a command or filter expression.
    """

    text: str
    style: TextStyle
    anchor: OverlayAnchor
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class PlannedLogo:
    source_path: Path
    anchor: OverlayAnchor
    width_ratio: float
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class PlannedCaption:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class PlannedVoiceover:
    """Synthesized speech, already materialized as local files, in the order it is spoken.

    One path per script line, because slice 2C stores one object per line. The adapter joins
    them; the plan does not, because joining is an encode and encodes live below this boundary.
    """

    segment_paths: tuple[Path, ...]
    gain_db: int


@dataclass(frozen=True, slots=True)
class PlannedAudio:
    """The bed, and the voice laid over it.

    `source` names the bed — the footage's own sound today; music needs a licence record and is
    not a supported source yet. `voiceover` is `None` for a timeline that places no speech, which
    is what keeps every render that existed before slice 2E byte-identical.

    `duck_under_voice` is the timeline's own per-track flag, read from the bed. PRD §18.2 shows it
    on a music track; it means "hold this down while the voice speaks", and with music
    unsupported the footage is the track that has to give way.
    """

    source: AudioTrackKind
    gain_db: int
    voiceover: PlannedVoiceover | None = None
    duck_under_voice: bool = False


@dataclass(frozen=True, slots=True)
class RenderPlan:
    """Everything a render needs, with nothing left to resolve and nothing to fetch."""

    profile: RenderProfile
    canvas: Canvas
    segments: tuple[PlannedSegment, ...]
    texts: tuple[PlannedText, ...]
    logos: tuple[PlannedLogo, ...]
    captions: tuple[PlannedCaption, ...]
    caption_style: TextStyle
    audio: PlannedAudio
    ai_disclosure: AiDisclosureState

    @property
    def duration_ms(self) -> int:
        return sum(segment.duration_ms for segment in self.segments)


@dataclass(frozen=True, slots=True)
class RenderRequest:
    plan: RenderPlan
    workdir: Path
    preview_profile: RenderProfile
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class RenderedArtifact:
    kind: str
    path: Path
    content_type: str
    byte_size: int
    sha256_checksum: str


@dataclass(frozen=True, slots=True)
class RenderSummary:
    """The technical description of the master output, observed rather than assumed."""

    duration_ms: int
    width: int
    height: int
    video_codec: str
    audio_codec: str | None


@dataclass(frozen=True, slots=True)
class RenderResult:
    master: RenderedArtifact
    preview: RenderedArtifact
    thumbnail: RenderedArtifact
    summary: RenderSummary
    provenance: ProvenanceState


class RenderPort(Protocol):
    """Turn a validated plan into a master, a preview and a thumbnail."""

    @property
    def capabilities(self) -> RenderCapabilities: ...

    async def render(self, *, request: RenderRequest) -> RenderResult: ...
