"""The timeline document: PRD §18.2's schema as a parsed, closed, pure value object.

Three properties of this module are load-bearing and none of them are stylistic.

**The document is parsed, never trusted.** A timeline arrives as JSON from an HTTP client
today and from a script-generation model in slice 2B. Both are untrusted. `parse_timeline`
accepts exactly the keys PRD §18.2 defines and rejects every unknown key, so a field nobody
reviewed cannot ride along in a stored document and reappear in the renderer later.

**Rejecting unknown keys is how the parametric-editing decision (K4) is enforced structurally
rather than by review.** A free `{"x": 120, "y": 400}` on an overlay is not "ignored" — it is a
parse error. Position is a nine-cell grid anchor, style is a token from a closed registry, and
text is either a literal or a reference to a verified record. There is no coordinate space to
escape into, so the safe-area and forbidden-word rules in `validation.py` cannot be walked
around by a client that simply declines to use the safe fields.

**Nothing here touches a database, a provider, or a clock.** Parsing and serialization are
pure and total, which is what lets the same code run at the API boundary, inside the patch
path, and again in the worker before a render starts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

TIMELINE_VERSION: Final = "1.0"

# Bounds exist so a hostile or broken document cannot turn into an unbounded filter graph.
# They are generous for real content and small enough that the worst case still renders on the
# single server of ADR-013.
MAX_VIDEO_TRACKS: Final = 4
MAX_CLIPS_PER_TRACK: Final = 60
MAX_AUDIO_TRACKS: Final = 4
MAX_OVERLAYS: Final = 20
MAX_CANVAS_DURATION_MS: Final = 10 * 60 * 1000
MAX_LITERAL_TEXT_CHARS: Final = 200
MIN_CLIP_DURATION_MS: Final = 100


class TimelineSchemaError(ValueError):
    """The document is not a timeline. Carries a documented code, never raw input.

    The rejected value is deliberately absent from the message: a timeline can hold text
    copied out of an uploaded video, and echoing it back into an error body would hand
    untrusted content a path into logs and API responses.
    """

    def __init__(self, code: str, pointer: str) -> None:
        super().__init__(f"{code} at {pointer}")
        self.code = code
        self.pointer = pointer


class CropMode(StrEnum):
    """How a source clip is fitted to a canvas of a different aspect ratio."""

    SMART_COVER = "smart_cover"
    BLUR_PAD = "blur_pad"
    CONTAIN = "contain"


class TransitionKind(StrEnum):
    CUT = "cut"
    FADE = "fade"


class AudioTrackKind(StrEnum):
    """PRD §18.2 names voiceover and music; `original` is the source clip's own audio."""

    ORIGINAL = "original"
    VOICEOVER = "voiceover"
    MUSIC = "music"


class OverlayKind(StrEnum):
    TEXT = "text"
    LOGO = "logo"


class OverlayAnchor(StrEnum):
    """The nine-cell grid (K4). This is the entire position space — there is no x/y."""

    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    MIDDLE_LEFT = "middle_left"
    MIDDLE_CENTER = "middle_center"
    MIDDLE_RIGHT = "middle_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class TextSource(StrEnum):
    """Where an overlay's text comes from. The values are PRD §18.2's dotted reference form.

    Everything except `LITERAL` resolves from a tenant's own verified records and cannot be
    typed by a user or produced by a model (PRD §2.2, §11.3). A literal is still checked
    against the brand's forbidden lists before it can render.
    """

    LITERAL = "literal"
    VERIFIED_CAMPAIGN_TITLE = "verified_campaign.title"
    VERIFIED_CAMPAIGN_LEGAL_TEXT = "verified_campaign.legal_text"
    VERIFIED_PRODUCT_PRICE = "verified_product.price"
    VERIFIED_CTA_TEXT = "verified_cta.text"

    @property
    def is_verified(self) -> bool:
        return self is not TextSource.LITERAL


class CaptionSource(StrEnum):
    """`voiceover` is PRD §18.2's value; it needs slice 2C's TTS output to resolve."""

    TRANSCRIPT = "transcript"
    VOICEOVER = "voiceover"


