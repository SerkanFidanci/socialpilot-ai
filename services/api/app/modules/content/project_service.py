"""The content project: the API side that opens one, and the worker side that walks it.

Slice 2E's whole claim is that a project **sequences** the capabilities 2A–2D built and does not
own them. Nothing below re-implements script generation, speech, timeline authoring, rendering or
quality control; every step calls the service that already does that job, with its own
authorization, its own idempotency and its own provider accounting intact. What this module adds
is the order, the record of it, and the two bounds that make an automatic pipeline safe to leave
running.

**The project row is the durable job.** There is no paired `jobs` row, because a sequencer's
state *is* its result and two rows saying it is two rows that can disagree after a crash. The
properties AGENTS.md requires of background work are all present on the project itself: a status
(`state`), a timeout (`state_entered_at` against `LIFECYCLE_STEP_TIMEOUT_SECONDS`), attempt
counters, a correlation id, and `FAILED` as the dead letter. `next_check_at` is both the due time
the claim orders by and the lease: it is pushed forward inside the claim transaction, so a worker
that dies mid-step releases the project when the lease expires rather than holding it forever.

**Every step is idempotent by construction.** Each sub-call carries a deterministic idempotency
key derived from the project and the step, so a crash between "the provider answered" and "the
project row was updated" replays the stored answer instead of paying for a second one. This is
the reason those keys are not random.

**Two transactions per step, never one.** The claim reads and leases; the work runs with no
transaction open, because a script generation or a render request opens its own; the settlement
re-locks and applies the transitions. Holding a transaction across a provider call would pin a
PostgreSQL connection for the length of someone else's timeout.

The state machine, the QC decision table and the timeline composition are all in `lifecycle.py`
and all pure. This file is the part that touches the world.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.core.pagination import Cursor, Page, build_page, resolve_limit
from app.modules.businesses.models import BusinessStatus
from app.modules.businesses.repository import BusinessRepository
from app.modules.content.lifecycle import (
    FAILURE_RENDER_ATTEMPTS_EXHAUSTED,
    FAILURE_SCRIPT_FAILED,
    FAILURE_SOURCE_NOT_ANALYZED,
    FAILURE_STATE_TIMEOUT,
    FAILURE_TIMELINE_REJECTED,
    FAILURE_VOICEOVER_FAILED,
    ComposerSegment,
    LifecycleOutcome,
    LifecycleTransitionError,
    ProjectEvent,
    ProjectState,
    TimelineCompositionError,
    compose_timeline,
    decide_after_qc,
    decide_after_render_failure,
    is_terminal,
    require_next_state,
    waits_for_user,
)
from app.modules.content.models import (
    ContentProject,
    ContentProjectTransition,
    RenderStatus,
    RenderTrigger,
)
from app.modules.content.policy import ContentAction, permits_action
from app.modules.content.qc import QcRunStatus, RemediationPath
from app.modules.content.render import RenderPort, RenderProfile
from app.modules.content.repository import (
    PROJECT_RESOURCE_TYPE,
    ContentFactsReader,
    ContentRepository,
    ScriptFactsReader,
)
from app.modules.content.script import ScenarioCode, ScriptGenerationPort, ScriptStatus
from app.modules.content.script_service import ScriptGenerationService, ScriptRequest
from app.modules.content.service import ContentTimelineService
from app.modules.content.timeline import serialize_timeline
from app.modules.content.tts import AudioProbePort, TTSPort, VoiceoverStatus
from app.modules.content.tts_service import VoiceoverRequest, VoiceoverService
from app.modules.entitlement.ledger import SourceOutcome
from app.modules.entitlement.models import SOURCE_CONTENT_PROJECT
from app.modules.entitlement.service import EntitlementService
from app.modules.media.storage import MultipartStoragePort
from app.modules.operations.models import AuditLog, OutboxEvent, OutboxStatus
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import (
    IdempotencyService,
    OperationsService,
    request_fingerprint,
)

PROJECT_ADVANCE_EVENT = "content.project.advance.requested"

# How many detected scenes one composition may consider. Generous for a handful of uploads and
# small enough that a tenant with a long library cannot turn selection into an unbounded read.
MAX_SCENE_CANDIDATES = 200

# What each state of PRD §20's machine means to the credit ledger. Three answers, not eleven:
# entitlement does not know this state machine and must not, because publishing and advertising
# will consume credits under machines of their own.
#
# The table is written out rather than derived from `is_terminal` so that adding a state is a
# decision here about what it costs. The import-time check below makes a forgotten state a
# start-up failure, which is the only moment at which "we never decided" is cheap.
_SOURCE_OUTCOMES: Final[dict[ProjectState, SourceOutcome]] = {
    ProjectState.PLANNED: SourceOutcome.RUNNING,
    ProjectState.WAITING_MEDIA: SourceOutcome.RUNNING,
    ProjectState.ANALYZING: SourceOutcome.RUNNING,
    ProjectState.SCRIPTING: SourceOutcome.RUNNING,
    ProjectState.VOICE_GENERATION: SourceOutcome.RUNNING,
    ProjectState.TIMELINE_BUILDING: SourceOutcome.RUNNING,
    ProjectState.RENDERING: SourceOutcome.RUNNING,
    ProjectState.QUALITY_CHECK: SourceOutcome.RUNNING,
    ProjectState.RETRYING: SourceOutcome.RUNNING,
    # PRD §12.7 draws `RESERVED --> CONSUMED` from "ön izleme başarıyla hazır". This is that
    # state, and it is the only one that charges.
    ProjectState.PREVIEW_READY: SourceOutcome.DELIVERED,
    ProjectState.FAILED: SourceOutcome.ABANDONED,
}

_UNMAPPED_STATES = tuple(state.value for state in ProjectState if state not in _SOURCE_OUTCOMES)
if _UNMAPPED_STATES:  # pragma: no cover - a start-up failure, asserted by the unit suite
    raise RuntimeError(f"project states with no entitlement outcome: {_UNMAPPED_STATES}")


def source_outcome(state: ProjectState) -> SourceOutcome:
    """Total over `ProjectState`. What the ledger needs to know about where a project got to."""

    return _SOURCE_OUTCOMES[state]


class ContentProjectService:
    """The API side: open a project, attach media to one, read where one got to."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = ContentRepository(session)
        self._facts = ScriptFactsReader(session)
        self._businesses = BusinessRepository(session)
        self._entitlement = EntitlementService(session, settings)

    async def create_project(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        scenario_code: ScenarioCode,
        profile: RenderProfile,
        product_id: UUID,
        cta_id: UUID,
        campaign_offer_id: UUID | None,
        source_asset_ids: tuple[UUID, ...],
        idempotency_key: str | None,
        correlation_id: str,
    ) -> ContentProject:
        """Open a project in `PLANNED` and hand it to the sequencer.

        The verified references are checked here, against this tenant, before anything is
        scheduled. A project that names another business's product would otherwise fail four
        steps later, in a worker, with a code about script generation.

        This is also where the generation is paid for. The reservation is opened in *this*
        transaction, so there is no instant at which a project exists without a hold behind it —
        which is what makes two requests aiming at the same last credit resolve to one project
        and one `402` rather than to two projects. PRD §12.8's unit is the content, not the step:
        script generation, speech and every render this project asks for sit inside the one hold,
        and an automatic re-render after a failed check buys nothing further (K4).
        """

        async with self._session.begin():
            await self._authorize(user_id, business_id, ContentAction.PROJECT_WRITE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                operation="content.project.create",
                key=idempotency_key,
                payload={
                    "scenario_code": scenario_code.value,
                    "profile": profile.value,
                    "product_id": str(product_id),
                    "cta_id": str(cta_id),
                    "campaign_offer_id": None
                    if campaign_offer_id is None
                    else str(campaign_offer_id),
                    "source_asset_ids": sorted(str(value) for value in source_asset_ids),
                },
                correlation_id=correlation_id,
            )
            if replay is not None and replay.project_id is not None:
                existing = await self._repository.get_project(business_id, replay.project_id)
                if existing is not None:
                    return existing
            await self._require_inputs(
                business_id,
                product_id=product_id,
                cta_id=cta_id,
                campaign_offer_id=campaign_offer_id,
                source_asset_ids=source_asset_ids,
            )
            now = datetime.now(UTC)
            project = ContentProject(
                id=uuid4(),
                business_id=business_id,
                scenario_code=scenario_code,
                profile=profile,
                state=ProjectState.PLANNED,
                product_id=product_id,
                campaign_offer_id=campaign_offer_id,
                cta_id=cta_id,
                source_asset_ids=[str(value) for value in source_asset_ids],
                render_attempts=0,
                step_attempts=0,
                requires_human_review=False,
                recommended_path=RemediationPath.NONE,
                state_entered_at=now,
                # Due immediately: the outbox event below wakes a worker, and the beat tick is
                # the second net if the broker lost it.
                next_check_at=now,
                requested_by_user_id=user_id,
                correlation_id=correlation_id,
                updated_at=now,
            )
            self._repository.add(project)
            await self._session.flush()
            # Insufficient credit raises `402` from here, and the whole transaction — project
            # row, idempotency record and all — goes with it. A refused generation leaves nothing
            # behind to explain later.
            await self._entitlement.reserve(
                business_id=business_id,
                user_id=user_id,
                scenario_code=scenario_code,
                profile=profile,
                source_type=SOURCE_CONTENT_PROJECT,
                source_id=project.id,
                # Derived from the project rather than taken from the request: the reservation
                # must deduplicate on the work, not on whichever header the caller sent.
                idempotency_key=_reservation_key(project.id),
                correlation_id=correlation_id,
            )
            self._record_transition(
                project,
                from_state=None,
                to_state=ProjectState.PLANNED,
                event=ProjectEvent.CREATED,
                sequence=1,
                reason=None,
                actor_user_id=user_id,
            )
            self._wake_sequencer(project)
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="content.project.created",
                resource_id=project.id,
                correlation_id=correlation_id,
                details={"scenario_code": scenario_code.value, "profile": profile.value},
            )
            await self._complete_idempotent(
                replay, response_status=201, body={"project_id": str(project.id)}
            )
            return project

    async def attach_media(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        project_id: UUID,
        source_asset_ids: tuple[UUID, ...],
        correlation_id: str,
    ) -> ContentProject:
        """Give a project waiting on footage the assets it was waiting for.

        Only `WAITING_MEDIA` accepts this. Replacing the source list of a project that is already
        scripting would mean a script written about footage that is no longer there — the sort of
        silent inconsistency the state machine exists to make impossible.
        """

        if not source_asset_ids:
            raise ProblemException(
                status=422,
                code="PROJECT_SOURCES_REQUIRED",
                title="No media supplied",
                detail="Attaching media requires at least one asset.",
            )
        async with self._session.begin():
            await self._authorize(user_id, business_id, ContentAction.PROJECT_WRITE)
            await self._require_active_business(business_id)
            project = await self._repository.get_project(business_id, project_id, lock=True)
            if project is None:
                raise _not_found("PROJECT_NOT_FOUND", "Project not found")
            if project.state is not ProjectState.WAITING_MEDIA:
                raise ProblemException(
                    status=409,
                    code="PROJECT_TRANSITION_NOT_ALLOWED",
                    title="Project is not waiting for media",
                    detail="Media can only be attached while the project is waiting for it.",
                    meta={"state": project.state.value},
                )
            await self._require_assets(business_id, source_asset_ids)
            project.source_asset_ids = [str(value) for value in source_asset_ids]
            _apply_transition(
                project,
                event=ProjectEvent.MEDIA_ATTACHED,
                reason=None,
                actor_user_id=user_id,
                sequence=await self._repository.next_transition_sequence(business_id, project_id),
                session_add=self._repository.add,
                poll_seconds=0,
            )
            self._wake_sequencer(project)
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="content.project.media_attached",
                resource_id=project.id,
                correlation_id=correlation_id,
                details={"source_assets": len(source_asset_ids)},
            )
            return project

    # --- reads -------------------------------------------------------------------------------

    async def get_project(
        self, *, user_id: UUID, business_id: UUID, project_id: UUID
    ) -> ContentProject:
        await self._authorize(user_id, business_id, ContentAction.PROJECT_READ)
        project = await self._repository.get_project(business_id, project_id)
        if project is None:
            # Another tenant's real project id answers exactly like a made-up one: the query is
            # tenant-scoped, so the two are indistinguishable by construction.
            raise _not_found("PROJECT_NOT_FOUND", "Project not found")
        return project

    async def list_projects(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        cursor: Cursor | None,
        limit: int | None,
        state: ProjectState | None,
    ) -> Page[ContentProject]:
        await self._authorize(user_id, business_id, ContentAction.PROJECT_READ)
        page_size = resolve_limit(limit)
        rows = await self._repository.list_projects(
            business_id, cursor=cursor, limit=page_size, state=state
        )
        return build_page(rows, limit=page_size, key=lambda row: (row.created_at, row.id))

    async def list_transitions(
        self, *, user_id: UUID, business_id: UUID, project_id: UUID
    ) -> list[ContentProjectTransition]:
        """PRD §20's record, read back. This is the answer to "where did this get stuck?"."""

        await self._authorize(user_id, business_id, ContentAction.PROJECT_READ)
        project = await self._repository.get_project(business_id, project_id)
        if project is None:
            raise _not_found("PROJECT_NOT_FOUND", "Project not found")
        return await self._repository.list_transitions(business_id, project_id)

    # --- plumbing ----------------------------------------------------------------------------

    async def _require_inputs(
        self,
        business_id: UUID,
        *,
        product_id: UUID,
        cta_id: UUID,
        campaign_offer_id: UUID | None,
        source_asset_ids: tuple[UUID, ...],
    ) -> None:
        now = datetime.now(UTC)
        if await self._facts.product_brief(business_id, product_id) is None:
            raise _not_found("PROJECT_INPUT_NOT_FOUND", "Input not found")
        if await self._facts.cta_text(business_id, cta_id) is None:
            raise _not_found("PROJECT_INPUT_NOT_FOUND", "Input not found")
        if campaign_offer_id is not None:
            campaign = await self._facts.campaign_brief(business_id, campaign_offer_id, now=now)
            if campaign is None:
                raise _not_found("PROJECT_INPUT_NOT_FOUND", "Input not found")
        await self._require_assets(business_id, source_asset_ids)

    async def _require_assets(self, business_id: UUID, source_asset_ids: tuple[UUID, ...]) -> None:
        if not source_asset_ids:
            return
        if len(source_asset_ids) > self._settings.script_generation_max_source_assets:
            raise ProblemException(
                status=422,
                code="PROJECT_TOO_MANY_SOURCE_ASSETS",
                title="Too many source assets",
                detail="Fewer source assets are allowed for one project.",
            )
        known = await self._facts.known_asset_ids(business_id, source_asset_ids)
        if len(known) != len(set(source_asset_ids)):
            raise _not_found("PROJECT_INPUT_NOT_FOUND", "Input not found")

    def _record_transition(
        self,
        project: ContentProject,
        *,
        from_state: ProjectState | None,
        to_state: ProjectState,
        event: ProjectEvent,
        sequence: int,
        reason: str | None,
        actor_user_id: UUID | None,
    ) -> None:
        self._repository.add(
            ContentProjectTransition(
                id=uuid4(),
                business_id=project.business_id,
                project_id=project.id,
                sequence=sequence,
                from_state=from_state,
                to_state=to_state,
                event=event,
                reason=reason,
                actor_user_id=actor_user_id,
                correlation_id=project.correlation_id,
            )
        )

    def _wake_sequencer(self, project: ContentProject) -> None:
        """Ask the worker to look now, through the outbox rather than the broker directly.

        The event carries no state and the Celery message carries no arguments at all: the worker
        re-reads the project under its own tenant-scoped claim. A lost message costs one beat
        interval, which is exactly what the tick is there for.
        """

        OperationsRepository(self._session).add(
            OutboxEvent(
                id=uuid4(),
                business_id=project.business_id,
                event_type=PROJECT_ADVANCE_EVENT,
                aggregate_type=PROJECT_RESOURCE_TYPE,
                aggregate_id=project.id,
                payload={"project_id": str(project.id)},
                correlation_id=project.correlation_id,
                status=OutboxStatus.PENDING,
                max_attempts=self._settings.render_max_attempts,
                next_attempt_at=datetime.now(UTC),
            )
        )

    async def _authorize(self, user_id: UUID, business_id: UUID, action: ContentAction) -> None:
        membership = await self._businesses.get_active_membership(business_id, user_id)
        if membership is None:
            raise _not_found("BUSINESS_NOT_FOUND", "Business not found")
        if not permits_action(membership.role, action):
            raise ProblemException(
                status=403,
                code="INSUFFICIENT_PERMISSION",
                title="Forbidden",
                detail="You do not have this permission.",
            )

    async def _require_active_business(self, business_id: UUID) -> None:
        business = await self._businesses.get_business(business_id)
        if business is None:
            raise _not_found("BUSINESS_NOT_FOUND", "Business not found")
        if business.status != BusinessStatus.ACTIVE:
            raise ProblemException(
                status=409,
                code="BUSINESS_NOT_MUTABLE",
                title="Business is not mutable",
                detail="Suspended or archived businesses cannot be changed.",
            )

    async def _begin_idempotent(
        self,
        *,
        business_id: UUID,
        user_id: UUID,
        operation: str,
        key: str | None,
        payload: dict[str, object],
        correlation_id: str,
    ) -> _IdempotentProject | None:
        if key is None:
            return None
        result = await IdempotencyService(OperationsRepository(self._session)).acquire(
            business_id=business_id,
            actor_user_id=user_id,
            operation=operation,
            key=key,
            fingerprint=request_fingerprint(payload),
            correlation_id=correlation_id,
        )
        body = result.record.response_body or {}
        project_id = body.get("project_id") if result.is_replay else None
        return _IdempotentProject(
            record=result.record,
            project_id=UUID(project_id) if isinstance(project_id, str) else None,
        )

    async def _complete_idempotent(
        self,
        request: _IdempotentProject | None,
        *,
        response_status: int,
        body: dict[str, object],
    ) -> None:
        if request is None:
            return
        await OperationsService(self._session, self._settings).complete_idempotency(
            request.record, response_status=response_status, response_body=body
        )

    def _audit(
        self,
        *,
        business_id: UUID,
        user_id: UUID,
        action: str,
        resource_id: UUID,
        correlation_id: str,
        details: dict[str, object],
    ) -> None:
        OperationsRepository(self._session).add(
            AuditLog(
                id=uuid4(),
                business_id=business_id,
                actor_user_id=user_id,
                action=action,
                resource_type=PROJECT_RESOURCE_TYPE,
                resource_id=resource_id,
                correlation_id=correlation_id,
                details=details,
            )
        )


