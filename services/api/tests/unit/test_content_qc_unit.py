"""The QC contract: totality, fail-closed, and a decision table with no undefined corner.

Everything here is pure. No database, no file, no provider — which is the point: if the
judgement half of QC needed infrastructure to be tested, the combinations that matter would be
tested by sampling instead of exhaustively.

Three properties carry the slice and each has a test that fails loudly if it stops holding:

1. **The check set is closed and total.** Every member of `QcCheck` has a policy, appears in
   every report, and is covered by the decision table.
2. **Nothing unmeasured passes.** `unknown` never yields `passed`, from any direction.
3. **The decision is a function.** The same results always produce the same verdict and the same
   suggested path, and no assignment of statuses leaves `decide` undefined.
"""

from __future__ import annotations

import itertools
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.modules.content.qc import (
    CHECK_POLICIES,
    CODE_AUDIO_SILENT,
    CODE_BLACK_FRAMES,
    CODE_CONTAINER_UNREADABLE,
    CODE_DURATION_OUT_OF_TOLERANCE,
    CODE_LOUDNESS_OUT_OF_WINDOW,
    CODE_MEASUREMENT_UNAVAILABLE,
    CODE_NO_AUDIO_STREAM,
    CODE_NOT_RUN,
    CODE_PROVIDER_DISABLED,
    CODE_SPEECH_DRIFT,
    CODE_STATIC_FRAMES,
    CODE_TEXT_OUTSIDE_SAFE_AREA,
    CODE_VERIFIED_VALUE_OUT_OF_WINDOW,
    CODE_VERIFIED_VALUE_SUPERSEDED,
    CODE_VERIFIED_VALUE_UNRESOLVABLE,
    DETERMINISTIC_CHECKS,
    MODEL_CHECKS,
    CheckResult,
    CheckStatus,
    OverlayTextFact,
    QcCheck,
    QcCheckKind,
    QcFacts,
    QcMeasurement,
    QcThresholds,
    QcVerdict,
    RemediationPath,
    VerifiedRecordState,
    VerifiedSourceAudit,
    VisualQcFinding,
    VisualQcReport,
    audit_verified_sources,
    build_results,
    decide,
    evaluate_deterministic,
    model_check_results,
    serialize_results,
)
from app.modules.content.render import RenderProfile
from app.modules.content.timeline import TEXT_STYLES

THRESHOLDS = QcThresholds(
    version=1,
    duration_tolerance_ms=750,
    loudness_target_lufs=-14.0,
    loudness_tolerance_lu=3.0,
    silence_floor_lufs=-50.0,
    black_ratio_limit=0.05,
    static_ratio_limit=0.30,
    unusable_source_ratio=0.90,
    speech_drift_ms=1_500,
)


def measurement(**overrides: object) -> QcMeasurement:
    base: dict[str, object] = {
        "duration_ms": 8_000,
        "width": 1080,
        "height": 1920,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio_stream": True,
        "integrated_loudness_lufs": -14.0,
        "black_ratio": 0.0,
        "longest_black_ms": 0,
        "static_ratio": 0.0,
        "longest_static_ms": 0,
    }
    return QcMeasurement(**(base | overrides))  # type: ignore[arg-type]


def facts(**overrides: object) -> QcFacts:
    base: dict[str, object] = {
        "profile": RenderProfile.INSTAGRAM_REELS_1080X1920,
        "expected_duration_ms": 8_000,
        "expects_audio": True,
        "overlay_texts": (),
        "voiceover_drift_ms": None,
        "verified": VerifiedSourceAudit(references=0, stale=()),
    }
    return QcFacts(**(base | overrides))  # type: ignore[arg-type]


def statuses(mapping: dict[QcCheck, CheckStatus]) -> tuple[CheckResult, ...]:
    return build_results(
        tuple(CheckResult(check=check, status=status) for check, status in mapping.items())
    )


# --- 1. the check set is closed and total -------------------------------------------------------


def test_every_check_has_a_policy_and_every_policy_names_a_check() -> None:
    """A check added without a policy is the one way a report could reach an undefined decision."""

    assert set(CHECK_POLICIES) == set(QcCheck)


def test_the_deterministic_and_model_partitions_cover_the_whole_set() -> None:
    assert set(DETERMINISTIC_CHECKS) | set(MODEL_CHECKS) == set(QcCheck)
    assert not set(DETERMINISTIC_CHECKS) & set(MODEL_CHECKS)


