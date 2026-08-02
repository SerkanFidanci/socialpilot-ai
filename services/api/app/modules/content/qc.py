"""Automatic quality control (PRD §19.4): what is checked, how it is judged, nothing acted on.

Slices 2A–2C built production. None of them built *trust*: a render that finished is recorded
`succeeded` today without anyone establishing that the file opens, carries sound, keeps its text
inside the frame, or still quotes the price the record holds. This module is the contract for
establishing that, and three properties decide its whole shape.

**QC fails closed.** A check that could not be run is `unknown`, and any `unknown` drags the
verdict to at least `needs_review`. Nothing is skipped, nothing defaults to `passed`. The reason
is not caution for its own sake: a QC that approves what it did not measure is *worse* than no QC
at all, because it manufactures confidence. `build_results` therefore starts from the complete
check set marked `unknown` and lets callers overwrite entries — omitting a check is not
expressible rather than merely discouraged.

**QC decides; it never acts.** `decide` returns a verdict and a *suggested* path. It never
triggers a re-render, never switches provider, never counts an attempt. Binding an action to a
judgement here would bury an unbounded render loop inside the checker; the loop bound belongs to
the lifecycle (slice 2E), so the decision has to be inert until that exists.

**The check set is the requirement's, not ours.** Every entry in `QcCheck` is one line of PRD
§19.4, in order. That matters because this pipeline has lost four rounds to hand-counted sets
(a confusables table met Coptic; an invisibles list met an unassigned code point; an inflection
list met `lirayla`). The answer here is not a longer list — the list is fixed by the PRD — but
totality: `CHECK_POLICIES` covers every member, `build_results` emits every member, and
`decide` refuses a set that is missing one. Adding a check to the enum without a policy fails a
test rather than silently producing a report with a hole in it.

Thresholds live in configuration and are *snapshotted into every report* (`QcThresholds`), not
just versioned. A number nobody recorded makes yesterday's report incomparable with today's.

Nothing here reads a database, touches a file, or names a media tool. The evaluators are pure
functions over a measurement and a set of tenant facts the service gathered, which is what lets
the same judgement run in the worker and in a test with no infrastructure at all.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol
from uuid import UUID

from app.modules.content.render import RenderProfile, profile_spec
from app.modules.content.script import ProviderDescriptor, RouteSnapshot
from app.modules.content.timeline import TextStyle
from app.modules.content.validation import layout_text_in_frame

__all__ = [
    "CHECK_POLICIES",
    "CheckResult",
    "CheckStatus",
    "DETERMINISTIC_CHECKS",
    "MODEL_CHECKS",
    "MediaQcProbePermanentError",
    "MediaQcProbePort",
    "MediaQcProbeTransientError",
    "OverlayTextFact",
    "ProviderDescriptor",
    "QcCheck",
    "QcCheckKind",
    "QcCheckPolicy",
    "QcDecision",
    "QcFacts",
    "QcMeasurement",
    "QcProbeRequest",
    "QcRunStatus",
    "QcThresholds",
    "QcVerdict",
    "RemediationPath",
    "RouteSnapshot",
    "VISUAL_QC_CAPABILITY",
    "VerifiedRecordState",
    "VerifiedSourceAudit",
    "VisualQcDisabledError",
    "VisualQcFinding",
    "VisualQcPermanentError",
    "VisualQcPort",
    "VisualQcReport",
    "VisualQcRequest",
    "VisualQcTransientError",
    "audit_verified_sources",
    "build_results",
    "decide",
    "evaluate_deterministic",
    "merge_check_results",
    "model_check_results",
    "serialize_results",
]

# PRD §17.1's capability naming, applied to the visual half of §19.4. It is what a
# `provider_usage` row records, so the cost of every inspection sums with one equality test.
VISUAL_QC_CAPABILITY: Final = "visual_qc"


class QcCheck(StrEnum):
    """PRD §19.4's list, in the order it is written there.

    The order is load-bearing twice over: a report iterates it, and `decide` scans it to pick
    which offender's remediation to suggest. Both mean the enum is the single place the check
    set is defined — a check that is not here cannot be reported, and one that is here cannot be
    left out of a report.
    """

    # "Video açılıyor mu"
    CONTAINER_READABLE = "container_readable"
    # "Süre doğru mu"
    DURATION_MATCHES_PLAN = "duration_matches_plan"
    # "Ses var mı"
    AUDIO_PRESENT = "audio_present"
    # "Loudness"
    LOUDNESS = "loudness"
    # "Siyah frame"
    BLACK_FRAMES = "black_frames"
    # "Boş/sabit görüntü"
    STATIC_FRAMES = "static_frames"
    # "Yazılar kadraj dışında mı"
    TEXT_WITHIN_SAFE_AREA = "text_within_safe_area"
    # "Logo görünür mü"
    LOGO_VISIBLE = "logo_visible"
    # "Altyazı senkronu" — measured here as the drift between synthesized speech and the
    # script's target (slice 2C's `drift_ms`). Captions in this pipeline are projected from
    # stored transcript rows onto cut geometry by deterministic arithmetic, so they cannot drift
    # away from the picture; speech produced by a provider can, and does. Checking the thing
    # that can actually be wrong is the honest reading of this line.
    SPEECH_SYNC = "speech_sync"
    # "Fiyat ve tarih kaynağa uyuyor mu"
    VERIFIED_VALUES_CURRENT = "verified_values_current"
    # "Hassas/uygunsuz içerik"
    SENSITIVE_CONTENT = "sensitive_content"
    # "Yüz bozulması"
    FACE_INTEGRITY = "face_integrity"
    # "Üretken sahnede ürün şekli değişmiş mi"
    PRODUCT_SHAPE = "product_shape"


class QcCheckKind(StrEnum):
    """How a check is answered. `model` checks need a vision provider; `deterministic` do not."""

    DETERMINISTIC = "deterministic"
    MODEL = "model"


class CheckStatus(StrEnum):
    """`unknown` is a first-class answer, not an absence.

    A check that raised, whose adapter is switched off, or whose measurement never arrived is
    `unknown`. It is never `passed`, and it is never omitted — those are the two ways a quality
    gate quietly stops being one.
    """

    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class QcVerdict(StrEnum):
    PASSED = "passed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class RemediationPath(StrEnum):
    """PRD §19.4's "QC başarısızsa" list, plus `none` for a report with nothing to fix.

    These are *suggestions*. Nothing in this slice executes one: automatic re-render, scene
    substitution, provider switching and attempt limits are slice 2E's, because that is where a
    loop can be bounded.
    """

    NONE = "none"
    RETRY_RENDER = "retry_render"
    ALTERNATIVE_SCENE = "alternative_scene"
    ALTERNATIVE_PROVIDER = "alternative_provider"
    HUMAN_REVIEW = "human_review"
    REQUEST_NEW_MEDIA = "request_new_media"


class QcRunStatus(StrEnum):
    """The lifecycle of one QC run, separate from the verdict it reaches.

    `pending` is written and committed before any measurement, so a run killed mid-way leaves a
    row that says so. A `failed` run is one that could not complete — which is not the same fact
    as a `failed` verdict, and conflating the two would let an infrastructure outage read as a
    bad video.
    """

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class QcCheckPolicy:
    """What one check means when it does not pass.

    `blocking` separates "this output must not be shown to anyone" from "a human has to look".
    `on_failure` is the *default* remediation; an evaluator may narrow it with what it measured
    (a frame that is 3% black suggests a different cut, a frame that is 100% black suggests the
    source itself is unusable), which is why `CheckResult` carries its own path.
    """

    kind: QcCheckKind
    blocking: bool
    on_failure: RemediationPath


# One entry per member of `QcCheck`. A test asserts the two sets are equal, so a check added
# without a policy — the only way a report could reach an undefined decision — cannot ship.
CHECK_POLICIES: Final[Mapping[QcCheck, QcCheckPolicy]] = {
    # An output that does not open is worthless; retrying the encode is the only sensible move.
    QcCheck.CONTAINER_READABLE: QcCheckPolicy(
        QcCheckKind.DETERMINISTIC, blocking=True, on_failure=RemediationPath.RETRY_RENDER
    ),
    # A three-second file where twenty seconds of cuts were planned is a broken encode.
    QcCheck.DURATION_MATCHES_PLAN: QcCheckPolicy(
        QcCheckKind.DETERMINISTIC, blocking=True, on_failure=RemediationPath.RETRY_RENDER
    ),
    # A silent advertisement is a failed advertisement, not a stylistic choice.
    QcCheck.AUDIO_PRESENT: QcCheckPolicy(
        QcCheckKind.DETERMINISTIC, blocking=True, on_failure=RemediationPath.RETRY_RENDER
    ),
    # Too quiet or too loud still plays. A person decides whether it ships.
    QcCheck.LOUDNESS: QcCheckPolicy(
        QcCheckKind.DETERMINISTIC, blocking=False, on_failure=RemediationPath.RETRY_RENDER
    ),
    QcCheck.BLACK_FRAMES: QcCheckPolicy(
        QcCheckKind.DETERMINISTIC, blocking=True, on_failure=RemediationPath.ALTERNATIVE_SCENE
    ),
    QcCheck.STATIC_FRAMES: QcCheckPolicy(
        QcCheckKind.DETERMINISTIC, blocking=False, on_failure=RemediationPath.ALTERNATIVE_SCENE
    ),
    # Pre-render validation already refused text that does not fit the *profile*. Reaching here
    # means the frame that came out disagrees with the frame that was validated, and re-running
    # the same deterministic arithmetic would reach the same answer — so this is a question for
    # a person, never a retry that would loop.
    QcCheck.TEXT_WITHIN_SAFE_AREA: QcCheckPolicy(
        QcCheckKind.DETERMINISTIC, blocking=False, on_failure=RemediationPath.HUMAN_REVIEW
    ),
    QcCheck.LOGO_VISIBLE: QcCheckPolicy(
        QcCheckKind.MODEL, blocking=False, on_failure=RemediationPath.RETRY_RENDER
    ),
    # Speech that overshot its target is a synthesis problem; a different voice or provider is
    # the lever, not a re-encode of the same audio.
    QcCheck.SPEECH_SYNC: QcCheckPolicy(
        QcCheckKind.DETERMINISTIC, blocking=False, on_failure=RemediationPath.ALTERNATIVE_PROVIDER
    ),
    # Printing a price or a date the record no longer holds is the worst outcome this product
    # can produce, and re-rendering would quietly substitute a value nobody approved.
    QcCheck.VERIFIED_VALUES_CURRENT: QcCheckPolicy(
        QcCheckKind.DETERMINISTIC, blocking=True, on_failure=RemediationPath.HUMAN_REVIEW
    ),
    QcCheck.SENSITIVE_CONTENT: QcCheckPolicy(
        QcCheckKind.MODEL, blocking=True, on_failure=RemediationPath.HUMAN_REVIEW
    ),
    QcCheck.FACE_INTEGRITY: QcCheckPolicy(
        QcCheckKind.MODEL, blocking=False, on_failure=RemediationPath.ALTERNATIVE_SCENE
    ),
    QcCheck.PRODUCT_SHAPE: QcCheckPolicy(
        QcCheckKind.MODEL, blocking=False, on_failure=RemediationPath.ALTERNATIVE_SCENE
    ),
}

DETERMINISTIC_CHECKS: Final = tuple(
    check for check, policy in CHECK_POLICIES.items() if policy.kind is QcCheckKind.DETERMINISTIC
)
MODEL_CHECKS: Final = tuple(
    check for check, policy in CHECK_POLICIES.items() if policy.kind is QcCheckKind.MODEL
)

# Why a check has no answer. Codes, never values: a QC report is read by support staff and must
# not become a second place a tenant's price or a customer's face is written down.
CODE_NOT_RUN: Final = "QC_CHECK_NOT_RUN"
CODE_MEASUREMENT_UNAVAILABLE: Final = "QC_MEASUREMENT_UNAVAILABLE"
CODE_PROVIDER_DISABLED: Final = "QC_VISUAL_PROVIDER_DISABLED"
CODE_PROVIDER_UNAVAILABLE: Final = "QC_VISUAL_PROVIDER_UNAVAILABLE"
CODE_PROVIDER_FAILED: Final = "QC_VISUAL_PROVIDER_FAILED"
CODE_PROVIDER_SILENT: Final = "QC_VISUAL_PROVIDER_DID_NOT_ANSWER"
CODE_COST_LIMIT: Final = "QC_VISUAL_COST_LIMIT_EXCEEDED"

# Why a check failed.
CODE_CONTAINER_UNREADABLE: Final = "QC_CONTAINER_UNREADABLE"
CODE_DURATION_OUT_OF_TOLERANCE: Final = "QC_DURATION_OUT_OF_TOLERANCE"
CODE_NO_AUDIO_STREAM: Final = "QC_NO_AUDIO_STREAM"
CODE_AUDIO_SILENT: Final = "QC_AUDIO_SILENT"
CODE_LOUDNESS_OUT_OF_WINDOW: Final = "QC_LOUDNESS_OUT_OF_WINDOW"
CODE_BLACK_FRAMES: Final = "QC_BLACK_FRAMES_EXCEED_LIMIT"
CODE_STATIC_FRAMES: Final = "QC_STATIC_FRAMES_EXCEED_LIMIT"
CODE_TEXT_OUTSIDE_SAFE_AREA: Final = "QC_TEXT_OUTSIDE_SAFE_AREA"
CODE_SPEECH_DRIFT: Final = "QC_SPEECH_DRIFT_EXCEEDS_LIMIT"
CODE_VERIFIED_VALUE_STALE: Final = "QC_VERIFIED_VALUE_STALE"
CODE_VERIFIED_VALUE_UNRESOLVABLE: Final = "QC_VERIFIED_VALUE_UNRESOLVABLE"
CODE_VERIFIED_VALUE_OUT_OF_WINDOW: Final = "QC_VERIFIED_VALUE_OUT_OF_WINDOW"
CODE_VERIFIED_VALUE_SUPERSEDED: Final = "QC_VERIFIED_VALUE_SUPERSEDED"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check's answer, with the numbers it was reached from.

    `measured` carries measurements and thresholds — never rendered text, never a resolved
    price, never an object key. `pointer` names *where* a problem is in the timeline the same
    way `ValidationIssue` does, so a report can be acted on without echoing tenant content.
    """

    check: QcCheck
    status: CheckStatus
    code: str | None = None
    pointer: str | None = None
    measured: Mapping[str, Any] | None = None
    remediation: RemediationPath | None = None

    @property
    def path(self) -> RemediationPath:
        """The suggested route out. An unmeasured check is always a person's problem.

        A check nobody could run carries no information about *what* to change, so proposing a
        re-render or a different scene would be a guess dressed as a decision.
        """

        if self.status is CheckStatus.UNKNOWN:
            return RemediationPath.HUMAN_REVIEW
        if self.remediation is not None:
            return self.remediation
        return CHECK_POLICIES[self.check].on_failure

    def as_document(self) -> dict[str, Any]:
        return {
            "check": self.check.value,
            "kind": CHECK_POLICIES[self.check].kind.value,
            "status": self.status.value,
            "code": self.code,
            "pointer": self.pointer,
            "measured": dict(self.measured) if self.measured else {},
            "remediation": (
                RemediationPath.NONE.value if self.status is CheckStatus.PASSED else self.path.value
            ),
        }