@dataclass(frozen=True, slots=True)
class _IdempotentProject:
    record: Any
    project_id: UUID | None


def _not_found(code: str, title: str) -> ProblemException:
    return ProblemException(
        status=404, code=code, title=title, detail="The resource is not available."
    )


# --- the worker side ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ClaimedProject:
    """One leased project, lifted out of the claim transaction as values only."""

    business_id: UUID
    project_id: UUID
    user_id: UUID
    correlation_id: str
    state: ProjectState
    scenario_code: ScenarioCode
    profile: RenderProfile
    product_id: UUID | None
    cta_id: UUID | None
    campaign_offer_id: UUID | None
    source_asset_ids: tuple[UUID, ...]
    script_id: UUID | None
    voiceover_id: UUID | None
    timeline_id: UUID | None
    render_id: UUID | None
    render_attempts: int
    step_attempts: int
    expired: bool


@dataclass(frozen=True, slots=True)
class _Step:
    """What one step decided, applied by the settlement transaction and nowhere else."""

    events: tuple[ProjectEvent, ...] = ()
    reason: str | None = None
    failure_code: str | None = None
    requires_human_review: bool = False
    recommended_path: RemediationPath | None = None
    assignments: dict[str, UUID | None] = field(default_factory=dict)
    render_attempted: bool = False
    step_failed: bool = False

    @property
    def moved(self) -> bool:
        return bool(self.events)


