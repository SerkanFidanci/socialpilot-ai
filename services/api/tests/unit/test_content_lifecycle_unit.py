"""Slice 2E's pure half: the state machine, the QC decision table, the timeline composer.

Everything here is a total function over values, so the tests are enumerations rather than
scenarios. Three claims are worth more than the rest and each has its own permutation:

- the transition table answers for **every** `(state, event)` pair and reproduces PRD §20's
  diagram edge for edge, so a state cannot be skipped and a missing pair cannot crash a worker;
- `decide_after_qc` answers for **every** `(verdict, path, attempts)` combination and never says
  "retry" once the ceiling is reached, which is what makes an unbounded render loop inexpressible;
- the composer refuses rather than trims when speech will not fit the footage.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.infrastructure.render.ffmpeg import _audio_chain
from app.modules.content.lifecycle import (
    FAILURE_NO_USABLE_SCENE,
    FAILURE_RENDER_ATTEMPTS_EXHAUSTED,
    FAILURE_TIMELINE_TOO_LONG,
    FAILURE_TIMELINE_TOO_SHORT,
    ComposerSegment,
    ProjectEvent,
    ProjectState,
    SceneCandidate,
    TimelineCompositionError,
    advanceable_states,
    can_cancel,
    compose_timeline,
    decide_after_qc,
    decide_after_render_failure,
    is_terminal,
    next_state,
    normalize_scene_tag,
    require_next_state,
    waits_for_user,
    working_states,
)
from app.modules.content.qc import QcVerdict, RemediationPath
from app.modules.content.render import (
    AiDisclosureState,
    PlannedAudio,
    PlannedVoiceover,
    RenderPlan,
    RenderProfile,
)
from app.modules.content.timeline import (
    TEXT_STYLES,
    AudioTrackKind,
    Canvas,
    CropMode,
    TransitionKind,
    parse_timeline,
    serialize_timeline,
)

PROFILE = RenderProfile.INSTAGRAM_REELS_1080X1920
ASSET = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_ASSET = uuid.UUID("22222222-2222-4222-8222-222222222222")
VOICEOVER = uuid.UUID("33333333-3333-4333-8333-333333333333")

# PRD §20's diagram, transcribed. This is the specification, kept beside the table it checks so
# a reviewer can compare them without opening two files. `STEP_FAILED` is not here: it is the one
# documented extension, asserted separately below.
PRD_EDGES = {
    (ProjectState.PLANNED, ProjectEvent.MEDIA_REQUIRED): ProjectState.WAITING_MEDIA,
    (ProjectState.PLANNED, ProjectEvent.ANALYSIS_STARTED): ProjectState.ANALYZING,
    (ProjectState.WAITING_MEDIA, ProjectEvent.MEDIA_ATTACHED): ProjectState.ANALYZING,
    (ProjectState.ANALYZING, ProjectEvent.ANALYSIS_COMPLETE): ProjectState.SCRIPTING,
    (ProjectState.SCRIPTING, ProjectEvent.SCRIPT_READY): ProjectState.VOICE_GENERATION,
    (ProjectState.VOICE_GENERATION, ProjectEvent.VOICEOVER_READY): ProjectState.TIMELINE_BUILDING,
    (ProjectState.TIMELINE_BUILDING, ProjectEvent.TIMELINE_READY): ProjectState.RENDERING,
    (ProjectState.RENDERING, ProjectEvent.RENDER_SUCCEEDED): ProjectState.QUALITY_CHECK,
    (ProjectState.QUALITY_CHECK, ProjectEvent.QC_PASSED): ProjectState.PREVIEW_READY,
    (ProjectState.QUALITY_CHECK, ProjectEvent.QC_NEEDS_REVIEW): ProjectState.PREVIEW_READY,
    (ProjectState.QUALITY_CHECK, ProjectEvent.QC_FAILED): ProjectState.FAILED,
    (ProjectState.FAILED, ProjectEvent.RETRY_REQUESTED): ProjectState.RETRYING,
    (ProjectState.RETRYING, ProjectEvent.RETRY_STARTED): ProjectState.ANALYZING,
    # §21's approval loop, added by slice 2F. `PREVIEW_READY --> WAITING_APPROVAL` and
    # `WAITING_APPROVAL --> REVISION_REQUESTED --> SCRIPTING` are drawn in §20 exactly as
    # written here; the rest of 2F's edges are the documented extension below.
    (ProjectState.PREVIEW_READY, ProjectEvent.APPROVAL_REQUIRED): ProjectState.WAITING_APPROVAL,
    (ProjectState.WAITING_APPROVAL, ProjectEvent.REJECTED): ProjectState.REVISION_REQUESTED,
    (ProjectState.REVISION_REQUESTED, ProjectEvent.REVISION_SCOPED_TO_SCRIPT): (
        ProjectState.SCRIPTING
    ),
}

# The edges §20 does not draw, each one argued for in `lifecycle.py`'s module docstring. Kept as
# its own set so that "which parts of this machine are ours?" is a question with a written answer
# rather than a diff between a diagram and a table.
EXTENSION_EDGES = {
    # There is no scheduler until slice 2G, so an approved project has to rest somewhere that is
    # not "awaiting a decision" — otherwise the list of things awaiting a decision contains the
    # things that already got one.
    (ProjectState.PREVIEW_READY, ProjectEvent.AUTO_APPROVED): ProjectState.APPROVED,
    (ProjectState.WAITING_APPROVAL, ProjectEvent.APPROVED): ProjectState.APPROVED,
    # §21.3's small revisions: a new voice needs the same words, and a new caption style needs
    # neither new words nor new speech.
    (ProjectState.REVISION_REQUESTED, ProjectEvent.REVISION_SCOPED_TO_VOICE): (
        ProjectState.VOICE_GENERATION
    ),
    (ProjectState.REVISION_REQUESTED, ProjectEvent.REVISION_SCOPED_TO_TIMELINE): (
        ProjectState.TIMELINE_BUILDING
    ),
}


# --- the transition table (criterion 5) ------------------------------------------------------


def test_the_table_answers_for_every_state_and_event_pair() -> None:
    """Total, not merely large: the whole product is asked and nothing raises.

    A partial table would fail as a `KeyError` inside a worker holding a claim, which is the
    failure mode this shape exists to remove. `None` is an answer; an exception is not.
    """

    pairs = list(itertools.product(ProjectState, ProjectEvent))
    answers = {pair: next_state(*pair) for pair in pairs}

    assert len(answers) == len(ProjectState) * len(ProjectEvent)
    assert all(value is None or isinstance(value, ProjectState) for value in answers.values())


def test_the_defined_edges_are_exactly_the_prd_diagram_plus_the_documented_extension() -> None:
    """The set is asserted whole, so an edge cannot be added without being argued for."""

    defined = {
        pair: target
        for pair in itertools.product(ProjectState, ProjectEvent)
        if (target := next_state(*pair)) is not None
    }
    blanket = {ProjectEvent.STEP_FAILED, ProjectEvent.CANCELLED}
    step_failures = {
        pair: target for pair, target in defined.items() if pair[1] is ProjectEvent.STEP_FAILED
    }
    cancellations = {
        pair: target for pair, target in defined.items() if pair[1] is ProjectEvent.CANCELLED
    }

    assert {pair: target for pair, target in defined.items() if pair[1] not in blanket} == (
        PRD_EDGES | EXTENSION_EDGES
    )
    # The first extension: every state the *machine* works in can fail, and `FAILED` is the only
    # place it lands. The states that wait on a person are excluded, and so is `PREVIEW_READY` —
    # a customer who has not uploaded anything has not failed at anything, and a finished preview
    # cannot fail either. What ends those waits is the project sweep, by cancelling.
    assert set(step_failures) == {(state, ProjectEvent.STEP_FAILED) for state in working_states()}
    assert set(step_failures.values()) == {ProjectState.FAILED}
    # The second: a project can be withdrawn from anywhere it has not already finished, and from
    # nowhere else. Cancelling a finished project is refused rather than silently repeated,
    # which is what makes a duplicate cancel unable to refund twice.
    assert set(cancellations) == {
        (state, ProjectEvent.CANCELLED) for state in ProjectState if not is_terminal(state)
    }
    assert set(cancellations.values()) == {ProjectState.CANCELLED}
    assert all(can_cancel(state) is not is_terminal(state) for state in ProjectState)


@pytest.mark.parametrize(
    ("state", "event"),
    [
        # The escape the work order names: a render that skips its check.
        (ProjectState.RENDERING, ProjectEvent.QC_PASSED),
        # Backwards, forwards, and straight past the middle of the pipeline.
        (ProjectState.PLANNED, ProjectEvent.RENDER_SUCCEEDED),
        (ProjectState.SCRIPTING, ProjectEvent.TIMELINE_READY),
        (ProjectState.ANALYZING, ProjectEvent.QC_PASSED),
        (ProjectState.TIMELINE_BUILDING, ProjectEvent.RENDER_SUCCEEDED),
        (ProjectState.PREVIEW_READY, ProjectEvent.QC_FAILED),
        (ProjectState.PREVIEW_READY, ProjectEvent.STEP_FAILED),
        (ProjectState.FAILED, ProjectEvent.QC_PASSED),
        (ProjectState.QUALITY_CHECK, ProjectEvent.RETRY_STARTED),
        # Slice 2F's own escapes: approving something nobody was asked about, revising something
        # nobody rejected, deciding on a project that is already finished, and cancelling one
        # that is already cancelled.
        (ProjectState.PREVIEW_READY, ProjectEvent.APPROVED),
        (ProjectState.QUALITY_CHECK, ProjectEvent.APPROVAL_REQUIRED),
        (ProjectState.WAITING_APPROVAL, ProjectEvent.REVISION_SCOPED_TO_SCRIPT),
        (ProjectState.PREVIEW_READY, ProjectEvent.REVISION_SCOPED_TO_TIMELINE),
        (ProjectState.APPROVED, ProjectEvent.REJECTED),
        (ProjectState.CANCELLED, ProjectEvent.CANCELLED),
        (ProjectState.APPROVED, ProjectEvent.CANCELLED),
    ],
)
def test_a_state_cannot_be_skipped_or_walked_backwards(
    state: ProjectState, event: ProjectEvent
) -> None:
    assert next_state(state, event) is None
    with pytest.raises(Exception, match="PROJECT_TRANSITION_NOT_ALLOWED"):
        require_next_state(state, event)


def test_creation_is_an_entry_arrow_and_not_a_transition() -> None:
    """§20 draws `[*] --> PLANNED`. Nothing may "create" a project that already exists."""

    assert all(next_state(state, ProjectEvent.CREATED) is None for state in ProjectState)


def test_terminal_states_are_never_claimed_and_wait_states_are() -> None:
    assert set(advanceable_states()) == {state for state in ProjectState if not is_terminal(state)}
    # The terminal set slice 2F moved to. `PREVIEW_READY` is deliberately no longer in it: the
    # sequencer passes through that state to apply §21.1's policy, and a project that stopped
    # there would never be offered for approval at all.
    assert set(ProjectState) - set(advanceable_states()) == {
        ProjectState.APPROVED,
        ProjectState.FAILED,
        ProjectState.CANCELLED,
    }
    assert ProjectState.PREVIEW_READY in advanceable_states()
    # A project waiting on a person is still claimed — it has to notice its media arrived, or its
    # decision — but the step timeout must not apply to it.
    assert {state for state in ProjectState if waits_for_user(state)} == {
        ProjectState.WAITING_MEDIA,
        ProjectState.WAITING_APPROVAL,
        ProjectState.REVISION_REQUESTED,
    }
    assert all(state in advanceable_states() for state in ProjectState if waits_for_user(state))


# --- the QC decision table (criteria 3 and 4) -------------------------------------------------


def test_the_qc_decision_is_total_over_every_verdict_path_and_attempt_count() -> None:
    """3 verdicts x 6 paths x below/at/over the ceiling, all answered, all reaching a real edge."""

    combinations = list(itertools.product(QcVerdict, RemediationPath, (0, 1, 2, 3)))
    for verdict, path, attempts in combinations:
        outcome = decide_after_qc(
            verdict=verdict, path=path, attempts_used=attempts, max_attempts=2
        )
        assert outcome.events, (verdict, path, attempts)
        # Every event sequence has to be walkable from `QUALITY_CHECK`, or the decision would
        # name a transition the table refuses.
        state = ProjectState.QUALITY_CHECK
        for event in outcome.events:
            state = require_next_state(state, event)
    assert len(combinations) == len(QcVerdict) * len(RemediationPath) * 4


def test_retry_is_unreachable_once_the_ceiling_is_reached() -> None:
    """The loop bound, stated as an absence: no input returns "retry" at or over the ceiling."""

    for verdict, path, attempts in itertools.product(QcVerdict, RemediationPath, (2, 3, 9)):
        outcome = decide_after_qc(
            verdict=verdict, path=path, attempts_used=attempts, max_attempts=2
        )
        assert not outcome.retries_render, (verdict, path, attempts)
    for attempts in (2, 3, 9):
        assert not decide_after_render_failure(
            attempts_used=attempts, max_attempts=2
        ).retries_render


def test_a_render_that_never_passes_qc_stops_at_the_ceiling_with_a_counted_attempt() -> None:
    """The whole loop, driven to exhaustion, with the counter asserted at every step.

    This is criterion 4 in miniature and it is deliberately a *simulation* of the sequencer's
    arithmetic rather than a mock of it: the ceiling has to hold in the pure layer, because that
    is the layer the service is not allowed to disagree with.
    """

    ceiling = 2
    state = ProjectState.QUALITY_CHECK
    attempts = 1  # the first render has already happened
    renders = 1
    for _ in range(20):
        outcome = decide_after_qc(
            verdict=QcVerdict.FAILED,
            path=RemediationPath.RETRY_RENDER,
            attempts_used=attempts,
            max_attempts=ceiling,
        )
        for event in outcome.events:
            state = require_next_state(state, event)
        if not outcome.retries_render:
            break
        # `RETRYING -> ANALYZING -> ... -> RENDERING` is the path back; the counter increments
        # exactly where a render is requested.
        state = ProjectState.RENDERING
        attempts += 1
        renders += 1
        state = require_next_state(state, ProjectEvent.RENDER_SUCCEEDED)

    assert state is ProjectState.FAILED
    assert renders == ceiling
    assert attempts == ceiling
    exhausted = decide_after_qc(
        verdict=QcVerdict.FAILED,
        path=RemediationPath.RETRY_RENDER,
        attempts_used=attempts,
        max_attempts=ceiling,
    )
    assert exhausted.failure_code == FAILURE_RENDER_ATTEMPTS_EXHAUSTED
    assert exhausted.requires_human_review


def test_needs_review_reaches_the_preview_and_carries_the_flag() -> None:
    """Today's only real path: fail-closed QC marks every render for a person to look at.

    Calling it a failure would stop the product until a vision provider is connected, which is
    the trade slice 2D's fail-closed rule was explicitly *not* asking for.
    """

    outcome = decide_after_qc(
        verdict=QcVerdict.NEEDS_REVIEW,
        path=RemediationPath.HUMAN_REVIEW,
        attempts_used=0,
        max_attempts=2,
    )

    assert outcome.events == (ProjectEvent.QC_NEEDS_REVIEW,)
    assert require_next_state(ProjectState.QUALITY_CHECK, outcome.events[0]) is (
        ProjectState.PREVIEW_READY
    )
    assert outcome.requires_human_review
    assert outcome.failure_code is None


@pytest.mark.parametrize(
    "path",
    [
        RemediationPath.ALTERNATIVE_SCENE,
        RemediationPath.ALTERNATIVE_PROVIDER,
        RemediationPath.REQUEST_NEW_MEDIA,
    ],
)
def test_a_suggestion_this_slice_cannot_carry_out_is_recorded_and_not_acted_on(
    path: RemediationPath,
) -> None:
    """Each of these needs a capability that does not exist yet; none of them retries."""

    outcome = decide_after_qc(verdict=QcVerdict.FAILED, path=path, attempts_used=0, max_attempts=2)

    assert outcome.events == (ProjectEvent.QC_FAILED,)
    assert not outcome.retries_render
    assert outcome.recommended_path is path
    assert outcome.requires_human_review


def test_a_broken_encode_and_a_failed_check_share_one_counter() -> None:
    """Alternating between the two must not buy a project unlimited renders."""

    first = decide_after_render_failure(attempts_used=1, max_attempts=2)
    assert first.retries_render
    state = ProjectState.RENDERING
    for event in first.events:
        state = require_next_state(state, event)
    assert state is ProjectState.RETRYING
    assert not decide_after_render_failure(attempts_used=2, max_attempts=2).retries_render


# --- composing a timeline ---------------------------------------------------------------------


def scene(asset: uuid.UUID, start: int, end: int, *tags: str) -> SceneCandidate:
    return SceneCandidate(asset_id=asset, start_ms=start, end_ms=end, tags=frozenset(tags))


def segment(duration: int, *tags: str) -> ComposerSegment:
    return ComposerSegment(required_scene_tags=tags, target_duration_ms=duration)


def compose(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "segments": (segment(2_000, "product_closeup"), segment(3_000, "preparation")),
        "candidates": (
            scene(ASSET, 0, 5_000, "preparation"),
            scene(ASSET, 5_000, 12_000, "product_closeup"),
        ),
        "profile": PROFILE,
        "voiceover_id": VOICEOVER,
        "voiceover_duration_ms": 4_000,
        "max_duration_ms": 180_000,
    }
    return compose_timeline(**(base | overrides))


def test_a_composed_timeline_is_a_document_the_parser_accepts() -> None:
    """The composer builds value objects directly, so the round trip is the real contract."""

    timeline = compose()
    reparsed = parse_timeline(serialize_timeline(timeline))

    assert reparsed == timeline
    assert reparsed.canvas.width == 1080
    assert reparsed.canvas.height == 1920
    assert reparsed.canvas.duration_ms == sum(clip.duration_ms for clip in reparsed.clips)


def test_each_segment_takes_the_first_scene_that_matches_what_it_asked_for() -> None:
    """Selection is deterministic and by tag; the fallback is position, never randomness."""

    clips = compose().clips

    # Segment 0 wanted `product_closeup`, which is the *second* candidate.
    assert clips[0].source_start_ms == 5_000
    assert clips[0].duration_ms == 2_000
    assert clips[1].source_start_ms == 0
    assert clips[1].duration_ms == 3_000
    assert clips[1].timeline_start_ms == 2_000
    assert all(clip.crop_mode is CropMode.SMART_COVER for clip in clips)
    assert all(clip.transition_out is TransitionKind.CUT for clip in clips)


def test_a_segment_whose_tags_match_nothing_still_gets_footage() -> None:
    """A tag is a preference, not a precondition — a script must not be unbuildable over it."""

    timeline = compose(
        segments=(segment(1_000, "aurora_borealis"),),
        candidates=(scene(ASSET, 0, 9_000),),
        voiceover_duration_ms=None,
        voiceover_id=None,
    )

    assert timeline.clips[0].asset_id == ASSET
    assert timeline.clips[0].duration_ms == 1_000


def test_the_cut_is_lengthened_so_the_speech_fits_inside_it() -> None:
    """§18.3 refuses speech longer than the canvas, so the composer must not produce one."""

    timeline = compose(
        segments=(segment(1_000, "preparation"),),
        candidates=(scene(ASSET, 0, 9_000, "preparation"),),
        voiceover_duration_ms=7_500,
    )

    assert timeline.canvas.duration_ms >= 7_500
    assert timeline.clips[0].duration_ms == 7_500


def test_more_footage_is_appended_when_one_scene_cannot_hold_the_speech() -> None:
    timeline = compose(
        segments=(segment(1_000, "preparation"),),
        candidates=(
            scene(ASSET, 0, 2_000, "preparation"),
            scene(OTHER_ASSET, 0, 6_000),
        ),
        voiceover_duration_ms=5_000,
    )

    assert timeline.canvas.duration_ms >= 5_000
    assert {clip.asset_id for clip in timeline.clips} == {ASSET, OTHER_ASSET}


def test_speech_that_outlasts_every_frame_available_is_refused_not_trimmed() -> None:
    """The audio is the thing that was written and approved; the footage is what is missing."""

    with pytest.raises(TimelineCompositionError) as error:
        compose(
            segments=(segment(1_000, "preparation"),),
            candidates=(scene(ASSET, 0, 2_000, "preparation"),),
            voiceover_duration_ms=30_000,
        )

    assert error.value.code == FAILURE_TIMELINE_TOO_SHORT


def test_a_project_with_no_usable_scene_is_refused_with_a_documented_code() -> None:
    with pytest.raises(TimelineCompositionError) as empty:
        compose(candidates=())
    assert empty.value.code == FAILURE_NO_USABLE_SCENE

    with pytest.raises(TimelineCompositionError) as tiny:
        # Shorter than one clip may be: real footage, unusable as a cut.
        compose(candidates=(scene(ASSET, 0, 40, "preparation"),))
    assert tiny.value.code == FAILURE_NO_USABLE_SCENE


def test_a_cut_longer_than_the_adapter_allows_is_refused_before_a_render_is_asked_for() -> None:
    with pytest.raises(TimelineCompositionError) as error:
        compose(
            segments=(segment(60_000, "preparation"),),
            candidates=(scene(ASSET, 0, 120_000, "preparation"),),
            voiceover_duration_ms=None,
            max_duration_ms=30_000,
        )

    assert error.value.code == FAILURE_TIMELINE_TOO_LONG


def test_the_composed_audio_is_the_footage_ducked_under_the_voice() -> None:
    tracks = {track.kind: track for track in compose().audio_tracks}

    assert set(tracks) == {AudioTrackKind.ORIGINAL, AudioTrackKind.VOICEOVER}
    assert tracks[AudioTrackKind.VOICEOVER].asset_id == VOICEOVER
    # The flag sits on the bed, which is the track that has to give way.
    assert tracks[AudioTrackKind.ORIGINAL].duck_under_voice
    assert tracks[AudioTrackKind.ORIGINAL].asset_id is None
    assert not tracks[AudioTrackKind.VOICEOVER].duck_under_voice


def test_a_project_without_speech_composes_no_voiceover_track_and_no_ducking() -> None:
    tracks = compose(voiceover_id=None, voiceover_duration_ms=None).audio_tracks

    assert [track.kind for track in tracks] == [AudioTrackKind.ORIGINAL]
    assert not tracks[0].duck_under_voice


def test_the_composer_places_no_overlay_and_burns_no_caption() -> None:
    """Stated as a test because it is a scope line, not an omission — see the composer's note."""

    timeline = compose()

    assert timeline.overlays == ()
    assert not timeline.captions.enabled


