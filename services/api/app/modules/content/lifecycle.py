"""PRD §20's content project lifecycle as a closed, pure state machine — and nothing else.

Slices 2A–2D produced five capabilities that do not know one another exists. This module is the
skeleton that orders them, and it is deliberately the *pure* half: no session, no clock, no
provider, no I/O. Everything here is a total function over values, so the interesting questions —
"can this project go there?", "what does a failed QC mean for a project that has already been
rendered twice?" — are answered by a table a test can enumerate rather than by control flow
scattered across a service.

Three properties are load-bearing.

**The transition table is closed and total.** `next_state` answers for every `(state, event)`
pair in the product, and answers `None` for the ones PRD §20 does not draw. A caller that asks
for `RENDERING → PREVIEW_READY` gets a refusal, not a `KeyError` and not a silent success, which
is what makes "no state may be skipped" a property of the data rather than of everyone's care.

**The states are PRD §20's, not a summary of them.** `SCHEDULED`, `PUBLISHING` and `PUBLISHED` are
absent because slice 2G and Phase 4 own them; every state this slice does reach is spelled the way
§20 spells it. There are three documented extensions, each one a state or event the diagram does
not draw and the product needs:

- `ProjectEvent.STEP_FAILED` (slice 2E). The diagram reaches `FAILED` only from `QUALITY_CHECK`
  and `PUBLISHING`, and a project whose script generation failed has nowhere to be.
- `ProjectState.APPROVED` (slice 2F). §20 draws `WAITING_APPROVAL --> SCHEDULED` directly, and
  there is no scheduler until 2G. An approved project has to rest *somewhere* that is not
  `WAITING_APPROVAL`, or "approved" would be invisible to every query the product asks — the list
  of things awaiting a decision would contain the things that already got one. Slice 2G adds the
  edge `APPROVED --> SCHEDULED`; §20's arrow is that path with this state named in the middle.
- `ProjectState.CANCELLED` (slice 2F). A customer withdrawing a project is not a failure, and the
  distinction is not cosmetic: `failure_code` drives the refund classification and a support
  answer that cannot separate "the encoder died" from "they changed their mind" is not an answer.

**Reaching `PREVIEW_READY` is no longer the end.** Slice 2E made it terminal because approval did
not exist. It is now a state the sequencer passes through — it evaluates §21.1's policy there and
either asks for approval or records an automatic one. What did *not* move is the moment the credit
is spent: PRD §12.7 consumes on "ön izleme başarıyla hazır", so the project records
`preview_delivered_at` on first arrival and every later outcome is `DELIVERED` regardless of where
the project ends up. Without that, a revision that failed after a good preview would try to hand
back credit for a preview the customer already received, and the ledger would refuse it as a
contradiction.

**The revision loop is bounded by quota, the render loop by attempts.** These are two different
loops with two different bounds and they must not share one. `render_attempts` bounds what the
*machine* does on its own after a failed check; a revision is a person asking for something
different, so it resets that counter and spends the revision quota instead. Sharing one counter
would mean either that a person cannot get a re-render after two automatic ones, or that the
automatic loop could be reopened indefinitely from outside.

**Bounding the render loop is expressed here, not in the service.** `decide_after_qc` takes the
attempts already spent and the ceiling, and there is no combination of inputs for which it
returns "retry" once the ceiling is reached. The service cannot loop forever because the function
it has to ask never says so — which is the shape slice 2D asked for when it refused to hold a
render port at all.

`compose_timeline` lives here for the same reason: turning a script plus a set of detected scenes
into a §18.2 document is arithmetic over values, and keeping it pure is what lets the interesting
cases (a voiceover longer than the footage, a segment whose tags match nothing) be unit tests
rather than integration fixtures.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.modules.content.approval import RevisionScope
from app.modules.content.qc import QcVerdict, RemediationPath
from app.modules.content.render import RenderProfile, profile_spec
from app.modules.content.text_normalization import normalize_encoding
from app.modules.content.timeline import (
    MAX_CANVAS_DURATION_MS,
    MAX_CLIPS_PER_TRACK,
    MIN_CLIP_DURATION_MS,
    TIMELINE_VERSION,
    AudioTrack,
    AudioTrackKind,
    Canvas,
    Captions,
    CaptionSource,
    Clip,
    CropMode,
    Timeline,
    TransitionKind,
    VideoTrack,
)

# --- failure codes ---------------------------------------------------------------------------
# Every one of these is listed in docs/architecture/error-handling.md. They name *why a project
# stopped*, never what a tenant's content said.

FAILURE_NO_USABLE_SCENE: Final = "PROJECT_NO_USABLE_SCENE"
FAILURE_TIMELINE_TOO_LONG: Final = "PROJECT_TIMELINE_TOO_LONG"
FAILURE_TIMELINE_TOO_SHORT: Final = "PROJECT_TIMELINE_TOO_SHORT_FOR_VOICEOVER"
FAILURE_RENDER_ATTEMPTS_EXHAUSTED: Final = "PROJECT_RENDER_ATTEMPTS_EXHAUSTED"
FAILURE_STATE_TIMEOUT: Final = "PROJECT_STATE_TIMEOUT"
FAILURE_SCRIPT_FAILED: Final = "PROJECT_SCRIPT_FAILED"
FAILURE_VOICEOVER_FAILED: Final = "PROJECT_VOICEOVER_FAILED"
FAILURE_TIMELINE_REJECTED: Final = "PROJECT_TIMELINE_REJECTED"
FAILURE_RENDER_FAILED: Final = "PROJECT_RENDER_FAILED"
FAILURE_SOURCE_NOT_ANALYZED: Final = "PROJECT_SOURCE_NOT_ANALYZED"
# The two ways a project ends without having broken. They are codes rather than an absent one
# because `failure_code` is what the ledger classifies a settlement by, and a support answer that
# cannot separate "the encoder died" from "they changed their mind" is not an answer.
FAILURE_CANCELLED: Final = "PROJECT_CANCELLED"
FAILURE_ABANDONED: Final = "PROJECT_ABANDONED"


class ProjectState(StrEnum):
    """PRD §20's states, up to the point slice 2F is responsible for.

    The diagram continues past approval into scheduling and publishing. Those are slice 2G and
    Phase 4; adding them here as unreachable values would put states in the database that nothing
    can produce and nothing can consume. `APPROVED` and `CANCELLED` are the two documented
    extensions — see the module docstring for why each exists.
    """

    PLANNED = "planned"
    WAITING_MEDIA = "waiting_media"
    ANALYZING = "analyzing"
    SCRIPTING = "scripting"
    VOICE_GENERATION = "voice_generation"
    TIMELINE_BUILDING = "timeline_building"
    RENDERING = "rendering"
    QUALITY_CHECK = "quality_check"
    PREVIEW_READY = "preview_ready"
    WAITING_APPROVAL = "waiting_approval"
    REVISION_REQUESTED = "revision_requested"
    APPROVED = "approved"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class ProjectEvent(StrEnum):
    """What happened, never what to do next.

    Events name facts the sequencer observed — a script settled, a render succeeded, QC reached a
    verdict — and the table decides where each fact leads. Naming them as commands (`go_to_x`)
    would put the transition rule back at the call site, which is what the table exists to stop.
    """

    CREATED = "created"
    MEDIA_REQUIRED = "media_required"
    MEDIA_ATTACHED = "media_attached"
    ANALYSIS_STARTED = "analysis_started"
    ANALYSIS_COMPLETE = "analysis_complete"
    SCRIPT_READY = "script_ready"
    VOICEOVER_READY = "voiceover_ready"
    TIMELINE_READY = "timeline_ready"
    RENDER_SUCCEEDED = "render_succeeded"
    QC_PASSED = "qc_passed"
    QC_NEEDS_REVIEW = "qc_needs_review"
    QC_FAILED = "qc_failed"
    RETRY_REQUESTED = "retry_requested"
    RETRY_STARTED = "retry_started"
    STEP_FAILED = "step_failed"
    # §21.1's policy was evaluated over a finished preview and answered one of two ways. Both are
    # facts about what the policy said, not instructions: the table decides where each one leads.
    APPROVAL_REQUIRED = "approval_required"
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    # Which stage the revision invalidated (`approval.RevisionScope`). Three events rather than
    # one because the transition table maps a pair to a single state, and a revision that only
    # changes the caption style must not walk back through a script generation. The *scope* is
    # decided by a pure classifier over the changed fields; these events only carry its answer.
    REVISION_SCOPED_TO_SCRIPT = "revision_scoped_to_script"
    REVISION_SCOPED_TO_VOICE = "revision_scoped_to_voice"
    REVISION_SCOPED_TO_TIMELINE = "revision_scoped_to_timeline"
    CANCELLED = "cancelled"


# PRD §20's edges, verbatim, for the part of the diagram this slice covers. `STEP_FAILED` is the
# documented extension: it is added from every non-terminal state and from nowhere else, so a
# project can always reach `FAILED` honestly instead of sitting in a working state forever.
_WORKING_STATES: Final[tuple[ProjectState, ...]] = (
    ProjectState.PLANNED,
    ProjectState.WAITING_MEDIA,
    ProjectState.ANALYZING,
    ProjectState.SCRIPTING,
    ProjectState.VOICE_GENERATION,
    ProjectState.TIMELINE_BUILDING,
    ProjectState.RENDERING,
    ProjectState.QUALITY_CHECK,
    ProjectState.RETRYING,
)

_TERMINAL_STATES: Final[frozenset[ProjectState]] = frozenset(
    {ProjectState.APPROVED, ProjectState.FAILED, ProjectState.CANCELLED}
)

# The states in which the sequencer is waiting on a person rather than on itself. They are
# claimed like any other working state — a project has to be able to notice its media arrived —
# but the step timeout does not apply to them, because "the customer has not uploaded yet" and
# "the approver is at lunch" are not stalled jobs. What catches a project abandoned in one of
# these is the age sweep, which is a much longer clock and gives the credit back.
_USER_WAIT_STATES: Final[frozenset[ProjectState]] = frozenset(
    {ProjectState.WAITING_MEDIA, ProjectState.WAITING_APPROVAL, ProjectState.REVISION_REQUESTED}
)

_TRANSITIONS: Final[Mapping[tuple[ProjectState, ProjectEvent], ProjectState]] = {
    (ProjectState.PLANNED, ProjectEvent.MEDIA_REQUIRED): ProjectState.WAITING_MEDIA,
    (ProjectState.PLANNED, ProjectEvent.ANALYSIS_STARTED): ProjectState.ANALYZING,
    (ProjectState.WAITING_MEDIA, ProjectEvent.MEDIA_ATTACHED): ProjectState.ANALYZING,
    (ProjectState.ANALYZING, ProjectEvent.ANALYSIS_COMPLETE): ProjectState.SCRIPTING,
    (ProjectState.SCRIPTING, ProjectEvent.SCRIPT_READY): ProjectState.VOICE_GENERATION,
    (ProjectState.VOICE_GENERATION, ProjectEvent.VOICEOVER_READY): (ProjectState.TIMELINE_BUILDING),
    (ProjectState.TIMELINE_BUILDING, ProjectEvent.TIMELINE_READY): ProjectState.RENDERING,
    (ProjectState.RENDERING, ProjectEvent.RENDER_SUCCEEDED): ProjectState.QUALITY_CHECK,
    (ProjectState.QUALITY_CHECK, ProjectEvent.QC_PASSED): ProjectState.PREVIEW_READY,
    # `needs_review` reaches the same state as `passed` and is distinguished by the project's
    # `requires_human_review` flag rather than by a state of its own. Today *every* render is
    # `needs_review`, because the vision adapter is disabled in production and slice 2D's
    # fail-closed rule turns an unmeasured model check into an unknown one; a second state would
    # therefore be the state every project ends in, and the approval flow below has to treat the
    # two identically anyway — §21.1's `low_confidence_only` reads the verdict, not the state.
    (ProjectState.QUALITY_CHECK, ProjectEvent.QC_NEEDS_REVIEW): ProjectState.PREVIEW_READY,
    (ProjectState.QUALITY_CHECK, ProjectEvent.QC_FAILED): ProjectState.FAILED,
    (ProjectState.FAILED, ProjectEvent.RETRY_REQUESTED): ProjectState.RETRYING,
    (ProjectState.RETRYING, ProjectEvent.RETRY_STARTED): ProjectState.ANALYZING,
    # --- §21's approval loop ----------------------------------------------------------------
    # The sequencer applies §21.1's policy the moment a preview exists and takes one of two
    # edges. Neither is optional and there is no third: a preview that nobody decided about
    # would sit in a state the product has no screen for.
    (ProjectState.PREVIEW_READY, ProjectEvent.APPROVAL_REQUIRED): ProjectState.WAITING_APPROVAL,
    (ProjectState.PREVIEW_READY, ProjectEvent.AUTO_APPROVED): ProjectState.APPROVED,
    (ProjectState.WAITING_APPROVAL, ProjectEvent.APPROVED): ProjectState.APPROVED,
    (ProjectState.WAITING_APPROVAL, ProjectEvent.REJECTED): ProjectState.REVISION_REQUESTED,
    # §20 draws `REVISION_REQUESTED --> SCRIPTING` and that is the major-revision edge. The two
    # beside it are what "yalnızca etkilenen adımdan yeniden başlar" means once the pipeline
    # actually has stages: a new voice does not need new words, and a new caption style does not
    # need new speech.
    (ProjectState.REVISION_REQUESTED, ProjectEvent.REVISION_SCOPED_TO_SCRIPT): (
        ProjectState.SCRIPTING
    ),
    (ProjectState.REVISION_REQUESTED, ProjectEvent.REVISION_SCOPED_TO_VOICE): (
        ProjectState.VOICE_GENERATION
    ),
    (ProjectState.REVISION_REQUESTED, ProjectEvent.REVISION_SCOPED_TO_TIMELINE): (
        ProjectState.TIMELINE_BUILDING
    ),
    **{(state, ProjectEvent.STEP_FAILED): ProjectState.FAILED for state in _WORKING_STATES},
    # Cancellation is available from every state the project has not already finished in, and
    # from nowhere else. Written over the same closed list the terminal set is defined by, so
    # "can this be cancelled?" and "is this over?" cannot drift apart.
    **{
        (state, ProjectEvent.CANCELLED): ProjectState.CANCELLED
        for state in ProjectState
        if state not in _TERMINAL_STATES
    },
}

INITIAL_STATE: Final = ProjectState.PLANNED


def next_state(state: ProjectState, event: ProjectEvent) -> ProjectState | None:
    """Where `event` leads from `state`, or `None` when PRD §20 draws no such edge.

    Total over the whole `(state, event)` product: there is no pair this raises on, and the
    permutation test enumerates every one of them. `None` is a refusal a caller must handle —
    an API request that arrived out of order — and never a reason to guess.
    """

    return _TRANSITIONS.get((state, event))


def is_terminal(state: ProjectState) -> bool:
    """A state the project never leaves. `APPROVED`, `FAILED` and `CANCELLED`, and no others.

    `PREVIEW_READY` was terminal in slice 2E and is not any more: the sequencer passes through
    it to apply §21.1's policy. `WAITING_APPROVAL` and `REVISION_REQUESTED` are not terminal
    either — they wait on a person, which `waits_for_user` is what says.
    """

    return state in _TERMINAL_STATES


def can_cancel(state: ProjectState) -> bool:
    """Whether a customer may still withdraw this project. Exactly the non-terminal states."""

    return next_state(state, ProjectEvent.CANCELLED) is not None


# Which event carries a revision of a given scope. The scope is decided by `approval`'s pure
# classifier over the changed fields; the mapping from that answer to an edge lives here, beside
# the table the edge is in, so a new scope cannot be added without a transition to go with it.
_SCOPE_EVENTS: Final[Mapping[RevisionScope, ProjectEvent]] = {
    RevisionScope.SCRIPT: ProjectEvent.REVISION_SCOPED_TO_SCRIPT,
    RevisionScope.VOICE: ProjectEvent.REVISION_SCOPED_TO_VOICE,
    RevisionScope.TIMELINE: ProjectEvent.REVISION_SCOPED_TO_TIMELINE,
}

_UNROUTED_SCOPES = tuple(scope.value for scope in RevisionScope if scope not in _SCOPE_EVENTS)
if _UNROUTED_SCOPES:  # pragma: no cover - a start-up failure, asserted by the unit suite
    raise RuntimeError(f"revision scopes with no transition: {_UNROUTED_SCOPES}")


def revision_event(scope: RevisionScope) -> ProjectEvent:
    """Total over `RevisionScope`. The edge a revision of this scope takes out of the wait."""

    return _SCOPE_EVENTS[scope]


def waits_for_user(state: ProjectState) -> bool:
    """Whether the project is blocked on a person, so the step timeout must not apply."""

    return state in _USER_WAIT_STATES


def advanceable_states() -> tuple[ProjectState, ...]:
    """The states a worker may claim. Terminal states are never claimed."""

    return tuple(state for state in ProjectState if state not in _TERMINAL_STATES)


def working_states() -> tuple[ProjectState, ...]:
    """The states in which the machine is doing the work, so a step can fail underneath it.

    Distinct from `advanceable_states` by exactly the states that wait on a person and by
    `PREVIEW_READY`: a customer who has not uploaded anything has not failed at anything, and a
    finished preview has nothing left to fail at. Those waits end by cancellation, not failure.
    """

    return _WORKING_STATES


class LifecycleTransitionError(RuntimeError):
    """A transition PRD §20 does not draw was attempted. A defect, not tenant input."""

    def __init__(self, state: ProjectState, event: ProjectEvent) -> None:
        super().__init__(f"PROJECT_TRANSITION_NOT_ALLOWED: {state.value} -> {event.value}")
        self.state = state
        self.event = event


def require_next_state(state: ProjectState, event: ProjectEvent) -> ProjectState:
    """`next_state`, raising rather than returning `None`. For paths the code chose itself."""

    target = next_state(state, event)
    if target is None:
        raise LifecycleTransitionError(state, event)
    return target


# --- what a QC verdict means for a project ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    """One decision, expressed as the exact sequence of transitions it causes.

    Returning the events rather than an action name is what keeps the decision auditable: the
    transition log of a bounded retry reads `qc_failed` then `retry_requested`, which is PRD
    §20's own path through `FAILED`, and no reader has to know that a service somewhere chose to
    take two steps at once.
    """

    events: tuple[ProjectEvent, ...]
    requires_human_review: bool
    failure_code: str | None
    recommended_path: RemediationPath

    @property
    def retries_render(self) -> bool:
        return ProjectEvent.RETRY_REQUESTED in self.events


def decide_after_qc(
    *,
    verdict: QcVerdict,
    path: RemediationPath,
    attempts_used: int,
    max_attempts: int,
) -> LifecycleOutcome:
    """Turn slice 2D's judgement into slice 2E's transitions. Pure and total.

    The rules, in order, and the reason each one is where it is:

    - `passed` → `PREVIEW_READY`. Nothing to remediate, so the suggested path is recorded as it
      stands and ignored.
    - `needs_review` → `PREVIEW_READY` **with the human-review flag set**. Treating it as a
      failure would stop the product dead: the vision adapter is disabled until a real provider
      is connected (after W08), so slice 2D's fail-closed rule makes *every* render today
      `needs_review`. The output is shown and marked, which is the honest reading of "a person
      has to look".
    - `failed` + `retry_render` + attempts left → a bounded re-render, routed through `FAILED`
      and `RETRYING` exactly as §20 draws it.
    - `failed` anything else, or attempts exhausted → `FAILED` with the human-review flag.

    `alternative_scene`, `alternative_provider` and `request_new_media` are **recorded and not
    executed**. Each needs a capability this slice does not have — re-selecting footage, a second
    render provider, a message to the customer — and pretending to act on one would mean a
    project that says it tried something it did not.
    """

    if verdict is QcVerdict.PASSED:
        return LifecycleOutcome(
            events=(ProjectEvent.QC_PASSED,),
            requires_human_review=False,
            failure_code=None,
            recommended_path=path,
        )
    if verdict is QcVerdict.NEEDS_REVIEW:
        return LifecycleOutcome(
            events=(ProjectEvent.QC_NEEDS_REVIEW,),
            requires_human_review=True,
            failure_code=None,
            recommended_path=path,
        )
    if path is RemediationPath.RETRY_RENDER and attempts_used < max_attempts:
        return LifecycleOutcome(
            events=(ProjectEvent.QC_FAILED, ProjectEvent.RETRY_REQUESTED),
            requires_human_review=False,
            failure_code=None,
            recommended_path=path,
        )
    return LifecycleOutcome(
        events=(ProjectEvent.QC_FAILED,),
        requires_human_review=True,
        failure_code=(
            FAILURE_RENDER_ATTEMPTS_EXHAUSTED
            if path is RemediationPath.RETRY_RENDER
            else FAILURE_RENDER_FAILED
        ),
        recommended_path=path,
    )


def decide_after_render_failure(*, attempts_used: int, max_attempts: int) -> LifecycleOutcome:
    """A render job that never produced an output, bounded by the same counter.

    QC never ran, so there is no verdict and no suggestion; the only question is whether another
    encode is allowed. Sharing the counter with `decide_after_qc` is deliberate — a project that
    alternates between a broken encode and a failing check must still stop.
    """

    if attempts_used < max_attempts:
        return LifecycleOutcome(
            events=(ProjectEvent.STEP_FAILED, ProjectEvent.RETRY_REQUESTED),
            requires_human_review=False,
            failure_code=None,
            recommended_path=RemediationPath.RETRY_RENDER,
        )
    return LifecycleOutcome(
        events=(ProjectEvent.STEP_FAILED,),
        requires_human_review=True,
        failure_code=FAILURE_RENDER_ATTEMPTS_EXHAUSTED,
        recommended_path=RemediationPath.HUMAN_REVIEW,
    )


# --- composing the timeline -------------------------------------------------------------------


class TimelineCompositionError(ValueError):
    """The script and the available footage cannot produce a renderable §18.2 document."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ComposerSegment:
    """One script segment reduced to what clip selection actually uses."""

    required_scene_tags: tuple[str, ...]
    target_duration_ms: int