class ContentProjectAdvanceService:
    """Walk one project one step: claim, do the next thing, record the transition.

    Every capability arrives as the service that owns it. This class holds no provider port of
    its own and calls no adapter directly, which is what "the project is a sequencer, not an
    owner" means once it is written down in a constructor.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        render: RenderPort,
        script_generator: ScriptGenerationPort,
        tts: TTSPort,
        audio_probe: AudioProbePort,
        storage: MultipartStoragePort,
    ) -> None:
        self._session = session
        self._settings = settings
        self._repository = ContentRepository(session)
        self._facts = ContentFactsReader(session)
        self._timelines = ContentTimelineService(session, settings, render)
        self._scripts = ScriptGenerationService(session, settings, script_generator)
        self._voiceovers = VoiceoverService(session, settings, tts, audio_probe, storage)
        # Not a capability port: nothing is produced here and no provider is called. The ledger
        # is settled by the same transaction that makes the project terminal, which is the reason
        # this service holds it rather than a separate job doing it afterwards.
        self._entitlement = EntitlementService(session, settings)

    async def process_next(self) -> ContentProject | None:
        claimed = await self._claim()
        if claimed is None:
            return None
        step = await self._run(claimed)
        # A step that only *reads* — "has the render finished?", "is the script settled?" — runs
        # its queries outside any explicit transaction, and SQLAlchemy autobegins one for them
        # that nothing then closes. The settlement has to open its own, so the read snapshot is
        # released here rather than three lines later inside a confusing error. A no-op when a
        # sub-service already committed everything it did.
        await self._session.rollback()
        return await self._settle(claimed, step)

    # --- claim ---------------------------------------------------------------------------------

    async def _claim(self) -> _ClaimedProject | None:
        """Take one due project and push its due time out by the lease.

        The lease is what makes a crashed step recoverable without a second table: the project
        stays claimed for `LIFECYCLE_LEASE_SECONDS` and then becomes due again, at which point
        the step runs from the beginning — which is safe because every sub-call is idempotent.
        """

        async with self._session.begin():
            project = await self._repository.claim_next_due_project()
            if project is None:
                return None
            now = datetime.now(UTC)
            project.next_check_at = now + timedelta(seconds=self._settings.lifecycle_lease_seconds)
            project.updated_at = now
            age = (now - project.state_entered_at).total_seconds()
            return _ClaimedProject(
                business_id=project.business_id,
                project_id=project.id,
                user_id=project.requested_by_user_id,
                correlation_id=project.correlation_id,
                state=project.state,
                scenario_code=project.scenario_code,
                profile=project.profile,
                product_id=project.product_id,
                cta_id=project.cta_id,
                campaign_offer_id=project.campaign_offer_id,
                source_asset_ids=tuple(UUID(value) for value in project.source_asset_ids),
                script_id=project.script_id,
                voiceover_id=project.voiceover_id,
                timeline_id=project.timeline_id,
                render_id=project.render_id,
                render_attempts=project.render_attempts,
                step_attempts=project.step_attempts,
                # A project waiting on a person is not a stalled job, so the ceiling that catches
                # a step nobody will ever finish does not apply to it.
                expired=(
                    not waits_for_user(project.state)
                    and age > self._settings.lifecycle_step_timeout_seconds
                ),
            )

    # --- the steps -----------------------------------------------------------------------------

    async def _run(self, claimed: _ClaimedProject) -> _Step:
        if claimed.expired:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_STATE_TIMEOUT,
                failure_code=FAILURE_STATE_TIMEOUT,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        try:
            return await self._dispatch(claimed)
        except ProblemException as error:
            # A 4xx is the sub-service saying the request can never succeed as stated; a 5xx is
            # it saying "not now". The first ends the project, the second buys another attempt
            # until `LIFECYCLE_MAX_STEP_ATTEMPTS` runs out.
            if error.status < 500:
                return _Step(
                    events=(ProjectEvent.STEP_FAILED,),
                    reason=error.code[:96],
                    failure_code=error.code[:96],
                    requires_human_review=True,
                    recommended_path=RemediationPath.HUMAN_REVIEW,
                )
            return self._retry_step(claimed, code=error.code[:96])

    async def _dispatch(self, claimed: _ClaimedProject) -> _Step:
        if claimed.state is ProjectState.PLANNED:
            if not claimed.source_asset_ids:
                return _Step(events=(ProjectEvent.MEDIA_REQUIRED,))
            return _Step(events=(ProjectEvent.ANALYSIS_STARTED,))
        if claimed.state is ProjectState.WAITING_MEDIA:
            # Nothing to do until `attach_media` runs; that call moves the state itself.
            return _Step()
        if claimed.state is ProjectState.ANALYZING:
            return await self._step_analyzing(claimed)
        if claimed.state is ProjectState.SCRIPTING:
            return await self._step_scripting(claimed)
        if claimed.state is ProjectState.VOICE_GENERATION:
            return await self._step_voice(claimed)
        if claimed.state is ProjectState.TIMELINE_BUILDING:
            return await self._step_timeline(claimed)
        if claimed.state is ProjectState.RENDERING:
            return await self._step_rendering(claimed)
        if claimed.state is ProjectState.QUALITY_CHECK:
            return await self._step_quality_check(claimed)
        if claimed.state is ProjectState.RETRYING:
            # A retry keeps the script, the voiceover and the timeline — they are still valid,
            # and regenerating them would spend a provider call to reproduce the same words.
            # What it drops is the render and its report, so the next pass produces new ones.
            return _Step(
                events=(ProjectEvent.RETRY_STARTED,),
                assignments={"render_id": None, "qc_report_id": None},
            )
        # `PREVIEW_READY` and `FAILED` are terminal and are never claimed; reaching this line
        # would mean the claim predicate and `is_terminal` disagreed.
        raise LifecycleTransitionError(claimed.state, ProjectEvent.STEP_FAILED)

    async def _step_analyzing(self, claimed: _ClaimedProject) -> _Step:
        """Wait until every source has been through the media pipeline far enough to render."""

        async with self._session.begin():
            facts = await self._facts.asset_facts(claimed.business_id, claimed.source_asset_ids)
            ready = [
                asset_id
                for asset_id in claimed.source_asset_ids
                if (entry := facts.get(asset_id)) is not None and entry.renderable
            ]
        if len(ready) == len(claimed.source_asset_ids) and ready:
            return _Step(events=(ProjectEvent.ANALYSIS_COMPLETE,))
        # Not an error yet: analysis is a job of its own and may still be queued. The step
        # timeout is what turns "still not analyzed" into a failure eventually.
        return _Step(reason=FAILURE_SOURCE_NOT_ANALYZED)

    async def _step_scripting(self, claimed: _ClaimedProject) -> _Step:
        if claimed.script_id is not None:
            # Every read a step makes is scoped, and every value it needs leaves that scope as a
            # plain one. Two reasons, both load-bearing: the sub-services below open their own
            # transactions and cannot nest inside this one, and an ORM instance read here would
            # lazy-load against a session that has since moved on.
            async with self._session.begin():
                script = await self._repository.get_script(claimed.business_id, claimed.script_id)
                settled = script is not None and script.status is ScriptStatus.GENERATED
            if settled:
                return _Step(events=(ProjectEvent.SCRIPT_READY,))
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_SCRIPT_FAILED,
                failure_code=FAILURE_SCRIPT_FAILED,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        if claimed.product_id is None or claimed.cta_id is None:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_SCRIPT_FAILED,
                failure_code=FAILURE_SCRIPT_FAILED,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        script = await self._scripts.generate(
            user_id=claimed.user_id,
            business_id=claimed.business_id,
            request=ScriptRequest(
                scenario_code=claimed.scenario_code,
                product_id=claimed.product_id,
                cta_id=claimed.cta_id,
                campaign_offer_id=claimed.campaign_offer_id,
                source_asset_ids=claimed.source_asset_ids,
                target_duration_ms=None,
            ),
            idempotency_key=_step_key(claimed.project_id, "script"),
            correlation_id=claimed.correlation_id,
        )
        return _Step(events=(ProjectEvent.SCRIPT_READY,), assignments={"script_id": script.id})

    async def _step_voice(self, claimed: _ClaimedProject) -> _Step:
        if claimed.script_id is None:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_SCRIPT_FAILED,
                failure_code=FAILURE_SCRIPT_FAILED,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        if claimed.voiceover_id is not None:
            async with self._session.begin():
                voiceover = await self._repository.get_voiceover(
                    claimed.business_id, claimed.voiceover_id
                )
                settled = voiceover is not None and voiceover.status is VoiceoverStatus.GENERATED
            if settled:
                return _Step(events=(ProjectEvent.VOICEOVER_READY,))
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_VOICEOVER_FAILED,
                failure_code=FAILURE_VOICEOVER_FAILED,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        voiceover = await self._voiceovers.generate(
            user_id=claimed.user_id,
            business_id=claimed.business_id,
            request=VoiceoverRequest(script_id=claimed.script_id, voice_profile_code=None),
            idempotency_key=_step_key(claimed.project_id, "voiceover"),
            correlation_id=claimed.correlation_id,
        )
        return _Step(
            events=(ProjectEvent.VOICEOVER_READY,), assignments={"voiceover_id": voiceover.id}
        )

    async def _step_timeline(self, claimed: _ClaimedProject) -> _Step:
        if claimed.timeline_id is not None:
            return _Step(events=(ProjectEvent.TIMELINE_READY,))
        if claimed.script_id is None or claimed.voiceover_id is None:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_TIMELINE_REJECTED,
                failure_code=FAILURE_TIMELINE_REJECTED,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        async with self._session.begin():
            script = await self._repository.get_script(claimed.business_id, claimed.script_id)
            voiceover = await self._repository.get_voiceover(
                claimed.business_id, claimed.voiceover_id
            )
            document = None if script is None else script.document
            speech = None if voiceover is None else (voiceover.id, voiceover.total_duration_ms)
            candidates = await self._facts.scene_candidates(
                claimed.business_id, claimed.source_asset_ids, limit=MAX_SCENE_CANDIDATES
            )
        if document is None or speech is None:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_TIMELINE_REJECTED,
                failure_code=FAILURE_TIMELINE_REJECTED,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        try:
            timeline = compose_timeline(
                segments=_composer_segments(document),
                candidates=candidates,
                profile=claimed.profile,
                voiceover_id=speech[0],
                voiceover_duration_ms=speech[1],
                max_duration_ms=self._settings.render_max_duration_ms,
            )
        except TimelineCompositionError as error:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=error.code,
                failure_code=error.code,
                requires_human_review=True,
                recommended_path=RemediationPath.REQUEST_NEW_MEDIA,
            )
        view = await self._timelines.create_timeline(
            user_id=claimed.user_id,
            business_id=claimed.business_id,
            document=serialize_timeline(timeline),
            profile=claimed.profile,
            idempotency_key=_step_key(claimed.project_id, "timeline"),
            correlation_id=claimed.correlation_id,
        )
        return _Step(
            events=(ProjectEvent.TIMELINE_READY,), assignments={"timeline_id": view.record.id}
        )

    async def _step_rendering(self, claimed: _ClaimedProject) -> _Step:
        if claimed.timeline_id is None:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_TIMELINE_REJECTED,
                failure_code=FAILURE_TIMELINE_REJECTED,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        if claimed.render_id is None:
            ceiling = self._settings.lifecycle_max_render_attempts
            if claimed.render_attempts >= ceiling:
                # The counter is read *before* a render is requested, so the ceiling binds even
                # if every other guard is bypassed. There is no path from here that renders.
                return _Step(
                    events=(ProjectEvent.STEP_FAILED,),
                    reason=FAILURE_RENDER_ATTEMPTS_EXHAUSTED,
                    failure_code=FAILURE_RENDER_ATTEMPTS_EXHAUSTED,
                    requires_human_review=True,
                    recommended_path=RemediationPath.HUMAN_REVIEW,
                )
            requested = await self._timelines.request_render(
                user_id=claimed.user_id,
                business_id=claimed.business_id,
                timeline_id=claimed.timeline_id,
                profile=claimed.profile,
                idempotency_key=_step_key(claimed.project_id, f"render:{claimed.render_attempts}"),
                correlation_id=claimed.correlation_id,
                # A re-render of the same document generates nothing new and calls no provider,
                # so it draws on the revision quota rather than a generation right (plan §2, PRD
                # §12.8). The first render of a project is the generation.
                trigger=(
                    RenderTrigger.INITIAL
                    if claimed.render_attempts == 0
                    else RenderTrigger.REVISION
                ),
            )
            return _Step(assignments={"render_id": requested.id}, render_attempted=True)
        async with self._session.begin():
            render = await self._repository.get_render(claimed.business_id, claimed.render_id)
            outcome = None if render is None else (render.status, render.failure_code)
        if outcome is None:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_TIMELINE_REJECTED,
                failure_code=FAILURE_TIMELINE_REJECTED,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        if outcome[0] is RenderStatus.SUCCEEDED:
            return _Step(events=(ProjectEvent.RENDER_SUCCEEDED,))
        if outcome[0] is RenderStatus.FAILED:
            return _from_outcome(
                decide_after_render_failure(
                    attempts_used=claimed.render_attempts,
                    max_attempts=self._settings.lifecycle_max_render_attempts,
                ),
                reason=(outcome[1] or "")[:96] or None,
            )
        return _Step()

    async def _step_quality_check(self, claimed: _ClaimedProject) -> _Step:
        if claimed.render_id is None:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=FAILURE_TIMELINE_REJECTED,
                failure_code=FAILURE_TIMELINE_REJECTED,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        async with self._session.begin():
            report = await self._repository.latest_qc_report(claimed.business_id, claimed.render_id)
            judgement = (
                None
                if report is None or report.status is QcRunStatus.PENDING
                else (report.id, report.verdict, report.recommended_path)
            )
        if judgement is None:
            # Automatic QC opens its own report from its own claim; until it settles there is
            # nothing to decide. The step timeout catches a run that never does.
            return _Step()
        report_id, verdict, path = judgement
        step = _from_outcome(
            decide_after_qc(
                verdict=verdict,
                path=path,
                attempts_used=claimed.render_attempts,
                max_attempts=self._settings.lifecycle_max_render_attempts,
            ),
            reason=verdict.value,
        )
        if step.assignments:
            # A bounded retry already clears the render and its report so the next pass produces
            # new ones; naming the report that caused it here would put back the reference the
            # retry exists to drop.
            return step
        return _Step(
            events=step.events,
            reason=step.reason,
            failure_code=step.failure_code,
            requires_human_review=step.requires_human_review,
            recommended_path=step.recommended_path,
            assignments={"qc_report_id": report_id},
        )

    def _retry_step(self, claimed: _ClaimedProject, *, code: str) -> _Step:
        if claimed.step_attempts + 1 >= self._settings.lifecycle_max_step_attempts:
            return _Step(
                events=(ProjectEvent.STEP_FAILED,),
                reason=code,
                failure_code=code,
                requires_human_review=True,
                recommended_path=RemediationPath.HUMAN_REVIEW,
            )
        return _Step(reason=code, step_failed=True)

    # --- settlement ---------------------------------------------------------------------------

    async def _settle(self, claimed: _ClaimedProject, step: _Step) -> ContentProject | None:
        """Apply the step's transitions, or reschedule when nothing moved.

        The project is re-read and re-locked here; if its state changed while the step ran — an
        `attach_media` call, or a lease that expired and let another worker in — the settlement
        is dropped rather than forced onto a state it was not computed for.
        """

        async with self._session.begin():
            project = await self._repository.get_project(
                claimed.business_id, claimed.project_id, lock=True
            )
            if project is None or project.state is not claimed.state:
                return project
            now = datetime.now(UTC)
            for name, value in step.assignments.items():
                setattr(project, name, value)
            if step.render_attempted:
                project.render_attempts += 1
            if step.recommended_path is not None:
                project.recommended_path = step.recommended_path
            if step.requires_human_review:
                project.requires_human_review = True
            if step.failure_code is not None:
                project.failure_code = step.failure_code

            sequence = await self._repository.next_transition_sequence(
                claimed.business_id, claimed.project_id
            )
            for offset, event in enumerate(step.events):
                from_state = project.state
                to_state = require_next_state(from_state, event)
                project.state = to_state
                project.state_entered_at = now
                project.step_attempts = 0
                self._add_transition(
                    project,
                    from_state=from_state,
                    to_state=to_state,
                    event=event,
                    sequence=sequence + offset,
                    reason=step.reason,
                )
            if step.step_failed:
                project.step_attempts = claimed.step_attempts + 1
            project.updated_at = now
            project.next_check_at = self._due_after(project, step, now)
            # The hold is closed by the transaction that ends the project, not by a job that runs
            # afterwards. There is therefore no window in which a finished project still holds
            # credit, and no way for a crash to leave one: either both facts committed or
            # neither did. `settle` is a no-op on a project that has not finished.
            await self._entitlement.settle(
                business_id=project.business_id,
                source_type=SOURCE_CONTENT_PROJECT,
                source_id=project.id,
                outcome=source_outcome(project.state),
                failure_code=project.failure_code,
                correlation_id=project.correlation_id,
            )
            return project

    def _due_after(self, project: ContentProject, step: _Step, now: datetime) -> datetime | None:
        if is_terminal(project.state):
            # Terminal projects carry no due time, which is also what keeps them out of the
            # claim's partial index. The check constraint states the same rule in the schema.
            return None
        if step.moved:
            # Something happened: look again immediately so a whole pipeline can run through in
            # one drain batch instead of one state per beat tick.
            return now
        if step.step_failed:
            return now + timedelta(
                seconds=min(
                    2 ** (project.step_attempts + 1) * self._settings.lifecycle_poll_seconds,
                    self._settings.lifecycle_lease_seconds,
                )
            )
        return now + timedelta(seconds=self._settings.lifecycle_poll_seconds)

    def _add_transition(
        self,
        project: ContentProject,
        *,
        from_state: ProjectState | None,
        to_state: ProjectState,
        event: ProjectEvent,
        sequence: int,
        reason: str | None,
    ) -> None:
        self._repository.add(
            ContentProjectTransition(
                id=uuid4(),
                business_id=project.business_id,
                project_id=project.id,
                sequence=sequence,
                from_state=from_state,
                to_state=to_state,
                event=event,
                reason=reason,
                # No actor: the sequencer moved this one. `attach_media` and `create_project`
                # are the transitions a person causes, and those name them.
                actor_user_id=None,
                correlation_id=project.correlation_id,
            )
        )


def _from_outcome(outcome: LifecycleOutcome, *, reason: str | None) -> _Step:
    return _Step(
        events=outcome.events,
        reason=(outcome.failure_code or reason),
        failure_code=outcome.failure_code,
        requires_human_review=outcome.requires_human_review,
        recommended_path=outcome.recommended_path,
        # A bounded retry clears the render it is retrying, so the next pass through `RENDERING`
        # asks for a new one instead of re-reading the failed record forever.
        assignments={"render_id": None, "qc_report_id": None} if outcome.retries_render else {},
    )


def _composer_segments(document: Any) -> tuple[ComposerSegment, ...]:
    """Read §18.1's segments out of a stored script document, defensively.

    The document was produced by `resolve_script` and is therefore already valid, but it is JSONB
    on the way back and this is the one place its shape is trusted. Anything unreadable yields an
    empty tuple, which `compose_timeline` turns into a documented refusal rather than a crash.
    """

    if not isinstance(document, dict):
        return ()
    raw = document.get("segments")
    if not isinstance(raw, list):
        return ()
    segments: list[ComposerSegment] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        tags = entry.get("required_scene_tags")
        duration = entry.get("target_duration_ms")
        if not isinstance(duration, int):
            continue
        segments.append(
            ComposerSegment(
                required_scene_tags=tuple(str(tag) for tag in tags if isinstance(tag, str))
                if isinstance(tags, list)
                else (),
                target_duration_ms=duration,
            )
        )
    return tuple(segments)


def _reservation_key(project_id: UUID) -> str:
    """The idempotency key of a project's one generation hold. One project, one charge."""

    return f"project:{project_id}:generation"