# --- scene tag spelling -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ürün Çekimi", "ürün_çekimi"),
        ("  product-closeup  ", "product_closeup"),
        # Turkish lowercase, verbatim from `normalize_encoding`: `I` becomes `ı`, not `i`. This
        # is asserted rather than worked around, because the function's whole job is to agree
        # with what slice 2B stored.
        ("PREPARATION", "preparatıon"),
        # Encoding folded, letters kept: this value is compared with a stored one, so folding it
        # to ASCII would stop `ürün` ever matching `ürün`.
        ("ürün", "ürün"),
    ],
)
def test_a_scene_label_is_spelled_the_way_a_script_tag_is_spelled(raw: str, expected: str) -> None:
    """`script._scene_tags` applies exactly these two steps; both sides must agree or nothing
    ever matches. The end-to-end proof is in the integration suite, where a generated script's
    tags drive real scene selection."""

    assert normalize_scene_tag(raw) == expected


def test_a_capitalised_label_still_matches_a_lowercase_script_tag() -> None:
    """The Turkish `I` would otherwise split one vocabulary in two, silently.

    A provider writing `PREPARATION` stores `preparatıon`; a script asking for `preparation`
    stores `preparation`. Both go through the same normalizer and still differ, and the symptom
    would be scene selection quietly degrading to "first unused shot" with nothing to see. The
    comparison folds the dotless pair; nothing on disk changes.
    """

    timeline = compose(
        segments=(segment(1_000, "preparation"),),
        candidates=(
            scene(ASSET, 0, 4_000, normalize_scene_tag("HOOK")),
            scene(OTHER_ASSET, 0, 4_000, normalize_scene_tag("PREPARATION")),
        ),
        voiceover_id=None,
        voiceover_duration_ms=None,
    )

    assert timeline.clips[0].asset_id == OTHER_ASSET


