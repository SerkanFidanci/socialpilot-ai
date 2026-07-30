"""Parametric timeline edits (K4): a closed set of operations over an existing document.

The product decision this file implements is that a user never drags pixels. Editing means
changing named parameters of a document that is already JSON — the text in a slot, which of
nine anchors holds it, which style token it uses, where a clip is cut. Free x/y placement and
frame-by-frame assembly are out (PRD §3.3, plan §2).

That restriction is not an interface preference, it is what makes §18.3 enforceable. A
forbidden-word rule, a safe-area rule and a verified-price rule can all be checked exactly when
the edit space is this small; none of them survives contact with arbitrary composition. The
second benefit is cost: applying a patch makes no provider call at all, so a revision is
arithmetic rather than a bill.

Applying a patch produces a *new* `Timeline` value. It never mutates, never validates, and
never renders — the caller re-runs the full §18.3 validation over the result, because an edit
that was legal to express can still be illegal to render.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final
from uuid import UUID

from app.modules.content.timeline import (
    LOGO_STYLES,
    MAX_LITERAL_TEXT_CHARS,
    MIN_CLIP_DURATION_MS,
    TEXT_STYLES,
    Clip,
    OverlayAnchor,
    OverlayKind,
    TextSource,
    Timeline,
    TimelineSchemaError,
    VideoTrack,
)

MAX_PATCH_OPERATIONS: Final = 20


@dataclass(frozen=True, slots=True)
class SetOverlayText:
    index: int
    text_source: TextSource
    text: str | None
    reference_id: UUID | None


@dataclass(frozen=True, slots=True)
class SetOverlayAnchor:
    index: int
    anchor: OverlayAnchor


@dataclass(frozen=True, slots=True)
class SetOverlayStyle:
    index: int
    style_id: str


@dataclass(frozen=True, slots=True)
class SetClipRange:
    track_index: int
    clip_index: int
    source_start_ms: int
    source_end_ms: int


@dataclass(frozen=True, slots=True)
class SetCaptions:
    enabled: bool
    style_id: str | None


PatchOperation = SetOverlayText | SetOverlayAnchor | SetOverlayStyle | SetClipRange | SetCaptions


def parse_patch(document: Any) -> tuple[PatchOperation, ...]:
    """Parse an untrusted patch body into operations, or raise `TimelineSchemaError`."""

    if not isinstance(document, Sequence) or isinstance(document, str | bytes):
        raise TimelineSchemaError("PATCH_FIELD_INVALID", "$")
    if not document:
        raise TimelineSchemaError("PATCH_EMPTY", "$")
    if len(document) > MAX_PATCH_OPERATIONS:
        raise TimelineSchemaError("PATCH_TOO_MANY_OPERATIONS", "$")
    return tuple(_parse_operation(entry, f"$[{index}]") for index, entry in enumerate(document))


def _parse_operation(entry: Any, pointer: str) -> PatchOperation:
    if not isinstance(entry, Mapping):
        raise TimelineSchemaError("PATCH_FIELD_INVALID", pointer)
    operation = entry.get("op")
    parsers: dict[str, Callable[[Mapping[str, Any], str], PatchOperation]] = {
        "set_overlay_text": _parse_set_overlay_text,
        "set_overlay_anchor": _parse_set_overlay_anchor,
        "set_overlay_style": _parse_set_overlay_style,
        "set_clip_range": _parse_set_clip_range,
        "set_captions": _parse_set_captions,
    }
    parser = parsers.get(operation) if isinstance(operation, str) else None
    if parser is None:
        raise TimelineSchemaError("PATCH_OPERATION_UNKNOWN", f"{pointer}.op")
    return parser(entry, pointer)


def _keys(entry: Mapping[str, Any], allowed: set[str], pointer: str) -> None:
    unknown = sorted(set(entry) - allowed - {"op"})
    if unknown:
        raise TimelineSchemaError("PATCH_UNKNOWN_FIELD", f"{pointer}.{unknown[0]}")


def _index(entry: Mapping[str, Any], key: str, pointer: str) -> int:
    value = entry.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TimelineSchemaError("PATCH_FIELD_INVALID", f"{pointer}.{key}")
    return value


def _parse_set_overlay_text(entry: Mapping[str, Any], pointer: str) -> SetOverlayText:
    _keys(entry, {"index", "text_source", "text", "reference_id"}, pointer)
    raw_source = entry.get("text_source")
    try:
        source = TextSource(raw_source) if isinstance(raw_source, str) else None
    except ValueError:
        source = None
    if source is None:
        raise TimelineSchemaError("PATCH_FIELD_INVALID", f"{pointer}.text_source")
    text = entry.get("text")
    raw_reference = entry.get("reference_id")
    reference: UUID | None = None
    if raw_reference is not None:
        try:
            reference = UUID(raw_reference) if isinstance(raw_reference, str) else None
        except ValueError:
            reference = None
        if reference is None:
            raise TimelineSchemaError("PATCH_FIELD_INVALID", f"{pointer}.reference_id")
    if source is TextSource.LITERAL:
        if not isinstance(text, str) or not text.strip():
            raise TimelineSchemaError("PATCH_FIELD_INVALID", f"{pointer}.text")
        if len(text) > MAX_LITERAL_TEXT_CHARS:
            raise TimelineSchemaError("PATCH_FIELD_OUT_OF_RANGE", f"{pointer}.text")
        if reference is not None:
            raise TimelineSchemaError("TIMELINE_LITERAL_WITH_REFERENCE", f"{pointer}.reference_id")
    else:
        # The same rule the schema enforces on creation: a verified slot takes a reference, and
        # prose offered alongside it is refused rather than dropped. Without this, a patch would
        # be the way around the "a model never writes a price" guarantee.
        if text is not None:
            raise TimelineSchemaError("TIMELINE_VERIFIED_FIELD_NOT_LITERAL", f"{pointer}.text")
        if reference is None:
            raise TimelineSchemaError(
                "TIMELINE_VERIFIED_REFERENCE_MISSING", f"{pointer}.reference_id"
            )
    return SetOverlayText(
        index=_index(entry, "index", pointer),
        text_source=source,
        text=text if isinstance(text, str) else None,
        reference_id=reference,
    )


def _parse_set_overlay_anchor(entry: Mapping[str, Any], pointer: str) -> SetOverlayAnchor:
    _keys(entry, {"index", "anchor"}, pointer)
    raw = entry.get("anchor")
    try:
        anchor = OverlayAnchor(raw) if isinstance(raw, str) else None
    except ValueError:
        anchor = None
    if anchor is None:
        raise TimelineSchemaError("PATCH_FIELD_INVALID", f"{pointer}.anchor")
    return SetOverlayAnchor(index=_index(entry, "index", pointer), anchor=anchor)


def _parse_set_overlay_style(entry: Mapping[str, Any], pointer: str) -> SetOverlayStyle:
    _keys(entry, {"index", "style_id"}, pointer)
    style_id = entry.get("style_id")
    if not isinstance(style_id, str) or style_id not in (TEXT_STYLES.keys() | LOGO_STYLES.keys()):
        raise TimelineSchemaError("TIMELINE_STYLE_TOKEN_UNKNOWN", f"{pointer}.style_id")
    return SetOverlayStyle(index=_index(entry, "index", pointer), style_id=style_id)


def _parse_set_clip_range(entry: Mapping[str, Any], pointer: str) -> SetClipRange:
    _keys(entry, {"track_index", "clip_index", "source_start_ms", "source_end_ms"}, pointer)
    start = _index(entry, "source_start_ms", pointer)
    end = _index(entry, "source_end_ms", pointer)
    if end - start < MIN_CLIP_DURATION_MS:
        raise TimelineSchemaError("TIMELINE_CLIP_TOO_SHORT", pointer)
    return SetClipRange(
        track_index=_index(entry, "track_index", pointer),
        clip_index=_index(entry, "clip_index", pointer),
        source_start_ms=start,
        source_end_ms=end,
    )


def _parse_set_captions(entry: Mapping[str, Any], pointer: str) -> SetCaptions:
    _keys(entry, {"enabled", "style_id"}, pointer)
    enabled = entry.get("enabled")
    if not isinstance(enabled, bool):
        raise TimelineSchemaError("PATCH_FIELD_INVALID", f"{pointer}.enabled")
    style_id = entry.get("style_id")
    if style_id is not None and (not isinstance(style_id, str) or style_id not in TEXT_STYLES):
        raise TimelineSchemaError("TIMELINE_STYLE_TOKEN_UNKNOWN", f"{pointer}.style_id")
    return SetCaptions(enabled=enabled, style_id=style_id)


# --- application ---------------------------------------------------------------------------


def apply_patch(
    timeline: Timeline,
    operations: Sequence[PatchOperation],
    *,
    snap_points: Mapping[UUID, tuple[int, ...]],
    snap_tolerance_ms: int,
) -> Timeline:
    """Apply operations in order and return the resulting document.

    Raises `TimelineSchemaError` when an operation addresses something that is not there — an
    overlay index past the end, a text edit aimed at a logo. Out-of-range edits are refused
    rather than ignored so a client cannot believe a change landed when it did not.
    """

    result = timeline
    for position, operation in enumerate(operations):
        pointer = f"$[{position}]"
        match operation:
            case SetOverlayText():
                result = _apply_overlay_text(result, operation, pointer)
            case SetOverlayAnchor():
                result = _apply_overlay_anchor(result, operation, pointer)
            case SetOverlayStyle():
                result = _apply_overlay_style(result, operation, pointer)
            case SetClipRange():
                result = _apply_clip_range(
                    result, operation, pointer, snap_points, snap_tolerance_ms
                )
            case SetCaptions():
                result = _apply_captions(result, operation)
    return result


def _overlay_at(timeline: Timeline, index: int, pointer: str) -> int:
    if index >= len(timeline.overlays):
        raise TimelineSchemaError("PATCH_TARGET_NOT_FOUND", f"{pointer}.index")
    return index


def _apply_overlay_text(timeline: Timeline, operation: SetOverlayText, pointer: str) -> Timeline:
    index = _overlay_at(timeline, operation.index, pointer)
    overlay = timeline.overlays[index]
    if overlay.kind is not OverlayKind.TEXT:
        raise TimelineSchemaError("PATCH_TARGET_NOT_TEXT", f"{pointer}.index")
    updated = replace(
        overlay,
        text_source=operation.text_source,
        text=operation.text,
        reference_id=operation.reference_id,
    )
    return _with_overlay(timeline, index, updated)


def _apply_overlay_anchor(
    timeline: Timeline, operation: SetOverlayAnchor, pointer: str
) -> Timeline:
    index = _overlay_at(timeline, operation.index, pointer)
    return _with_overlay(
        timeline, index, replace(timeline.overlays[index], anchor=operation.anchor)
    )


def _apply_overlay_style(timeline: Timeline, operation: SetOverlayStyle, pointer: str) -> Timeline:
    index = _overlay_at(timeline, operation.index, pointer)
    overlay = timeline.overlays[index]
    registry = TEXT_STYLES if overlay.kind is OverlayKind.TEXT else LOGO_STYLES
    if operation.style_id not in registry:
        raise TimelineSchemaError("TIMELINE_STYLE_TOKEN_UNKNOWN", f"{pointer}.style_id")
    return _with_overlay(timeline, index, replace(overlay, style_id=operation.style_id))


def _with_overlay(timeline: Timeline, index: int, overlay: Any) -> Timeline:
    overlays = list(timeline.overlays)
    overlays[index] = overlay
    return replace(timeline, overlays=tuple(overlays))


def _apply_clip_range(
    timeline: Timeline,
    operation: SetClipRange,
    pointer: str,
    snap_points: Mapping[UUID, tuple[int, ...]],
    snap_tolerance_ms: int,
) -> Timeline:
    if operation.track_index >= len(timeline.video_tracks):
        raise TimelineSchemaError("PATCH_TARGET_NOT_FOUND", f"{pointer}.track_index")
    track = timeline.video_tracks[operation.track_index]
    if operation.clip_index >= len(track.clips):
        raise TimelineSchemaError("PATCH_TARGET_NOT_FOUND", f"{pointer}.clip_index")
    clip = track.clips[operation.clip_index]
    boundaries = snap_points.get(clip.asset_id, ())
    start = _snap(operation.source_start_ms, boundaries, snap_tolerance_ms)
    end = _snap(operation.source_end_ms, boundaries, snap_tolerance_ms)
    if end - start < MIN_CLIP_DURATION_MS:
        # Snapping can pull two nearby cut points together; refuse rather than emit a clip the
        # renderer would treat as an empty segment.
        raise TimelineSchemaError("TIMELINE_CLIP_TOO_SHORT", pointer)
    clips = list(track.clips)
    clips[operation.clip_index] = replace(clip, source_start_ms=start, source_end_ms=end)
    tracks = list(timeline.video_tracks)
    tracks[operation.track_index] = VideoTrack(track=track.track, clips=_relayout(clips))
    updated = replace(timeline, video_tracks=tuple(tracks))
    return replace(updated, canvas=replace(updated.canvas, duration_ms=_content_duration(updated)))


def _apply_captions(timeline: Timeline, operation: SetCaptions) -> Timeline:
    """Toggle burned-in captions and optionally restyle them.

    Omitting `style_id` keeps the current token rather than resetting to a default, so turning
    captions off and on again does not silently discard a style choice.
    """

    captions = replace(
        timeline.captions,
        enabled=operation.enabled,
        style_id=operation.style_id or timeline.captions.style_id,
    )
    return replace(timeline, captions=captions)


def _snap(value: int, boundaries: tuple[int, ...], tolerance_ms: int) -> int:
    """Pull a cut point onto the nearest detected scene boundary when one is close.

    Cutting mid-motion looks like a mistake; the analysis pipeline already knows where the
    scene changes are, so an edit within tolerance of one is treated as meaning that boundary.
    Beyond the tolerance the caller's exact value is kept — snapping is an assist, not a rule.
    """

    if not boundaries:
        return value
    nearest = min(boundaries, key=lambda boundary: (abs(boundary - value), boundary))
    return nearest if abs(nearest - value) <= tolerance_ms else value


def _relayout(clips: Sequence[Clip]) -> tuple[Clip, ...]:
    """Re-pack a track contiguously from its first clip's start.

    Changing one clip's length would otherwise leave a hole or an overlap behind it. The
    document keeps no independent notion of intended gaps in this slice, so the honest
    behaviour is to close up the track and let the canvas duration follow.
    """

    if not clips:
        return ()
    cursor = clips[0].timeline_start_ms
    packed: list[Clip] = []
    for clip in clips:
        packed.append(replace(clip, timeline_start_ms=cursor))
        cursor += clip.duration_ms
    return tuple(packed)


def _content_duration(timeline: Timeline) -> int:
    return max(
        (clip.timeline_end_ms for track in timeline.video_tracks for clip in track.clips),
        default=timeline.canvas.duration_ms,
    )
