"""Timeline, render and script HTTP transport only; every rule lives in the service.

The request models here are deliberately thin. A timeline document is *not* modelled as a
Pydantic tree: it arrives as an opaque object and `parse_timeline` validates it, because that
same parser has to run in the worker and inside the patch path where no Pydantic model is in
play. Modelling it twice would create two schemas that agree until the day they do not.

The script endpoints carry the same idea one step further: no request model describes a script
at all. A script is produced by a provider and validated by `parse_script`, so the only thing a
client sends is which verified records the generation may draw on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.core.errors import ProblemException
from app.core.pagination import MAX_PAGE_SIZE, decode_cursor
from app.infrastructure.ai import create_audio_probe, create_script_generator, create_tts
from app.infrastructure.database.session import get_session
from app.infrastructure.render import create_render
from app.modules.content.models import (
    ContentScript,
    RenderOutput,
    RenderQcReport,
    RenderStatus,
    RenderTrigger,
    VoiceoverAsset,
)
from app.modules.content.patch import MAX_PATCH_OPERATIONS, parse_patch
from app.modules.content.qc import QcRunStatus, QcVerdict, RemediationPath
from app.modules.content.qc_service import ContentQcReportService
from app.modules.content.render import AiDisclosureState, ProvenanceState, RenderProfile
from app.modules.content.script import ScenarioCode, ScriptGenerationPort, ScriptStatus
from app.modules.content.script_service import ScriptGenerationService, ScriptRequest
from app.modules.content.service import ContentTimelineService, TimelineView
from app.modules.content.timeline import TimelineSchemaError
from app.modules.content.tts import TTSPort, VoiceoverStatus
from app.modules.content.tts_service import VoiceoverRequest, VoiceoverService
from app.modules.identity.models import User
from app.modules.media.storage import MultipartStoragePort

router = APIRouter(prefix="/v1", tags=["content"])

MAX_SOURCE_ASSETS = 50


def service(session: AsyncSession, request: Request) -> ContentTimelineService:
    settings = cast(Settings, request.app.state.settings)
    return ContentTimelineService(session, settings, create_render(settings))


def get_script_generator(request: Request) -> ScriptGenerationPort:
    """The capability port, resolved through FastAPI so a test can substitute one adapter.

    The render port is built inline in `service()` above because nothing needs to replace it —
    the fake and the real adapter declare identical capabilities. A script adapter is different:
    the interesting cases are hostile *responses*, so the suite has to be able to hand the
    service a provider that returns exactly one.
    """

    return create_script_generator(cast(Settings, request.app.state.settings))


def script_service(
    session: AsyncSession, request: Request, generator: ScriptGenerationPort
) -> ScriptGenerationService:
    settings = cast(Settings, request.app.state.settings)
    return ScriptGenerationService(session, settings, generator)


def get_tts(request: Request) -> TTSPort:
    """The speech port, resolved through FastAPI so a test can substitute one adapter.

    Same reason as `get_script_generator`: the interesting cases are a provider that fails part
    way through a multi-line run, and one that misreports the length of the file it just wrote.
    Neither can be produced without handing the service an adapter that does exactly that.
    """

    return create_tts(cast(Settings, request.app.state.settings))


def voiceover_service(session: AsyncSession, request: Request, tts: TTSPort) -> VoiceoverService:
    settings = cast(Settings, request.app.state.settings)
    return VoiceoverService(
        session,
        settings,
        tts,
        # No fixture probe exists: measurement is the guarantee this slice makes, so it runs
        # ffprobe in every environment.
        create_audio_probe(settings),
        cast(MultipartStoragePort, request.app.state.storage),
    )


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


class QcCheckResponse(BaseModel):
    """One check as the report stored it. Codes and numbers only — never the text on the frame."""

    check: str
    kind: str
    status: str
    code: str | None = None
    pointer: str | None = None
    measured: dict[str, Any] = Field(default_factory=dict)
    remediation: str


class QcReportResponse(BaseModel):
    id: UUID
    business_id: UUID
    render_id: UUID
    status: QcRunStatus
    verdict: QcVerdict
    recommended_path: RemediationPath
    checks: list[QcCheckResponse]
    # What the output measured. No object key and no signed URL: a download link is minted on
    # demand by the storage adapter, and putting one here would put it in logs and caches.
    measurement: dict[str, Any]
    qc_version: int
    # The exact thresholds this verdict was reached under. Returned rather than implied, so a
    # client comparing two reports can see whether the ruleset moved underneath them.
    thresholds: dict[str, Any]
    failure_code: str | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def make(cls, report: RenderQcReport) -> QcReportResponse:
        return cls(
            id=report.id,
            business_id=report.business_id,
            render_id=report.render_id,
            status=report.status,
            verdict=report.verdict,
            recommended_path=report.recommended_path,
            checks=[QcCheckResponse.model_validate(entry) for entry in report.checks or []],
            measurement=dict(report.measurement or {}),
            qc_version=report.qc_version,
            thresholds=dict(report.thresholds or {}),
            failure_code=report.failure_code,
            created_at=report.created_at,
            completed_at=report.completed_at,
        )


@router.get(
    "/businesses/{business_id}/content/renders/{render_id}/qc",
    response_model=QcReportResponse,
)
async def get_render_qc_report(
    business_id: UUID,
    render_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> QcReportResponse:
    """Read the automatic QC report for one render: every check, its verdict, its thresholds"""

    report = await ContentQcReportService(session).get_report(
        user_id=user.id, business_id=business_id, render_id=render_id
    )
    return QcReportResponse.make(report)


class ScriptGenerateRequest(BaseModel):
    """Which verified records the generation may draw on — never any content.

    There is no field here for text, a price, a date or a CTA string. The caller names records;
    the model writes prose around slots; code fills the slots from those records. A request body
    that could carry a price would be the shortest path around the rule this slice exists for.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_code: ScenarioCode
    product_id: UUID
    cta_id: UUID
    campaign_offer_id: UUID | None = None
    source_asset_ids: list[UUID] = Field(default_factory=list, max_length=MAX_SOURCE_ASSETS)
    target_duration_ms: int | None = Field(default=None, ge=5_000, le=90_000)


