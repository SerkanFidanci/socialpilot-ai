"""Persistence for timeline revisions and render outputs.

Two tables, and the shape of the first one is the interesting decision: a timeline revision is
a **new row**, not an update. Slice 2F has to show a reviewer what changed between the version
they rejected and the version they are being asked to approve, and slice 2E has to prove that a
re-render consumed no fresh entitlement. Neither is answerable from a document that was
overwritten in place, and both are free if history is the storage model.

`render_outputs` carries two fields this slice deliberately fills with "nothing happened":
`ai_disclosure_state` and `provenance_state`. Nothing here calls a model, so the honest values
are `none` and `stripped_pending_reattach`. They exist now because a record written from the
first render is trustworthy and a column back-filled after the fact is not — see the notes on
each enum in `render.py`.

Slice 2B adds two more (migration `0013`). `content_scripts` keeps a generation's provenance —
which prompt version, which route, which usage row — beside the script itself, because a script
whose origin is unknown cannot be audited when a customer disputes what a post said.
`prompt_templates` (PRD §17.6) is platform configuration rather than tenant data and therefore
carries no `business_id`; it is the one table here a tenant filter would be meaningless on.

Slice 2D adds `render_qc_reports` (PRD §19.4, migration `0015`). Two of its columns are the
reason it is a table rather than a handful of fields on `render_outputs`. `checks` holds the
complete check set with the value each one measured, so a report can be read years later without
re-running anything. `thresholds` holds the numbers that produced the verdict — a version alone
would say *which* ruleset ran and not *what it compared against*, and two reports written a month
apart would not be comparable. `verdict` and `recommended_path` are `NOT NULL` from the `pending`
row onward and start at `needs_review`/`human_review`: a run killed mid-measurement must read as
unreviewed, never as approved, and a nullable column would have left that to whoever wrote the
query.

Slice 2C adds `voiceover_assets` (PRD §28.5, migration `0014`). Its one structural decision is
that the per-line records live in a JSONB column rather than a child table: PRD §28.5 names one
table, the lines are written and read as a set in a single transaction, and their shape is a
contract (`VoiceoverSegment.as_document`) rather than a query surface. What is *not* in JSONB is
everything a later slice filters or joins on — status, total duration, drift, the voice profile
version, the route and usage references — because those are the questions QC and entitlement
will ask across rows.

Slice 2E adds `content_projects` and `content_project_transitions` (PRD §20, migration `0016`)
and one column to `render_outputs`. The project row is itself the durable record of a sequencer
run: it carries the state, the counters that bound the render loop, and the timestamps its claim
orders by, so there is no second job table restating the same facts in different words. The
transition table beside it is §20's closing sentence — every transition recorded, with who and
why — kept as its own queryable surface rather than folded into the audit log, because the
question it exists to answer ("where did this project get stuck?") is a walk over one project's
history while the audit log is a stream over everything a tenant did.

Slice 2F adds `content_approvals` and `content_revisions` (PRD §21, migration `0018`). They are
two tables rather than one because they are two acts by two roles: an approver decides, and an
editor then says what should be different. Folding the rejection reason and the changed fields
into one row would force both to happen at once and would put an approver's judgement and an
editor's request behind the same authorization.

`content_approvals.note` is the one column in this module that holds a **tenant's own prose**.
§21.2 allows a free note beside the closed reason and makes it mandatory for `other`. It is
stored and it is never logged, never put in an error body, never merged into a prompt, and never
scanned by the fabrication detector — that detector exists to stop a *model* inventing a price,
and a customer writing what the price should be is telling us something true about their own
catalogue. §21.2's closing sentence ("kullanıcıya özel kalmalıdır") is why the row carries
`business_id` even though `project_id` already implies it: any future aggregation has to be
expressible as a tenant-scoped query, and this slice performs none.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.content.approval import (
    ApprovalDecision,
    ApprovalPolicy,
    RejectionReason,
    RevisionClass,
    RevisionScope,
)
from app.modules.content.lifecycle import ProjectEvent, ProjectState
from app.modules.content.qc import QcRunStatus, QcVerdict, RemediationPath
from app.modules.content.render import AiDisclosureState, ProvenanceState, RenderProfile
from app.modules.content.script import ScenarioCode, ScriptStatus
from app.modules.content.tts import VoiceoverStatus
from app.modules.identity.models import Base


class RenderStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RenderTrigger(StrEnum):
    """Why a render ran — the fact entitlement accounting keys off.

    `initial` is the first render of a timeline lineage and is the one that will consume a
    generation right when slice 2E wires the ledger. `revision` is a re-render after a
    parametric patch: no provider was called and no new content was generated, so it draws on
    the revision quota instead (plan §2, PRD §12.8). Recording the reason at render time rather
    than inferring it later is what keeps that rule auditable.
    """

    INITIAL = "initial"
    REVISION = "revision"


def _enum(enum_type: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_type, name=name, values_callable=lambda values: [item.value for item in values]
    )


def _business_id() -> Mapped[UUID]:
    return mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )


class ContentTimeline(Base):
    """One immutable revision of a timeline document (PRD §18.2)."""

    __tablename__ = "content_timelines"
    __table_args__ = (
        UniqueConstraint("business_id", "root_id", "revision", name="uq_content_timeline_revision"),
        Index("ix_content_timelines_business_created", "business_id", "created_at", "id"),
        Index("ix_content_timelines_root", "business_id", "root_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    # The first revision of a lineage points at itself, so "every revision of this timeline" is
    # one indexed equality test rather than a recursive walk.
    root_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_timelines.id", ondelete="SET NULL"),
        nullable=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RenderOutput(Base):
    """One render of one timeline revision, with the objects it produced."""

    __tablename__ = "render_outputs"
    __table_args__ = (
        Index("ix_render_outputs_business_created", "business_id", "created_at", "id"),
        Index("ix_render_outputs_timeline", "business_id", "timeline_id"),
        # The set of outputs still waiting for automatic QC, as an index rather than as an
        # anti-join. Slice 2D measured its claim at 134 ms per tick over 200k renders and showed
        # the planner would not use an index on the old shape, because nothing told it that
        # unreported renders are always the newest ones. A partial index over a predicate that
        # becomes false the moment QC opens states that correlation directly: in steady state
        # this index holds the empty set, and the claim is an index scan over it.
        Index(
            "ix_render_outputs_awaiting_qc",
            "completed_at",
            "id",
            postgresql_where=text("status = 'succeeded' AND qc_claimed_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    timeline_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_timelines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # The durable job that will do the work. Nullable because the job row is written in the
    # same transaction and a cancelled job may be pruned before its output record is.
    job_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    profile: Mapped[RenderProfile] = mapped_column(_enum(RenderProfile, "render_profile"))
    status: Mapped[RenderStatus] = mapped_column(_enum(RenderStatus, "render_status"))
    trigger: Mapped[RenderTrigger] = mapped_column(_enum(RenderTrigger, "render_trigger"))
    # Derived from `trigger` at creation and stored, not computed on read: the entitlement
    # ledger in slice 2E must be able to audit what the rule decided at the time.
    consumes_entitlement: Mapped[bool] = mapped_column(Boolean, nullable=False)

    master_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    preview_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    thumbnail_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)

    ai_disclosure_state: Mapped[AiDisclosureState] = mapped_column(
        _enum(AiDisclosureState, "ai_disclosure_state")
    )
    provenance_state: Mapped[ProvenanceState] = mapped_column(
        _enum(ProvenanceState, "render_provenance_state")
    )
    # Where a signed C2PA manifest will live once signing exists. Always NULL in this slice.
    provenance_manifest_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    failure_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When automatic QC opened a report over this output. Set once, in the same transaction that
    # writes the `pending` report, and never cleared — a re-run against changed thresholds is a
    # deliberate second report, not a repeat of the automatic pass. It exists so "awaiting QC"
    # is a predicate on this row rather than an anti-join against another table; see the index
    # above and slice 2D's measurement.
    qc_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RenderQcReport(Base):
    """One automatic QC run over one render output (PRD §19.4).

    The row is written `pending` and committed *before* anything is measured, following the
    pattern `content_scripts` and `voiceover_assets` set: a process killed during the run leaves
    a record that says a run was under way rather than no record at all. What is different here
    is what the pending row *claims*. A pending script says nothing about the script; a pending
    QC report has to say something about the output, and the only safe thing to say is
    `needs_review`. Both judgement columns are therefore `NOT NULL` and start pessimistic.

    `checks` carries every member of `QcCheck` with its status, its reason code and the numbers
    it was reached from — never a rendered string, a resolved price or an object key. A QC report
    is read by support staff and kept indefinitely; turning it into a second place a tenant's
    price is written down would undo what slice 2B built.

    There is no unique constraint on `render_id`, only a partial unique index over runs still
    `pending`. Automatic QC runs once per render because the claim looks for renders with no
    report at all; a later slice re-running QC against changed thresholds should produce a second
    row, and comparing the two is exactly why `thresholds` is snapshotted.
    """

    __tablename__ = "render_qc_reports"
    __table_args__ = (
        Index("ix_render_qc_reports_business_created", "business_id", "created_at", "id"),
        Index("ix_render_qc_reports_business_render", "business_id", "render_id"),
        Index("ix_render_qc_reports_business_verdict", "business_id", "verdict"),
        # One run at a time per render. The claim already takes a row lock, so this is the
        # database saying the same thing independently of the query that happens to run.
        Index(
            "uq_render_qc_report_pending",
            "render_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    # RESTRICT: the report is the evidence about the output, and deleting the output must not
    # silently erase the record of what was found in it.
    render_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("render_outputs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[QcRunStatus] = mapped_column(_enum(QcRunStatus, "qc_run_status"))
    verdict: Mapped[QcVerdict] = mapped_column(_enum(QcVerdict, "qc_verdict"))
    recommended_path: Mapped[RemediationPath] = mapped_column(
        _enum(RemediationPath, "qc_remediation_path")
    )

    checks: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    # What the file itself said. Empty when the measurement could not be taken — which is a
    # fact the checks already carry as `unknown`, not something to be inferred from a NULL.
    measurement: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    qc_version: Mapped[int] = mapped_column(Integer, nullable=False)
    thresholds: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)

    # Which vision adapter was asked, under which ceiling (ADR-007). Written with the pending
    # row, so a call that was billed and never returned still names its route.
    route_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    provider_usage_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("provider_usage.id", ondelete="SET NULL"),
        nullable=True,
    )

    failure_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PromptTemplate(Base):
    """One versioned prompt (PRD §17.6). Platform configuration, not tenant data.

    Rows are append-only: a new prompt is a new version, never an edit, because a stored script
    names the row it was produced from and editing that row would rewrite history for every
    script already generated with it. A partial unique index keeps exactly one active version per
    code, so "which prompt is live" is a database fact rather than a convention.
    """

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_prompt_template_version"),
        Index(
            "uq_prompt_template_active",
            "code",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    # What we *ask* the provider for. What we *accept* is `parse_script`; the two are compared
    # by a test rather than assumed to agree.
    output_schema: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    experiment_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ContentScript(Base):
    """One generation attempt and, when it succeeded, the validated script (PRD §18.1).

    The row is written and committed **before** the provider is called, carrying the route
    snapshot (ADR-007). That ordering is the point: a call that is billed and never returns
    still leaves a `pending` row naming the provider, the model and the ceiling it ran under.
    Settling the attempt afterwards fills in the document, the usage reference and the outcome.

    Both `template` and `document` are kept. `template` is the provider's output with
    `{{price:…}}` slots intact — the evidence that the model referenced a record rather than
    writing a figure — and `document` is §18.1's contract with those slots resolved by code.
    A rejected generation stores neither: text that invented a price must not be persisted just
    because it was interesting.
    """

    __tablename__ = "content_scripts"
    __table_args__ = (
        Index("ix_content_scripts_business_created", "business_id", "created_at", "id"),
        Index("ix_content_scripts_business_status", "business_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    scenario_code: Mapped[ScenarioCode] = mapped_column(
        _enum(ScenarioCode, "content_scenario_code")
    )
    status: Mapped[ScriptStatus] = mapped_column(_enum(ScriptStatus, "content_script_status"))

    # The verified records this generation was allowed to reference. RESTRICT, not CASCADE:
    # deleting a product must not silently erase the record of what was said about it.
    product_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    campaign_offer_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campaign_offers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cta_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("approved_ctas.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_asset_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    template: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    document: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)

    prompt_template_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("prompt_templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    prompt_code: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    route_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    provider_usage_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("provider_usage.id", ondelete="SET NULL"),
        nullable=True,
    )

    failure_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContentProject(Base):
    """One run of the content pipeline, from a brief to a preview (PRD §20).

    This row is the durable job. The other worker services in this module put their state in
    `jobs` and their result in a table beside it, and that split is right when the work is one
    step; a sequencer's state *is* its result, and duplicating it into a second row would create
    two answers to "where is this project" that can disagree after a crash. What the row keeps
    from the job pattern is every property AGENTS.md requires of background work: a status
    (`state`), a timeout (`state_entered_at` against the configured step ceiling), an attempt
    count (`render_attempts`, `step_attempts`), a correlation id, and a terminal `FAILED` state
    that is the dead letter.

    `render_attempts` is the counter that makes an unbounded re-render loop inexpressible. It is
    incremented where a render is *requested*, never where one is judged, so the ceiling holds
    whether the loop came from a failing check or a broken encode. Slice 2D refused to hold a
    render port for exactly this reason; the port is not here either — the service reads this
    number before it asks anyone to render anything.

    The produced artefacts are `RESTRICT` references, not `CASCADE`: the project is the record of
    what was made, and deleting the script it was made from must not quietly leave a project
    claiming a preview whose origin is gone.
    """

    __tablename__ = "content_projects"
    __table_args__ = (
        Index("ix_content_projects_business_created", "business_id", "created_at", "id"),
        Index("ix_content_projects_business_state", "business_id", "state"),
        # The worker's claim. Partial over the non-terminal states so the index holds only live
        # work: a tenant with ten thousand finished projects contributes nothing to it.
        Index(
            "ix_content_projects_due",
            "next_check_at",
            "id",
            postgresql_where=text("state NOT IN ('scheduled', 'failed', 'cancelled')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    scenario_code: Mapped[ScenarioCode] = mapped_column(
        _enum(ScenarioCode, "content_scenario_code")
    )
    profile: Mapped[RenderProfile] = mapped_column(_enum(RenderProfile, "render_profile"))
    state: Mapped[ProjectState] = mapped_column(_enum(ProjectState, "content_project_state"))

    # The verified records the script generation may draw on — the same three the script service
    # takes, held here because a retry has to ask for the same thing a second time.
    product_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    campaign_offer_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campaign_offers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cta_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("approved_ctas.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_asset_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    script_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_scripts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    voiceover_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("voiceover_assets.id", ondelete="RESTRICT"),
        nullable=True,
    )
    timeline_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_timelines.id", ondelete="RESTRICT"),
        nullable=True,
    )
    render_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("render_outputs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    qc_report_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("render_qc_reports.id", ondelete="RESTRICT"),
        nullable=True,
    )

    render_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set when QC said `needs_review`, or when the project failed and a person has to look. It is
    # also `never_within_guardrails`' guardrail signal: a project nobody could verify is exactly
    # the one that policy refuses to let through unseen.
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- approval and revision (PRD §21, slice 2F) ------------------------------------------
    # The §21.1 policy this project was opened under, captured rather than looked up. A tenant
    # that loosens its policy next month must not retroactively change what was required of a
    # preview produced today — the same reason a reservation stores the point-table version it
    # was priced at. §12.2 puts this on a subscription item; that arrives with Phase 3, and
    # until then the value comes from the request or the deployment default.
    approval_policy: Mapped[ApprovalPolicy] = mapped_column(
        _enum(ApprovalPolicy, "content_approval_policy")
    )
    # §12.3's "üç revizyon", frozen onto the project for the same reason as the policy above.
    revision_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    # How many revisions were asked for, and what they cost. Two numbers rather than one because
    # they answer different questions: the count is what makes each revision's sub-calls
    # idempotently distinct, and the weighted total is what §12.3's allowance is spent from
    # (a major revision costs two, because it buys a fresh script generation).
    revisions_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revision_quota_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # When this project first reached `preview_ready` — i.e. the moment PRD §12.7 says the credit
    # is consumed. It is a stored fact rather than a state test because `preview_ready` is no
    # longer terminal: a project that got its preview and then failed a revision must still
    # settle as delivered, or the ledger would be asked to refund a preview the customer already
    # has and would refuse the contradiction.
    preview_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # What slice 2D suggested, recorded even when this slice cannot carry it out. A project that
    # failed with `alternative_scene` is a queryable backlog for 2F/2G rather than a lost note.
    recommended_path: Mapped[RemediationPath] = mapped_column(
        _enum(RemediationPath, "qc_remediation_path")
    )

    # --- scheduling (PRD §13.1, §20, slice 2G) ------------------------------------------------
    # When this content is meant to go out, in UTC. Written by the planner as the project leaves
    # `approved`, and `NOT NULL` in `scheduled` by a check constraint — a scheduled project with
    # no time would be a calendar entry with no date on it.
    #
    # It is a plain instant and not a reference to an obligation, which is what keeps this module
    # independent of `planner`: the sequencer never asks where the time came from, and a project
    # created by hand carries one just the same. The obligation, when there is one, points *here*.
    scheduled_publish_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    state_entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When the sequencer should look at this project again. `NULL` in a terminal state, which is
    # also what keeps finished projects out of the claim index.
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    failure_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ContentProjectTransition(Base):
    """One recorded state change (PRD §20: "her durum geçişi transactional olarak kaydedilmeli").

    Written in the same transaction as the state change it describes, never after it — a
    transition that committed without its record would be exactly the gap this table exists to
    close. `sequence` is per project and unique, so the history has an order that does not depend
    on timestamp resolution and a duplicated write is refused by the database.

    `from_state` is nullable for the one entry that is not a transition: §20's `[*] --> PLANNED`,
    written when the project is created. `reason` is a documented code, never prose and never
    tenant text — a project can fail because a script mentioned something forbidden, and this
    table must not become the place that sentence is stored.

    `actor_user_id` is nullable because most transitions are the sequencer's own. When a person
    caused one — creating the project, attaching media — the row names them, which is the "kim"
    half of §20's sentence.
    """

    __tablename__ = "content_project_transitions"
    __table_args__ = (
        UniqueConstraint("project_id", "sequence", name="uq_content_project_transition_sequence"),
        Index("ix_content_project_transitions_project", "business_id", "project_id", "sequence"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    # CASCADE, unlike every produced artefact above: this row is not evidence *about* the
    # project, it is part of it, and a history without its project is unreadable.
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[ProjectState | None] = mapped_column(
        _enum(ProjectState, "content_project_state"), nullable=True
    )
    to_state: Mapped[ProjectState] = mapped_column(_enum(ProjectState, "content_project_state"))
    event: Mapped[ProjectEvent] = mapped_column(_enum(ProjectEvent, "content_project_event"))
    reason: Mapped[str | None] = mapped_column(String(96), nullable=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ContentApproval(Base):
    """One decision about one preview (PRD §21.1, §21.2).

    Rows are appended, never updated: an approval that was later withdrawn is two rows, and a
    project rejected twice has two reasons. The table is the answer to "who let this out, and
    under which policy" — which is a question an automatic approval has to answer too, so
    `AUTO_APPROVED` is a decision here with a null actor rather than an absent row.

    `note` is the tenant's own words (§21.2's free text, mandatory when the reason is `other`).
    Three constraints below say what may accompany what, so a row that claims a reason without a
    rejection, or `other` without an explanation, cannot exist even if a service forgot to check.
    """

    __tablename__ = "content_approvals"
    __table_args__ = (
        UniqueConstraint("project_id", "sequence", name="uq_content_approval_sequence"),
        Index("ix_content_approvals_project", "business_id", "project_id", "sequence"),
        Index("ix_content_approvals_business_created", "business_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[ApprovalDecision] = mapped_column(
        _enum(ApprovalDecision, "content_approval_decision")
    )
    # The policy that asked for this decision — or, for an automatic approval, the policy that
    # decided nobody had to be asked. Stored per decision because a project can be rejected,
    # revised and judged again, and the policy is read afresh each time.
    policy: Mapped[ApprovalPolicy] = mapped_column(_enum(ApprovalPolicy, "content_approval_policy"))
    rejection_reason: Mapped[RejectionReason | None] = mapped_column(
        _enum(RejectionReason, "content_rejection_reason"), nullable=True
    )
    # Untrusted tenant prose. Never logged, never in an error body, never in a prompt.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The render this decision was made about. `RESTRICT`, like every produced artefact: an
    # approval whose subject is gone is a claim nobody can check.
    render_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("render_outputs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Null exactly when the decision was automatic. PRD §4's approver is the only role that can
    # fill it, which the service enforces and the constraint below makes structurally visible.
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ContentRevision(Base):
    """One revision request (PRD §21.3): what should change, what it cost, where it restarts.

    `fields` is a JSONB list of `RevisionField` values rather than a child table for the same
    reason a voiceover's segments are: it is written and read as a set inside one transaction and
    its shape is a contract, not a query surface. What is *not* in JSONB is everything a later
    question filters on — the class, the scope and the quota arithmetic.

    `quota_used_after` is a running total stored beside the delta on purpose, and it is the one
    place this module keeps a derived number. The entitlement ledger deliberately does not
    (ADR-017): its balance is a sum over entries, and a stored total that disagreed with them
    could not be adjudicated. Here the opposite holds — the quota is a small per-project counter
    whose authority is the project row itself, and this column is the receipt showing what the
    counter read after each request, so "which revision used up the allowance" is answerable
    without replaying the classifier over historical field sets.
    """

    __tablename__ = "content_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "sequence", name="uq_content_revision_sequence"),
        Index("ix_content_revisions_project", "business_id", "project_id", "sequence"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # The approval this revision answers. Nullable only because the decision row could in
    # principle be absent for a project revised outside the reject path; today it is always set.
    approval_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_approvals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    revision_class: Mapped[RevisionClass] = mapped_column(
        _enum(RevisionClass, "content_revision_class")
    )
    scope: Mapped[RevisionScope] = mapped_column(_enum(RevisionScope, "content_revision_scope"))
    quota_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    quota_used_after: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VoiceoverAsset(Base):
    """One voiceover produced from one script (PRD §28.5, §14.8).

    Like `content_scripts`, the row is written and committed **before** the first provider call,
    carrying the route snapshot (ADR-007), so calls that were billed and never settled leave a
    `pending` row naming the provider, the model and the ceiling they ran under.

    Two columns are the reason this table exists rather than a field on the script.
    `total_duration_ms` is the sum of **ffprobe measurements**, never a provider's declaration —
    it is what §18.3's "seslendirme süresi" check compares against the canvas, so a number
    nobody verified would make that check theatre. `drift_ms` is that measurement minus the
    script's own target: slice 2D decides what an unacceptable drift is; this slice only records
    it, which is why there is no threshold anywhere in this module.

    `voice_profile` stores the exact document handed to the provider, beside its code and
    version (§17.6's pattern). Audio whose voice and speaking rate cannot be named later is
    audio nobody can reproduce, and a registry edited tomorrow must not rewrite what was true
    today.
    """

    __tablename__ = "voiceover_assets"
    __table_args__ = (
        Index("ix_voiceover_assets_business_created", "business_id", "created_at", "id"),
        Index("ix_voiceover_assets_business_script", "business_id", "script_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    # RESTRICT, not CASCADE: the script is the evidence of what was said, and the audio is the
    # evidence of how it was said. Deleting one must not silently orphan the other.
    script_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_scripts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[VoiceoverStatus] = mapped_column(_enum(VoiceoverStatus, "voiceover_status"))

    voice_profile_code: Mapped[str] = mapped_column(String(64), nullable=False)
    voice_profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    voice_profile: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    audio_format: Mapped[str] = mapped_column(String(16), nullable=False)

    # One entry per script segment: object key, measured duration, the provider's declaration,
    # and the script's target. Written even for a failed run, so partially produced objects are
    # visible rather than orphaned in storage.
    segments: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    total_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drift_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    route_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    # The usage row that settled the run. Every call writes its own row (§39.1 attributes each
    # external call); they share this request's `correlation_id`, and this column names the last
    # one, whose `outcome` therefore matches this row's `status`.
    provider_usage_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("provider_usage.id", ondelete="SET NULL"),
        nullable=True,
    )

    failure_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
