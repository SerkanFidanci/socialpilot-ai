"""Slice 2F's pure half: PRD §21's approval policies and revision classification.

Everything under test here is a total function over a closed domain, so the tests are
enumerations rather than scenarios. Four claims carry the slice:

- `requires_approval` answers for **every** `(policy, context)` combination and each policy reads
  its own dimension and no other, so a setting cannot quietly mean the same as its neighbour;
- `revision_class` and `revision_scope` answer for **every** subset of the field vocabulary, and
  both fall to the expensive side when the set says nothing;
- what a revision *throws away* agrees with where it restarts, so a re-render cannot be produced
  from an artefact the revision already invalidated;
- the free note reaches storage and nothing else — asserted structurally, over the module's own
  syntax tree, because "we would never log it" is not a property a reader can check.
"""

from __future__ import annotations

import ast
import itertools
import tokenize
from pathlib import Path
from typing import Any

import pytest

from app.modules.content.approval import (
    MAX_REJECTION_NOTE_CHARS,
    ApprovalContext,
    ApprovalDecision,
    ApprovalPolicy,
    RejectionReason,
    RevisionClass,
    RevisionField,
    RevisionScope,
    is_advertisement,
    qc_is_confident,
    requires_approval,
    revision_class,
    revision_cost,
    revision_scope,
    script_names_price,
)
from app.modules.content.approval_service import _SCOPE_CLEARS
from app.modules.content.lifecycle import ProjectState, next_state, revision_event
from app.modules.content.qc import QcVerdict
from app.modules.content.script import ScenarioCode, SegmentPurpose

MODULES = Path(__file__).resolve().parents[2] / "app" / "modules"

# PRD §21.3's two lists, transcribed. Kept beside the table they check so a reviewer can compare
# them without opening two files.
PRD_MINOR = {
    RevisionField.CTA,
    RevisionField.HEADLINE,
    RevisionField.SINGLE_CUT,
    RevisionField.VOICE,
    RevisionField.MUSIC,
    RevisionField.CAPTION_STYLE,
}
PRD_MAJOR = {
    RevisionField.CONTENT_TYPE,
    RevisionField.PRODUCT,
    RevisionField.CONCEPT,
    RevisionField.DURATION_CLASS,
}

# Which artefact each pipeline stage produces. Used to check that a revision throws away exactly
# what its restart point invalidates — no more (which would buy a needless provider call) and no
# less (which would render the old thing again).
STAGE_ARTEFACTS: dict[ProjectState, str] = {
    ProjectState.SCRIPTING: "script_id",
    ProjectState.VOICE_GENERATION: "voiceover_id",
    ProjectState.TIMELINE_BUILDING: "timeline_id",
    ProjectState.RENDERING: "render_id",
    ProjectState.QUALITY_CHECK: "qc_report_id",
}
STAGE_ORDER = tuple(STAGE_ARTEFACTS)


def context(**overrides: Any) -> ApprovalContext:
    base: dict[str, Any] = {
        "is_campaign": False,
        "has_price_or_discount": False,
        "is_advertisement": False,
        "delivered_content_count": 99,
        "first_n_contents": 3,
        "qc_confident": True,
        "within_guardrails": True,
    }
    return ApprovalContext(**{**base, **overrides})


# --- the policy table (criterion 3) --------------------------------------------------------------


def test_the_policy_answers_for_every_policy_and_context_combination() -> None:
    """Total, not merely large: the whole product is asked and nothing raises.

    Seven policies times every combination of the five booleans times both sides of the count
    threshold. A partial table would fail inside a worker holding a claim, which is the failure
    mode this shape exists to remove.
    """

    flags = list(itertools.product((False, True), repeat=5))
    counts = (0, 2, 3, 4)
    answers = {}
    for policy, (campaign, price, ad, confident, guarded), count in itertools.product(
        ApprovalPolicy, flags, counts
    ):
        subject = context(
            is_campaign=campaign,
            has_price_or_discount=price,
            is_advertisement=ad,
            qc_confident=confident,
            within_guardrails=guarded,
            delivered_content_count=count,
        )
        answers[(policy, campaign, price, ad, confident, guarded, count)] = requires_approval(
            policy, subject
        )

    assert len(answers) == len(ApprovalPolicy) * len(flags) * len(counts)
    assert all(isinstance(value, bool) for value in answers.values())