def normalize_scene_tag(value: str) -> str:
    """Put a scene label into the spelling a script's `required_scene_tags` are stored in.

    This is the third caller of `text_normalization`, and it is deliberately the *only* new one:
    slice 2B normalized a script's tags with `normalize_encoding` and then joined words with
    underscores, so anything compared against them has to be put through the same two steps or
    the comparison silently never matches. Doing it here rather than in the repository keeps one
    definition of "a scene tag" beside the matcher that uses it.

    `normalize_encoding`, never `normalize_for_matching`: this value is *compared with a stored
    value*, not scanned for a forbidden phrase. Folding it to ASCII would turn `ürün` into `urun`
    on one side of an equality only — which is the product bug wearing a security fix that
    slice 2B's own comment warns about.
    """

    return normalize_encoding(value.strip()).replace(" ", "_").replace("-", "_")


@dataclass(frozen=True, slots=True)
class SceneCandidate:
    """One window of one source asset that may become a clip, with what was seen in it.

    `tags` are the labels video understanding produced, put through `normalize_scene_tag` so they
    are spelled the way a script's `required_scene_tags` are spelled.
    """

    asset_id: UUID
    start_ms: int
    end_ms: int
    tags: frozenset[str]

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def compose_timeline(
    *,
    segments: Sequence[ComposerSegment],
    candidates: Sequence[SceneCandidate],
    profile: RenderProfile,
    voiceover_id: UUID | None,
    voiceover_duration_ms: int | None,
    max_duration_ms: int,
) -> Timeline:
    """Lay a script over detected scenes and return a §18.2 document. Pure — no I/O, no clock.

    The selection rule is deliberately simple and deterministic: walk the segments in order and
    take the first unused scene whose tags intersect what the segment asked for, falling back to
    the first unused scene at all. A cleverer selector is a real feature — it wants embeddings
    and a similarity search, which is the Phase 1 gap PRD §16.4–16.5 still names — and a
    non-deterministic one would make "why did this render use that shot?" unanswerable.

    Nothing is *composed* beyond the cuts: no overlays, and captions off. That is the line this
    slice draws rather than an oversight. Overlay text is the parametric-editing surface (K4) and
    an automatically placed one would be a claim about a frame nobody chose; captions sourced
    from a transcript would caption the *original* audio underneath a voiceover saying something
    else. Both are slice 2F's, on a document a person is already looking at.
    """

    if not segments:
        raise TimelineCompositionError(FAILURE_NO_USABLE_SCENE)
    usable = [item for item in candidates if item.duration_ms >= MIN_CLIP_DURATION_MS]
    if not usable:
        raise TimelineCompositionError(FAILURE_NO_USABLE_SCENE)

    spec = profile_spec(profile)
    remaining = list(usable)
    placed: list[tuple[Clip, SceneCandidate]] = []
    cursor = 0
    for segment in segments[:MAX_CLIPS_PER_TRACK]:
        if not remaining:
            # Fewer scenes than segments: the footage runs out and the video is shorter than the
            # script asked for. The voiceover fit check below is what turns that into a refusal
            # when it actually matters.
            break
        candidate = remaining.pop(_best_match(remaining, segment.required_scene_tags))
        duration = min(max(segment.target_duration_ms, MIN_CLIP_DURATION_MS), candidate.duration_ms)
        placed.append((_clip(candidate, duration=duration, timeline_start_ms=cursor), candidate))
        cursor += duration

    if not placed:
        raise TimelineCompositionError(FAILURE_NO_USABLE_SCENE)

    required = voiceover_duration_ms or 0
    if cursor < required:
        placed, cursor = _extend_to(placed, remaining, cursor, required)
    if cursor < required:
        # Speech that outlasts every frame available. Refusing here costs a failed project;
        # allowing it costs a video whose last sentence is cut off mid-word, which §18.3's
        # duration rule would reject a moment later anyway with a less useful code.
        raise TimelineCompositionError(FAILURE_TIMELINE_TOO_SHORT)
    if cursor > min(max_duration_ms, MAX_CANVAS_DURATION_MS):
        raise TimelineCompositionError(FAILURE_TIMELINE_TOO_LONG)

    audio_tracks = [
        AudioTrack(
            kind=AudioTrackKind.ORIGINAL,
            asset_id=None,
            gain_db=0,
            # §18.2 shows `duck_under_voice` on a music track; it is a per-track flag meaning
            # "hold this bed down while the voice speaks", and the bed here is the footage's own
            # sound. Music is not a supported source yet (it needs a licence record), so the
            # original track is the one that has to give way.
            duck_under_voice=voiceover_id is not None,
        )
    ]
    if voiceover_id is not None:
        audio_tracks.append(
            AudioTrack(
                kind=AudioTrackKind.VOICEOVER,
                asset_id=voiceover_id,
                gain_db=0,
                duck_under_voice=False,
            )
        )

    return Timeline(
        version=TIMELINE_VERSION,
        canvas=Canvas(width=spec.width, height=spec.height, fps=spec.fps, duration_ms=cursor),
        video_tracks=(VideoTrack(track=1, clips=tuple(clip for clip, _ in placed)),),
        audio_tracks=tuple(audio_tracks),
        overlays=(),
        captions=Captions(enabled=False, source=CaptionSource.TRANSCRIPT, style_id=_CAPTION_STYLE),
    )


