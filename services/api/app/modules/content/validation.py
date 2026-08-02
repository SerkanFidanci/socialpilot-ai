"""Pre-render timeline validation (PRD §18.3): deterministic code, never a model.

Every rule here is ordinary arithmetic and set membership over facts already read from the
tenant's own rows. Nothing consults a provider, and nothing is advisory — a timeline that
fails validation does not reach the renderer, so a bad clip range or a forbidden word costs
zero render CPU rather than being discovered in the output.

Two design choices carry most of the weight.

**Validation is a pure function over a context the caller assembled.** The database reads live
in the repository; `validate_timeline` sees only values. That is what makes the same rules
runnable at the API boundary, again after a patch, and a third time in the worker immediately
before rendering — with no chance of the three disagreeing.

**Validation resolves the text it checks, and hands it back.** `ValidationOutcome.resolved_texts`
is the exact set of strings that will be drawn. If the plan builder re-resolved text on its
own, a verified price could pass the forbidden-word check and a different value could reach the
frame; here that is structurally impossible.

**The forbidden-term gate is the script detector's, imported (slice 2D).** Four rounds of
adversarial review closed a class of bypasses on the script side — invisible characters,
confusables, NFD spellings, Latin letters wearing a diacritic, decorated digits — by folding
every candidate and every pattern to one ASCII form and refusing letters the fold cannot spell.
This file used to run its own `re.IGNORECASE` matcher over raw text, which meant every one of
those bypasses was still open on the timeline: the same banned claim that `parse_text` refuses
in a script could be typed into an overlay with a zero-width space in it and drawn on the frame.
It now calls `script.forbidden_matcher` over `normalize_for_matching`, and refuses text carrying
an unfoldable letter through `contains_unsupported_letter` — the same two functions, not a
second copy of them. A second implementation is how the two gates would drift apart again, and
`ṬL` is what that drift looked like from the outside.

Inflection is deliberately **not** matched (PM, W18): a brand forbidding `şeker` does not
thereby forbid `şekerli`. The list belongs to the brand and the pattern belongs to us; stemming
would let our rule ban words the brand never named, and the existing pin — `az` forbidden,
`lezzetli` free — is the shape of that promise.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.modules.content.render import (
    RenderCapabilities,
    RenderProfile,
    SafeArea,
    profile_spec,
)
from app.modules.content.text_normalization import (
    contains_unsupported_letter,
    normalize_for_matching,
)
from app.modules.content.timeline import (
    TEXT_STYLES,
    AudioTrack,
    AudioTrackKind,
    Overlay,
    OverlayKind,
    TextSource,
    TextStyle,
    Timeline,
)

# Aspect ratios are compared with a tolerance rather than for equality: 1080x1350 is 0.8 and a
# caller computing from a rounded height should not be rejected over a rounding artefact.
_ASPECT_TOLERANCE = 0.02
# A line box is taller than the glyphs it holds; ascender plus descender plus leading lands
# near 1.3x the nominal font height for the fonts this pipeline ships.
_LINE_BOX_RATIO = 1.30
# Beyond three lines an overlay stops being an overlay and starts being a paragraph competing
# with the footage. The limit is a product choice, not a technical one.
MAX_TEXT_LINES = 3


def wrap_text(text: str, *, max_chars: int) -> list[str]:
    """Greedy word wrap at a character budget.

    This is the single definition of how a caption breaks. Validation uses it to decide whether
    text fits the safe area, and the render plan carries the wrapped result verbatim — so the
    lines that were measured are exactly the lines that get drawn. A word longer than the budget
    is left intact on its own line and reported as overflowing rather than being cut in half.
    """

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if not current or len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True, slots=True)
class AssetFacts:
    """What validation needs to know about one source asset, already tenant-scoped.

    A caller can only obtain these through a repository that filters by `business_id`, so an
    asset belonging to another tenant is simply absent from the mapping and fails the
    accessibility rule. There is no cross-tenant code path to get wrong.
    """

    asset_id: UUID
    duration_ms: int | None
    width: int | None
    height: int | None
    has_audio: bool
    renderable: bool
    source_object_key: str | None


@dataclass(frozen=True, slots=True)
class VoiceoverFacts:
    """What validation needs to know about one voiceover (slice 2C), already tenant-scoped.

    `duration_ms` is the sum of ffprobe measurements taken when the audio was produced, never a
    provider's declaration. §18.3's "seslendirme süresi" rule compares it against the canvas, and
    a rule that compared an unverified number would be theatre.
    """

    voiceover_id: UUID
    usable: bool
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class VerifiedValue:
    """A value resolved from a verified record, with its validity window if it has one."""

    text: str
    within_window: bool


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """The tenant facts the rules need, gathered once by the caller."""

    assets: Mapping[UUID, AssetFacts]
    logo_asset_ids: frozenset[UUID]
    forbidden_terms: tuple[str, ...]
    verified_values: Mapping[tuple[str, UUID], VerifiedValue]
    now: datetime
    # Keyed by voiceover id. Empty by default so every existing caller keeps compiling and every
    # timeline without a voiceover track behaves exactly as before.
    voiceovers: Mapping[UUID, VoiceoverFacts] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One documented rejection. The offending value is never included."""

    code: str
    pointer: str


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    issues: tuple[ValidationIssue, ...]
    resolved_texts: Mapping[int, str]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


