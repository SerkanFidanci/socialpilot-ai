"""PRD §21's approval system as data and total functions. No session, no clock, no provider.

Three closed sets and three questions, and every one of them is answered by a table a test can
enumerate rather than by a condition somebody has to remember to keep in sync.

**Whether approval is needed is a policy applied to a context, not a branch.** §21.1 names seven
policies and each one reads exactly like its name: `campaign_only` asks whether this is a
campaign, `ads_only` whether this is an advertisement, `low_confidence_only` whether the quality
check was confident. `requires_approval` is total over `ApprovalPolicy × ApprovalContext`, so
there is no combination that falls through to a default — which matters because the default in an
approval system is the expensive direction in both directions: asking for approval nobody wanted,
or publishing something nobody saw.

**Why a person rejected is a closed set plus their own words.** §21.2's ten reasons are an enum;
the free note beside them is the tenant's own text and is treated as such throughout — stored,
never logged, never merged into a prompt. It does not go through the fabrication detector either,
and that is deliberate: the detector exists to stop a *model* inventing a price, and a customer
writing "the price should be 165 TL" is telling us something true about their own catalogue.

**What kind of revision this is comes from the fields that changed, not from what the user calls
it.** Nobody types "this is a major revision". They say what they want different, and §21.3 says
which of those are small and which are not. Two independent total functions read the same closed
field set: `revision_class` answers what it costs, and `revision_scope` answers where the pipeline
has to restart. They are separate because they are separate questions — PRD §21.3 lists the CTA as
a *small* revision, and the CTA text is nonetheless resolved into the script document and spoken
by the voiceover, so it costs one unit of quota and still restarts at the script. Collapsing them
would force one of those two facts to be wrong.

Both classifiers fail closed. An empty or unrecognised set of fields is `MAJOR` and restarts at
`SCRIPT`: an unnecessary regeneration costs one provider call, and the other direction ships a
video that still says the thing the customer asked to have removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.modules.content.qc import QcVerdict
from app.modules.content.script import ScenarioCode, ScriptSchemaError, SlotKind, parse_script

# --- documented error codes ---------------------------------------------------------------------
# All listed in docs/architecture/error-handling.md. Every one of them names a *rule*, never a
# tenant's own words: the free note attached to a rejection is not allowed to reach an error body
# any more than it is allowed to reach a log line.

ERROR_APPROVAL_REASON_REQUIRED: Final = "APPROVAL_REASON_REQUIRED"
ERROR_APPROVAL_REASON_NOT_ALLOWED: Final = "APPROVAL_REASON_NOT_ALLOWED"
ERROR_APPROVAL_NOTE_REQUIRED: Final = "APPROVAL_NOTE_REQUIRED"
ERROR_APPROVAL_NOTE_NOT_ALLOWED: Final = "APPROVAL_NOTE_NOT_ALLOWED"
ERROR_APPROVAL_NOTE_INVALID: Final = "APPROVAL_NOTE_INVALID"
ERROR_APPROVAL_NOT_PENDING: Final = "APPROVAL_NOT_PENDING"
ERROR_REVISION_NOT_REQUESTED: Final = "REVISION_NOT_REQUESTED"
ERROR_REVISION_FIELDS_REQUIRED: Final = "REVISION_FIELDS_REQUIRED"
ERROR_REVISION_QUOTA_EXHAUSTED: Final = "REVISION_QUOTA_EXHAUSTED"
# Cancelling a finished project has no code of its own: it is exactly "a transition §20 does not
# draw from here", so it reuses `PROJECT_TRANSITION_NOT_ALLOWED`. A second name for the same
# refusal would be a second thing a client has to learn about one rule.

# How much prose §21.2's free note may carry. A ceiling rather than no limit because this column
# takes tenant text on an unauthenticated-by-content path: long enough for anyone explaining what
# is wrong with their video, short enough that it cannot be used as storage.
MAX_REJECTION_NOTE_CHARS: Final = 2_000


class ApprovalPolicy(StrEnum):
    """PRD §21.1's seven policies, verbatim.

    A policy is a *setting*, which is why this is an enum and not seven code paths. The value a
    project was created under is stored on the project rather than read live: a policy changed
    next week must not change what was required of a preview produced today.
    """

    ALWAYS = "always"
    CAMPAIGN_ONLY = "campaign_only"
    PRICE_OR_DISCOUNT_ONLY = "price_or_discount_only"
    ADS_ONLY = "ads_only"
    FIRST_N_CONTENTS = "first_n_contents"
    LOW_CONFIDENCE_ONLY = "low_confidence_only"
    NEVER_WITHIN_GUARDRAILS = "never_within_guardrails"


class ApprovalDecision(StrEnum):
    """What happened to a preview that needed a decision.

    `AUTO_APPROVED` is a decision with no actor, and it is recorded exactly like the other two.
    A policy that did not ask for approval is the reason a video went out unreviewed, and a
    system that records only the approvals a human gave cannot answer "who let this through?".
    """

    APPROVED = "approved"
    AUTO_APPROVED = "auto_approved"
    REJECTED = "rejected"


class RejectionReason(StrEnum):
    """PRD §21.2's ten reasons, closed.

    Closed because these are the dimension the product learns along, and a free-text reason is
    a dimension with one value per customer. §21.2's own sentence — the reasons may become model
    learning data but stay specific to the user — is only expressible over a closed set; that
    aggregation is explicitly *not* built here (see the module docstring of `approval_service`).
    """

    WRONG_PRODUCT = "wrong_product"
    WRONG_PRICE = "wrong_price"
    WRONG_CUT = "wrong_cut"
    OFF_BRAND_TONE = "off_brand_tone"
    UNSUITABLE_VOICE = "unsuitable_voice"
    UNSUITABLE_MUSIC = "unsuitable_music"
    WRONG_LENGTH = "wrong_length"
    LOW_QUALITY = "low_quality"
    NEW_CONCEPT = "new_concept"
    OTHER = "other"


class RevisionField(StrEnum):
    """What the customer wants different. PRD §21.3's two lists, as one closed vocabulary.

    One vocabulary rather than two because the *user* names a field and the *code* names the
    class: a request that carried its own class would let "this is only a small change" be an
    assertion rather than a consequence, which is precisely the judgement §21.3 hands to the
    rules engine.
    """

    # §21.3 "Küçük revizyon"
    CTA = "cta"
    HEADLINE = "headline"
    SINGLE_CUT = "single_cut"
    VOICE = "voice"
    MUSIC = "music"
    CAPTION_STYLE = "caption_style"
    # §21.3 "Büyük revizyon"
    CONTENT_TYPE = "content_type"
    PRODUCT = "product"
    CONCEPT = "concept"
    DURATION_CLASS = "duration_class"


class RevisionClass(StrEnum):
    """What a revision costs. §21.3's small/large split, and nothing else."""

    MINOR = "minor"
    MAJOR = "major"