class ContentProjectReservationProbe:
    """Tells the entitlement sweep which projects can no longer settle their own hold.

    It lives here, in the module that owns `content_projects`, because the dependency has to run
    one way: content already calls entitlement, so a query from entitlement into this table would
    close the loop and make either module unusable without the other.

    The query is deliberately not a method on `ContentRepository`. That class's contract is that
    every statement is tenant-scoped, and this one is not: a maintenance sweep runs in a worker
    with no user and no business behind it, and scoping it to a tenant would mean it could only
    ever reconcile a tenant somebody named.

    A project id that returns no row counts as closed. That is the case the sweep exists for: the
    work is gone, so nothing will ever settle the hold it left.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def closed_sources(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]:
        if not source_ids:
            return frozenset()
        statement = select(ContentProject.id).where(
            ContentProject.id.in_(source_ids),
            ContentProject.state.notin_([ProjectState.PREVIEW_READY, ProjectState.FAILED]),
        )
        live = set(await self._session.scalars(statement))
        return frozenset(source_id for source_id in source_ids if source_id not in live)


def _step_key(project_id: UUID, step: str) -> str:
    """A deterministic idempotency key, so a replayed step replays rather than repays.

    This is the whole reason the sub-calls are safe to retry after a crash: the key names the
    project and the step, not the attempt, so the second run of a step whose provider call
    already settled gets the stored answer back.
    """

    return f"project:{project_id}:{step}"


def _apply_transition(
    project: ContentProject,
    *,
    event: ProjectEvent,
    reason: str | None,
    actor_user_id: UUID | None,
    sequence: int,
    session_add: Any,
    poll_seconds: int,
) -> None:
    """Move a project from an API request and record it, in the caller's transaction."""

    from_state = project.state
    to_state = require_next_state(from_state, event)
    now = datetime.now(UTC)
    project.state = to_state
    project.state_entered_at = now
    project.step_attempts = 0
    project.updated_at = now
    project.next_check_at = None if is_terminal(to_state) else now + timedelta(seconds=poll_seconds)
    session_add(
        ContentProjectTransition(
            id=uuid4(),
            business_id=project.business_id,
            project_id=project.id,
            sequence=sequence,
            from_state=from_state,
            to_state=to_state,
            event=event,
            reason=reason,
            actor_user_id=actor_user_id,
            correlation_id=project.correlation_id,
        )
    )