def test_a_report_always_carries_the_complete_check_set() -> None:
    """Omitting a check is not expressible: `build_results` completes what the caller supplied."""

    results = build_results(())
    assert tuple(result.check for result in results) == tuple(QcCheck)
    assert {result.status for result in results} == {CheckStatus.UNKNOWN}
    assert {result.code for result in results} == {CODE_NOT_RUN}


def test_a_partial_result_set_is_completed_rather_than_trusted() -> None:
    results = build_results((CheckResult(check=QcCheck.LOUDNESS, status=CheckStatus.PASSED),))
    answered = {result.check: result for result in results}
    assert len(answered) == len(QcCheck)
    assert answered[QcCheck.LOUDNESS].status is CheckStatus.PASSED
    assert answered[QcCheck.BLACK_FRAMES].status is CheckStatus.UNKNOWN


def test_decide_refuses_an_incomplete_set_instead_of_guessing() -> None:
    with pytest.raises(ValueError, match="QC_REPORT_INCOMPLETE"):
        decide((CheckResult(check=QcCheck.LOUDNESS, status=CheckStatus.PASSED),))


def test_the_serialized_report_names_every_check_and_its_kind() -> None:
    document = serialize_results(build_results(()))
    assert [entry["check"] for entry in document] == [check.value for check in QcCheck]
    assert {entry["kind"] for entry in document} == {kind.value for kind in QcCheckKind}
    # No measured value, no rendered text — an unrun check has nothing to say about the output.
    assert all(entry["measured"] == {} for entry in document)


# --- 2. nothing unmeasured passes ---------------------------------------------------------------


def test_all_passed_is_the_only_way_to_reach_passed() -> None:
    decision = decide(statuses(dict.fromkeys(QcCheck, CheckStatus.PASSED)))
    assert decision.verdict is QcVerdict.PASSED
    assert decision.path is RemediationPath.NONE


@pytest.mark.parametrize("check", list(QcCheck))
def test_one_unknown_check_is_enough_to_demand_review(check: QcCheck) -> None:
    """Fail-closed from every direction: any single unmeasured check blocks approval."""

    mapping = dict.fromkeys(QcCheck, CheckStatus.PASSED)
    mapping[check] = CheckStatus.UNKNOWN
    decision = decide(statuses(mapping))
    assert decision.verdict is QcVerdict.NEEDS_REVIEW
    assert decision.path is RemediationPath.HUMAN_REVIEW


def test_an_empty_run_reads_as_unreviewed_rather_than_clean() -> None:
    decision = decide(build_results(()))
    assert decision.verdict is QcVerdict.NEEDS_REVIEW
    assert decision.path is RemediationPath.HUMAN_REVIEW


def test_an_unmeasured_check_never_suggests_an_automatic_fix() -> None:
    """Nobody measured it, so nothing is known about *what* to change."""

    for check in QcCheck:
        result = CheckResult(check=check, status=CheckStatus.UNKNOWN)
        assert result.path is RemediationPath.HUMAN_REVIEW


# --- 3. the decision is a total, deterministic function -----------------------------------------


@pytest.mark.parametrize("check", [c for c, p in CHECK_POLICIES.items() if p.blocking])
def test_a_blocking_failure_fails_the_output(check: QcCheck) -> None:
    mapping = dict.fromkeys(QcCheck, CheckStatus.PASSED)
    mapping[check] = CheckStatus.FAILED
    decision = decide(statuses(mapping))
    assert decision.verdict is QcVerdict.FAILED
    assert decision.path is CHECK_POLICIES[check].on_failure


@pytest.mark.parametrize("check", [c for c, p in CHECK_POLICIES.items() if not p.blocking])
def test_a_non_blocking_failure_asks_for_review(check: QcCheck) -> None:
    mapping = dict.fromkeys(QcCheck, CheckStatus.PASSED)
    mapping[check] = CheckStatus.FAILED
    decision = decide(statuses(mapping))
    assert decision.verdict is QcVerdict.NEEDS_REVIEW
    assert decision.path is CHECK_POLICIES[check].on_failure