def validate_timeline(
    timeline: Timeline,
    *,
    context: ValidationContext,
    capabilities: RenderCapabilities,
    profile: RenderProfile,
    min_resolution_ratio: float,
) -> ValidationOutcome:
    """Run every §18.3 rule and return all failures at once.

    Collecting rather than short-circuiting is deliberate: a caller fixing a timeline should
    see every problem in one response instead of rediscovering them one render at a time.
    """

    issues: list[ValidationIssue] = []
    resolved: dict[int, str] = {}

    issues.extend(_check_profile(timeline, capabilities=capabilities, profile=profile))
    issues.extend(_check_durations(timeline, capabilities=capabilities))
    issues.extend(_check_clips(timeline, context=context, capabilities=capabilities))
    issues.extend(
        _check_resolution(timeline, context=context, profile=profile, ratio=min_resolution_ratio)
    )
    issues.extend(_check_audio(timeline, capabilities=capabilities, context=context))
    issues.extend(_check_captions(timeline, capabilities=capabilities))
    issues.extend(_check_overlays(timeline, context=context, profile=profile, resolved=resolved))
    return ValidationOutcome(issues=tuple(issues), resolved_texts=resolved)


# --- profile and capability -----------------------------------------------------------------


def _check_profile(
    timeline: Timeline, *, capabilities: RenderCapabilities, profile: RenderProfile
) -> list[ValidationIssue]:
    if profile not in capabilities.profiles:
        return [ValidationIssue("RENDER_PROFILE_UNSUPPORTED", "$.profile")]
    spec = profile_spec(profile)
    canvas_aspect = timeline.canvas.width / timeline.canvas.height
    target_aspect = spec.width / spec.height
    if abs(canvas_aspect - target_aspect) > _ASPECT_TOLERANCE:
        return [ValidationIssue("TIMELINE_ASPECT_RATIO_MISMATCH", "$.canvas")]
    return []


