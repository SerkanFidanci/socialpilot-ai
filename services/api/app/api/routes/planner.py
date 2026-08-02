"""Planner HTTP transport only; every rule lives in the service.

There is no endpoint here that creates an obligation and none that converts one, and that is PM
decision 2 expressed in the routing table. Obligations are materialised by a clock and converted
by a worker; a request that could do either would be a second path to the same effect, and one of
the two would eventually stop agreeing with the other about §13.2's order.

What is exposed is the configuration (`business.update`), the queue read side, and two reports —
the ranked plan with every candidate's derivation, and §13.3's measured distribution. The plan
endpoint is deliberately the *same* pure function the dispatcher ranks with, over the same facts:
"why is this next?" has to be answered by the thing that decides rather than by a second
explanation written to look like it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.core.errors import ProblemException
from app.core.pagination import MAX_PAGE_SIZE, decode_cursor
from app.infrastructure.database.session import get_session
from app.modules.identity.models import User
from app.modules.planner.models import ContentObligation, PlannerSubscriptionItem
from app.modules.planner.obligation import (
    ContentCategory,
    ContentType,
    MixObservation,
    MixTargets,
    ObligationStatus,
    PlanItemStatus,
    PlannerError,
    PlanPeriod,
    RankedObligation,
)
from app.modules.planner.service import PlannerConfigService, PlanningProfile

router = APIRouter(prefix="/v1", tags=["planner"])

MINUTES_PER_DAY = 24 * 60


def service(session: AsyncSession, request: Request) -> PlannerConfigService:
    return PlannerConfigService(session, cast(Settings, request.app.state.settings))


def correlation() -> str:
    return get_correlation_id() or "unknown"


def _problem(error: PlannerError) -> ProblemException:
    """Turn a pure refusal into the documented HTTP one.

    The planner's value layer raises codes, not statuses, because it also runs in a worker where
    there is nobody to return a status to. The mapping lives here, at the one boundary that has a
    response to write.
    """

    return ProblemException(
        status=422,
        code=error.code,
        title="Planner configuration is not valid",
        detail="The planner settings supplied cannot be applied.",
    )


# --- request models --------------------------------------------------------------------------


class MixTargetRequest(BaseModel):
    """§13.3's distribution. Every category, whole percentage points, summing to 100."""

    model_config = ConfigDict(extra="forbid")

    product_service: Annotated[int, Field(strict=True, ge=0, le=100)]
    educational: Annotated[int, Field(strict=True, ge=0, le=100)]
    brand_story: Annotated[int, Field(strict=True, ge=0, le=100)]
    social_proof: Annotated[int, Field(strict=True, ge=0, le=100)]
    entertainment: Annotated[int, Field(strict=True, ge=0, le=100)]
    campaign: Annotated[int, Field(strict=True, ge=0, le=100)]
    corporate: Annotated[int, Field(strict=True, ge=0, le=100)]

    def targets(self) -> MixTargets:
        return MixTargets(
            shares={category: getattr(self, category.value) for category in ContentCategory}
        )


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # Minutes past *local* midnight. Equal values mean "no quiet window", which is a real answer
    # and not a missing one — a business that publishes at any hour says so this way.
    quiet_hours_start_minute: Annotated[int, Field(strict=True, ge=0, lt=MINUTES_PER_DAY)] = 1_320
    quiet_hours_end_minute: Annotated[int, Field(strict=True, ge=0, lt=MINUTES_PER_DAY)] = 480
    mix_targets: MixTargetRequest | None = None
    planning_horizon_days: Annotated[int, Field(strict=True, ge=0, le=60)] = 7


class ItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: ContentType
    category: ContentCategory
    period: PlanPeriod
    publish_minute: Annotated[int, Field(strict=True, ge=0, lt=MINUTES_PER_DAY)]
    # How long before publication generation must start (§13.1's `generation_deadline_at`). A week
    # is the ceiling, because a lead time longer than the longest period would put every deadline
    # before the previous window's slot.
    lead_time_minutes: Annotated[int, Field(strict=True, ge=0, le=10_080)] = 360
    preference_rank: Annotated[int, Field(strict=True, ge=0, le=999)] = 0
    product_id: UUID
    cta_id: UUID
    campaign_offer_id: UUID | None = None
    source_asset_ids: list[UUID] = Field(default_factory=list)


class ItemStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PlanItemStatus


# --- response models -------------------------------------------------------------------------


class SettingsResponse(BaseModel):
    business_id: UUID
    # The tenant's own zone, echoed because every instant below is only meaningful against it.
    timezone: str
    enabled: bool
    quiet_hours_start_minute: int
    quiet_hours_end_minute: int
    mix_targets: dict[str, int]
    planning_horizon_days: int

    @classmethod
    def make(cls, profile: PlanningProfile) -> SettingsResponse:
        return cls(
            business_id=profile.business_id,
            timezone=profile.timezone_name,
            enabled=profile.enabled,
            quiet_hours_start_minute=profile.quiet_hours.start_minute,
            quiet_hours_end_minute=profile.quiet_hours.end_minute,
            mix_targets=profile.mix_targets.as_document(),
            planning_horizon_days=profile.horizon_days,
        )