def test_a_blocking_failure_outranks_a_non_blocking_one_and_an_unknown() -> None:
    """The tie-break is stated once and it puts the most serious reason first."""

    mapping = dict.fromkeys(QcCheck, CheckStatus.PASSED)
    mapping[QcCheck.LOUDNESS] = CheckStatus.FAILED  # non-blocking, earlier in enum order
    mapping[QcCheck.STATIC_FRAMES] = CheckStatus.UNKNOWN
    mapping[QcCheck.VERIFIED_VALUES_CURRENT] = CheckStatus.FAILED  # blocking, later in order
    decision = decide(statuses(mapping))
    assert decision.verdict is QcVerdict.FAILED
    assert decision.path is RemediationPath.HUMAN_REVIEW


def test_every_status_assignment_is_defined_and_stable() -> None:
    """No combination is undefined, and the same combination always answers the same way.

    Exhausting 3^13 assignments would be slow for no extra coverage, so this sweeps every
    single- and double-check perturbation exhaustively — where the tie-break rules live — and
    then a seeded pseudo-random sample of the rest of the space.
    """

    seen: dict[tuple[CheckStatus, ...], tuple[QcVerdict, RemediationPath]] = {}

    def observe(mapping: dict[QcCheck, CheckStatus]) -> None:
        key = tuple(mapping[check] for check in QcCheck)
        decision = decide(statuses(mapping))
        assert isinstance(decision.verdict, QcVerdict)
        assert isinstance(decision.path, RemediationPath)
        # `passed` may not be reached with anything outstanding, and a passing verdict may not
        # suggest a fix.
        if decision.verdict is QcVerdict.PASSED:
            assert set(key) == {CheckStatus.PASSED}
            assert decision.path is RemediationPath.NONE
        else:
            assert decision.path is not RemediationPath.NONE
        previous = seen.setdefault(key, (decision.verdict, decision.path))
        assert previous == (decision.verdict, decision.path)

    for pair in itertools.combinations(QcCheck, 2):
        for combination in itertools.product(CheckStatus, repeat=2):
            mapping = dict.fromkeys(QcCheck, CheckStatus.PASSED)
            mapping.update(dict(zip(pair, combination, strict=True)))
            observe(mapping)
            observe(mapping)

    generator = random.Random(20260802)
    for _ in range(3_000):
        mapping = {check: generator.choice(list(CheckStatus)) for check in QcCheck}
        observe(mapping)


def test_request_new_media_is_reachable_and_only_when_the_source_is_unusable() -> None:
    """PRD §19.4's fifth path is not decoration: a wholly black output has no other scene to pick."""

    partly = evaluate_deterministic(
        facts=facts(), measurement=measurement(black_ratio=0.3), thresholds=THRESHOLDS
    )
    wholly = evaluate_deterministic(
        facts=facts(), measurement=measurement(black_ratio=1.0), thresholds=THRESHOLDS
    )
    assert _result(partly, QcCheck.BLACK_FRAMES).path is RemediationPath.ALTERNATIVE_SCENE
    assert _result(wholly, QcCheck.BLACK_FRAMES).path is RemediationPath.REQUEST_NEW_MEDIA


def test_every_remediation_path_is_reachable_from_some_check() -> None:
    """A path nothing can produce is a promise the report cannot keep."""

    reachable = {policy.on_failure for policy in CHECK_POLICIES.values()}
    reachable.add(RemediationPath.NONE)
    reachable.add(RemediationPath.HUMAN_REVIEW)  # every unknown
    reachable.add(RemediationPath.REQUEST_NEW_MEDIA)  # an unusable source, above
    assert reachable == set(RemediationPath)


# --- the deterministic evaluators ---------------------------------------------------------------


def _result(results: tuple[CheckResult, ...], check: QcCheck) -> CheckResult:
    return next(result for result in results if result.check is check)


def test_a_missing_measurement_from_an_outage_leaves_every_measured_check_unknown() -> None:
    results = build_results(
        evaluate_deterministic(
            facts=facts(),
            measurement=None,
            thresholds=THRESHOLDS,
            measurement_error="QC_PROBE_TIMEOUT",
        )
    )
    for check in (
        QcCheck.CONTAINER_READABLE,
        QcCheck.DURATION_MATCHES_PLAN,
        QcCheck.AUDIO_PRESENT,
        QcCheck.LOUDNESS,
        QcCheck.BLACK_FRAMES,
        QcCheck.STATIC_FRAMES,
        QcCheck.TEXT_WITHIN_SAFE_AREA,
    ):
        assert _result(results, check).status is CheckStatus.UNKNOWN
        assert _result(results, check).code == "QC_PROBE_TIMEOUT"
    assert decide(results).verdict is QcVerdict.NEEDS_REVIEW