class AbandonedRunSweeper:
    """Settle provider runs that opened, were possibly billed, and never came back.

    Slices 2B and 2C write a `pending` row *before* the first provider call so a billed call that
    never returns still names its route. That is right, and it leaves a debt: nothing ever closes
    those rows. A project sequencing a voiceover would wait on one forever, and a support query
    for "what is still running" would answer with rows that stopped running weeks ago.

    The sweep is a clock comparison, nothing else. `LIFECYCLE_PENDING_SWEEP_AGE_SECONDS` is
    validated to exceed the longest honest run of either capability, so a healthy row cannot be
    caught by it — that bound is a config rule rather than a comment, because a threshold that
    drifted under the timeout would start failing runs that were merely slow.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = ContentRepository(session)

    async def process_next(self) -> dict[str, int] | None:
        """Settle one batch. Returns `None` when there was nothing to sweep, so the drain stops."""

        cutoff = datetime.now(UTC) - timedelta(
            seconds=self._settings.lifecycle_pending_sweep_age_seconds
        )
        limit = self._settings.lifecycle_sweep_batch_size
        async with self._session.begin():
            scripts = await self._repository.claim_stale_pending_scripts(
                older_than=cutoff, limit=limit
            )
            voiceovers = await self._repository.claim_stale_pending_voiceovers(
                older_than=cutoff, limit=limit
            )
            if not scripts and not voiceovers:
                return None
            now = datetime.now(UTC)
            for script in scripts:
                script.status = ScriptStatus.FAILED
                script.failure_code = SCRIPT_ABANDONED
                script.completed_at = now
            for voiceover in voiceovers:
                voiceover.status = VoiceoverStatus.FAILED
                voiceover.failure_code = VOICEOVER_ABANDONED
                voiceover.completed_at = now
            return {"scripts": len(scripts), "voiceovers": len(voiceovers)}


# The run was open long enough that no honest provider call could still be in flight. Distinct
# from the codes a settled failure carries: nobody observed this one fail, which is the fact.
SCRIPT_ABANDONED = "SCRIPT_GENERATION_ABANDONED"
VOICEOVER_ABANDONED = "VOICEOVER_ABANDONED"


def project_summary(project: ContentProject) -> dict[str, object]:
    """The fields a caller needs to know where a project is, with no tenant content in them."""

    return {
        "state": project.state.value,
        "render_attempts": project.render_attempts,
        "requires_human_review": project.requires_human_review,
        "recommended_path": project.recommended_path.value,
        "failure_code": project.failure_code,
    }


__all__ = [
    "MAX_SCENE_CANDIDATES",
    "PROJECT_ADVANCE_EVENT",
    "SCRIPT_ABANDONED",
    "VOICEOVER_ABANDONED",
    "AbandonedRunSweeper",
    "ContentProjectAdvanceService",
    "ContentProjectReservationProbe",
    "ContentProjectService",
    "project_summary",
    "source_outcome",
]