# How bad each status is. Merging two answers for one check keeps the worst, which is what makes
# the merge **commutative**: `failed` then `passed` and `passed` then `failed` are the same input
# set, so they must reach the same verdict. Last-write-wins was the bug — a failing check could be
# dropped from a report by supplying a passing one after it (Codex, 2026-08-02).
_STATUS_SEVERITY: Final[Mapping[CheckStatus, int]] = {
    CheckStatus.PASSED: 0,
    CheckStatus.UNKNOWN: 1,
    CheckStatus.FAILED: 2,
}

# The tie-break when two answers share a status, in PRD §19.4's own escalation order: re-encode,
# re-cut, re-route, ask a person, ask for new footage. It carries no claim that asking for media
# is "worse" than asking a person — it exists so that merging is *total*, which is what lets a
# shuffled input produce a byte-identical report rather than merely the same verdict.
_PATH_SEVERITY: Final[Mapping[RemediationPath, int]] = {
    path: index for index, path in enumerate(RemediationPath)
}


def _severity(result: CheckResult) -> tuple[int, int, str, str]:
    return (
        _STATUS_SEVERITY[result.status],
        _PATH_SEVERITY[result.path],
        result.code or "",
        result.pointer or "",
    )


def merge_check_results(results: Sequence[CheckResult]) -> tuple[CheckResult, ...]:
    """Collapse repeated answers for a check into one, keeping the worst. Order cannot matter.

    Two callers legitimately produce more than one answer for the same check, and they are not
    the same kind of event:

    - a **provider** may repeat itself in one response. That is data, not a defect: an adapter is
      outside our control, and the right reading of "sensitive content: failed, sensitive
      content: passed" is `failed`. Rejecting the response would turn a provider's sloppiness
      into an outage;
    - our **own** code supplying a check twice is a defect, and `build_results` says so —
      but it says so *after* merging, so the report is fail-closed either way.

    Worst-wins is the only merge that keeps the guarantee this module exists for. Any rule that
    could let `failed` lose is a rule by which a bad output reaches a customer.
    """

    grouped: dict[QcCheck, CheckResult] = {}
    for result in results:
        previous = grouped.get(result.check)
        if previous is None or _severity(result) > _severity(previous):
            grouped[result.check] = result
    return tuple(grouped[check] for check in QcCheck if check in grouped)


