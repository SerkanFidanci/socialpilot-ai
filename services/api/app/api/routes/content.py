"""Timeline and render HTTP transport only; every rule lives in the service.

The request models here are deliberately thin. A timeline document is *not* modelled as a
Pydantic tree: it arrives as an opaque object and `parse_timeline` validates it, because that
same parser has to run in the worker and inside the patch path where no Pydantic model is in
play. Modelling it twice would create two schemas that agree until the day they do not.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.core.errors import ProblemException
from app.infrastructure.database.session import get_session
from app.infrastructure.render import create_render
from app.modules.content.models import RenderOutput, RenderStatus, RenderTrigger
from app.modules.content.patch import MAX_PATCH_OPERATIONS, parse_patch
from app.modules.content.render import AiDisclosureState, ProvenanceState, RenderProfile
from app.modules.content.service import ContentTimelineService, TimelineView
from app.modules.content.timeline import TimelineSchemaError
from app.modules.identity.models import User

router = APIRouter(prefix="/v1", tags=["content"])


def service(session: AsyncSession, request: Request) -> ContentTimelineService:
    settings = cast(Settings, request.app.state.settings)
    return ContentTimelineService(session, settings, create_render(settings))


def correlation() -> str:
    return get_correlation_id() or "unknown"


class TimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: RenderProfile
    # Opaque on purpose: `parse_timeline` is the one schema, and it runs here, in the patch
    # path, and again in the worker. See the module docstring.
    document: dict[str, Any]


class PatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: RenderProfile
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_PATCH_OPERATIONS)


class RenderRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: RenderProfile


class TimelineResponse(BaseModel):
    id: UUID
    business_id: UUID
    root_id: UUID
    parent_id: UUID | None
    revision: int
    document: dict[str, Any]
    created_at: datetime

    @classmethod
    def make(cls, view: TimelineView) -> TimelineResponse:
        record = view.record
        return cls(
            id=record.id,
            business_id=record.business_id,
            root_id=record.root_id,
            parent_id=record.parent_id,
            revision=record.revision,
            document=dict(record.document),
            created_at=record.created_at,
        )


class RenderResponse(BaseModel):
    id: UUID
    business_id: UUID
    timeline_id: UUID
    profile: RenderProfile
    status: RenderStatus
    trigger: RenderTrigger
    consumes_entitlement: bool
    ai_disclosure_state: AiDisclosureState
    provenance_state: ProvenanceState
    master_object_key: str | None
    preview_object_key: str | None
    thumbnail_object_key: str | None
    duration_ms: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    byte_size: int | None
    failure_code: str | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def make(cls, render: RenderOutput) -> RenderResponse:
        return cls(
            id=render.id,
            business_id=render.business_id,
            timeline_id=render.timeline_id,
            profile=render.profile,
            status=render.status,
            trigger=render.trigger,
            consumes_entitlement=render.consumes_entitlement,
            ai_disclosure_state=render.ai_disclosure_state,
            provenance_state=render.provenance_state,
            # Object keys, never signed URLs. A download link is minted on demand by the
            # storage adapter; putting one in a response body would put it in logs and caches.
            master_object_key=render.master_object_key,
            preview_object_key=render.preview_object_key,
            thumbnail_object_key=render.thumbnail_object_key,
            duration_ms=render.duration_ms,
            width=render.width,
            height=render.height,
            video_codec=render.video_codec,
            audio_codec=render.audio_codec,
            byte_size=render.byte_size,
            failure_code=render.failure_code,
            created_at=render.created_at,
            completed_at=render.completed_at,
        )


@router.post(
    "/businesses/{business_id}/content/timelines",
    response_model=TimelineResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_timeline(
    business_id: UUID,
    payload: TimelineRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TimelineResponse:
    view = await service(session, request).create_timeline(
        user_id=user.id,
        business_id=business_id,
        document=payload.document,
        profile=payload.profile,
        idempotency_key=idempotency_key,
        correlation_id=correlation(),
    )
    return TimelineResponse.make(view)


@router.post(
    "/businesses/{business_id}/content/timelines/{timeline_id}/patch",
    response_model=TimelineResponse,
    status_code=status.HTTP_201_CREATED,
)
async def patch_timeline(
    business_id: UUID,
    timeline_id: UUID,
    payload: PatchRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TimelineResponse:
    """Apply a parametric patch, producing a new revision rather than editing in place."""

    try:
        operations = parse_patch(payload.operations)
    except TimelineSchemaError as error:
        raise ProblemException(
            status=422,
            code="TIMELINE_PATCH_INVALID",
            title="Patch is not valid",
            detail="The patch could not be applied.",
            meta={"issue": error.code, "pointer": error.pointer},
        ) from error
    view = await service(session, request).patch_timeline(
        user_id=user.id,
        business_id=business_id,
        timeline_id=timeline_id,
        operations=operations,
        profile=payload.profile,
        idempotency_key=idempotency_key,
        correlation_id=correlation(),
    )
    return TimelineResponse.make(view)


@router.get(
    "/businesses/{business_id}/content/timelines/{timeline_id}", response_model=TimelineResponse
)
async def get_timeline(
    business_id: UUID,
    timeline_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TimelineResponse:
    view = await service(session, request).get_timeline(
        user_id=user.id, business_id=business_id, timeline_id=timeline_id
    )
    return TimelineResponse.make(view)


@router.post(
    "/businesses/{business_id}/content/timelines/{timeline_id}/renders",
    response_model=RenderResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_render(
    business_id: UUID,
    timeline_id: UUID,
    payload: RenderRequestBody,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RenderResponse:
    """Validate and enqueue a render. The response is the record, not the video."""

    render = await service(session, request).request_render(
        user_id=user.id,
        business_id=business_id,
        timeline_id=timeline_id,
        profile=payload.profile,
        idempotency_key=idempotency_key,
        correlation_id=correlation(),
    )
    return RenderResponse.make(render)


@router.get("/businesses/{business_id}/content/renders/{render_id}", response_model=RenderResponse)
async def get_render(
    business_id: UUID,
    render_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RenderResponse:
    render = await service(session, request).get_render(
        user_id=user.id, business_id=business_id, render_id=render_id
    )
    return RenderResponse.make(render)
