"""Entitlement HTTP transport only; every rule lives in the service.

There is no endpoint here that spends credit, and that is the design rather than an omission.
Spending happens as part of the operation that needs it — opening a content project — inside that
operation's transaction, because a check that commits separately from the hold it authorises is
not a check. An endpoint whose whole effect was "take credits away" would be a second, weaker
path to the same table.

What is exposed is the ledger read side and the one credit *source* this slice recognises: a
manual grant, owner-only. Store verification, renewals and plan mapping are Phase 3 (K1); until
then a grant is how credit enters the system, and it is deliberately the sort of thing that
leaves an audit row with a name on it.
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
from app.core.pagination import MAX_PAGE_SIZE, decode_cursor
from app.infrastructure.database.session import get_session
from app.modules.entitlement.ledger import CreditEntryType, ReservationStatus
from app.modules.entitlement.models import CreditLedgerEntry, UsageReservation
from app.modules.entitlement.service import BalanceView, EntitlementService
from app.modules.identity.models import User

router = APIRouter(prefix="/v1", tags=["entitlement"])

# A ceiling on the request model as well as in the service. The field bound rejects nonsense
# before any rule runs; the configured bound is the one an operator can lower.
MAX_GRANT_CREDITS = 10_000_000


def service(session: AsyncSession, request: Request) -> EntitlementService:
    return EntitlementService(session, cast(Settings, request.app.state.settings))


def correlation() -> str:
    return get_correlation_id() or "unknown"


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Strict: a JSON float is refused rather than coerced. Credits are whole units, and a client
    # computing one in floating point sends `5.0` most of the time and `4.999999999999999`
    # occasionally — the same failure shape `core.money.MinorUnits` exists to close.
    credits: Annotated[int, Field(strict=True, ge=1, le=MAX_GRANT_CREDITS)]
    # A short free-text note for the audit trail ("migration goodwill", "annual contract").
    reason: str | None = Field(default=None, max_length=96)


class BalanceResponse(BaseModel):
    business_id: UUID
    # Spendable now. Derived from the entries at read time; nothing stores it.
    balance_credits: int
    # Of the spend already applied, how much is held by work that has not finished. Reported so a
    # refusal can be explained, and explicitly *not* a second term to subtract: an open
    # reservation has already reduced `balance_credits`.
    reserved_credits: int
    points_table_version: int

    @classmethod
    def make(cls, view: BalanceView) -> BalanceResponse:
        return cls(
            business_id=view.business_id,
            balance_credits=view.balance_credits,
            reserved_credits=view.reserved_credits,
            points_table_version=view.points_table_version,
        )


class LedgerEntryResponse(BaseModel):
    id: UUID
    entry_type: CreditEntryType
    # Signed, exactly as stored. The balance is the sum of these, so a client that adds them up
    # gets the same number the server does.
    delta_credits: int
    points_table_version: int | None
    source_type: str
    source_id: UUID | None
    reservation_id: UUID | None
    reason: str | None
    created_at: datetime

    @classmethod
    def make(cls, entry: CreditLedgerEntry) -> LedgerEntryResponse:
        return cls(
            id=entry.id,
            entry_type=entry.entry_type,
            delta_credits=entry.delta_credits,
            points_table_version=entry.points_table_version,
            source_type=entry.source_type,
            source_id=entry.source_id,
            reservation_id=entry.reservation_id,
            reason=entry.reason,
            created_at=entry.created_at,
        )


class LedgerPageResponse(BaseModel):
    items: list[LedgerEntryResponse]
    next_cursor: str | None


class ReservationResponse(BaseModel):
    id: UUID
    status: ReservationStatus
    credits: int
    points_table_version: int
    point_kind: str
    source_type: str
    source_id: UUID
    failure_code: str | None
    created_at: datetime
    settled_at: datetime | None

    @classmethod
    def make(cls, reservation: UsageReservation) -> ReservationResponse:
        return cls(
            id=reservation.id,
            status=reservation.status,
            credits=reservation.credits,
            points_table_version=reservation.points_table_version,
            point_kind=reservation.point_kind,
            source_type=reservation.source_type,
            source_id=reservation.source_id,
            failure_code=reservation.failure_code,
            created_at=reservation.created_at,
            settled_at=reservation.settled_at,
        )


class ReservationPageResponse(BaseModel):
    items: list[ReservationResponse]
    next_cursor: str | None


@router.get("/businesses/{business_id}/entitlement/balance", response_model=BalanceResponse)
async def read_balance(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BalanceResponse:
    """Read how much credit this business has left, derived from its ledger"""

    view = await service(session, request).read_balance(user_id=user.id, business_id=business_id)
    return BalanceResponse.make(view)


@router.get("/businesses/{business_id}/entitlement/ledger", response_model=LedgerPageResponse)
async def list_ledger(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> LedgerPageResponse:
    """List this business's credit movements newest first, with an opaque cursor"""

    page = await service(session, request).list_ledger(
        user_id=user.id, business_id=business_id, cursor=decode_cursor(cursor), limit=limit
    )
    return LedgerPageResponse(
        items=[LedgerEntryResponse.make(entry) for entry in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/businesses/{business_id}/entitlement/reservations", response_model=ReservationPageResponse
)
async def list_reservations(
    business_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    reservation_status: Annotated[ReservationStatus | None, Query(alias="status")] = None,
) -> ReservationPageResponse:
    """List this business's usage reservations — what credit is held, and by what"""

    page = await service(session, request).list_reservations(
        user_id=user.id,
        business_id=business_id,
        cursor=decode_cursor(cursor),
        limit=limit,
        status=reservation_status,
    )
    return ReservationPageResponse(
        items=[ReservationResponse.make(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/businesses/{business_id}/entitlement/grants",
    response_model=LedgerEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_grant(
    business_id: UUID,
    payload: GrantRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> LedgerEntryResponse:
    """Add credit to a business by hand. Owner only; the only credit source before Phase 3"""

    entry = await service(session, request).grant(
        user_id=user.id,
        business_id=business_id,
        credits=payload.credits,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        correlation_id=correlation(),
    )
    return LedgerEntryResponse.make(entry)