def build_results(results: Sequence[CheckResult]) -> tuple[CheckResult, ...]:
    """Complete `results` into the full check set, in `QcCheck` order.

    Every member starts `unknown` with `QC_CHECK_NOT_RUN` and is replaced only if a caller
    supplied an answer for it. This is the structural half of fail-closed: a service that
    forgets a check produces a report that says so, rather than a report that is silently one
    check short and reads as clean.

    Repeated answers are merged worst-first and *then* refused. Both halves are deliberate. The
    merge runs first so the fail-closed property never depends on the error being raised — a
    caller that catches it, or a future one that does not raise at all, still gets the worst
    answer. The refusal follows because a duplicate from our own code is a defect in the caller
    rather than something to absorb, exactly like the incomplete set `decide` refuses.
    """

    merged = merge_check_results(results)
    if len(merged) != len(results):
        counts = Counter(result.check for result in results)
        repeated = sorted(check.value for check, seen in counts.items() if seen > 1)
        raise ValueError(f"QC_REPORT_DUPLICATE_RESULT: {', '.join(repeated)}")
    supplied = {result.check: result for result in merged}
    return tuple(
        supplied.get(check, CheckResult(check=check, status=CheckStatus.UNKNOWN, code=CODE_NOT_RUN))
        for check in QcCheck
    )