@pytest.mark.parametrize(
    ("policy", "dimension"),
    [
        (ApprovalPolicy.CAMPAIGN_ONLY, "is_campaign"),
        (ApprovalPolicy.PRICE_OR_DISCOUNT_ONLY, "has_price_or_discount"),
        (ApprovalPolicy.ADS_ONLY, "is_advertisement"),
    ],
)
def test_each_policy_reads_its_own_dimension_and_no_other(
    policy: ApprovalPolicy, dimension: str
) -> None:
    """The property that makes the setting mean something.

    The tempting alternative — letting every policy also fire when the guardrails are breached —
    sounds safer and is not. With the vision adapter disabled, slice 2D's fail-closed rule marks
    every render `needs_review`, so a universal guardrail escape would make all seven policies
    behave identically and the customer's choice would be decoration.
    """

    assert not requires_approval(policy, context(**{dimension: False}))
    assert requires_approval(policy, context(**{dimension: True}))
    # Every other dimension moves without moving the answer.
    for other in ("is_campaign", "has_price_or_discount", "is_advertisement", "within_guardrails"):
        if other == dimension:
            continue
        assert not requires_approval(policy, context(**{dimension: False, other: True}))


def test_always_asks_and_never_within_guardrails_asks_only_outside_them() -> None:
    for subject in (context(), context(within_guardrails=False), context(is_campaign=True)):
        assert requires_approval(ApprovalPolicy.ALWAYS, subject)

    assert not requires_approval(ApprovalPolicy.NEVER_WITHIN_GUARDRAILS, context())
    assert requires_approval(
        ApprovalPolicy.NEVER_WITHIN_GUARDRAILS, context(within_guardrails=False)
    )


def test_first_n_contents_counts_deliveries_and_stops_at_the_threshold() -> None:
    for count in (0, 1, 2):
        assert requires_approval(
            ApprovalPolicy.FIRST_N_CONTENTS,
            context(delivered_content_count=count, first_n_contents=3),
        )
    for count in (3, 4, 100):
        assert not requires_approval(
            ApprovalPolicy.FIRST_N_CONTENTS,
            context(delivered_content_count=count, first_n_contents=3),
        )


def test_low_confidence_only_asks_for_approval_on_everything_today_and_that_is_correct() -> None:
    """Pinned **with its reason**, so nobody reads the behaviour as a bug.

    The confidence this policy reads is the quality check's, and no render can currently be
    confident: the vision provider is `disabled` in production until W08's benchmark picks one,
    so §19.4's model checks come back `unknown` and slice 2D refuses to call an unmeasured check
    a pass. Every verdict a project can carry today is therefore `needs_review`, and this policy
    asks for a person on all of them. When a real provider is connected it starts discriminating
    without a line of `approval.py` changing — which is what the second half of this test pins.
    """

    assert not qc_is_confident(QcVerdict.NEEDS_REVIEW)
    assert not qc_is_confident(QcVerdict.FAILED)
    assert qc_is_confident(QcVerdict.PASSED)

    today = context(qc_confident=qc_is_confident(QcVerdict.NEEDS_REVIEW))
    assert requires_approval(ApprovalPolicy.LOW_CONFIDENCE_ONLY, today)
    # The day a provider can pass a render, and only then, the policy stops asking.
    tomorrow = context(qc_confident=qc_is_confident(QcVerdict.PASSED))
    assert not requires_approval(ApprovalPolicy.LOW_CONFIDENCE_ONLY, tomorrow)


def test_qc_confidence_is_total_over_every_verdict() -> None:
    for verdict in QcVerdict:
        assert isinstance(qc_is_confident(verdict), bool)


def test_no_scenario_answers_the_advertising_question_by_omission() -> None:
    """`ads_only` asks for approval on nothing today because §14 has opened no ad scenario.

    That is the honest reading and it is one table line away from changing. What must not happen
    is a *new* scenario answering "not an ad" because nobody was asked — the import-time check in
    `approval.py` is what makes that a start-up failure, and this is what makes it a test failure.
    """

    for code in ScenarioCode:
        assert isinstance(is_advertisement(code), bool)
    assert not is_advertisement(ScenarioCode.PRODUCT_REELS)


# --- revision classification (criterion 4) -------------------------------------------------------


def test_the_field_vocabulary_is_exactly_prd_21_3() -> None:
    assert PRD_MINOR | PRD_MAJOR == set(RevisionField)
    assert not PRD_MINOR & PRD_MAJOR
    for field in PRD_MINOR:
        assert revision_class(frozenset({field})) is RevisionClass.MINOR
    for field in PRD_MAJOR:
        assert revision_class(frozenset({field})) is RevisionClass.MAJOR


def test_classification_and_scope_answer_for_every_subset_of_the_vocabulary() -> None:
    """All 1024 subsets, including the empty one. Total means total."""

    fields = list(RevisionField)
    subsets = [
        frozenset(itertools.compress(fields, mask))
        for mask in itertools.product((0, 1), repeat=len(fields))
    ]
    assert len(subsets) == 2 ** len(fields)
    for subset in subsets:
        assert isinstance(revision_class(subset), RevisionClass)
        assert isinstance(revision_scope(subset), RevisionScope)