class RevisionScope(StrEnum):
    """The earliest pipeline stage a revision invalidates.

    Deliberately not the same axis as `RevisionClass`. The class is a price and the scope is a
    restart point, and PRD §21.3 fixes only the first: the CTA is listed as a small revision, and
    the CTA text is still resolved into the script document (`SlotKind.CTA`) and spoken by the
    voiceover, so the small revision has to restart at the script. Deriving one from the other
    would make either the price or the restart point wrong.
    """

    SCRIPT = "script"
    VOICE = "voice"
    TIMELINE = "timeline"


# §21.3's own split. Every field appears exactly once; the totality check below refuses a value
# that was added to the vocabulary without a decision about what it costs.
_FIELD_CLASSES: Final[dict[RevisionField, RevisionClass]] = {
    RevisionField.CTA: RevisionClass.MINOR,
    RevisionField.HEADLINE: RevisionClass.MINOR,
    RevisionField.SINGLE_CUT: RevisionClass.MINOR,
    RevisionField.VOICE: RevisionClass.MINOR,
    RevisionField.MUSIC: RevisionClass.MINOR,
    RevisionField.CAPTION_STYLE: RevisionClass.MINOR,
    RevisionField.CONTENT_TYPE: RevisionClass.MAJOR,
    RevisionField.PRODUCT: RevisionClass.MAJOR,
    RevisionField.CONCEPT: RevisionClass.MAJOR,
    RevisionField.DURATION_CLASS: RevisionClass.MAJOR,
}