def serialize_results(results: Sequence[CheckResult]) -> list[dict[str, Any]]:
    return [result.as_document() for result in results]


@dataclass(frozen=True, slots=True)
class QcDecision:
    verdict: QcVerdict
    path: RemediationPath


def decide(results: Sequence[CheckResult]) -> QcDecision:
    """Turn a complete set of check results into one verdict and one suggested path.

    Pure and total. The same set always produces the same answer, and there is no combination it
    does not cover:

    - any *blocking* check `failed` → `failed`
    - otherwise any check `failed` or `unknown` → `needs_review`
    - otherwise → `passed`

    The suggested path is the first offender's, scanned in `QcCheck` order, with blocking
    failures considered before non-blocking ones and those before unknowns. That ordering is the
    whole tie-break rule: it is stated once, it is deterministic, and it puts the most serious
    reason first rather than whichever check happened to run last.
    """

    answered = {result.check: result for result in results}
    missing = [check.value for check in QcCheck if check not in answered]
    if missing:
        # Not a verdict — a defect in the caller. Guessing a verdict for a report that is
        # missing checks is precisely the failure mode this module exists to prevent.
        raise ValueError(f"QC_REPORT_INCOMPLETE: {', '.join(missing)}")

    blocking = [
        check
        for check in QcCheck
        if answered[check].status is CheckStatus.FAILED and CHECK_POLICIES[check].blocking
    ]
    if blocking:
        return QcDecision(QcVerdict.FAILED, answered[blocking[0]].path)
    advisory = [check for check in QcCheck if answered[check].status is CheckStatus.FAILED]
    if advisory:
        return QcDecision(QcVerdict.NEEDS_REVIEW, answered[advisory[0]].path)
    unknown = [check for check in QcCheck if answered[check].status is CheckStatus.UNKNOWN]
    if unknown:
        return QcDecision(QcVerdict.NEEDS_REVIEW, answered[unknown[0]].path)
    return QcDecision(QcVerdict.PASSED, RemediationPath.NONE)


# --- thresholds -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QcThresholds:
    """Every number a check compares against, snapshotted into the report that used it.

    A version alone would not be enough. "Which thresholds produced this verdict" has to be
    answerable from the row itself, or two reports written a month apart cannot be compared and
    a threshold changed by accident is invisible in the record.

    None of these are platform facts. Instagram publishes no loudness contract this repository is
    allowed to quote, so the window below is *our* product default in the region streaming
    services normalize to, and it is configuration precisely because it is a judgement call.
    """

    version: int
    duration_tolerance_ms: int
    loudness_target_lufs: float
    loudness_tolerance_lu: float
    silence_floor_lufs: float
    black_ratio_limit: float
    static_ratio_limit: float
    unusable_source_ratio: float
    speech_drift_ms: int

    def as_document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "duration_tolerance_ms": self.duration_tolerance_ms,
            "loudness_target_lufs": self.loudness_target_lufs,
            "loudness_tolerance_lu": self.loudness_tolerance_lu,
            "silence_floor_lufs": self.silence_floor_lufs,
            "black_ratio_limit": self.black_ratio_limit,
            "static_ratio_limit": self.static_ratio_limit,
            "unusable_source_ratio": self.unusable_source_ratio,
            "speech_drift_ms": self.speech_drift_ms,
        }