def test_a_file_that_does_not_open_is_a_verdict_not_an_outage() -> None:
    """The distinction the whole error taxonomy rests on, asserted rather than assumed."""

    results = build_results(
        evaluate_deterministic(
            facts=facts(),
            measurement=None,
            thresholds=THRESHOLDS,
            measurement_error=CODE_CONTAINER_UNREADABLE,
        )
    )
    assert _result(results, QcCheck.CONTAINER_READABLE).status is CheckStatus.FAILED
    assert decide(results).verdict is QcVerdict.FAILED
    assert decide(results).path is RemediationPath.RETRY_RENDER


def test_facts_only_checks_still_answer_without_any_measurement() -> None:
    """Verified values and speech drift are facts about rows, not about the file."""

    results = evaluate_deterministic(
        facts=facts(
            voiceover_drift_ms=9_000,
            verified=VerifiedSourceAudit(
                references=1, stale=(("$.overlays[0]", CODE_VERIFIED_VALUE_SUPERSEDED),)
            ),
        ),
        measurement=None,
        thresholds=THRESHOLDS,
        measurement_error="QC_PROBE_TIMEOUT",
    )
    assert _result(results, QcCheck.SPEECH_SYNC).status is CheckStatus.FAILED
    assert _result(results, QcCheck.VERIFIED_VALUES_CURRENT).status is CheckStatus.FAILED


@pytest.mark.parametrize(
    ("delta_ms", "expected"),
    [(0, CheckStatus.PASSED), (750, CheckStatus.PASSED), (751, CheckStatus.FAILED)],
)
def test_duration_is_compared_against_the_sum_of_the_cuts(
    delta_ms: int, expected: CheckStatus
) -> None:
    results = evaluate_deterministic(
        facts=facts(),
        measurement=measurement(duration_ms=8_000 + delta_ms),
        thresholds=THRESHOLDS,
    )
    result = _result(results, QcCheck.DURATION_MATCHES_PLAN)
    assert result.status is expected
    if expected is CheckStatus.FAILED:
        assert result.code == CODE_DURATION_OUT_OF_TOLERANCE


def test_a_missing_audio_stream_fails_and_a_silent_one_fails_too() -> None:
    """ "Ses var mı" is two questions: an AAC track of digital silence answers only the easy one."""

    missing = evaluate_deterministic(
        facts=facts(),
        measurement=measurement(has_audio_stream=False, audio_codec=None),
        thresholds=THRESHOLDS,
    )
    assert _result(missing, QcCheck.AUDIO_PRESENT).code == CODE_NO_AUDIO_STREAM

    silent = evaluate_deterministic(
        facts=facts(),
        measurement=measurement(integrated_loudness_lufs=-70.0),
        thresholds=THRESHOLDS,
    )
    assert _result(silent, QcCheck.AUDIO_PRESENT).code == CODE_AUDIO_SILENT
    # And the loudness check does not also fire: one defect must not become two failures with a
    # suggested path that depends on enum order.
    assert _result(silent, QcCheck.LOUDNESS).status is CheckStatus.PASSED


def test_an_unmeasurable_loudness_is_unknown_rather_than_acceptable() -> None:
    results = evaluate_deterministic(
        facts=facts(),
        measurement=measurement(integrated_loudness_lufs=None),
        thresholds=THRESHOLDS,
    )
    assert _result(results, QcCheck.LOUDNESS).status is CheckStatus.UNKNOWN
    assert _result(results, QcCheck.LOUDNESS).code == CODE_MEASUREMENT_UNAVAILABLE
    assert _result(results, QcCheck.AUDIO_PRESENT).status is CheckStatus.UNKNOWN


