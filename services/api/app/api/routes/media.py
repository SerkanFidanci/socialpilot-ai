"""Direct-upload control plane routes; no media bytes are accepted."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.config import Settings
from app.infrastructure.database.session import get_session
from app.modules.identity.models import User
from app.modules.media.models import IngestStatus, MediaAssetStatus, UploadSessionStatus
from app.modules.media.service import MediaService
from app.modules.media.storage import CompletedPart, MultipartStoragePort, UploadPartInstruction

router = APIRouter(prefix="/v1", tags=["media"])


class PartResponse(BaseModel):
    part_number: int
    upload_url: str

    @classmethod
    def make(cls, instruction: UploadPartInstruction) -> PartResponse:
        return cls(part_number=instruction.part_number, upload_url=instruction.upload_url)


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    business_id: UUID
    content_type: str
    byte_size: int
    sha256_checksum: str
    status: MediaAssetStatus
    ingest_status: IngestStatus
    created_at: datetime
    uploaded_at: datetime | None


class SessionResponse(BaseModel):
    id: UUID
    asset_id: UUID
    status: UploadSessionStatus
    expires_at: datetime
    parts: list[PartResponse]


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=127)
    byte_size: int = Field(gt=0)
    sha256_checksum: str = Field(min_length=64, max_length=64)
    part_count: int = Field(ge=1, le=1000)


class PartsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_numbers: list[int] = Field(min_length=1, max_length=1000)


class CompletedPartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_number: int = Field(ge=1)
    etag: str = Field(min_length=1, max_length=512)


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256_checksum: str = Field(min_length=64, max_length=64)
    parts: list[CompletedPartRequest] = Field(min_length=1, max_length=1000)


def service(session: AsyncSession, request: Request) -> MediaService:
    return MediaService(
        session,
        cast(Settings, request.app.state.settings),
        cast(MultipartStoragePort, request.app.state.storage),
    )


def response(
    upload_id: UUID,
    asset_id: UUID,
    status: UploadSessionStatus,
    expires_at: datetime,
    instructions: tuple[UploadPartInstruction, ...],
) -> SessionResponse:
    return SessionResponse(
        id=upload_id,
        asset_id=asset_id,
        status=status,
        expires_at=expires_at,
        parts=[PartResponse.make(value) for value in instructions],
    )


@router.post(
    "/businesses/{business_id}/media/uploads", response_model=SessionResponse, status_code=201
)
async def create(
    business_id: UUID,
    payload: CreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionResponse:
    upload, instructions = await service(session, request).create(
        user_id=user.id,
        business_id=business_id,
        filename=payload.filename,
        content_type=payload.content_type,
        byte_size=payload.byte_size,
        checksum=payload.sha256_checksum,
        part_count=payload.part_count,
    )
    return response(upload.id, upload.asset_id, upload.status, upload.expires_at, instructions)


@router.post(
    "/businesses/{business_id}/media/uploads/{upload_session_id}/parts",
    response_model=SessionResponse,
)
async def parts(
    business_id: UUID,
    upload_session_id: UUID,
    payload: PartsRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionResponse:
    upload, instructions = await service(session, request).parts(
        user_id=user.id,
        business_id=business_id,
        session_id=upload_session_id,
        numbers=tuple(payload.part_numbers),
    )
    return response(upload.id, upload.asset_id, upload.status, upload.expires_at, instructions)


@router.post(
    "/businesses/{business_id}/media/uploads/{upload_session_id}/complete",
    response_model=AssetResponse,
)
async def complete(
    business_id: UUID,
    upload_session_id: UUID,
    payload: CompleteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AssetResponse:
    asset = await service(session, request).complete(
        user_id=user.id,
        business_id=business_id,
        session_id=upload_session_id,
        checksum=payload.sha256_checksum,
        parts=tuple(CompletedPart(part.part_number, part.etag) for part in payload.parts),
    )
    return AssetResponse.model_validate(asset)


@router.post("/businesses/{business_id}/media/uploads/{upload_session_id}/cancel", status_code=204)
async def cancel(
    business_id: UUID,
    upload_session_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await service(session, request).cancel(
        user_id=user.id, business_id=business_id, session_id=upload_session_id
    )
    return Response(status_code=204)


@router.get("/businesses/{business_id}/media/{asset_id}", response_model=AssetResponse)
async def asset(
    business_id: UUID,
    asset_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AssetResponse:
    return AssetResponse.model_validate(
        await service(session, request).asset(
            user_id=user.id, business_id=business_id, asset_id=asset_id
        )
    )