# --- the deterministic measurement port -------------------------------------------------------


class MediaQcProbeTransientError(RuntimeError):
    """The measurement could not be taken. Says nothing about the file — the checks go `unknown`."""


class MediaQcProbePermanentError(RuntimeError):
    """The file is not media this pipeline can measure. That *is* an answer: it does not open."""


@dataclass(frozen=True, slots=True)
class QcProbeRequest:
    """One local file to measure, plus where sample frames may be written.

    The path is a file the worker materialized into its own scratch directory. No object key, no
    signed URL and no credential crosses this boundary — the adapter is handed bytes on disk and
    nothing that could fetch more.
    """

    path: Path
    workdir: Path
    frame_sample_count: int
    frame_max_width: int
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class QcMeasurement:
    """What the rendered file itself says, observed rather than assumed.

    Every field is measured from the output, not copied from the plan that asked for it. That is
    the entire point of the exercise: `render_outputs` already records what the adapter *said* it
    produced, and a check against the adapter's own account would verify nothing.
    """

    duration_ms: int
    width: int
    height: int
    video_codec: str
    audio_codec: str | None
    has_audio_stream: bool
    # `None` when there is no audio stream to integrate, or when the loudness pass could not run.
    # There is deliberately no true-peak field: PRD §19.4 asks for "Loudness", and integrated
    # loudness is that. Clipping is §18.3's line and belongs to the pre-render rules.
    integrated_loudness_lufs: float | None
    black_ratio: float
    longest_black_ms: int
    static_ratio: float
    longest_static_ms: int
    # Sampled frames for the model checks. Local paths in the run's scratch directory; they are
    # deliberately absent from `as_document`, because a report is a record and these are debris.
    frames: tuple[Path, ...] = ()

    def as_document(self) -> dict[str, Any]:
        return {
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "has_audio_stream": self.has_audio_stream,
            "integrated_loudness_lufs": self.integrated_loudness_lufs,
            "black_ratio": self.black_ratio,
            "longest_black_ms": self.longest_black_ms,
            "static_ratio": self.static_ratio,
            "longest_static_ms": self.longest_static_ms,
            "frame_sample_count": len(self.frames),
        }


class MediaQcProbePort(Protocol):
    """Measure one rendered file. There is no fixture implementation, on purpose.

    `create_audio_probe` made the same call in slice 2C and for the same reason: this port *is*
    the check that nobody's account of the output is taken at face value, so a fake probe would
    be a fixture verifying a fixture. It runs in every environment, and when it cannot run the
    checks that depend on it go `unknown`.
    """

    async def measure(self, *, request: QcProbeRequest) -> QcMeasurement: ...


# --- the model-check capability port ----------------------------------------------------------


class VisualQcTransientError(RuntimeError):
    """The provider failed for a reason that may not recur."""


class VisualQcPermanentError(RuntimeError):
    """The provider failed for a reason retrying cannot fix."""


class VisualQcDisabledError(RuntimeError):
    """No adapter may answer the model checks in this environment.

    Raised on call rather than at startup, under the rule W13 settled and the PM generalized: a
    capability whose output a human could approve falls back to a `disabled` adapter with a
    documented code, while infrastructure adapters keep being refused in `Settings`. A fixture
    that reports "no sensitive content, logo visible, faces intact" is exactly the kind of
    human-approvable output that rule is about — it is an *approval*, and an approval nobody
    computed must never look like one. The consequence is deliberate and visible: with no vision
    provider configured, all four model checks are `unknown` and every report says
    `needs_review`.
    """


@dataclass(frozen=True, slots=True)
class VisualQcRequest:
    """Provider-neutral input: sampled frames and what the timeline expects to be in them.

    `expects_logo` is a fact from the tenant's own timeline, not a hint the model may ignore — a
    render with no logo overlay cannot fail a logo-visibility check, and asking anyway would
    invent a finding.
    """

    frames: tuple[Path, ...]
    checks: tuple[QcCheck, ...]
    expects_logo: bool
    max_frames: int


@dataclass(frozen=True, slots=True)
class VisualQcFinding:
    """One model check's answer. `code` is a documented reason, never a description of a person."""

    check: QcCheck
    status: CheckStatus
    confidence: float
    code: str | None = None


@dataclass(frozen=True, slots=True)
class VisualQcReport:
    provider: str
    model: str
    findings: tuple[VisualQcFinding, ...]
    actual_cost_minor: int
    currency: str


class VisualQcPort(Protocol):
    """PRD §19.4's model-answered checks, behind ADR-004's adapter boundary."""

    @property
    def descriptor(self) -> ProviderDescriptor: ...

    async def inspect(
        self, *, request: VisualQcRequest, timeout_seconds: int
    ) -> VisualQcReport: ...


# --- what the deterministic checks are judged against -----------------------------------------


@dataclass(frozen=True, slots=True)
class VerifiedSourceAudit:
    """The result of re-reading every verified reference the timeline draws.

    `stale` holds `(pointer, code)` pairs — never the old value and never the new one. A QC
    report is read by support staff and stored indefinitely; making it a second place a tenant's
    price is written down would be a privacy and correctness regression dressed as diagnostics.
    """

    references: int
    stale: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class VerifiedRecordState:
    """One verified record as it stands *now*, in the three terms QC can reason about.

    `changed_at` is when the current value became the current value: a product price is append
    only, so it is the open row's `effective_from`; a campaign is edited in place, so it is its
    `updated_at`. `None` means the record carries no change timestamp at all — an approved CTA
    is the case today — and a value whose history cannot be read is one QC can only check for
    existence. That limit is stated here rather than hidden behind a `True`.
    """

    exists: bool
    within_window: bool
    changed_at: datetime | None