@pytest.mark.parametrize(
    ("lufs", "expected"),
    [
        (-14.0, CheckStatus.PASSED),
        (-17.0, CheckStatus.PASSED),
        (-21.8, CheckStatus.FAILED),
        (-10.0, CheckStatus.FAILED),
    ],
)
def test_loudness_is_judged_against_the_configured_window(
    lufs: float, expected: CheckStatus
) -> None:
    results = evaluate_deterministic(
        facts=facts(),
        measurement=measurement(integrated_loudness_lufs=lufs),
        thresholds=THRESHOLDS,
    )
    result = _result(results, QcCheck.LOUDNESS)
    assert result.status is expected
    if expected is CheckStatus.FAILED:
        assert result.code == CODE_LOUDNESS_OUT_OF_WINDOW


def test_black_and_static_pictures_are_caught_at_their_own_limits() -> None:
    black = evaluate_deterministic(
        facts=facts(),
        measurement=measurement(black_ratio=0.06, longest_black_ms=500),
        thresholds=THRESHOLDS,
    )
    assert _result(black, QcCheck.BLACK_FRAMES).code == CODE_BLACK_FRAMES
    assert _result(black, QcCheck.STATIC_FRAMES).status is CheckStatus.PASSED

    static = evaluate_deterministic(
        facts=facts(),
        measurement=measurement(static_ratio=0.35, longest_static_ms=3_000),
        thresholds=THRESHOLDS,
    )
    assert _result(static, QcCheck.STATIC_FRAMES).code == CODE_STATIC_FRAMES


def test_text_is_re_measured_against_the_frame_that_actually_came_out() -> None:
    """Validation measured against the profile. A render at another size is the gap QC closes."""

    overlay = OverlayTextFact(
        pointer="$.overlays[0]",
        text="Kampanyamız başladı",
        style=TEXT_STYLES["brand-title-v1"],
        safe_area=True,
    )
    at_profile = evaluate_deterministic(
        facts=facts(overlay_texts=(overlay,)), measurement=measurement(), thresholds=THRESHOLDS
    )
    assert _result(at_profile, QcCheck.TEXT_WITHIN_SAFE_AREA).status is CheckStatus.PASSED

    # A *proportional* resize changes nothing — the layout is relative all the way down, which is
    # why the pre-render check could be trusted at all. What breaks it is an output whose aspect
    # differs from the profile: at 400x1920 the safe box is a third as wide while the font is
    # still sized from the height, so "Kampanyamız" no longer fits on any line. That is the one
    # way validated text ends up outside the frame, and it is the gap this check exists to close.
    narrow = evaluate_deterministic(
        facts=facts(overlay_texts=(overlay,)),
        measurement=measurement(width=400, height=1920),
        thresholds=THRESHOLDS,
    )
    result = _result(narrow, QcCheck.TEXT_WITHIN_SAFE_AREA)
    assert result.status is CheckStatus.FAILED
    assert result.code == CODE_TEXT_OUTSIDE_SAFE_AREA
    assert result.pointer == "$.overlays[0]"


def test_speech_drift_becomes_a_number_here_and_not_before() -> None:
    within = evaluate_deterministic(
        facts=facts(voiceover_drift_ms=1_500), measurement=measurement(), thresholds=THRESHOLDS
    )
    assert _result(within, QcCheck.SPEECH_SYNC).status is CheckStatus.PASSED
    beyond = evaluate_deterministic(
        facts=facts(voiceover_drift_ms=-1_501), measurement=measurement(), thresholds=THRESHOLDS
    )
    assert _result(beyond, QcCheck.SPEECH_SYNC).code == CODE_SPEECH_DRIFT


def test_a_timeline_with_no_voiceover_is_not_applicable_rather_than_unmeasured() -> None:
    """Not applicable is a known state of the document; unknown is nobody having looked."""

    results = evaluate_deterministic(
        facts=facts(voiceover_drift_ms=None), measurement=measurement(), thresholds=THRESHOLDS
    )
    result = _result(results, QcCheck.SPEECH_SYNC)
    assert result.status is CheckStatus.PASSED
    assert result.measured == {"applicable": False}


# --- the verified-source audit ------------------------------------------------------------------


RENDERED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def test_a_reference_that_no_longer_resolves_is_stale() -> None:
    audit = audit_verified_sources(
        [("verified_product.price", _uuid(1), "$.overlays[0]")], {}, rendered_at=RENDERED_AT
    )
    assert audit.stale == (("$.overlays[0]", CODE_VERIFIED_VALUE_UNRESOLVABLE),)