class ScriptResponse(BaseModel):
    id: UUID
    business_id: UUID
    scenario_code: ScenarioCode
    status: ScriptStatus
    product_id: UUID | None
    campaign_offer_id: UUID | None
    cta_id: UUID | None
    source_asset_ids: list[UUID]
    # The resolved §18.1 contract, and the provider's output with `{{price:…}}` slots intact.
    # Both are returned: the template is the evidence that a figure in the script came from a
    # record rather than from the model.
    document: dict[str, Any] | None
    template: dict[str, Any] | None
    prompt_code: str
    prompt_version: int
    provider: str | None
    model_name: str | None
    failure_code: str | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def make(cls, script: ContentScript) -> ScriptResponse:
        route = script.route_snapshot or {}
        return cls(
            id=script.id,
            business_id=script.business_id,
            scenario_code=script.scenario_code,
            status=script.status,
            product_id=script.product_id,
            campaign_offer_id=script.campaign_offer_id,
            cta_id=script.cta_id,
            source_asset_ids=[UUID(value) for value in script.source_asset_ids],
            document=dict(script.document) if script.document else None,
            template=dict(script.template) if script.template else None,
            prompt_code=script.prompt_code,
            prompt_version=script.prompt_version,
            # Provider and model only. The rest of the route snapshot — the cost ceiling, the
            # data region — is operational and stays out of a tenant-facing body.
            provider=_route_text(route, "provider"),
            model_name=_route_text(route, "model"),
            failure_code=script.failure_code,
            created_at=script.created_at,
            completed_at=script.completed_at,
        )


class ScriptPageResponse(BaseModel):
    items: list[ScriptResponse]
    next_cursor: str | None


def _route_text(route: dict[str, Any], key: str) -> str | None:
    value = route.get(key)
    return value if isinstance(value, str) else None