def audit_verified_sources(
    references: Sequence[tuple[str, UUID, str]],
    states: Mapping[tuple[str, UUID], VerifiedRecordState],
    *,
    rendered_at: datetime,
) -> VerifiedSourceAudit:
    """Compare every verified reference the frame drew against the record behind it today.

    The render resolved these references when the pixels were drawn and kept no copy — by design,
    since a copy of a price is a second place a price lives. So the question is answered from the
    records' own history instead: a reference is stale when it no longer resolves, when it has
    fallen outside its validity window, or when the value it resolves to *became* the current
    value after the render finished. The third is the "price changed" case and it is exact,
    because `product_prices` is append-only — the open row's `effective_from` is later than the
    render, which can only mean the figure on the frame is the row that was closed.

    Only pointers and codes come back. A report that quoted the old and new prices would solve
    the diagnosis problem by recreating the disclosure problem.
    """

    stale: list[tuple[str, str]] = []
    for source, reference_id, pointer in references:
        state = states.get((source, reference_id))
        if state is None or not state.exists:
            stale.append((pointer, CODE_VERIFIED_VALUE_UNRESOLVABLE))
            continue
        if not state.within_window:
            stale.append((pointer, CODE_VERIFIED_VALUE_OUT_OF_WINDOW))
            continue
        if state.changed_at is not None and state.changed_at > rendered_at:
            stale.append((pointer, CODE_VERIFIED_VALUE_SUPERSEDED))
    return VerifiedSourceAudit(references=len(references), stale=tuple(stale))


@dataclass(frozen=True, slots=True)
class OverlayTextFact:
    """One text overlay as it was drawn: the final string, its style, and its safe-area flag."""

    pointer: str
    text: str
    style: TextStyle
    safe_area: bool


@dataclass(frozen=True, slots=True)
class QcFacts:
    """Everything the deterministic evaluators need, gathered once by the service.

    Values only — no ORM instance, no session, no path. That keeps `evaluate_deterministic` a
    pure function of (facts, measurement, thresholds), which is why the decision table can be
    tested exhaustively without a database.
    """

    profile: RenderProfile
    expected_duration_ms: int
    expects_audio: bool
    overlay_texts: tuple[OverlayTextFact, ...]
    # `None` when the timeline places no voiceover. Absence is "nothing to be out of sync",
    # which is a measured fact about the document rather than a check nobody ran.
    voiceover_drift_ms: int | None
    verified: VerifiedSourceAudit


def evaluate_deterministic(
    *,
    facts: QcFacts,
    measurement: QcMeasurement | None,
    thresholds: QcThresholds,
    measurement_error: str | None = None,
) -> tuple[CheckResult, ...]:
    """Answer every deterministic check. Pure: no I/O, no clock, no provider.

    `measurement is None` splits two situations that must not be conflated. A *permanent* probe
    failure means the file did not open, and "does the video open" is thereby answered `failed`.
    A *transient* one means nothing was learned, and every check goes `unknown`. Reporting the
    second as the first would blame a video for a missing binary; reporting the first as the
    second would let an unopenable file reach `needs_review` instead of `failed`.

    Two checks are answered from the timeline alone and stay answerable even with no measurement
    at all: whether every verified reference still matches its record, and whether the speech
    drifted. Both are facts about tenant rows, not about the file.
    """

    results: list[CheckResult] = [
        _check_verified_values(facts),
        _check_speech_sync(facts, thresholds),
    ]
    if measurement is None:
        readable = (
            CheckResult(
                check=QcCheck.CONTAINER_READABLE,
                status=CheckStatus.FAILED,
                code=CODE_CONTAINER_UNREADABLE,
            )
            if measurement_error == CODE_CONTAINER_UNREADABLE
            else CheckResult(
                check=QcCheck.CONTAINER_READABLE,
                status=CheckStatus.UNKNOWN,
                code=measurement_error or CODE_MEASUREMENT_UNAVAILABLE,
            )
        )
        unmeasurable = (
            QcCheck.DURATION_MATCHES_PLAN,
            QcCheck.AUDIO_PRESENT,
            QcCheck.LOUDNESS,
            QcCheck.BLACK_FRAMES,
            QcCheck.STATIC_FRAMES,
            QcCheck.TEXT_WITHIN_SAFE_AREA,
        )
        return tuple(
            [readable]
            + [
                CheckResult(
                    check=check,
                    status=CheckStatus.UNKNOWN,
                    code=measurement_error or CODE_MEASUREMENT_UNAVAILABLE,
                )
                for check in unmeasurable
            ]
            + results
        )

    results.extend(
        (
            CheckResult(
                check=QcCheck.CONTAINER_READABLE,
                status=CheckStatus.PASSED,
                measured={
                    "duration_ms": measurement.duration_ms,
                    "width": measurement.width,
                    "height": measurement.height,
                    "video_codec": measurement.video_codec,
                },
            ),
            _check_duration(facts, measurement, thresholds),
            _check_audio_present(facts, measurement, thresholds),
            _check_loudness(measurement, thresholds),
            _check_black(measurement, thresholds),
            _check_static(measurement, thresholds),
            _check_safe_area(facts, measurement),
        )
    )
    return tuple(results)