def test_a_campaign_that_ended_after_the_render_is_stale() -> None:
    audit = audit_verified_sources(
        [("verified_campaign.title", _uuid(2), "$.overlays[1]")],
        {
            ("verified_campaign.title", _uuid(2)): VerifiedRecordState(
                exists=True, within_window=False, changed_at=RENDERED_AT
            )
        },
        rendered_at=RENDERED_AT,
    )
    assert audit.stale == (("$.overlays[1]", CODE_VERIFIED_VALUE_OUT_OF_WINDOW),)


def test_a_price_row_opened_after_the_render_means_the_frame_shows_the_old_figure() -> None:
    """`product_prices` is append-only, so this comparison is exact rather than heuristic."""

    audit = audit_verified_sources(
        [("verified_product.price", _uuid(3), "$.overlays[2]")],
        {
            ("verified_product.price", _uuid(3)): VerifiedRecordState(
                exists=True, within_window=True, changed_at=RENDERED_AT + timedelta(hours=1)
            )
        },
        rendered_at=RENDERED_AT,
    )
    assert audit.stale == (("$.overlays[2]", CODE_VERIFIED_VALUE_SUPERSEDED),)


def test_an_unchanged_record_is_not_reported_stale() -> None:
    audit = audit_verified_sources(
        [("verified_product.price", _uuid(4), "$.overlays[3]")],
        {
            ("verified_product.price", _uuid(4)): VerifiedRecordState(
                exists=True, within_window=True, changed_at=RENDERED_AT - timedelta(days=3)
            )
        },
        rendered_at=RENDERED_AT,
    )
    assert audit.stale == ()
    assert audit.references == 1


def test_the_audit_never_carries_a_value_only_a_pointer_and_a_code() -> None:
    """A QC report is kept indefinitely; it must not become a second place a price is written."""

    audit = audit_verified_sources(
        [("verified_product.price", _uuid(5), "$.overlays[0]")], {}, rendered_at=RENDERED_AT
    )
    results = evaluate_deterministic(
        facts=facts(verified=audit), measurement=measurement(), thresholds=THRESHOLDS
    )
    document = _result(results, QcCheck.VERIFIED_VALUES_CURRENT).as_document()
    assert document["measured"] == {"references": 1, "stale": 1}
    assert "149" not in repr(document)


# --- the model checks ---------------------------------------------------------------------------


def test_a_provider_that_answers_nothing_leaves_every_model_check_unknown() -> None:
    results = model_check_results(None, requested=MODEL_CHECKS, code=CODE_PROVIDER_DISABLED)
    assert {result.status for result in results} == {CheckStatus.UNKNOWN}
    assert {result.code for result in results} == {CODE_PROVIDER_DISABLED}


def test_a_provider_that_answers_partially_leaves_the_rest_unknown() -> None:
    """Trusting the adapter to return a complete set would put fail-closed on the wrong side."""

    report = VisualQcReport(
        provider="p",
        model="m",
        findings=(
            VisualQcFinding(
                check=QcCheck.SENSITIVE_CONTENT, status=CheckStatus.PASSED, confidence=0.9
            ),
        ),
        actual_cost_minor=0,
        currency="TRY",
    )
    results = {
        result.check: result
        for result in model_check_results(report, requested=MODEL_CHECKS, code=None)
    }
    assert results[QcCheck.SENSITIVE_CONTENT].status is CheckStatus.PASSED
    assert results[QcCheck.FACE_INTEGRITY].status is CheckStatus.UNKNOWN
    assert results[QcCheck.PRODUCT_SHAPE].status is CheckStatus.UNKNOWN


# --- thresholds ---------------------------------------------------------------------------------


def test_the_threshold_snapshot_carries_every_number_a_check_uses() -> None:
    """A version says which ruleset ran; only the snapshot says what it compared against."""

    document = THRESHOLDS.as_document()
    assert document["version"] == 1
    assert set(document) == {
        "version",
        "duration_tolerance_ms",
        "loudness_target_lufs",
        "loudness_tolerance_lu",
        "silence_floor_lufs",
        "black_ratio_limit",
        "static_ratio_limit",
        "unusable_source_ratio",
        "speech_drift_ms",
    }


def _uuid(seed: int) -> UUID:
    return UUID(int=seed)