class ItemResponse(BaseModel):
    id: UUID
    status: PlanItemStatus
    content_type: ContentType
    category: ContentCategory
    period: PlanPeriod
    publish_minute: int
    lead_time_minutes: int
    preference_rank: int
    product_id: UUID
    cta_id: UUID
    campaign_offer_id: UUID | None
    source_asset_ids: list[UUID]
    next_plan_at: datetime | None
    created_at: datetime

    @classmethod
    def make(cls, item: PlannerSubscriptionItem) -> ItemResponse:
        return cls(
            id=item.id,
            status=item.status,
            content_type=item.content_type,
            category=item.category,
            period=item.period,
            publish_minute=item.publish_minute,
            lead_time_minutes=item.lead_time_minutes,
            preference_rank=item.preference_rank,
            product_id=item.product_id,
            cta_id=item.cta_id,
            campaign_offer_id=item.campaign_offer_id,
            source_asset_ids=[UUID(value) for value in item.source_asset_ids],
            next_plan_at=item.next_plan_at,
            created_at=item.created_at,
        )


class ItemPageResponse(BaseModel):
    items: list[ItemResponse]
    next_cursor: str | None


class ObligationResponse(BaseModel):
    """PRD §13.1's record, read back, plus why it is where it is."""

    id: UUID
    subscription_item_id: UUID
    content_type: ContentType
    category: ContentCategory
    status: ObligationStatus
    period_start: datetime
    period_end: datetime
    planned_publish_at: datetime
    generation_deadline_at: datetime
    quiet_hours_shifted: bool
    project_id: UUID | None
    # A documented code, never prose. This is what makes `blocked` visible instead of silent.
    reason_code: str | None
    attempts: int
    next_attempt_at: datetime | None
    created_at: datetime

    @classmethod
    def make(cls, obligation: ContentObligation) -> ObligationResponse:
        return cls(
            id=obligation.id,
            subscription_item_id=obligation.subscription_item_id,
            content_type=obligation.content_type,
            category=obligation.category,
            status=obligation.status,
            period_start=obligation.period_start,
            period_end=obligation.period_end,
            planned_publish_at=obligation.planned_publish_at,
            generation_deadline_at=obligation.generation_deadline_at,
            quiet_hours_shifted=obligation.quiet_hours_shifted,
            project_id=obligation.project_id,
            reason_code=obligation.reason_code,
            attempts=obligation.attempts,
            next_attempt_at=obligation.next_attempt_at,
            created_at=obligation.created_at,
        )


class ObligationPageResponse(BaseModel):
    items: list[ObligationResponse]
    next_cursor: str | None


class RankReasonResponse(BaseModel):
    """One §13.2 priority's answer for one candidate."""

    priority: int
    rank: int
    code: str


class PlanEntryResponse(BaseModel):
    obligation_id: UUID
    position: int
    reasons: list[RankReasonResponse]

    @classmethod
    def make(cls, ranked: RankedObligation) -> PlanEntryResponse:
        return cls(
            obligation_id=ranked.obligation_id,
            position=ranked.position,
            reasons=[
                RankReasonResponse(
                    priority=int(reason.priority), rank=reason.rank, code=reason.code
                )
                for reason in ranked.reasons
            ],
        )


class PlanResponse(BaseModel):
    entries: list[PlanEntryResponse]


class MixEntryResponse(BaseModel):
    category: ContentCategory
    target_share: int
    actual_share: int
    observed: int
    # Positive means under-served. A measurement — nothing refuses anything because of it.
    deviation_points: int

    @classmethod
    def make(cls, observation: MixObservation) -> MixEntryResponse:
        return cls(
            category=observation.category,
            target_share=observation.target_share,
            actual_share=observation.actual_share,
            observed=observation.observed,
            deviation_points=observation.deviation_points,
        )


class MixResponse(BaseModel):
    window_days: int
    entries: list[MixEntryResponse]


# --- settings -----------------------------------------------------------------------------------