def _check_duration(
    facts: QcFacts, measurement: QcMeasurement, thresholds: QcThresholds
) -> CheckResult:
    """Compare the measured length against the length the cuts add up to.

    The timeline's canvas is an upper bound, not a target — a document may legitimately place
    twelve seconds of clips on a twenty-second canvas — so the expectation is the sum of the cut
    windows, which is exactly what the renderer concatenates.
    """

    delta = measurement.duration_ms - facts.expected_duration_ms
    measured = {
        "expected_duration_ms": facts.expected_duration_ms,
        "measured_duration_ms": measurement.duration_ms,
        "delta_ms": delta,
        "tolerance_ms": thresholds.duration_tolerance_ms,
    }
    if abs(delta) > thresholds.duration_tolerance_ms:
        return CheckResult(
            check=QcCheck.DURATION_MATCHES_PLAN,
            status=CheckStatus.FAILED,
            code=CODE_DURATION_OUT_OF_TOLERANCE,
            measured=measured,
        )
    return CheckResult(
        check=QcCheck.DURATION_MATCHES_PLAN, status=CheckStatus.PASSED, measured=measured
    )


def _check_audio_present(
    facts: QcFacts, measurement: QcMeasurement, thresholds: QcThresholds
) -> CheckResult:
    """ "Ses var mı" is two questions, and a stream alone answers only the easy one.

    An AAC track full of digital silence satisfies "there is an audio stream" and fails what the
    requirement is actually asking. So the stream has to exist *and* the integrated loudness has
    to sit above a silence floor. When there is no measurable loudness the answer is `unknown`,
    not `passed` — the stream might be silent and nothing established otherwise.
    """

    if not measurement.has_audio_stream:
        return CheckResult(
            check=QcCheck.AUDIO_PRESENT,
            status=CheckStatus.FAILED,
            code=CODE_NO_AUDIO_STREAM,
            measured={"has_audio_stream": False, "expects_audio": facts.expects_audio},
        )
    if measurement.integrated_loudness_lufs is None:
        return CheckResult(
            check=QcCheck.AUDIO_PRESENT,
            status=CheckStatus.UNKNOWN,
            code=CODE_MEASUREMENT_UNAVAILABLE,
            measured={"has_audio_stream": True},
        )
    measured = {
        "has_audio_stream": True,
        "integrated_loudness_lufs": measurement.integrated_loudness_lufs,
        "silence_floor_lufs": thresholds.silence_floor_lufs,
    }
    if measurement.integrated_loudness_lufs <= thresholds.silence_floor_lufs:
        return CheckResult(
            check=QcCheck.AUDIO_PRESENT,
            status=CheckStatus.FAILED,
            code=CODE_AUDIO_SILENT,
            measured=measured,
        )
    return CheckResult(check=QcCheck.AUDIO_PRESENT, status=CheckStatus.PASSED, measured=measured)


def _check_loudness(measurement: QcMeasurement, thresholds: QcThresholds) -> CheckResult:
    """EBU R128 integrated loudness against a configured window, plus a true-peak ceiling."""

    if measurement.integrated_loudness_lufs is None:
        return CheckResult(
            check=QcCheck.LOUDNESS,
            status=CheckStatus.UNKNOWN,
            code=CODE_MEASUREMENT_UNAVAILABLE,
        )
    low = thresholds.loudness_target_lufs - thresholds.loudness_tolerance_lu
    high = thresholds.loudness_target_lufs + thresholds.loudness_tolerance_lu
    measured = {
        "integrated_loudness_lufs": measurement.integrated_loudness_lufs,
        "window_lufs": [low, high],
    }
    # A silent track is judged by `audio_present`; reporting it here as well would double-count
    # one defect into two failures and make the suggested path depend on enum order rather than
    # on what is wrong.
    if measurement.integrated_loudness_lufs <= thresholds.silence_floor_lufs:
        return CheckResult(check=QcCheck.LOUDNESS, status=CheckStatus.PASSED, measured=measured)
    if not low <= measurement.integrated_loudness_lufs <= high:
        return CheckResult(
            check=QcCheck.LOUDNESS,
            status=CheckStatus.FAILED,
            code=CODE_LOUDNESS_OUT_OF_WINDOW,
            measured=measured,
        )
    return CheckResult(check=QcCheck.LOUDNESS, status=CheckStatus.PASSED, measured=measured)


def _check_black(measurement: QcMeasurement, thresholds: QcThresholds) -> CheckResult:
    return _picture_defect(
        check=QcCheck.BLACK_FRAMES,
        ratio=measurement.black_ratio,
        longest_ms=measurement.longest_black_ms,
        limit=thresholds.black_ratio_limit,
        unusable_ratio=thresholds.unusable_source_ratio,
        code=CODE_BLACK_FRAMES,
    )


def _check_static(measurement: QcMeasurement, thresholds: QcThresholds) -> CheckResult:
    return _picture_defect(
        check=QcCheck.STATIC_FRAMES,
        ratio=measurement.static_ratio,
        longest_ms=measurement.longest_static_ms,
        limit=thresholds.static_ratio_limit,
        unusable_ratio=thresholds.unusable_source_ratio,
        code=CODE_STATIC_FRAMES,
    )