# Where the pipeline has to pick up again, per field. Read against how this product actually
# builds a video: the script carries the spoken words (hook and CTA among them), the voiceover
# speaks that resolved document, and the timeline lays cuts, audio tracks and captions over it.
#
# `CTA` and `HEADLINE` therefore restart at the script even though §21.3 prices them as small
# revisions: their text is *inside* the script document and inside the audio, so restarting at the
# timeline would produce a new cut of a video still saying the old words. That is the one place
# this table and PRD §21.3's phrasing have to be read as answering different questions.
_FIELD_SCOPES: Final[dict[RevisionField, RevisionScope]] = {
    RevisionField.CTA: RevisionScope.SCRIPT,
    RevisionField.HEADLINE: RevisionScope.SCRIPT,
    RevisionField.VOICE: RevisionScope.VOICE,
    RevisionField.SINGLE_CUT: RevisionScope.TIMELINE,
    RevisionField.MUSIC: RevisionScope.TIMELINE,
    RevisionField.CAPTION_STYLE: RevisionScope.TIMELINE,
    RevisionField.CONTENT_TYPE: RevisionScope.SCRIPT,
    RevisionField.PRODUCT: RevisionScope.SCRIPT,
    RevisionField.CONCEPT: RevisionScope.SCRIPT,
    RevisionField.DURATION_CLASS: RevisionScope.SCRIPT,
}

# Earliest first. A set of fields restarts at the earliest stage any one of them invalidates,
# because a later restart would leave a stage holding output the revision has already invalidated.
_SCOPE_ORDER: Final[tuple[RevisionScope, ...]] = (
    RevisionScope.SCRIPT,
    RevisionScope.VOICE,
    RevisionScope.TIMELINE,
)

# Which scenarios PRD §21.1's `ads_only` is about. Written as a table over the whole enum rather
# than as `code in {...}` so that adding an advertising scenario is a decision made here: an
# unmapped scenario would otherwise quietly answer "not an ad" and skip approval on exactly the
# content type that most needs it. Today §14 has opened one scenario and it is not an ad, so
# `ads_only` asks for approval on nothing — the honest reading, and it changes with one line.
_ADVERTISING_SCENARIOS: Final[dict[ScenarioCode, bool]] = {
    ScenarioCode.PRODUCT_REELS: False,
}

_UNPRICED_FIELDS = tuple(field.value for field in RevisionField if field not in _FIELD_CLASSES)
if _UNPRICED_FIELDS:  # pragma: no cover - a start-up failure, asserted by the unit suite
    raise RuntimeError(f"revision fields with no class: {_UNPRICED_FIELDS}")

_UNSCOPED_FIELDS = tuple(field.value for field in RevisionField if field not in _FIELD_SCOPES)
if _UNSCOPED_FIELDS:  # pragma: no cover - a start-up failure, asserted by the unit suite
    raise RuntimeError(f"revision fields with no scope: {_UNSCOPED_FIELDS}")

_UNCLASSIFIED_SCENARIOS = tuple(
    code.value for code in ScenarioCode if code not in _ADVERTISING_SCENARIOS
)
if _UNCLASSIFIED_SCENARIOS:  # pragma: no cover - a start-up failure, asserted by the unit suite
    raise RuntimeError(f"scenarios with no advertising answer: {_UNCLASSIFIED_SCENARIOS}")


@dataclass(frozen=True, slots=True)
class ApprovalContext:
    """Everything §21.1's policies are allowed to look at, and nothing else.

    Five facts, each one already established elsewhere: whether the project names a campaign
    offer, whether its script resolved a price or a discount, whether its scenario is an
    advertisement, how many contents this business has already had delivered, and what the
    quality check concluded. Passing the project row instead would let a policy start reading
    fields nobody enumerated, and the permutation test that makes this total would stop being
    a proof of anything.
    """

    is_campaign: bool
    has_price_or_discount: bool
    is_advertisement: bool
    delivered_content_count: int
    first_n_contents: int
    qc_confident: bool
    within_guardrails: bool


def requires_approval(policy: ApprovalPolicy, context: ApprovalContext) -> bool:
    """Total over `ApprovalPolicy × ApprovalContext`. Whether a person must look before publish.

    Each policy asks about its own dimension and no other. That is a design choice worth stating
    because the tempting alternative — letting every policy also fire when the guardrails are
    breached — sounds safer and is not: with the vision adapter disabled, slice 2D's fail-closed
    rule marks *every* render `needs_review`, so a universal guardrail escape would make all seven
    policies behave identically and the setting would mean nothing. `never_within_guardrails` is
    the policy that names guardrails, and it is the one that watches them.

    **`low_confidence_only` asks for approval on everything today, and that is correct.** The
    confidence it reads is the quality check's, and no render can currently be confident: the
    vision provider is `disabled` in production until W08's benchmark picks one, so §19.4's model
    checks come back `unknown` and slice 2D refuses to call an unmeasured check a pass. When a
    real provider is connected this policy starts discriminating without a line changing here.
    The unit suite pins the behaviour *and* the reason, so nobody reads it as a bug.
    """

    if policy is ApprovalPolicy.ALWAYS:
        return True
    if policy is ApprovalPolicy.CAMPAIGN_ONLY:
        return context.is_campaign
    if policy is ApprovalPolicy.PRICE_OR_DISCOUNT_ONLY:
        return context.has_price_or_discount
    if policy is ApprovalPolicy.ADS_ONLY:
        return context.is_advertisement
    if policy is ApprovalPolicy.FIRST_N_CONTENTS:
        return context.delivered_content_count < context.first_n_contents
    if policy is ApprovalPolicy.LOW_CONFIDENCE_ONLY:
        return not context.qc_confident
    return not context.within_guardrails