_CAPTION_STYLE: Final = "brand-caption-v1"


def _best_match(candidates: Sequence[SceneCandidate], tags: Sequence[str]) -> int:
    """Index of the first candidate sharing a tag, else 0. Deterministic by construction."""

    wanted = {_match_key(tag) for tag in tags}
    for index, candidate in enumerate(candidates):
        if {_match_key(tag) for tag in candidate.tags} & wanted:
            return index
    return 0


def _match_key(tag: str) -> str:
    """The one extra fold selection compares on, applied identically to both sides.

    `normalize_scene_tag` reproduces exactly what slice 2B stored, which includes a **Turkish**
    lowercase: `I` becomes `ı`, not `i`. That is right for Turkish prose and wrong for a label
    vocabulary, where a vision provider writing `PREPARATION` and a script asking for
    `preparation` mean the same thing and would otherwise never meet.

    The fold is deliberately not in `normalize_scene_tag`: that function has to keep agreeing
    with `script._scene_tags` character for character, because it describes how a tag is
    *stored*. This one exists only inside the comparison, changes nothing on disk, and its worst
    case is that two genuinely different Turkish words select the same shot — which costs a
    differently chosen cut, not a wrong claim on a frame.
    """

    return tag.replace("ı", "i")


def _clip(candidate: SceneCandidate, *, duration: int, timeline_start_ms: int) -> Clip:
    return Clip(
        asset_id=candidate.asset_id,
        source_start_ms=candidate.start_ms,
        source_end_ms=candidate.start_ms + duration,
        timeline_start_ms=timeline_start_ms,
        crop_mode=CropMode.SMART_COVER,
        transition_out=TransitionKind.CUT,
    )