def _picture_defect(
    *,
    check: QcCheck,
    ratio: float,
    longest_ms: int,
    limit: float,
    unusable_ratio: float,
    code: str,
) -> CheckResult:
    """Black and frozen picture share a rule and a remediation split.

    A short black stretch is a bad *cut*: another scene from the same media fixes it. A file that
    is black or frozen nearly end to end is a bad *source*: there is no other scene to pick, and
    the only thing left to ask for is different footage. That is the one place in this module
    where the suggested path depends on a measured value rather than on the check's identity,
    and it is also the only reason PRD §19.4's `request_new_media` path is reachable at all.
    """

    measured = {"ratio": ratio, "longest_ms": longest_ms, "limit": limit}
    if ratio <= limit:
        return CheckResult(check=check, status=CheckStatus.PASSED, measured=measured)
    return CheckResult(
        check=check,
        status=CheckStatus.FAILED,
        code=code,
        measured=measured | {"unusable_source_ratio": unusable_ratio},
        remediation=(
            RemediationPath.REQUEST_NEW_MEDIA
            if ratio >= unusable_ratio
            else RemediationPath.ALTERNATIVE_SCENE
        ),
    )


def _check_safe_area(facts: QcFacts, measurement: QcMeasurement) -> CheckResult:
    """Re-run the safe-area arithmetic against the frame that actually came out.

    Pre-render validation measured the text against the *profile's* geometry. This measures it
    against the output's, so a render that silently landed at a different resolution — the one
    way validated text can end up outside the frame — is caught rather than assumed away.
    """

    spec = profile_spec(facts.profile)
    offenders: list[str] = []
    for overlay in facts.overlay_texts:
        _, fits = layout_text_in_frame(
            text=overlay.text,
            style=overlay.style,
            safe_area=spec.safe_area if overlay.safe_area else None,
            frame_width=measurement.width,
            frame_height=measurement.height,
        )
        if not fits:
            offenders.append(overlay.pointer)
    measured = {
        "overlays": len(facts.overlay_texts),
        "frame": [measurement.width, measurement.height],
        "profile_frame": [spec.width, spec.height],
    }
    if offenders:
        return CheckResult(
            check=QcCheck.TEXT_WITHIN_SAFE_AREA,
            status=CheckStatus.FAILED,
            code=CODE_TEXT_OUTSIDE_SAFE_AREA,
            pointer=offenders[0],
            measured=measured | {"offending_overlays": len(offenders)},
        )
    return CheckResult(
        check=QcCheck.TEXT_WITHIN_SAFE_AREA, status=CheckStatus.PASSED, measured=measured
    )


def _check_speech_sync(facts: QcFacts, thresholds: QcThresholds) -> CheckResult:
    """Slice 2C measured the drift; this is where it finally becomes a number with consequences.

    A timeline that places no voiceover passes with `applicable: false`. That is not a skipped
    check wearing a different hat: there is no speech in the output, so there is nothing that
    could be out of sync, and the fact is read from the document rather than assumed. The
    distinction that matters is *not applicable* (a known state of the timeline) versus *not
    measured* (nobody looked), and only the second one is `unknown`.
    """

    if facts.voiceover_drift_ms is None:
        return CheckResult(
            check=QcCheck.SPEECH_SYNC,
            status=CheckStatus.PASSED,
            measured={"applicable": False},
        )
    measured = {
        "applicable": True,
        "drift_ms": facts.voiceover_drift_ms,
        "limit_ms": thresholds.speech_drift_ms,
    }
    if abs(facts.voiceover_drift_ms) > thresholds.speech_drift_ms:
        return CheckResult(
            check=QcCheck.SPEECH_SYNC,
            status=CheckStatus.FAILED,
            code=CODE_SPEECH_DRIFT,
            measured=measured,
        )
    return CheckResult(check=QcCheck.SPEECH_SYNC, status=CheckStatus.PASSED, measured=measured)


def _check_verified_values(facts: QcFacts) -> CheckResult:
    """Did the frame's prices and dates survive contact with the records they came from?"""

    measured = {
        "references": facts.verified.references,
        "stale": len(facts.verified.stale),
    }
    if facts.verified.stale:
        pointer, code = facts.verified.stale[0]
        return CheckResult(
            check=QcCheck.VERIFIED_VALUES_CURRENT,
            status=CheckStatus.FAILED,
            code=code or CODE_VERIFIED_VALUE_STALE,
            pointer=pointer,
            measured=measured,
        )
    return CheckResult(
        check=QcCheck.VERIFIED_VALUES_CURRENT, status=CheckStatus.PASSED, measured=measured
    )


def model_check_results(
    report: VisualQcReport | None, *, requested: Sequence[QcCheck], code: str | None
) -> tuple[CheckResult, ...]:
    """Fold a provider's findings onto the model checks, filling every gap with `unknown`.

    A provider that answers three of four questions has not answered the fourth, and the report
    must say so. Trusting the adapter to return a complete set would put the fail-closed
    guarantee on the far side of the boundary, which is exactly where it cannot be enforced.

    A provider that answers the *same* question twice is the mirror image of that, and it is
    handled rather than refused: an adapter is outside our control, so "sensitive content:
    failed, sensitive content: passed" is merged to `failed` instead of turning a sloppy
    response into an outage. Reading only the last finding was how a refusal could be talked
    back out of a report (Codex, 2026-08-02). A finding for a check nobody asked about is
    dropped — an adapter does not get to widen the question set either.
    """

    answered = merge_check_results(
        [
            CheckResult(
                check=finding.check,
                status=finding.status,
                code=finding.code,
                measured={"confidence": finding.confidence},
            )
            for finding in (() if report is None else report.findings)
            if finding.check in set(requested)
        ]
    )
    by_check = {result.check: result for result in answered}
    return tuple(
        by_check.get(
            check,
            CheckResult(check=check, status=CheckStatus.UNKNOWN, code=code or CODE_PROVIDER_SILENT),
        )
        for check in requested
    )