@router.post(
    "/businesses/{business_id}/scripts",
    response_model=ScriptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_script(
    business_id: UUID,
    payload: ScriptGenerateRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    generator: Annotated[ScriptGenerationPort, Depends(get_script_generator)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ScriptResponse:
    """Generate one script (PRD §18.1) from verified records, synchronously."""

    script = await script_service(session, request, generator).generate(
        user_id=user.id,
        business_id=business_id,
        request=ScriptRequest(
            scenario_code=payload.scenario_code,
            product_id=payload.product_id,
            cta_id=payload.cta_id,
            campaign_offer_id=payload.campaign_offer_id,
            source_asset_ids=tuple(payload.source_asset_ids),
            target_duration_ms=payload.target_duration_ms,
        ),
        idempotency_key=idempotency_key,
        correlation_id=correlation(),
    )
    return ScriptResponse.make(script)


@router.get("/businesses/{business_id}/scripts", response_model=ScriptPageResponse)
async def list_scripts(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    generator: Annotated[ScriptGenerationPort, Depends(get_script_generator)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    scenario_code: Annotated[ScenarioCode | None, Query()] = None,
    script_status: Annotated[ScriptStatus | None, Query(alias="status")] = None,
) -> ScriptPageResponse:
    """List this business's scripts newest first, with an opaque cursor"""

    page = await script_service(session, request, generator).list_scripts(
        user_id=user.id,
        business_id=business_id,
        cursor=decode_cursor(cursor),
        limit=limit,
        scenario_code=scenario_code,
        status=script_status,
    )
    return ScriptPageResponse(
        items=[ScriptResponse.make(script) for script in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/businesses/{business_id}/scripts/{script_id}", response_model=ScriptResponse)
async def get_script(
    business_id: UUID,
    script_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    generator: Annotated[ScriptGenerationPort, Depends(get_script_generator)],
) -> ScriptResponse:
    """Read one script: the resolved contract, the slot template, and its provenance"""

    script = await script_service(session, request, generator).get_script(
        user_id=user.id, business_id=business_id, script_id=script_id
    )
    return ScriptResponse.make(script)


class VoiceoverGenerateRequest(BaseModel):
    """Which script to voice, and in which registered voice — nothing else.

    There is no text field, and adding one would be the shortest path around the rule slice 2B
    exists for. What gets spoken is the script's resolved document, where every price and date
    was substituted by code from a verified record. `voice_profile_code` names an entry in the
    closed `VOICE_PROFILES` registry; a speaking rate or a raw provider voice id is not a
    caller's to choose, so neither is expressible here.
    """

    model_config = ConfigDict(extra="forbid")

    script_id: UUID
    voice_profile_code: str | None = Field(default=None, max_length=64)


class VoiceoverSegmentResponse(BaseModel):
    index: int
    purpose: str
    # The object key, never a signed URL. A download link is minted on demand by the storage
    # adapter; putting one in a response body would put it in logs and caches.
    object_key: str
    content_type: str
    byte_size: int
    duration_ms: int
    # What the provider claimed. Returned beside the measurement rather than instead of it, so a
    # provider that misreports its own output is visible to a client too.
    declared_duration_ms: int | None
    target_duration_ms: int
    drift_ms: int


class VoiceoverResponse(BaseModel):
    id: UUID
    business_id: UUID
    script_id: UUID
    status: VoiceoverStatus
    voice_profile_code: str
    voice_profile_version: int
    audio_format: str
    segments: list[VoiceoverSegmentResponse]
    total_duration_ms: int | None
    target_duration_ms: int | None
    drift_ms: int | None
    provider: str | None
    model_name: str | None
    failure_code: str | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def make(cls, voiceover: VoiceoverAsset) -> VoiceoverResponse:
        route = voiceover.route_snapshot or {}
        return cls(
            id=voiceover.id,
            business_id=voiceover.business_id,
            script_id=voiceover.script_id,
            status=voiceover.status,
            voice_profile_code=voiceover.voice_profile_code,
            voice_profile_version=voiceover.voice_profile_version,
            audio_format=voiceover.audio_format,
            segments=[
                VoiceoverSegmentResponse.model_validate(segment)
                for segment in voiceover.segments or []
            ],
            total_duration_ms=voiceover.total_duration_ms,
            target_duration_ms=voiceover.target_duration_ms,
            drift_ms=voiceover.drift_ms,
            # Provider and model only. The cost ceiling and data region are operational and stay
            # out of a tenant-facing body.
            provider=_route_text(route, "provider"),
            model_name=_route_text(route, "model"),
            failure_code=voiceover.failure_code,
            created_at=voiceover.created_at,
            completed_at=voiceover.completed_at,
        )


class VoiceoverPageResponse(BaseModel):
    items: list[VoiceoverResponse]
    next_cursor: str | None


@router.post(
    "/businesses/{business_id}/voiceovers",
    response_model=VoiceoverResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_voiceover(
    business_id: UUID,
    payload: VoiceoverGenerateRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tts: Annotated[TTSPort, Depends(get_tts)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> VoiceoverResponse:
    """Voice one generated script (PRD §14.8), synchronously, one object per line."""

    voiceover = await voiceover_service(session, request, tts).generate(
        user_id=user.id,
        business_id=business_id,
        request=VoiceoverRequest(
            script_id=payload.script_id, voice_profile_code=payload.voice_profile_code
        ),
        idempotency_key=idempotency_key,
        correlation_id=correlation(),
    )
    return VoiceoverResponse.make(voiceover)


@router.get("/businesses/{business_id}/voiceovers", response_model=VoiceoverPageResponse)
async def list_voiceovers(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tts: Annotated[TTSPort, Depends(get_tts)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    script_id: Annotated[UUID | None, Query()] = None,
    voiceover_status: Annotated[VoiceoverStatus | None, Query(alias="status")] = None,
) -> VoiceoverPageResponse:
    """List this business's voiceovers newest first, with an opaque cursor"""

    page = await voiceover_service(session, request, tts).list_voiceovers(
        user_id=user.id,
        business_id=business_id,
        cursor=decode_cursor(cursor),
        limit=limit,
        script_id=script_id,
        status=voiceover_status,
    )
    return VoiceoverPageResponse(
        items=[VoiceoverResponse.make(voiceover) for voiceover in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/businesses/{business_id}/voiceovers/{voiceover_id}", response_model=VoiceoverResponse)
async def get_voiceover(
    business_id: UUID,
    voiceover_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tts: Annotated[TTSPort, Depends(get_tts)],
) -> VoiceoverResponse:
    """Read one voiceover: its measured segments, its drift, and its provenance"""

    voiceover = await voiceover_service(session, request, tts).get_voiceover(
        user_id=user.id, business_id=business_id, voiceover_id=voiceover_id
    )
    return VoiceoverResponse.make(voiceover)