def test_one_major_field_makes_the_whole_set_major() -> None:
    for major, minor in itertools.product(PRD_MAJOR, PRD_MINOR):
        assert revision_class(frozenset({major, minor})) is RevisionClass.MAJOR


def test_an_empty_or_unstated_change_falls_to_the_expensive_side() -> None:
    """Fail-closed, in both classifiers.

    A classifier whose answer to "I don't know what changed" is "the cheap one" has a bypass in
    it: an unnecessary regeneration costs one provider call, and the other direction ships a
    video that still contains the thing the customer asked to have removed.
    """

    assert revision_class(frozenset()) is RevisionClass.MAJOR
    assert revision_scope(frozenset()) is RevisionScope.SCRIPT


def test_a_set_restarts_at_the_earliest_stage_any_of_its_fields_invalidates() -> None:
    assert revision_scope(frozenset({RevisionField.CAPTION_STYLE})) is RevisionScope.TIMELINE
    assert revision_scope(frozenset({RevisionField.VOICE})) is RevisionScope.VOICE
    # Adding an earlier field moves the restart earlier; adding a later one never moves it back.
    assert (
        revision_scope(frozenset({RevisionField.CAPTION_STYLE, RevisionField.VOICE}))
        is RevisionScope.VOICE
    )
    assert (
        revision_scope(frozenset({RevisionField.CAPTION_STYLE, RevisionField.CTA}))
        is RevisionScope.SCRIPT
    )
    assert (
        revision_scope(frozenset({RevisionField.VOICE, RevisionField.PRODUCT}))
        is RevisionScope.SCRIPT
    )


def test_the_cta_is_a_small_revision_that_still_restarts_at_the_script() -> None:
    """The one place the class and the scope deliberately disagree, pinned with its reason.

    PRD §21.3 lists the CTA as a small revision, so it costs one unit of allowance. The CTA text
    is nonetheless resolved into the script document (`SlotKind.CTA`) and spoken by the
    voiceover, so restarting at the timeline would produce a new cut of a video still saying the
    old words. Both facts are true; deriving either from the other would make one of them wrong.
    """

    for field in (RevisionField.CTA, RevisionField.HEADLINE):
        assert revision_class(frozenset({field})) is RevisionClass.MINOR
        assert revision_scope(frozenset({field})) is RevisionScope.SCRIPT


def test_a_minor_revision_costs_one_and_a_major_two() -> None:
    assert revision_cost(RevisionClass.MINOR, minor_cost=1, major_cost=2) == 1
    assert revision_cost(RevisionClass.MAJOR, minor_cost=1, major_cost=2) == 2
    # The weights are configuration because only the allowance is a product decision; the 2 is an
    # estimate standing in for a measurement W08's benchmark has not made yet.
    assert revision_cost(RevisionClass.MAJOR, minor_cost=1, major_cost=5) == 5


# --- what a revision throws away -----------------------------------------------------------------


def test_every_scope_clears_exactly_what_its_restart_point_invalidates() -> None:
    """The agreement between the pure state machine and the service that acts on it.

    A scope restarts at a stage; everything that stage and the stages after it produced is stale.
    Clearing less would re-render the artefact the revision was asked to change; clearing more
    would buy a provider call nobody asked for.
    """

    for scope in RevisionScope:
        resume = next_state(ProjectState.REVISION_REQUESTED, revision_event(scope))
        assert resume in STAGE_ORDER
        expected = {STAGE_ARTEFACTS[stage] for stage in STAGE_ORDER[STAGE_ORDER.index(resume) :]}
        assert set(_SCOPE_CLEARS[scope]) == expected


def test_the_script_survives_a_revision_that_only_changes_how_it_sounds() -> None:
    assert "script_id" not in _SCOPE_CLEARS[RevisionScope.VOICE]
    assert "script_id" not in _SCOPE_CLEARS[RevisionScope.TIMELINE]
    assert "voiceover_id" not in _SCOPE_CLEARS[RevisionScope.TIMELINE]
    # And every scope drops the render and its report: a decision was made about that video.
    for scope in RevisionScope:
        assert {"render_id", "qc_report_id"} <= set(_SCOPE_CLEARS[scope])


# --- price detection -----------------------------------------------------------------------------


