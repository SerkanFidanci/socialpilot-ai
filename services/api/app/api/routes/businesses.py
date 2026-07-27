"""Business and membership HTTP transport only."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.infrastructure.database.session import get_session
from app.modules.businesses.models import BusinessRole, BusinessStatus, MembershipStatus
from app.modules.businesses.service import BusinessService
from app.modules.identity.models import User

router = APIRouter(prefix="/v1", tags=["businesses"])


class BusinessResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    status: BusinessStatus
    timezone: str
    created_by_user_id: UUID


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    user_id: UUID
    role: BusinessRole
    status: MembershipStatus


class CreateBusinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    timezone: str = Field(min_length=1, max_length=64)


class UpdateBusinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    status: BusinessStatus | None = None


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    role: BusinessRole


class UpdateMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: BusinessRole | None = None
    status: MembershipStatus | None = None


@router.get("/businesses", response_model=list[BusinessResponse])
async def list_businesses(
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[BusinessResponse]:
    businesses = await BusinessService(session).list_businesses(current_user.id)
    return [BusinessResponse.model_validate(business) for business in businesses]


@router.post("/businesses", response_model=BusinessResponse, status_code=201)
async def create_business(
    payload: CreateBusinessRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BusinessResponse:
    business = await BusinessService(session).create_business(
        user_id=current_user.id, name=payload.name, timezone=payload.timezone
    )
    return BusinessResponse.model_validate(business)


@router.get("/businesses/{business_id}", response_model=BusinessResponse)
async def get_business(
    business_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BusinessResponse:
    business = await BusinessService(session).get_business(
        user_id=current_user.id, business_id=business_id
    )
    return BusinessResponse.model_validate(business)


@router.patch("/businesses/{business_id}", response_model=BusinessResponse)
async def update_business(
    business_id: UUID,
    payload: UpdateBusinessRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BusinessResponse:
    business = await BusinessService(session).update_business(
        user_id=current_user.id,
        business_id=business_id,
        name=payload.name,
        timezone=payload.timezone,
        status=payload.status,
    )
    return BusinessResponse.model_validate(business)


@router.get("/businesses/{business_id}/members", response_model=list[MemberResponse])
async def list_members(
    business_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MemberResponse]:
    members = await BusinessService(session).list_members(
        user_id=current_user.id, business_id=business_id
    )
    return [MemberResponse.model_validate(member) for member in members]


@router.post("/businesses/{business_id}/members", response_model=MemberResponse, status_code=201)
async def add_member(
    business_id: UUID,
    payload: AddMemberRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MemberResponse:
    member = await BusinessService(session).add_member(
        user_id=current_user.id, business_id=business_id, email=payload.email, role=payload.role
    )
    return MemberResponse.model_validate(member)


@router.patch("/businesses/{business_id}/members/{member_id}", response_model=MemberResponse)
async def update_member(
    business_id: UUID,
    member_id: UUID,
    payload: UpdateMemberRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MemberResponse:
    member = await BusinessService(session).update_member(
        user_id=current_user.id,
        business_id=business_id,
        member_id=member_id,
        role=payload.role,
        status=payload.status,
    )
    return MemberResponse.model_validate(member)
