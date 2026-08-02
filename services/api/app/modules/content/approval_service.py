"""PRD §21's decisions and revisions: the half that touches the world.

Everything that decides anything is in `approval.py` and is pure. What is here is authorization,
the guarded transition, the record, and the arithmetic of a quota — and three properties are worth
stating before the code, because each one is a rule somebody could reasonably have written the
other way.

**Deciding and revising are two acts by two roles.** An approver rejects with a reason; an editor
then says what should change. PRD §4 gives those to different people, so they are different calls
with different permissions, and a rejection does not silently spend the quota that the revision
answering it will. Collapsing them into one "reject and revise" call would have forced the
approver to hold the editor's permission or the editor to hold the approver's.

**The free note is stored and goes nowhere else.** §21.2 allows a note beside the closed reason and
requires one for `other`. It reaches exactly two places: the `content_approvals` row, and a read
of that row by an authorized member of the same tenant. It is not in the audit detail, not in the
transition `reason` (that column is documented codes), not in an error body, not in a log or a
span, and it is not passed to any provider — the revision that follows a rejection is described to
the pipeline by a set of closed field names, never by the customer's sentence. It is also not run
through the fabrication detector: that exists to stop a *model* inventing a price, and a customer
writing what the price should be is telling us something true about their own catalogue.

**§21.2's last sentence is honoured by not building the thing it warns about.** Rejection reasons
may become model learning data but must stay specific to the user. This slice performs no
aggregation of any kind, cross-tenant or otherwise; the rows carry `business_id` so that any
future use *can* be written as a tenant-scoped query, and doing anything with them across tenants
is a separate decision requiring separate consent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.modules.businesses.models import BusinessStatus
from app.modules.businesses.repository import BusinessRepository
from app.modules.content.approval import (
    ERROR_APPROVAL_NOT_PENDING,
    ERROR_APPROVAL_NOTE_INVALID,
    ERROR_APPROVAL_NOTE_NOT_ALLOWED,
    ERROR_APPROVAL_NOTE_REQUIRED,
    ERROR_APPROVAL_REASON_NOT_ALLOWED,
    ERROR_APPROVAL_REASON_REQUIRED,
    ERROR_REVISION_FIELDS_REQUIRED,
    ERROR_REVISION_NOT_REQUESTED,
    ERROR_REVISION_QUOTA_EXHAUSTED,
    MAX_REJECTION_NOTE_CHARS,
    ApprovalDecision,
    RejectionReason,
    RevisionField,
    RevisionScope,
    revision_class,
    revision_cost,
    revision_scope,
)
from app.modules.content.lifecycle import (
    ProjectEvent,
    ProjectState,
    revision_event,
)
from app.modules.content.models import ContentApproval, ContentProject, ContentRevision
from app.modules.content.policy import ContentAction, permits_action
from app.modules.content.project_service import apply_transition, wake_sequencer
from app.modules.content.repository import PROJECT_RESOURCE_TYPE, ContentRepository
from app.modules.operations.models import AuditLog
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import (
    IdempotencyService,
    OperationsService,
    request_fingerprint,
)

APPROVE_OPERATION = "content.project.decide"
REVISION_OPERATION = "content.project.revise"

# Which produced artefacts a revision of a given scope throws away. Written as data next to the
# scope it belongs to, and read as "everything from this stage onwards": a new voice needs the
# same words, so the script survives, but the timeline that was cut to the old audio does not.
#
# The column names appear here rather than in `lifecycle.py` on purpose — that module is the pure
# state machine and knows nothing about storage. What it does own is the *stage* each scope
# restarts at, and the two have to agree; the unit suite checks that every artefact produced at or
# after a scope's restart stage is in its list.
_SCOPE_CLEARS: Final[dict[RevisionScope, tuple[str, ...]]] = {
    RevisionScope.SCRIPT: ("script_id", "voiceover_id", "timeline_id", "render_id", "qc_report_id"),
    RevisionScope.VOICE: ("voiceover_id", "timeline_id", "render_id", "qc_report_id"),
    RevisionScope.TIMELINE: ("timeline_id", "render_id", "qc_report_id"),
}

_UNMAPPED_SCOPES = tuple(scope.value for scope in RevisionScope if scope not in _SCOPE_CLEARS)
if _UNMAPPED_SCOPES:  # pragma: no cover - a start-up failure, asserted by the unit suite
    raise RuntimeError(f"revision scopes with nothing to clear: {_UNMAPPED_SCOPES}")


class ContentApprovalService:
    """Approve, reject and revise one project (PRD §21). Holds no capability port."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = ContentRepository(session)
        self._businesses = BusinessRepository(session)

    # --- decisions -----------------------------------------------------------------------------

    async def decide(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        project_id: UUID,
        approved: bool,
        rejection_reason: RejectionReason | None,
        note: str | None,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> ContentProject:
        """Record an approver's decision about a preview and move the project.

        One method for both answers because they are one act with one authorization and one
        guard: the project has to be waiting for a decision, and exactly one decision may be
        recorded per wait. Splitting them would duplicate that guard and let the two copies
        drift.
        """

        _validate_note(approved=approved, rejection_reason=rejection_reason, note=note)
        async with self._session.begin():
            await self._authorize(user_id, business_id, ContentAction.PROJECT_DECIDE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                operation=APPROVE_OPERATION,
                key=idempotency_key,
                payload={
                    "project_id": str(project_id),
                    "approved": approved,
                    "rejection_reason": None
                    if rejection_reason is None
                    else rejection_reason.value,
                    # The note is part of the request, so it is part of the fingerprint — but
                    # `request_fingerprint` hashes, so replaying the *same* note is recognised
                    # without the raw text being kept in the idempotency record.
                    "note": note,
                },
                correlation_id=correlation_id,
            )
            if (existing := await self._replayed_project(business_id, replay)) is not None:
                return existing

            project = await self._locked_project(business_id, project_id)
            if project.state is not ProjectState.WAITING_APPROVAL:
                raise ProblemException(
                    status=409,
                    code=ERROR_APPROVAL_NOT_PENDING,
                    title="Project is not waiting for a decision",
                    detail="Only a project awaiting approval can be approved or rejected.",
                    meta={"state": project.state.value},
                )

            self._repository.add(
                ContentApproval(
                    id=uuid4(),
                    business_id=business_id,
                    project_id=project.id,
                    sequence=await self._repository.next_approval_sequence(business_id, project.id),
                    decision=(ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED),
                    policy=project.approval_policy,
                    rejection_reason=rejection_reason,
                    note=note,
                    render_id=project.render_id,
                    actor_user_id=user_id,
                    correlation_id=correlation_id,
                )
            )
            self._apply(
                project,
                event=ProjectEvent.APPROVED if approved else ProjectEvent.REJECTED,
                # A documented code or an enum value, never the note. This column is read back
                # into an API response and into support tooling.
                reason=None if approved else rejection_reason.value if rejection_reason else None,
                actor_user_id=user_id,
                sequence=await self._repository.next_transition_sequence(business_id, project.id),
            )
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="content.project.approved" if approved else "content.project.rejected",
                resource_id=project.id,
                correlation_id=correlation_id,
                # The reason code is a closed enum value and is safe to record. The note is not
                # here, and the sentinel test in the suite is what keeps it that way.
                details={
                    "policy": project.approval_policy.value,
                    "rejection_reason": (
                        None if rejection_reason is None else rejection_reason.value
                    ),
                },
            )
            await self._complete_idempotent(replay, project_id=project.id)
            return project

    # --- revisions -----------------------------------------------------------------------------

    async def request_revision(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        project_id: UUID,
        fields: frozenset[RevisionField],
        idempotency_key: str | None,
        correlation_id: str,
    ) -> ContentProject:
        """Say what should be different, and restart the pipeline where that actually matters.

        The caller names *fields*, never a class and never a restart point: §21.3 hands that
        judgement to the rules engine, and a request that carried its own class would let "this
        is only a small change" be an assertion. Both answers come from the pure classifiers, and
        both are recorded on the revision row so the decision is auditable afterwards.

        **No new entitlement is consumed here** (K4). The reservation this project opened covers
        every step and every render it will ever run; what a revision spends is §12.3's revision
        allowance, which is a per-project counter and not credit. That is a property of where the
        reservation is anchored rather than a rule this method has to remember — there is no call
        to `EntitlementService` in this file at all.
        """

        if not fields:
            raise ProblemException(
                status=422,
                code=ERROR_REVISION_FIELDS_REQUIRED,
                title="No revision requested",
                detail="A revision names at least one field that should change.",
            )
        revision = revision_class(fields)
        scope = revision_scope(fields)
        cost = revision_cost(
            revision,
            minor_cost=self._settings.revision_quota_minor_cost,
            major_cost=self._settings.revision_quota_major_cost,
        )
        async with self._session.begin():
            await self._authorize(user_id, business_id, ContentAction.PROJECT_WRITE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                operation=REVISION_OPERATION,
                key=idempotency_key,
                payload={
                    "project_id": str(project_id),
                    "fields": sorted(field.value for field in fields),
                },
                correlation_id=correlation_id,
            )
            if (existing := await self._replayed_project(business_id, replay)) is not None:
                return existing

            project = await self._locked_project(business_id, project_id)
            if project.state is not ProjectState.REVISION_REQUESTED:
                raise ProblemException(
                    status=409,
                    code=ERROR_REVISION_NOT_REQUESTED,
                    title="Project is not awaiting a revision",
                    detail="A revision can only follow a rejection.",
                    meta={"state": project.state.value},
                )
            used_after = project.revision_quota_used + cost
            if used_after > project.revision_quota:
                # §12.3's allowance, spent. The way forward is a new project, which is a new
                # generation and therefore new credit — said in the detail rather than left for
                # the client to infer, because the alternative reads like a bug.
                raise ProblemException(
                    status=409,
                    code=ERROR_REVISION_QUOTA_EXHAUSTED,
                    title="Revision allowance is used up",
                    detail="This project has no revision allowance left; start a new project.",
                    meta={
                        "revision_quota": project.revision_quota,
                        "revision_quota_used": project.revision_quota_used,
                        "revision_cost": cost,
                        "revision_class": revision.value,
                    },
                )

            approval = await self._repository.latest_approval(business_id, project.id)
            self._repository.add(
                ContentRevision(
                    id=uuid4(),
                    business_id=business_id,
                    project_id=project.id,
                    sequence=await self._repository.next_revision_sequence(business_id, project.id),
                    approval_id=None if approval is None else approval.id,
                    fields=sorted(field.value for field in fields),
                    revision_class=revision,
                    scope=scope,
                    quota_cost=cost,
                    quota_used_after=used_after,
                    requested_by_user_id=user_id,
                    correlation_id=correlation_id,
                )
            )
            project.revisions_requested += 1
            project.revision_quota_used = used_after
            # The automatic render loop's budget is restored, because this is not the automatic
            # loop. `render_attempts` bounds what the machine does to itself after a failed check;
            # a person asking for something different is bounded by the allowance just spent
            # above. Sharing one counter would mean either that a customer cannot get a re-render
            # after two automatic ones, or that the automatic loop could be reopened from outside.
            project.render_attempts = 0
            project.requires_human_review = False
            project.failure_code = None
            for column in _SCOPE_CLEARS[scope]:
                setattr(project, column, None)
            self._apply(
                project,
                event=revision_event(scope),
                reason=revision.value,
                actor_user_id=user_id,
                sequence=await self._repository.next_transition_sequence(business_id, project.id),
            )
            self._wake_sequencer(project)
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="content.project.revision_requested",
                resource_id=project.id,
                correlation_id=correlation_id,
                # Closed field names, a class and a scope. Nothing the customer typed.
                details={
                    "revision_class": revision.value,
                    "scope": scope.value,
                    "fields": sorted(field.value for field in fields),
                    "quota_used": used_after,
                },
            )
            await self._complete_idempotent(replay, project_id=project.id)
            return project

    # --- reads ---------------------------------------------------------------------------------

    async def list_approvals(
        self, *, user_id: UUID, business_id: UUID, project_id: UUID
    ) -> list[ContentApproval]:
        await self._authorize(user_id, business_id, ContentAction.PROJECT_READ)
        await self._require_project(business_id, project_id)
        return await self._repository.list_approvals(business_id, project_id)

    async def list_revisions(
        self, *, user_id: UUID, business_id: UUID, project_id: UUID
    ) -> list[ContentRevision]:
        await self._authorize(user_id, business_id, ContentAction.PROJECT_READ)
        await self._require_project(business_id, project_id)
        return await self._repository.list_project_revisions(business_id, project_id)

    # --- plumbing ------------------------------------------------------------------------------

    def _apply(
        self,
        project: ContentProject,
        *,
        event: ProjectEvent,
        reason: str | None,
        actor_user_id: UUID,
        sequence: int,
    ) -> None:
        apply_transition(
            project,
            event=event,
            reason=reason,
            actor_user_id=actor_user_id,
            sequence=sequence,
            session_add=self._repository.add,
            # A project that just moved because a person acted on it is due now: the outbox event
            # is the wake-up and this is the net under it.
            poll_seconds=0,
        )

    def _wake_sequencer(self, project: ContentProject) -> None:
        wake_sequencer(self._session, project, settings=self._settings)

    async def _locked_project(self, business_id: UUID, project_id: UUID) -> ContentProject:
        project = await self._repository.get_project(business_id, project_id, lock=True)
        if project is None:
            raise _not_found()
        return project

    async def _require_project(self, business_id: UUID, project_id: UUID) -> None:
        if await self._repository.get_project(business_id, project_id) is None:
            raise _not_found()

    async def _authorize(self, user_id: UUID, business_id: UUID, action: ContentAction) -> None:
        membership = await self._businesses.get_active_membership(business_id, user_id)
        if membership is None:
            # Another tenant's business id answers exactly like a made-up one.
            raise ProblemException(
                status=404,
                code="BUSINESS_NOT_FOUND",
                title="Business not found",
                detail="The resource is not available.",
            )
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
            raise ProblemException(
                status=404,
                code="BUSINESS_NOT_FOUND",
                title="Business not found",
                detail="The resource is not available.",
            )
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
    ) -> _IdempotentDecision | None:
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
        return _IdempotentDecision(
            record=result.record,
            project_id=UUID(project_id) if isinstance(project_id, str) else None,
        )

    async def _replayed_project(
        self, business_id: UUID, replay: _IdempotentDecision | None
    ) -> ContentProject | None:
        if replay is None or replay.project_id is None:
            return None
        return await self._repository.get_project(business_id, replay.project_id)

    async def _complete_idempotent(
        self, request: _IdempotentDecision | None, *, project_id: UUID
    ) -> None:
        if request is None:
            return
        await OperationsService(self._session, self._settings).complete_idempotency(
            request.record, response_status=200, response_body={"project_id": str(project_id)}
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
class _IdempotentDecision:
    record: Any
    project_id: UUID | None


def _validate_note(
    *, approved: bool, rejection_reason: RejectionReason | None, note: str | None
) -> None:
    """§21.2's rules about what may accompany what. The database repeats every one of them.

    Checked here as well as in the schema because a constraint violation is a 500 and these are
    all things a client can fix: a documented 422 says which one. The schema is not the
    validation — it is the proof that a row breaking one of these rules cannot exist even if a
    future caller forgets.
    """

    if approved and rejection_reason is not None:
        raise ProblemException(
            status=422,
            code=ERROR_APPROVAL_REASON_NOT_ALLOWED,
            title="A reason explains a rejection",
            detail="Only a rejection carries a reason.",
        )
    if not approved and rejection_reason is None:
        # §21.2's set is closed *and* mandatory. A rejection with no reason is the one shape that
        # would make the whole enum optional in practice, and it is the shape a client reaches
        # for by accident — so it gets its own code rather than a constraint violation.
        raise ProblemException(
            status=422,
            code=ERROR_APPROVAL_REASON_REQUIRED,
            title="This rejection needs a reason",
            detail="Rejecting a preview requires one of the documented reasons.",
        )
    if approved and note is not None:
        raise ProblemException(
            status=422,
            code=ERROR_APPROVAL_NOTE_NOT_ALLOWED,
            title="A note explains a rejection",
            detail="Only a rejection carries a note.",
        )
    if rejection_reason is RejectionReason.OTHER and not (note or "").strip():
        raise ProblemException(
            status=422,
            code=ERROR_APPROVAL_NOTE_REQUIRED,
            title="This rejection needs an explanation",
            detail="Rejecting for 'other' requires a note.",
        )
    if note is None:
        return
    if len(note) > MAX_REJECTION_NOTE_CHARS or "\x00" in note:
        # The ceiling and the NUL check are the only things done to the note. It is the tenant's
        # prose and is stored as written; neither the fabrication detector nor any normalization
        # runs over it, because it is not a model's output and never becomes one's input.
        raise ProblemException(
            status=422,
            code=ERROR_APPROVAL_NOTE_INVALID,
            title="Note cannot be stored",
            detail="The note is too long or contains a null character.",
            meta={"max_chars": MAX_REJECTION_NOTE_CHARS},
        )


def _not_found() -> ProblemException:
    return ProblemException(
        status=404,
        code="PROJECT_NOT_FOUND",
        title="Project not found",
        detail="The resource is not available.",
    )


__all__ = [
    "APPROVE_OPERATION",
    "REVISION_OPERATION",
    "ContentApprovalService",
]