@router.get("/businesses/{business_id}/planner/settings", response_model=SettingsResponse)
async def read_settings(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SettingsResponse:
    """Read this business's effective planning configuration, deployment defaults included"""

    profile = await service(session, request).read_settings(
        user_id=user.id, business_id=business_id
    )
    return SettingsResponse.make(profile)


@router.put("/businesses/{business_id}/planner/settings", response_model=SettingsResponse)
async def upsert_settings(
    business_id: UUID,
    payload: SettingsRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SettingsResponse:
    """Set the quiet window, the §13.3 targets and the planning horizon. Admin and owner only"""

    try:
        targets = (
            MixTargets.default() if payload.mix_targets is None else payload.mix_targets.targets()
        )
    except PlannerError as error:
        raise _problem(error) from error
    try:
        profile = await service(session, request).upsert_settings(
            user_id=user.id,
            business_id=business_id,
            enabled=payload.enabled,
            quiet_hours_start_minute=payload.quiet_hours_start_minute,
            quiet_hours_end_minute=payload.quiet_hours_end_minute,
            mix_targets=targets,
            planning_horizon_days=payload.planning_horizon_days,
            correlation_id=correlation(),
        )
    except PlannerError as error:
        raise _problem(error) from error
    return SettingsResponse.make(profile)


# --- standing demand ------------------------------------------------------------------------------


@router.post(
    "/businesses/{business_id}/planner/subscription-items",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    business_id: UUID,
    payload: ItemRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ItemResponse:
    """Register a standing demand for content — §13.1's `subscription_item`, in the small"""

    item = await service(session, request).create_item(
        user_id=user.id,
        business_id=business_id,
        content_type=payload.content_type,
        category=payload.category,
        period=payload.period,
        publish_minute=payload.publish_minute,
        lead_time_minutes=payload.lead_time_minutes,
        preference_rank=payload.preference_rank,
        product_id=payload.product_id,
        cta_id=payload.cta_id,
        campaign_offer_id=payload.campaign_offer_id,
        source_asset_ids=tuple(payload.source_asset_ids),
        idempotency_key=idempotency_key,
        correlation_id=correlation(),
    )
    return ItemResponse.make(item)


@router.get("/businesses/{business_id}/planner/subscription-items", response_model=ItemPageResponse)
async def list_items(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    item_status: Annotated[PlanItemStatus | None, Query(alias="status")] = None,
) -> ItemPageResponse:
    """List this business's standing demands, newest first, with an opaque cursor"""

    page = await service(session, request).list_items(
        user_id=user.id,
        business_id=business_id,
        cursor=decode_cursor(cursor),
        limit=limit,
        status=item_status,
    )
    return ItemPageResponse(
        items=[ItemResponse.make(row) for row in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/businesses/{business_id}/planner/subscription-items/{item_id}", response_model=ItemResponse
)
async def get_item(
    business_id: UUID,
    item_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ItemResponse:
    """Read one standing demand"""

    item = await service(session, request).get_item(
        user_id=user.id, business_id=business_id, item_id=item_id
    )
    return ItemResponse.make(item)


@router.post(
    "/businesses/{business_id}/planner/subscription-items/{item_id}/status",
    response_model=ItemResponse,
)
async def set_item_status(
    business_id: UUID,
    item_id: UUID,
    payload: ItemStatusRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ItemResponse:
    """Pause or resume a standing demand. Obligations already planned are left alone"""

    item = await service(session, request).set_item_status(
        user_id=user.id,
        business_id=business_id,
        item_id=item_id,
        status=payload.status,
        correlation_id=correlation(),
    )
    return ItemResponse.make(item)


# --- obligations ----------------------------------------------------------------------------------


@router.get("/businesses/{business_id}/planner/obligations", response_model=ObligationPageResponse)
async def list_obligations(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    obligation_status: Annotated[ObligationStatus | None, Query(alias="status")] = None,
) -> ObligationPageResponse:
    """List §13.1's queue — including everything that is blocked, and why"""

    page = await service(session, request).list_obligations(
        user_id=user.id,
        business_id=business_id,
        cursor=decode_cursor(cursor),
        limit=limit,
        status=obligation_status,
    )
    return ObligationPageResponse(
        items=[ObligationResponse.make(row) for row in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/businesses/{business_id}/planner/obligations/{obligation_id}",
    response_model=ObligationResponse,
)
async def get_obligation(
    business_id: UUID,
    obligation_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ObligationResponse:
    """Read one obligation and the project it became, if it became one"""

    obligation = await service(session, request).get_obligation(
        user_id=user.id, business_id=business_id, obligation_id=obligation_id
    )
    return ObligationResponse.make(obligation)


@router.post(
    "/businesses/{business_id}/planner/obligations/{obligation_id}/cancel",
    response_model=ObligationResponse,
)
async def cancel_obligation(
    business_id: UUID,
    obligation_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ObligationResponse:
    """Withdraw an obligation that has not become a project. Cancel the project otherwise"""

    obligation = await service(session, request).cancel_obligation(
        user_id=user.id,
        business_id=business_id,
        obligation_id=obligation_id,
        correlation_id=correlation(),
    )
    return ObligationResponse.make(obligation)


# --- the two reports ------------------------------------------------------------------------------


@router.get("/businesses/{business_id}/planner/plan", response_model=PlanResponse)
async def read_plan(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanResponse:
    """§13.2's order over what is convertible now, with every priority's reason attached"""

    ranked = await service(session, request).read_plan(user_id=user.id, business_id=business_id)
    return PlanResponse(entries=[PlanEntryResponse.make(entry) for entry in ranked])


@router.get("/businesses/{business_id}/planner/mix", response_model=MixResponse)
async def read_mix(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MixResponse:
    """§13.3's distribution, measured against the target. A report, never a quota"""

    settings = cast(Settings, request.app.state.settings)
    observations = await service(session, request).read_mix(
        user_id=user.id, business_id=business_id
    )
    return MixResponse(
        window_days=settings.planner_mix_window_days,
        entries=[MixEntryResponse.make(entry) for entry in observations],
    )