@dataclass(frozen=True, slots=True)
class Canvas:
    width: int
    height: int
    fps: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class Clip:
    asset_id: UUID
    source_start_ms: int
    source_end_ms: int
    timeline_start_ms: int
    crop_mode: CropMode
    transition_out: TransitionKind

    @property
    def duration_ms(self) -> int:
        return self.source_end_ms - self.source_start_ms

    @property
    def timeline_end_ms(self) -> int:
        return self.timeline_start_ms + self.duration_ms


@dataclass(frozen=True, slots=True)
class VideoTrack:
    track: int
    clips: tuple[Clip, ...]


@dataclass(frozen=True, slots=True)
class AudioTrack:
    kind: AudioTrackKind
    asset_id: UUID | None
    gain_db: int
    duck_under_voice: bool


@dataclass(frozen=True, slots=True)
class Overlay:
    """One text or logo overlay, positioned by anchor and styled by token only."""

    kind: OverlayKind
    start_ms: int
    end_ms: int
    anchor: OverlayAnchor
    safe_area: bool
    style_id: str
    text_source: TextSource | None
    text: str | None
    reference_id: UUID | None
    asset_id: UUID | None


@dataclass(frozen=True, slots=True)
class Captions:
    enabled: bool
    source: CaptionSource
    style_id: str


@dataclass(frozen=True, slots=True)
class Timeline:
    version: str
    canvas: Canvas
    video_tracks: tuple[VideoTrack, ...]
    audio_tracks: tuple[AudioTrack, ...]
    overlays: tuple[Overlay, ...]
    captions: Captions

    @property
    def clips(self) -> tuple[Clip, ...]:
        return tuple(clip for track in self.video_tracks for clip in track.clips)

    @property
    def asset_ids(self) -> tuple[UUID, ...]:
        """Every distinct **media asset** the document references, in first-seen order.

        A `voiceover` track's `asset_id` is deliberately not here: it names a `voiceover_assets`
        row produced by slice 2C, not an uploaded `media_assets` row. Including it would make
        the worker try to materialize a voiceover as a source video and would make validation
        look it up in the wrong table — both failures of the "not accessible" kind, reported
        about the wrong thing.
        """

        seen: dict[UUID, None] = {}
        for clip in self.clips:
            seen.setdefault(clip.asset_id, None)
        for track in self.audio_tracks:
            if track.asset_id is not None and track.kind is not AudioTrackKind.VOICEOVER:
                seen.setdefault(track.asset_id, None)
        for overlay in self.overlays:
            if overlay.asset_id is not None:
                seen.setdefault(overlay.asset_id, None)
        return tuple(seen)

    @property
    def voiceover_ids(self) -> tuple[UUID, ...]:
        """Every voiceover the document places, in first-seen order."""

        seen: dict[UUID, None] = {}
        for track in self.audio_tracks:
            if track.kind is AudioTrackKind.VOICEOVER and track.asset_id is not None:
                seen.setdefault(track.asset_id, None)
        return tuple(seen)


# --- style tokens ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextStyle:
    """A resolved style token. Font size is relative to canvas height, not absolute pixels.

    Styles are a closed registry rather than free font/colour values (K4): the safe-area rule
    can only be enforced deterministically if the renderer knows the text extent in advance,
    and a caller who may pass an arbitrary font size can always push text off-canvas.
    """

    font_height_ratio: float
    colour: str
    border_colour: str
    border_width: int
    # Mean advance width per character as a fraction of font height. DejaVu Sans sits near
    # 0.55; the value is rounded up so the estimated box is never narrower than the drawn one
    # and the safe-area check errs toward rejection.
    advance_ratio: float = 0.60


TEXT_STYLES: Final[Mapping[str, TextStyle]] = {
    "brand-title-v1": TextStyle(
        font_height_ratio=0.052, colour="white", border_colour="black", border_width=3
    ),
    "brand-caption-v1": TextStyle(
        font_height_ratio=0.034, colour="white", border_colour="black", border_width=2
    ),
    "brand-price-v1": TextStyle(
        font_height_ratio=0.044, colour="white", border_colour="black", border_width=3
    ),
}