def _check_durations(
    timeline: Timeline, *, capabilities: RenderCapabilities
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if timeline.canvas.duration_ms > capabilities.max_duration_ms:
        issues.append(ValidationIssue("TIMELINE_DURATION_OVERFLOW", "$.canvas.duration_ms"))
    if len(timeline.video_tracks) > capabilities.max_video_tracks:
        issues.append(ValidationIssue("TIMELINE_TOO_MANY_VIDEO_TRACKS", "$.video_tracks"))
    for track_index, track in enumerate(timeline.video_tracks):
        for clip_index, clip in enumerate(track.clips):
            if clip.timeline_end_ms > timeline.canvas.duration_ms:
                issues.append(
                    ValidationIssue(
                        "TIMELINE_DURATION_OVERFLOW",
                        f"$.video_tracks[{track_index}].clips[{clip_index}]",
                    )
                )
    for overlay_index, overlay in enumerate(timeline.overlays):
        if overlay.end_ms > timeline.canvas.duration_ms or overlay.start_ms >= overlay.end_ms:
            issues.append(
                ValidationIssue("TIMELINE_OVERLAY_WINDOW_INVALID", f"$.overlays[{overlay_index}]")
            )
    return issues


def _check_clips(
    timeline: Timeline, *, context: ValidationContext, capabilities: RenderCapabilities
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: set[tuple[UUID, int, int]] = set()
    for track_index, track in enumerate(timeline.video_tracks):
        previous_end = 0
        for clip_index, clip in enumerate(track.clips):
            pointer = f"$.video_tracks[{track_index}].clips[{clip_index}]"
            fingerprint = (clip.asset_id, clip.source_start_ms, clip.source_end_ms)
            if fingerprint in seen:
                issues.append(ValidationIssue("TIMELINE_DUPLICATE_CLIP", pointer))
            seen.add(fingerprint)

            if clip.crop_mode not in capabilities.crop_modes:
                issues.append(ValidationIssue("TIMELINE_UNSUPPORTED_CROP_MODE", pointer))
            if clip.transition_out not in capabilities.transitions:
                issues.append(ValidationIssue("TIMELINE_UNSUPPORTED_TRANSITION", pointer))
            if clip.timeline_start_ms < previous_end:
                issues.append(ValidationIssue("TIMELINE_CLIP_OVERLAP", pointer))
            previous_end = clip.timeline_end_ms

            facts = context.assets.get(clip.asset_id)
            if facts is None:
                # Absent means "not this tenant's, or not there at all" — the repository never
                # returns another business's rows, so cross-tenant reuse lands here.
                issues.append(ValidationIssue("TIMELINE_ASSET_NOT_ACCESSIBLE", pointer))
                continue
            if not facts.renderable or facts.source_object_key is None:
                issues.append(ValidationIssue("TIMELINE_ASSET_NOT_RENDERABLE", pointer))
                continue
            if facts.duration_ms is None or clip.source_end_ms > facts.duration_ms:
                issues.append(ValidationIssue("TIMELINE_CLIP_RANGE_INVALID", pointer))
    return issues


def _check_resolution(
    timeline: Timeline, *, context: ValidationContext, profile: RenderProfile, ratio: float
) -> list[ValidationIssue]:
    """Refuse sources too small to fill the target without visible upscaling."""

    spec = profile_spec(profile)
    minimum_short_edge = min(spec.width, spec.height) * ratio
    issues: list[ValidationIssue] = []
    for track_index, track in enumerate(timeline.video_tracks):
        for clip_index, clip in enumerate(track.clips):
            facts = context.assets.get(clip.asset_id)
            if facts is None or facts.width is None or facts.height is None:
                continue
            if min(facts.width, facts.height) < minimum_short_edge:
                issues.append(
                    ValidationIssue(
                        "TIMELINE_RESOLUTION_TOO_LOW",
                        f"$.video_tracks[{track_index}].clips[{clip_index}]",
                    )
                )
    return issues


def _check_audio(
    timeline: Timeline, *, capabilities: RenderCapabilities, context: ValidationContext
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, track in enumerate(timeline.audio_tracks):
        pointer = f"$.audio_tracks[{index}]"
        supported = track.kind in capabilities.audio_sources
        if not supported:
            issues.append(ValidationIssue("TIMELINE_UNSUPPORTED_AUDIO_SOURCE", pointer))
        if track.kind is AudioTrackKind.VOICEOVER:
            # Run regardless of the capability answer. Both statements are independently true —
            # "this adapter cannot mix speech yet" and "this speech does not fit the canvas" —
            # and collecting both is what lets the duration rule be a real rule rather than
            # something that only exists once some adapter grows a filter.
            issues.extend(
                _check_voiceover(track, timeline=timeline, context=context, pointer=pointer)
            )
            continue
        if not supported:
            continue
        if track.kind is AudioTrackKind.ORIGINAL:
            if track.asset_id is not None:
                issues.append(ValidationIssue("TIMELINE_AUDIO_TRACK_INVALID", pointer))
            continue
        if track.asset_id is None or track.asset_id not in context.assets:
            issues.append(ValidationIssue("TIMELINE_ASSET_NOT_ACCESSIBLE", pointer))
    return issues


def _check_voiceover(
    track: AudioTrack, *, timeline: Timeline, context: ValidationContext, pointer: str
) -> list[ValidationIssue]:
    """§18.3's "seslendirme süresi", bound to a real measurement (slice 2C).

    A voiceover track names a `voiceover_assets` row rather than an uploaded asset: the audio was
    produced by this pipeline from an already-validated script, so it resolves through its own
    tenant-scoped query and a `media_assets` id is simply not found here.

    The rule itself is one comparison — speech may not outlast the canvas it is laid over. Before
    2C there was no measured duration to compare, so the check could not exist honestly; now it
    can, and `TIMELINE_VOICEOVER_DURATION_OVERFLOW` costs a refused render instead of an output
    whose last sentence is cut off mid-word. The *drift* between the speech and the script's
    target is recorded on the voiceover row and deliberately not judged here: which drift is
    unacceptable is slice 2D's threshold to set.
    """

    if track.asset_id is None:
        return [ValidationIssue("TIMELINE_VOICEOVER_NOT_ACCESSIBLE", pointer)]
    facts = context.voiceovers.get(track.asset_id)
    if facts is None:
        # Absent means "not this tenant's, or not there at all" — the query is tenant-scoped.
        return [ValidationIssue("TIMELINE_VOICEOVER_NOT_ACCESSIBLE", pointer)]
    if not facts.usable or facts.duration_ms is None:
        # A run still pending, or one that failed with partial audio. Neither has a duration
        # anything may be laid out against.
        return [ValidationIssue("TIMELINE_VOICEOVER_NOT_READY", pointer)]
    if facts.duration_ms > timeline.canvas.duration_ms:
        return [ValidationIssue("TIMELINE_VOICEOVER_DURATION_OVERFLOW", pointer)]
    return []


def _check_captions(
    timeline: Timeline, *, capabilities: RenderCapabilities
) -> list[ValidationIssue]:
    if not timeline.captions.enabled:
        return []
    if timeline.captions.source not in capabilities.caption_sources:
        return [ValidationIssue("TIMELINE_UNSUPPORTED_CAPTION_SOURCE", "$.captions.source")]
    return []


# --- overlays: verified fields, forbidden words, safe area ---------------------------------


def _check_overlays(
    timeline: Timeline,
    *,
    context: ValidationContext,
    profile: RenderProfile,
    resolved: dict[int, str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    matcher = _forbidden_matcher(context.forbidden_terms)
    for index, overlay in enumerate(timeline.overlays):
        pointer = f"$.overlays[{index}]"
        if overlay.kind is OverlayKind.LOGO:
            issues.extend(_check_logo(overlay, context=context, pointer=pointer))
            continue
        text, text_issues = resolve_overlay_text(overlay, context=context, pointer=pointer)
        issues.extend(text_issues)
        if text is None:
            continue
        # The alphabet restriction runs before every content rule, exactly as `parse_text` runs
        # it before the script rules. A letter the fold cannot put on ASCII is one no rule here
        # can read, so drawing it would mean drawing text nothing checked. Fail-closed: at worst
        # a legitimate name is refused and the shared fold table gains a line.
        if contains_unsupported_letter(text):
            issues.append(ValidationIssue("TIMELINE_UNSUPPORTED_CHARACTER", pointer))
            continue
        # Forbidden terms are matched against the unwrapped, folded string: wrapping only inserts
        # line breaks at existing spaces, and a multi-word term must not slip through by landing
        # across a break.
        if matcher is not None and matcher.search(normalize_for_matching(text)):
            issues.append(ValidationIssue("TIMELINE_FORBIDDEN_TERM", pointer))
        laid_out, fits = _layout_text(overlay, text=text, timeline=timeline, profile=profile)
        if not fits:
            issues.append(ValidationIssue("TIMELINE_TEXT_OUTSIDE_SAFE_AREA", pointer))
        resolved[index] = laid_out
    return issues


def _check_logo(
    overlay: Overlay, *, context: ValidationContext, pointer: str
) -> list[ValidationIssue]:
    if overlay.asset_id is None or overlay.asset_id not in context.assets:
        return [ValidationIssue("TIMELINE_ASSET_NOT_ACCESSIBLE", pointer)]
    # §18.3 "logo kullanımı": the frame may only carry an asset the brand registered as its
    # logo, not any picture the tenant happens to own.
    if overlay.asset_id not in context.logo_asset_ids:
        return [ValidationIssue("TIMELINE_LOGO_ASSET_INVALID", pointer)]
    return []


def resolve_overlay_text(
    overlay: Overlay, *, context: ValidationContext, pointer: str
) -> tuple[str | None, list[ValidationIssue]]:
    """Resolve one overlay to the string it will draw, or say why it cannot be drawn.

    Public because slice 2D asks the same question of a render that has already happened: to
    check the frame's text against the frame's geometry, QC has to know what text was drawn, and
    the render deliberately kept no copy of it. One resolver answering both is the guarantee that
    what QC measures is what validation admitted.
    """

    source = overlay.text_source or TextSource.LITERAL
    if source is TextSource.LITERAL:
        return (overlay.text or "", [])
    if overlay.reference_id is None:
        return (None, [ValidationIssue("TIMELINE_VERIFIED_REFERENCE_MISSING", pointer)])
    verified = context.verified_values.get((source.value, overlay.reference_id))
    if verified is None:
        # The record does not exist, is another tenant's, or is not of the referenced kind.
        # All three mean the same thing here: this value cannot be vouched for, so it cannot
        # be drawn. A price or date that reaches a frame is always one a record already held.
        return (None, [ValidationIssue("TIMELINE_VERIFIED_FIELD_NOT_FOUND", pointer)])
    if not verified.within_window:
        return (None, [ValidationIssue("TIMELINE_CAMPAIGN_WINDOW_INVALID", pointer)])
    return (verified.text, [])


def layout_text_in_frame(
    *,
    text: str,
    style: TextStyle,
    safe_area: SafeArea | None,
    frame_width: int,
    frame_height: int,
) -> tuple[str, bool]:
    """Wrap `text` for a frame of the given size and report whether the result fits.

    The renderer anchors text inside the permitted rectangle, so a block that fits the rectangle
    can always be placed legally and one that does not never can — the answer needs no knowledge
    of the anchor. Extent is estimated from font metrics rather than measured, and the estimate
    is deliberately wide so it errs toward rejection.

    The wrapped string is returned because it is what will be drawn: the renderer writes these
    exact lines, so the block that was measured and the block that appears cannot diverge.

    The frame is a *parameter* rather than the timeline's canvas because slice 2D asks the same
    question of the frame that actually came out of the renderer. One definition answering both
    is the point: a second copy measuring the output would eventually disagree with the one that
    admitted the timeline, and the disagreement would be invisible.
    """

    if safe_area is None:
        x0, y0, x1, y1 = 0, 0, frame_width, frame_height
    else:
        x0, y0, x1, y1 = safe_area.box(width=frame_width, height=frame_height)
    font_px = frame_height * style.font_height_ratio
    if font_px <= 0:
        return (text, False)
    max_chars = int((x1 - x0) / (font_px * style.advance_ratio))
    if max_chars < 1:
        return (text, False)
    lines = wrap_text(text, max_chars=max_chars)
    if not lines:
        return (text, False)
    fits = (
        len(lines) <= MAX_TEXT_LINES
        # A single word wider than the budget cannot be broken, so it overflows regardless of
        # how few lines the block has.
        and max(len(line) for line in lines) <= max_chars
        and len(lines) * font_px * _LINE_BOX_RATIO <= (y1 - y0)
    )
    return ("\n".join(lines), fits)


def _layout_text(
    overlay: Overlay, *, text: str, timeline: Timeline, profile: RenderProfile
) -> tuple[str, bool]:
    """Pre-render layout: the timeline's own canvas, with the target profile's safe area."""

    spec = profile_spec(profile)
    return layout_text_in_frame(
        text=text,
        style=TEXT_STYLES[overlay.style_id],
        safe_area=spec.safe_area if overlay.safe_area else None,
        frame_width=timeline.canvas.width,
        frame_height=timeline.canvas.height,
    )


def _forbidden_matcher(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    """The script detector's matcher, applied to timeline text. Imported, never reimplemented.

    The import is deliberately inside the function. `script.py` imports `VerifiedValue` from this
    module, so a module-level import in the other direction is a cycle — and resolving it by
    copying four lines of `re.compile` here is exactly the mistake this call exists to undo: two
    matchers, one folded and one not, is how the same banned claim ends up refused in a script
    and drawn on a frame. The late binding has a second, useful consequence: a test can replace
    `script.forbidden_matcher` and watch timeline validation change with it, which is a stronger
    proof of "one implementation" than reading the import statement.

    Word boundaries rather than substring, and no stemming: a brand forbidding `az` must not
    reject `lezzetli`, and one forbidding `şeker` has not asked us to reject `şekerli` (PM, W18).
    """

    from app.modules.content.script import forbidden_matcher

    return forbidden_matcher(terms)