def _extend_to(
    placed: list[tuple[Clip, SceneCandidate]],
    remaining: list[SceneCandidate],
    cursor: int,
    required: int,
) -> tuple[list[tuple[Clip, SceneCandidate]], int]:
    """Lengthen the cut until the speech fits, first by widening the last clip, then by adding.

    Widening comes first because it keeps the shot count the script asked for. Only when the last
    scene has no more footage does an unused scene get appended — and when neither is possible
    the caller refuses rather than trimming the voiceover, because the audio is the thing that
    was written and approved.
    """

    last_clip, last_candidate = placed[-1]
    headroom = last_candidate.end_ms - last_clip.source_end_ms
    if headroom > 0:
        grow = min(headroom, required - cursor)
        placed[-1] = (
            _clip(
                last_candidate,
                duration=last_clip.duration_ms + grow,
                timeline_start_ms=last_clip.timeline_start_ms,
            ),
            last_candidate,
        )
        cursor += grow
    while cursor < required and remaining and len(placed) < MAX_CLIPS_PER_TRACK:
        candidate = remaining.pop(0)
        duration = min(candidate.duration_ms, max(MIN_CLIP_DURATION_MS, required - cursor))
        placed.append((_clip(candidate, duration=duration, timeline_start_ms=cursor), candidate))
        cursor += duration
    return (placed, cursor)
