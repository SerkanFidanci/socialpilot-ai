"""Timeline authoring, parametric editing, and the durable render job.

Two services live here because they run in two processes with different trust and different
failure modes.

`ContentTimelineService` is the API-side one: it authorizes, validates, and writes revisions
and render requests. `ContentRenderService` is the worker-side one: it claims a durable job,
materializes sources, drives `RenderPort`, and persists the result.

The load-bearing decision is that **the worker validates again**. The API already validated
when the revision was written, so re-running §18.3 immediately before rendering looks
redundant — it is not. Between the request and the render, a campaign can expire, a price row
can be superseded, an asset can be deleted or quarantined. Validating at render time means the
frame can only ever contain values that were true when the pixels were drawn, and it closes the
"apply a patch, then skip validation" path a caller might otherwise reach by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.modules.businesses.models import BusinessStatus
from app.modules.businesses.repository import BusinessRepository
from app.modules.content.models import (
    ContentTimeline,
    RenderOutput,
    RenderStatus,
    RenderTrigger,
)
from app.modules.content.patch import PatchOperation, apply_patch, serialize_patch
from app.modules.content.policy import ContentAction, permits_action
from app.modules.content.render import (
    AiDisclosureState,
    ProvenanceState,
    RenderPort,
    RenderProfile,
)
from app.modules.content.repository import (
    RENDER_JOB_TYPE,
    RENDER_RESOURCE_TYPE,
    ContentFactsReader,
    ContentRepository,
)
from app.modules.content.timeline import (
    Timeline,
    TimelineSchemaError,
    parse_timeline,
    serialize_timeline,
)
from app.modules.content.validation import (
    ValidationContext,
    ValidationOutcome,
    validate_timeline,
)
from app.modules.operations.models import (
    AuditLog,
    BackgroundJob,
    IdempotencyKey,
    JobStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import (
    IdempotencyService,
    OperationsService,
    request_fingerprint,
)

# A caption per 150ms of a three-minute render would be pathological; this ceiling keeps the
# generated subtitle file and the filter graph bounded regardless of what the transcript holds.
MAX_CAPTIONS = 400


def current_disclosure_state() -> AiDisclosureState:
    """This slice calls no model, so the only truthful answer is `none`.

    A function rather than a constant so that the moment a generative step joins the pipeline,
    there is exactly one place that has to start answering differently — and one place a test
    can point at to prove that today's answer is not merely a default nobody set.
    """

    return AiDisclosureState.NONE


@dataclass(frozen=True, slots=True)
class TimelineView:
    record: ContentTimeline
    timeline: Timeline


@dataclass(frozen=True, slots=True)
class _IdempotentRequest:
    record: IdempotencyKey
    timeline_id: UUID | None
    render_id: UUID | None


class ContentTimelineService:
    """Authoring and render requests. Every rule runs here, never in a controller."""

    def __init__(self, session: AsyncSession, settings: Settings, render: RenderPort) -> None:
        self._session = session
        self._settings = settings
        self._render = render
        self._repository = ContentRepository(session)
        self._facts = ContentFactsReader(session)
        self._businesses = BusinessRepository(session)

    # --- authoring -------------------------------------------------------------------------

    async def create_timeline(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        document: Any,
        profile: RenderProfile,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> TimelineView:
        timeline = self._parse(document)
        async with self._session.begin():
            await self._authorize(user_id, business_id, ContentAction.TIMELINE_WRITE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                operation="content.timeline.create",
                key=idempotency_key,
                payload={"document": serialize_timeline(timeline), "profile": profile.value},
                correlation_id=correlation_id,
            )
            if replay is not None and replay.timeline_id is not None:
                return await self._load(business_id, replay.timeline_id)
            await self._require_valid(business_id, timeline, profile)
            record = ContentTimeline(
                id=uuid4(),
                business_id=business_id,
                root_id=uuid4(),
                parent_id=None,
                revision=1,
                document=serialize_timeline(timeline),
                created_by_user_id=user_id,
                correlation_id=correlation_id,
            )
            # The first revision anchors its own lineage, so "every revision of this timeline"
            # stays a single indexed equality test rather than a recursive walk.
            record.root_id = record.id
            self._repository.add(record)
            await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="content.timeline.created",
                resource_type="content_timeline",
                resource_id=record.id,
                correlation_id=correlation_id,
                details={"revision": 1, "profile": profile.value},
            )
            await self._complete_idempotent(
                replay, response_status=201, body={"timeline_id": str(record.id)}
            )
            return TimelineView(record=record, timeline=timeline)

    async def patch_timeline(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        timeline_id: UUID,
        operations: tuple[PatchOperation, ...],
        profile: RenderProfile,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> TimelineView:
        """Apply a parametric patch as a new revision, re-validating the result.

        The patch never edits in place. A rejected version and the version offered in its place
        both stay readable, which is what slice 2F's approval flow needs and what makes "this
        revision consumed no new entitlement" auditable rather than asserted.
        """

        async with self._session.begin():
            await self._authorize(user_id, business_id, ContentAction.TIMELINE_WRITE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                operation="content.timeline.patch",
                key=idempotency_key,
                # The whole request, canonically: the target, the profile it will be rendered
                # for, and every operation. Counting operations was not a weaker fingerprint,
                # it was no fingerprint at all — the same key with different replacement text
                # replayed the first revision and the second edit vanished silently.
                payload={
                    "timeline_id": str(timeline_id),
                    "profile": profile.value,
                    "operations": serialize_patch(operations),
                },
                correlation_id=correlation_id,
            )
            if replay is not None and replay.timeline_id is not None:
                return await self._load(business_id, replay.timeline_id)
            parent = await self._repository.get_timeline(business_id, timeline_id)
            if parent is None:
                raise self._not_found("TIMELINE_NOT_FOUND", "Timeline not found")
            current = self._parse(parent.document)
            snap_points = await self._facts.scene_boundaries(business_id, current.asset_ids)
            try:
                patched = apply_patch(
                    current,
                    operations,
                    snap_points=snap_points,
                    snap_tolerance_ms=self._settings.render_snap_tolerance_ms,
                )
            except TimelineSchemaError as error:
                raise self._schema_problem(error) from error
            await self._require_valid(business_id, patched, profile)
            revision = await self._repository.latest_revision(business_id, parent.root_id) + 1
            record = ContentTimeline(
                id=uuid4(),
                business_id=business_id,
                root_id=parent.root_id,
                parent_id=parent.id,
                revision=revision,
                document=serialize_timeline(patched),
                created_by_user_id=user_id,
                correlation_id=correlation_id,
            )
            self._repository.add(record)
            await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="content.timeline.patched",
                resource_type="content_timeline",
                resource_id=record.id,
                correlation_id=correlation_id,
                details={"revision": revision, "operations": len(operations)},
            )
            await self._complete_idempotent(
                replay, response_status=201, body={"timeline_id": str(record.id)}
            )
            return TimelineView(record=record, timeline=patched)

    async def get_timeline(
        self, *, user_id: UUID, business_id: UUID, timeline_id: UUID
    ) -> TimelineView:
        await self._authorize(user_id, business_id, ContentAction.TIMELINE_READ)
        return await self._load(business_id, timeline_id)

    # --- render requests -------------------------------------------------------------------

    async def request_render(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        timeline_id: UUID,
        profile: RenderProfile,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> RenderOutput:
        async with self._session.begin():
            await self._authorize(user_id, business_id, ContentAction.RENDER_REQUEST)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                operation="content.render.request",
                key=idempotency_key,
                payload={"timeline_id": str(timeline_id), "profile": profile.value},
                correlation_id=correlation_id,
            )
            if replay is not None and replay.render_id is not None:
                existing = await self._repository.get_render(business_id, replay.render_id)
                if existing is not None:
                    return existing
            record = await self._repository.get_timeline(business_id, timeline_id)
            if record is None:
                raise self._not_found("TIMELINE_NOT_FOUND", "Timeline not found")
            timeline = self._parse(record.document)
            await self._require_valid(business_id, timeline, profile)

            trigger = RenderTrigger.INITIAL if record.revision == 1 else RenderTrigger.REVISION
            render = RenderOutput(
                id=uuid4(),
                business_id=business_id,
                timeline_id=record.id,
                job_id=None,
                profile=profile,
                status=RenderStatus.PENDING,
                trigger=trigger,
                # A pure re-render of an edited document generates nothing new and calls no
                # provider, so it draws on the revision quota rather than a generation right
                # (plan §2, PRD §12.8). Slice 2E reads this column; nothing else decides it.
                consumes_entitlement=trigger is RenderTrigger.INITIAL,
                ai_disclosure_state=current_disclosure_state(),
                provenance_state=ProvenanceState.ABSENT,
                correlation_id=correlation_id,
            )
            self._repository.add(render)
            await self._session.flush()
            job = self._schedule_job(
                business_id=business_id, render_id=render.id, correlation_id=correlation_id
            )
            await self._session.flush()
            render.job_id = job.id
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="content.render.requested",
                resource_type="render_output",
                resource_id=render.id,
                correlation_id=correlation_id,
                details={
                    "profile": profile.value,
                    "trigger": trigger.value,
                    "consumes_entitlement": render.consumes_entitlement,
                },
            )
            await self._complete_idempotent(
                replay, response_status=202, body={"render_id": str(render.id)}
            )
            return render

    async def get_render(
        self, *, user_id: UUID, business_id: UUID, render_id: UUID
    ) -> RenderOutput:
        await self._authorize(user_id, business_id, ContentAction.RENDER_READ)
        render = await self._repository.get_render(business_id, render_id)
        if render is None:
            raise self._not_found("RENDER_NOT_FOUND", "Render not found")
        return render

    def _schedule_job(
        self, *, business_id: UUID, render_id: UUID, correlation_id: str
    ) -> BackgroundJob:
        """Create the durable job and its outbox wake-up in the caller's transaction."""

        operations = OperationsRepository(self._session)
        job = BackgroundJob(
            id=uuid4(),
            business_id=business_id,
            job_type=RENDER_JOB_TYPE,
            resource_type=RENDER_RESOURCE_TYPE,
            resource_id=render_id,
            status=JobStatus.QUEUED,
            timeout_seconds=self._settings.render_job_timeout_seconds,
            max_attempts=self._settings.render_max_attempts,
            correlation_id=correlation_id,
            next_attempt_at=datetime.now(UTC),
        )
        operations.add(job)
        operations.add(
            OutboxEvent(
                id=uuid4(),
                business_id=business_id,
                event_type="content.render.requested",
                aggregate_type=RENDER_RESOURCE_TYPE,
                aggregate_id=render_id,
                payload={"job_id": str(job.id), "render_id": str(render_id)},
                correlation_id=correlation_id,
                status=OutboxStatus.PENDING,
                max_attempts=job.max_attempts,
                next_attempt_at=datetime.now(UTC),
            )
        )
        return job

    # --- validation plumbing ----------------------------------------------------------------

    async def _require_valid(
        self, business_id: UUID, timeline: Timeline, profile: RenderProfile
    ) -> None:
        outcome = await self.validate(business_id, timeline, profile)
        if not outcome.ok:
            raise ProblemException(
                status=422,
                code="TIMELINE_VALIDATION_FAILED",
                title="Timeline cannot be rendered",
                detail="The timeline failed pre-render validation.",
                meta={
                    "issues": [
                        {"code": issue.code, "pointer": issue.pointer} for issue in outcome.issues
                    ]
                },
            )

    async def validate(
        self, business_id: UUID, timeline: Timeline, profile: RenderProfile
    ) -> ValidationOutcome:
        context = await self.build_context(business_id, timeline)
        return validate_timeline(
            timeline,
            context=context,
            capabilities=self._render.capabilities,
            profile=profile,
            min_resolution_ratio=self._settings.render_min_resolution_ratio,
        )

    async def build_context(self, business_id: UUID, timeline: Timeline) -> ValidationContext:
        """Gather every tenant fact the rules need, in one place, tenant-scoped throughout."""

        references = [
            (overlay.text_source, overlay.reference_id)
            for overlay in timeline.overlays
            if overlay.text_source is not None
            and overlay.text_source.is_verified
            and overlay.reference_id is not None
        ]
        now = datetime.now(UTC)
        return ValidationContext(
            assets=await self._facts.asset_facts(business_id, timeline.asset_ids),
            logo_asset_ids=await self._facts.logo_asset_ids(business_id),
            forbidden_terms=await self._facts.forbidden_terms(business_id),
            verified_values=await self._facts.verified_values(business_id, references, now=now),
            now=now,
        )

    def _parse(self, document: Any) -> Timeline:
        try:
            return parse_timeline(document)
        except TimelineSchemaError as error:
            raise self._schema_problem(error) from error

    @staticmethod
    def _schema_problem(error: TimelineSchemaError) -> ProblemException:
        return ProblemException(
            status=422,
            code="TIMELINE_SCHEMA_INVALID",
            title="Timeline document is not valid",
            detail="The document does not match the timeline schema.",
            # The pointer names the location; the rejected value is never echoed, because a
            # timeline can carry text lifted out of an uploaded video.
            meta={"issue": error.code, "pointer": error.pointer},
        )

    async def _load(self, business_id: UUID, timeline_id: UUID) -> TimelineView:
        record = await self._repository.get_timeline(business_id, timeline_id)
        if record is None:
            raise self._not_found("TIMELINE_NOT_FOUND", "Timeline not found")
        return TimelineView(record=record, timeline=self._parse(record.document))

    # --- shared service plumbing -------------------------------------------------------------

    async def _authorize(self, user_id: UUID, business_id: UUID, action: ContentAction) -> None:
        """Membership first, then permission: an outsider gets `404`, a member gets `403`."""

        membership = await self._businesses.get_active_membership(business_id, user_id)
        if membership is None:
            raise self._not_found("BUSINESS_NOT_FOUND", "Business not found")
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
            raise self._not_found("BUSINESS_NOT_FOUND", "Business not found")
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
    ) -> _IdempotentRequest | None:
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
        timeline_id = body.get("timeline_id") if result.is_replay else None
        render_id = body.get("render_id") if result.is_replay else None
        return _IdempotentRequest(
            record=result.record,
            timeline_id=UUID(timeline_id) if isinstance(timeline_id, str) else None,
            render_id=UUID(render_id) if isinstance(render_id, str) else None,
        )

    async def _complete_idempotent(
        self, request: _IdempotentRequest | None, *, response_status: int, body: dict[str, object]
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
        resource_type: str,
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
                resource_type=resource_type,
                resource_id=resource_id,
                correlation_id=correlation_id,
                details=details,
            )
        )

    @staticmethod
    def _not_found(code: str, title: str) -> ProblemException:
        return ProblemException(
            status=404, code=code, title=title, detail="The resource is not available."
        )