# --- the mixing graph ---------------------------------------------------------------------------


def plan(*, voiceover: bool, duck: bool, bed_gain: int = 0, voice_gain: int = 0) -> RenderPlan:
    return RenderPlan(
        profile=PROFILE,
        canvas=Canvas(width=1080, height=1920, fps=30, duration_ms=6_000),
        segments=(),
        texts=(),
        logos=(),
        captions=(),
        caption_style=TEXT_STYLES["brand-caption-v1"],
        audio=PlannedAudio(
            source=AudioTrackKind.ORIGINAL,
            gain_db=bed_gain,
            voiceover=(
                PlannedVoiceover(segment_paths=(), gain_db=voice_gain) if voiceover else None
            ),
            duck_under_voice=duck,
        ),
        ai_disclosure=AiDisclosureState.NONE,
    )


def test_a_render_without_speech_takes_exactly_the_path_it_took_before() -> None:
    """The empty graph is the guarantee that slice 2E changed nothing for existing timelines."""

    chain, label = _audio_chain(plan(voiceover=False, duck=False), voice_index=None)

    assert chain == []
    assert label == "0:a"


def test_speech_is_mixed_over_the_bed_at_the_levels_the_timeline_asked_for() -> None:
    chain, label = _audio_chain(plan(voiceover=True, duck=False, bed_gain=-6), voice_index=2)
    graph = ";".join(chain)

    assert label == "[aout]"
    assert "volume=-6dB[bed]" in graph
    assert "[2:a]" in graph
    # `normalize=0` is what makes those decibels mean something, and the limiter is what stops
    # the sum clipping once they do.
    assert "normalize=0" in graph
    assert "alimiter" in graph
    assert "sidechaincompress" not in graph


def test_ducking_happens_only_when_the_timeline_asks_for_it() -> None:
    graph = ";".join(_audio_chain(plan(voiceover=True, duck=True), voice_index=1)[0])

    assert "sidechaincompress" in graph
    # The key has to be a copy: one stream cannot be both the sidechain input and a mix input.
    assert "asplit=2[voicemix][voicekey]" in graph
    assert "[bed][voicekey]" in graph
    assert "[bedducked][voicemix]amix" in graph


def test_the_mix_takes_its_length_from_the_footage_and_not_from_the_speech() -> None:
    """Speech shorter than the video leaves silence; it must never truncate the picture."""

    for duck in (True, False):
        graph = ";".join(_audio_chain(plan(voiceover=True, duck=duck), voice_index=1)[0])
        assert "duration=first" in graph


def test_settings_bound_the_render_loop_and_the_sweep_by_construction() -> None:
    """The ceiling is a bounded field, so no deployment can configure its way to a loop."""

    field = Settings.model_fields["lifecycle_max_render_attempts"]
    assert any(getattr(item, "le", None) == 10 for item in field.metadata)
    assert any(getattr(item, "ge", None) == 1 for item in field.metadata)