def is_advertisement(scenario_code: ScenarioCode) -> bool:
    """Whether §21.1's `ads_only` is about this scenario. Total over `ScenarioCode`."""

    return _ADVERTISING_SCENARIOS[scenario_code]


def qc_is_confident(verdict: QcVerdict) -> bool:
    """The confidence `low_confidence_only` reads. Only an outright pass counts.

    `needs_review` is slice 2D saying it could not measure something, which is the *definition*
    of low confidence rather than a near miss; `failed` never reaches an approval decision at all
    but answers here anyway, because this function has to be total over the verdict.
    """

    return verdict is QcVerdict.PASSED


def script_names_price(template: object) -> bool:
    """Whether a stored §18.1 template references a verified price slot. Pure.

    Read from the *template* rather than the resolved document on purpose: the template is the
    one representation that still says "a price goes here" instead of showing a number, so this
    question is answered without a price ever being compared, matched or copied.

    An unreadable template answers `True`. That is the fail-closed direction for this specific
    question — a document this parser cannot read is a document nobody can assert is free of
    prices, and `price_or_discount_only` asking for one unnecessary approval is cheaper than
    publishing an unreviewed price.
    """

    if template is None:
        return False
    try:
        draft = parse_script(template)
    except ScriptSchemaError:
        return True
    return any(slot.kind is SlotKind.PRICE for slot in draft.slots)


def revision_class(fields: frozenset[RevisionField]) -> RevisionClass:
    """§21.3's small/large split, derived from what changed. Total over every field set.

    The empty set is `MAJOR`. It should never arrive — the API refuses a revision that names
    nothing — but a classifier whose answer for "I don't know what changed" is "the cheap one"
    is a classifier with a bypass in it.
    """

    if not fields:
        return RevisionClass.MAJOR
    if any(_FIELD_CLASSES[field] is RevisionClass.MAJOR for field in fields):
        return RevisionClass.MAJOR
    return RevisionClass.MINOR


def revision_scope(fields: frozenset[RevisionField]) -> RevisionScope:
    """Where the pipeline restarts. The earliest stage any changed field invalidates.

    Total, and `SCRIPT` for the empty set: restarting further back costs a regeneration, and
    restarting too far forward ships a video that still contains the thing being revised.
    """

    if not fields:
        return RevisionScope.SCRIPT
    scopes = {_FIELD_SCOPES[field] for field in fields}
    return next(scope for scope in _SCOPE_ORDER if scope in scopes)


def revision_cost(revision: RevisionClass, *, minor_cost: int, major_cost: int) -> int:
    """How much of the revision quota one revision spends. Total over `RevisionClass`.

    The two numbers are configuration rather than constants because only one of them is a
    product decision. §12.3's "üç revizyon" fixes the allowance; the *weighting* — a major
    revision costing two because it buys a fresh script generation, and therefore a real provider
    call — is an estimate standing in for a measurement W08's benchmark has not made yet.
    """

    return major_cost if revision is RevisionClass.MAJOR else minor_cost


__all__ = [
    "ERROR_APPROVAL_NOTE_INVALID",
    "ERROR_APPROVAL_NOTE_NOT_ALLOWED",
    "ERROR_APPROVAL_NOTE_REQUIRED",
    "ERROR_APPROVAL_REASON_NOT_ALLOWED",
    "ERROR_APPROVAL_REASON_REQUIRED",
    "ERROR_APPROVAL_NOT_PENDING",
    "ERROR_REVISION_FIELDS_REQUIRED",
    "ERROR_REVISION_NOT_REQUESTED",
    "ERROR_REVISION_QUOTA_EXHAUSTED",
    "MAX_REJECTION_NOTE_CHARS",
    "ApprovalContext",
    "ApprovalDecision",
    "ApprovalPolicy",
    "RejectionReason",
    "RevisionClass",
    "RevisionField",
    "RevisionScope",
    "is_advertisement",
    "qc_is_confident",
    "requires_approval",
    "revision_class",
    "revision_cost",
    "revision_scope",
    "script_names_price",
]