def template(voice_text: str) -> dict[str, Any]:
    """A §18.1 template with slots intact — what `serialize_draft` stores.

    Two segments because §18.1's contract requires at least that many; the detector reads the
    hook and every segment, so the interesting text goes in the second one.
    """

    return {
        "hook": {"text": "Bugun taze.", "duration_ms": 2000},
        "segments": [
            {
                "purpose": SegmentPurpose.HOOK.value,
                "voice_text": "Bugun taze.",
                "required_scene_tags": ["product_closeup"],
                "target_duration_ms": 2000,
            },
            {
                "purpose": SegmentPurpose.PRODUCT.value,
                "voice_text": voice_text,
                "required_scene_tags": ["product_closeup"],
                "target_duration_ms": 3000,
            },
        ],
        "cta": {
            "source": "approved_cta",
            "reference_id": "11111111-1111-4111-8111-111111111111",
        },
    }


def test_a_price_slot_is_seen_and_a_script_without_one_is_not() -> None:
    priced = template("Sadece {{price:22222222-2222-4222-8222-222222222222}}.")
    plain = template("Sadece bugun.")

    assert script_names_price(priced)
    assert not script_names_price(plain)
    # A CTA slot is not a price. The question §21.1 asks is specific.
    assert not script_names_price(
        template("{{cta:22222222-2222-4222-8222-222222222222}} bekliyor.")
    )


def test_an_unreadable_template_is_treated_as_priced() -> None:
    """Fail-closed for this specific question, and only this one.

    A document the parser cannot read is a document nobody can assert is free of prices, and
    `price_or_discount_only` asking for one unnecessary approval is cheaper than publishing an
    unreviewed price. A missing script is different: there is nothing there at all.
    """

    assert script_names_price({"hook": "not a script"})
    assert script_names_price("")
    assert not script_names_price(None)


# --- structural guarantees (criterion 7) ---------------------------------------------------------


def executable_source(path: Path) -> str:
    """The module with comments and docstrings removed — prose may explain, code may not couple."""

    parts: list[str] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type not in (tokenize.COMMENT, tokenize.STRING):
                parts.append(token.string)
    return " ".join(parts)


def callee_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return "<expr>"


def calls_receiving(tree: ast.Module, name: str) -> set[str]:
    """Every callee that is handed the bare name `name` as a positional or keyword argument."""

    receivers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
        if any(isinstance(item, ast.Name) and item.id == name for item in arguments):
            receivers.add(callee_name(node.func))
    return receivers


def test_the_rejection_note_is_handed_to_nothing_but_validation_and_storage() -> None:
    """The privacy claim, made structural.

    "We would never log it" is not a property a reader can check. What is checkable: the only
    functions the note is passed to are the one that validates it and the row that stores it.
    An audit call, a log call, a span attribute or a prompt builder appearing in this set is the
    exact regression the assertion exists to catch.
    """

    tree = ast.parse((MODULES / "content" / "approval_service.py").read_text(encoding="utf-8"))

    assert calls_receiving(tree, "note") <= {"_validate_note", "ContentApproval", "len"}


def test_the_approval_service_cannot_reach_a_prompt_or_a_provider() -> None:
    """The note cannot be merged into a prompt payload because there is no prompt here at all."""

    source = executable_source(MODULES / "content" / "approval_service.py")

    for forbidden in (
        "prompt",
        "input_data",
        "system_prompt",
        "instruction",
        "ScriptGenerationService",
        "VoiceoverService",
        "ContentRenderService",
    ):
        assert forbidden not in source


def test_the_pure_approval_module_imports_no_infrastructure_and_no_session() -> None:
    tree = ast.parse((MODULES / "content" / "approval.py").read_text(encoding="utf-8"))
    imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]

    assert not [module for module in imported if module.startswith("app.infrastructure")]
    source = executable_source(MODULES / "content" / "approval.py")
    for forbidden in ("AsyncSession", "httpx", "requests", "datetime", "Settings"):
        assert forbidden not in source


def test_a_client_cannot_claim_a_decision_nobody_made() -> None:
    """`auto_approved` is what the policy decided when nobody was asked.

    The request model carries a boolean, so the value is not expressible from outside; this pins
    that the enum still has the third member and that it is the one with no actor.
    """

    assert set(ApprovalDecision) == {
        ApprovalDecision.APPROVED,
        ApprovalDecision.AUTO_APPROVED,
        ApprovalDecision.REJECTED,
    }


def test_the_rejection_reasons_are_prd_21_2_and_nothing_else() -> None:
    assert len(set(RejectionReason)) == 10
    assert RejectionReason.OTHER in set(RejectionReason)
    # A ceiling exists on the free note: long enough for anybody explaining what is wrong with
    # their video, short enough that the column cannot be used as storage.
    assert 200 <= MAX_REJECTION_NOTE_CHARS <= 10_000


def test_the_seven_policies_are_prd_21_1_verbatim() -> None:
    assert {policy.value for policy in ApprovalPolicy} == {
        "always",
        "campaign_only",
        "price_or_discount_only",
        "ads_only",
        "first_n_contents",
        "low_confidence_only",
        "never_within_guardrails",
    }