LOGO_STYLES: Final[Mapping[str, float]] = {
    "logo-small": 0.10,
    "logo-medium": 0.16,
    "logo-large": 0.24,
}


# --- parsing -----------------------------------------------------------------------------


def _mapping(value: Any, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TimelineSchemaError("TIMELINE_FIELD_INVALID", pointer)
    return value


def _closed(payload: Mapping[str, Any], allowed: frozenset[str], pointer: str) -> None:
    """Reject any key the schema does not define.

    This is where a raw coordinate, an unreviewed style override, or a stray field from a
    future schema version stops. Silently ignoring unknown keys would make the document's
    meaning depend on the reader's version, and would let `{"x": ..., "y": ...}` sit in a
    stored timeline looking like it does something.
    """

    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise TimelineSchemaError("TIMELINE_UNKNOWN_FIELD", f"{pointer}.{unknown[0]}")


def _int(payload: Mapping[str, Any], key: str, pointer: str, *, minimum: int, maximum: int) -> int:
    value = payload.get(key)
    # `bool` is an `int` subclass; accepting it here would let `true` mean 1 in a duration.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TimelineSchemaError("TIMELINE_FIELD_INVALID", f"{pointer}.{key}")
    if value < minimum or value > maximum:
        raise TimelineSchemaError("TIMELINE_FIELD_OUT_OF_RANGE", f"{pointer}.{key}")
    return value


def _bool(
    payload: Mapping[str, Any], key: str, pointer: str, *, default: bool | None = None
) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise TimelineSchemaError("TIMELINE_FIELD_INVALID", f"{pointer}.{key}")
    return value


def _enum_value[E: StrEnum](
    payload: Mapping[str, Any],
    key: str,
    pointer: str,
    enum_type: type[E],
    *,
    default: E | None = None,
) -> E:
    raw = payload.get(key, default.value if default is not None else None)
    if isinstance(raw, enum_type):
        return raw
    if not isinstance(raw, str):
        raise TimelineSchemaError("TIMELINE_FIELD_INVALID", f"{pointer}.{key}")
    try:
        return enum_type(raw)
    except ValueError:
        raise TimelineSchemaError("TIMELINE_FIELD_INVALID", f"{pointer}.{key}") from None


def _uuid(payload: Mapping[str, Any], key: str, pointer: str) -> UUID:
    raw = payload.get(key)
    if isinstance(raw, UUID):
        return raw
    if not isinstance(raw, str):
        raise TimelineSchemaError("TIMELINE_FIELD_INVALID", f"{pointer}.{key}")
    try:
        return UUID(raw)
    except ValueError:
        raise TimelineSchemaError("TIMELINE_FIELD_INVALID", f"{pointer}.{key}") from None


def _optional_uuid(payload: Mapping[str, Any], key: str, pointer: str) -> UUID | None:
    return None if payload.get(key) is None else _uuid(payload, key, pointer)


def _sequence(payload: Mapping[str, Any], key: str, pointer: str, *, maximum: int) -> Sequence[Any]:
    value = payload.get(key, ())
    # A string is a Sequence; excluding it keeps `"abc"` from parsing as three entries.
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TimelineSchemaError("TIMELINE_FIELD_INVALID", f"{pointer}.{key}")
    if len(value) > maximum:
        raise TimelineSchemaError("TIMELINE_TOO_MANY_ENTRIES", f"{pointer}.{key}")
    return value


def _style_id(payload: Mapping[str, Any], pointer: str, *, registry: Mapping[str, Any]) -> str:
    raw = payload.get("style_id")
    if not isinstance(raw, str) or raw not in registry:
        raise TimelineSchemaError("TIMELINE_STYLE_TOKEN_UNKNOWN", f"{pointer}.style_id")
    return raw


def parse_timeline(document: Any) -> Timeline:
    """Parse an untrusted document into a `Timeline`, or raise `TimelineSchemaError`.

    Structural only: this proves the document *is* a timeline. Whether it is a *renderable*
    timeline — clips inside their sources, text inside the safe area, no forbidden word — is
    `validation.py`'s job, because those answers need the tenant's assets and brand records.
    """

    payload = _mapping(document, "$")
    _closed(
        payload,
        frozenset({"version", "canvas", "video_tracks", "audio_tracks", "overlays", "captions"}),
        "$",
    )
    if payload.get("version") != TIMELINE_VERSION:
        raise TimelineSchemaError("TIMELINE_VERSION_UNSUPPORTED", "$.version")
    return Timeline(
        version=TIMELINE_VERSION,
        canvas=_parse_canvas(payload.get("canvas"), "$.canvas"),
        video_tracks=_parse_video_tracks(payload, "$"),
        audio_tracks=_parse_audio_tracks(payload, "$"),
        overlays=_parse_overlays(payload, "$"),
        captions=_parse_captions(payload.get("captions"), "$.captions"),
    )


def _parse_canvas(value: Any, pointer: str) -> Canvas:
    payload = _mapping(value, pointer)
    _closed(payload, frozenset({"width", "height", "fps", "duration_ms"}), pointer)
    return Canvas(
        width=_int(payload, "width", pointer, minimum=16, maximum=4_096),
        height=_int(payload, "height", pointer, minimum=16, maximum=4_096),
        fps=_int(payload, "fps", pointer, minimum=1, maximum=60),
        duration_ms=_int(
            payload, "duration_ms", pointer, minimum=1, maximum=MAX_CANVAS_DURATION_MS
        ),
    )


def _parse_video_tracks(payload: Mapping[str, Any], pointer: str) -> tuple[VideoTrack, ...]:
    raw_tracks = _sequence(payload, "video_tracks", pointer, maximum=MAX_VIDEO_TRACKS)
    if not raw_tracks:
        raise TimelineSchemaError("TIMELINE_NO_VIDEO_TRACK", f"{pointer}.video_tracks")
    tracks: list[VideoTrack] = []
    for index, raw in enumerate(raw_tracks):
        track_pointer = f"{pointer}.video_tracks[{index}]"
        track_payload = _mapping(raw, track_pointer)
        _closed(track_payload, frozenset({"track", "clips"}), track_pointer)
        raw_clips = _sequence(track_payload, "clips", track_pointer, maximum=MAX_CLIPS_PER_TRACK)
        if not raw_clips:
            raise TimelineSchemaError("TIMELINE_NO_CLIP", f"{track_pointer}.clips")
        tracks.append(
            VideoTrack(
                track=_int(
                    track_payload, "track", track_pointer, minimum=1, maximum=MAX_VIDEO_TRACKS
                ),
                clips=tuple(
                    _parse_clip(clip, f"{track_pointer}.clips[{clip_index}]")
                    for clip_index, clip in enumerate(raw_clips)
                ),
            )
        )
    numbers = [track.track for track in tracks]
    if len(set(numbers)) != len(numbers):
        raise TimelineSchemaError("TIMELINE_DUPLICATE_TRACK", f"{pointer}.video_tracks")
    return tuple(tracks)


def _parse_clip(value: Any, pointer: str) -> Clip:
    payload = _mapping(value, pointer)
    _closed(
        payload,
        frozenset(
            {
                "asset_id",
                "source_start_ms",
                "source_end_ms",
                "timeline_start_ms",
                "crop_mode",
                "transition_out",
            }
        ),
        pointer,
    )
    clip = Clip(
        asset_id=_uuid(payload, "asset_id", pointer),
        source_start_ms=_int(
            payload, "source_start_ms", pointer, minimum=0, maximum=MAX_CANVAS_DURATION_MS
        ),
        source_end_ms=_int(
            payload, "source_end_ms", pointer, minimum=1, maximum=MAX_CANVAS_DURATION_MS
        ),
        timeline_start_ms=_int(
            payload, "timeline_start_ms", pointer, minimum=0, maximum=MAX_CANVAS_DURATION_MS
        ),
        crop_mode=_enum_value(
            payload, "crop_mode", pointer, CropMode, default=CropMode.SMART_COVER
        ),
        transition_out=_enum_value(
            payload, "transition_out", pointer, TransitionKind, default=TransitionKind.CUT
        ),
    )
    if clip.duration_ms < MIN_CLIP_DURATION_MS:
        raise TimelineSchemaError("TIMELINE_CLIP_TOO_SHORT", pointer)
    return clip


def _parse_audio_tracks(payload: Mapping[str, Any], pointer: str) -> tuple[AudioTrack, ...]:
    raw_tracks = _sequence(payload, "audio_tracks", pointer, maximum=MAX_AUDIO_TRACKS)
    tracks: list[AudioTrack] = []
    for index, raw in enumerate(raw_tracks):
        track_pointer = f"{pointer}.audio_tracks[{index}]"
        track_payload = _mapping(raw, track_pointer)
        _closed(
            track_payload,
            frozenset({"type", "asset_id", "gain_db", "duck_under_voice"}),
            track_pointer,
        )
        tracks.append(
            AudioTrack(
                kind=_enum_value(track_payload, "type", track_pointer, AudioTrackKind),
                asset_id=_optional_uuid(track_payload, "asset_id", track_pointer),
                gain_db=_int(track_payload, "gain_db", track_pointer, minimum=-60, maximum=12),
                duck_under_voice=_bool(
                    track_payload, "duck_under_voice", track_pointer, default=False
                ),
            )
        )
    kinds = [track.kind for track in tracks]
    if len(set(kinds)) != len(kinds):
        raise TimelineSchemaError("TIMELINE_DUPLICATE_AUDIO_TRACK", f"{pointer}.audio_tracks")
    return tuple(tracks)


def _parse_overlays(payload: Mapping[str, Any], pointer: str) -> tuple[Overlay, ...]:
    raw_overlays = _sequence(payload, "overlays", pointer, maximum=MAX_OVERLAYS)
    return tuple(
        _parse_overlay(raw, f"{pointer}.overlays[{index}]")
        for index, raw in enumerate(raw_overlays)
    )


def _parse_overlay(value: Any, pointer: str) -> Overlay:
    payload = _mapping(value, pointer)
    kind = _enum_value(payload, "type", pointer, OverlayKind)
    if kind is OverlayKind.TEXT:
        return _parse_text_overlay(payload, pointer)
    return _parse_logo_overlay(payload, pointer)


def _parse_text_overlay(payload: Mapping[str, Any], pointer: str) -> Overlay:
    _closed(
        payload,
        frozenset(
            {
                "type",
                "text_source",
                "text",
                "reference_id",
                "anchor",
                "style_id",
                "start_ms",
                "end_ms",
                "safe_area",
            }
        ),
        pointer,
    )
    source = _enum_value(payload, "text_source", pointer, TextSource)
    text = payload.get("text")
    reference_id = _optional_uuid(payload, "reference_id", pointer)
    if source is TextSource.LITERAL:
        if not isinstance(text, str) or not text.strip():
            raise TimelineSchemaError("TIMELINE_FIELD_INVALID", f"{pointer}.text")
        if len(text) > MAX_LITERAL_TEXT_CHARS:
            raise TimelineSchemaError("TIMELINE_FIELD_OUT_OF_RANGE", f"{pointer}.text")
        if reference_id is not None:
            raise TimelineSchemaError("TIMELINE_LITERAL_WITH_REFERENCE", f"{pointer}.reference_id")
    else:
        # A verified slot resolves from a record. Supplying prose alongside the reference is
        # the exact move that would let an invented price ride into the frame, so it is a
        # parse error rather than a value the resolver quietly discards.
        if text is not None:
            raise TimelineSchemaError("TIMELINE_VERIFIED_FIELD_NOT_LITERAL", f"{pointer}.text")
        if reference_id is None:
            raise TimelineSchemaError(
                "TIMELINE_VERIFIED_REFERENCE_MISSING", f"{pointer}.reference_id"
            )
    return Overlay(
        kind=OverlayKind.TEXT,
        start_ms=_int(payload, "start_ms", pointer, minimum=0, maximum=MAX_CANVAS_DURATION_MS),
        end_ms=_int(payload, "end_ms", pointer, minimum=1, maximum=MAX_CANVAS_DURATION_MS),
        anchor=_enum_value(payload, "anchor", pointer, OverlayAnchor),
        safe_area=_bool(payload, "safe_area", pointer, default=True),
        style_id=_style_id(payload, pointer, registry=TEXT_STYLES),
        text_source=source,
        text=text if isinstance(text, str) else None,
        reference_id=reference_id,
        asset_id=None,
    )


def _parse_logo_overlay(payload: Mapping[str, Any], pointer: str) -> Overlay:
    _closed(
        payload,
        frozenset({"type", "asset_id", "anchor", "style_id", "start_ms", "end_ms", "safe_area"}),
        pointer,
    )
    return Overlay(
        kind=OverlayKind.LOGO,
        start_ms=_int(payload, "start_ms", pointer, minimum=0, maximum=MAX_CANVAS_DURATION_MS),
        end_ms=_int(payload, "end_ms", pointer, minimum=1, maximum=MAX_CANVAS_DURATION_MS),
        anchor=_enum_value(payload, "anchor", pointer, OverlayAnchor),
        safe_area=_bool(payload, "safe_area", pointer, default=True),
        style_id=_style_id(payload, pointer, registry=LOGO_STYLES),
        text_source=None,
        text=None,
        reference_id=None,
        asset_id=_uuid(payload, "asset_id", pointer),
    )


def _parse_captions(value: Any, pointer: str) -> Captions:
    payload = _mapping(value, pointer)
    _closed(payload, frozenset({"enabled", "source", "style_id"}), pointer)
    return Captions(
        enabled=_bool(payload, "enabled", pointer, default=False),
        source=_enum_value(
            payload, "source", pointer, CaptionSource, default=CaptionSource.TRANSCRIPT
        ),
        style_id=_style_id(payload, pointer, registry=TEXT_STYLES),
    )


# --- serialization -----------------------------------------------------------------------


def serialize_timeline(timeline: Timeline) -> dict[str, Any]:
    """Render the document back to JSON-safe primitives for JSONB storage and API output.

    `parse_timeline(serialize_timeline(t)) == t` for every parsed timeline; a unit test holds
    that round trip, because the patch path reads, edits and writes documents through it.
    """

    return {
        "version": timeline.version,
        "canvas": {
            "width": timeline.canvas.width,
            "height": timeline.canvas.height,
            "fps": timeline.canvas.fps,
            "duration_ms": timeline.canvas.duration_ms,
        },
        "video_tracks": [
            {
                "track": track.track,
                "clips": [
                    {
                        "asset_id": str(clip.asset_id),
                        "source_start_ms": clip.source_start_ms,
                        "source_end_ms": clip.source_end_ms,
                        "timeline_start_ms": clip.timeline_start_ms,
                        "crop_mode": clip.crop_mode.value,
                        "transition_out": clip.transition_out.value,
                    }
                    for clip in track.clips
                ],
            }
            for track in timeline.video_tracks
        ],
        "audio_tracks": [
            {
                "type": track.kind.value,
                "asset_id": None if track.asset_id is None else str(track.asset_id),
                "gain_db": track.gain_db,
                "duck_under_voice": track.duck_under_voice,
            }
            for track in timeline.audio_tracks
        ],
        "overlays": [_serialize_overlay(overlay) for overlay in timeline.overlays],
        "captions": {
            "enabled": timeline.captions.enabled,
            "source": timeline.captions.source.value,
            "style_id": timeline.captions.style_id,
        },
    }


def _serialize_overlay(overlay: Overlay) -> dict[str, Any]:
    common: dict[str, Any] = {
        "type": overlay.kind.value,
        "anchor": overlay.anchor.value,
        "style_id": overlay.style_id,
        "start_ms": overlay.start_ms,
        "end_ms": overlay.end_ms,
        "safe_area": overlay.safe_area,
    }
    if overlay.kind is OverlayKind.LOGO:
        return common | {"asset_id": str(overlay.asset_id)}
    source = overlay.text_source or TextSource.LITERAL
    return common | {
        "text_source": source.value,
        "text": overlay.text,
        "reference_id": None if overlay.reference_id is None else str(overlay.reference_id),
    }
