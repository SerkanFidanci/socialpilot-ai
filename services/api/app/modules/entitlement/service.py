"""Opening, settling and reading entitlement. The half that touches the world.

Two kinds of method live here and the difference is deliberate.

`grant`, `read_balance` and the list methods are request-shaped: they open their own transaction,
authorize, and return. `reserve` and `settle` are the opposite — they run **inside the caller's
transaction and never open one**. That is the whole design of the check. PRD §12.8 says the right
is checked and then held; if the check and the hold commit separately, two requests can both pass
the check. Making the reservation part of the transaction that creates the work means there is no
moment at which the work exists and the credit does not, in either direction.

The lock ordering is the same in both directions — tenant first, row second — so a reservation
being opened and a reservation being settled can never wait on each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.core.pagination import Cursor, Page, build_page, resolve_limit
from app.modules.businesses.models import BusinessStatus
from app.modules.businesses.repository import BusinessRepository
from app.modules.content.render import RenderProfile
from app.modules.content.script import ScenarioCode
from app.modules.entitlement.ledger import (
    ERROR_INSUFFICIENT_CREDITS,
    ERROR_RESERVATION_CONFLICT,
    RESERVATION_ABANDONED,
    SETTLED_STATUS,
    SETTLEMENT_ENTRY,
    CreditEntryType,
    ReservationStatus,
    SettlementAction,
    SourceOutcome,
    resolve_settlement,
    settlement_outcome,
    signed_credits,
)
from app.modules.entitlement.models import (
    RESERVATION_RESOURCE_TYPE,
    SOURCE_CONTENT_PROJECT,
    SOURCE_MANUAL_GRANT,
    CreditLedgerEntry,
    UsageReservation,
)
from app.modules.entitlement.points import point_table
from app.modules.entitlement.policy import EntitlementAction, permits_action
from app.modules.entitlement.repository import EntitlementRepository
from app.modules.operations.models import AuditLog
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import (
    IdempotencyService,
    OperationsService,
    request_fingerprint,
)

GRANT_OPERATION = "entitlement.grant.create"


@dataclass(frozen=True, slots=True)
class BalanceView:
    """What a tenant has, derived at read time from the entries and nothing else."""

    business_id: UUID
    balance_credits: int
    reserved_credits: int
    points_table_version: int


class EntitlementService:
    """The ledger's application layer: reserve, settle, grant, read."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = EntitlementRepository(session)
        self._businesses = BusinessRepository(session)

    # --- consumption, inside the caller's transaction ------------------------------------------

    async def reserve(
        self,
        *,
        business_id: UUID,
        user_id: UUID,
        scenario_code: ScenarioCode,
        profile: RenderProfile,
        source_type: str,
        source_id: UUID,
        idempotency_key: str,
        correlation_id: str,
    ) -> UsageReservation:
        """Hold the credits one unit of work costs. Raises `402` when there are not enough.

        **Runs in the caller's transaction.** The tenant lock is taken before the balance is read
        and held until that transaction ends, so the sequence "read the balance, decide, write the
        entry" is atomic with respect to every other reservation for the same tenant. Two requests
        aiming at the same last credit therefore serialise, and the second one sees the first
        one's `consume` entry rather than the balance that existed before it.
        """

        await self._repository.lock_tenant(business_id)
        replay = await self._repository.reservation_by_key(business_id, idempotency_key)
        if replay is not None:
            # The same unit of work asking again. Returning the existing hold is what makes the
            # creating request safe to retry: a second reservation would be a second charge.
            return replay

        table = point_table(self._settings.entitlement_points_version)
        kind = table.kind_for(scenario_code, profile)
        credits = table.points[kind]
        balance = await self._repository.balance(business_id)
        if balance < credits:
            raise ProblemException(
                status=402,
                code=ERROR_INSUFFICIENT_CREDITS,
                title="Not enough credits",
                detail="This business does not have enough credits for this generation.",
                meta={
                    "required_credits": credits,
                    "available_credits": balance,
                    "points_table_version": table.version,
                    "point_kind": kind.value,
                },
            )

        now = datetime.now(UTC)
        reservation = UsageReservation(
            id=uuid4(),
            business_id=business_id,
            status=ReservationStatus.RESERVED,
            credits=credits,
            points_table_version=table.version,
            point_kind=kind.value,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            requested_by_user_id=user_id,
            correlation_id=correlation_id,
            created_at=now,
            updated_at=now,
        )
        self._repository.add(reservation)
        await self._session.flush()
        # The charge happens now, not when the work finishes. An open reservation that did not
        # reduce the balance would let a tenant start as many generations as it liked and only
        # discover the overdraft when they all settled.
        self._repository.add(
            CreditLedgerEntry(
                id=uuid4(),
                business_id=business_id,
                entry_type=CreditEntryType.CONSUME,
                delta_credits=signed_credits(CreditEntryType.CONSUME, credits),
                points_table_version=table.version,
                source_type=source_type,
                source_id=source_id,
                reservation_id=reservation.id,
                idempotency_key=f"reserve:{reservation.id}",
                created_by_user_id=user_id,
                correlation_id=correlation_id,
                created_at=now,
            )
        )
        self._audit(
            business_id=business_id,
            user_id=user_id,
            action="entitlement.reservation.opened",
            resource_id=reservation.id,
            correlation_id=correlation_id,
            details={
                "credits": credits,
                "point_kind": kind.value,
                "points_table_version": table.version,
                "source_type": source_type,
            },
        )
        return reservation

    async def settle(
        self,
        *,
        business_id: UUID,
        source_type: str,
        source_id: UUID,
        outcome: SourceOutcome,
        failure_code: str | None,
        correlation_id: str,
    ) -> UsageReservation | None:
        """Close the reservation the finished work opened. Runs in the caller's transaction.

        Returns `None` when there is nothing to settle: work that never reserved (anything that
        predates this slice) or work that has not finished. Both are ordinary, so neither raises.
        """

        decision = settlement_outcome(outcome, failure_code)
        if decision is None:
            return None
        await self._repository.lock_tenant(business_id)
        reservation = await self._repository.reservation_for_source(
            business_id, source_type, source_id, lock=True
        )
        if reservation is None:
            return None
        action = resolve_settlement(reservation.status, decision)
        if action is SettlementAction.ALREADY_APPLIED:
            # A retried settlement. Writing nothing is the point: in an append-only ledger a
            # second refund entry is money the tenant keeps.
            return reservation
        if action is SettlementAction.CONFLICT:
            raise ProblemException(
                status=409,
                code=ERROR_RESERVATION_CONFLICT,
                title="Reservation already settled",
                detail="This reservation was already settled the other way.",
                meta={"status": reservation.status.value, "requested": decision.value},
            )

        now = datetime.now(UTC)
        reservation.status = SETTLED_STATUS[decision]
        reservation.settled_at = now
        reservation.updated_at = now
        if reservation.status is ReservationStatus.RELEASED:
            reservation.failure_code = (failure_code or RESERVATION_ABANDONED)[:96]
        entry_type = SETTLEMENT_ENTRY[decision]
        if entry_type is not None:
            self._repository.add(
                CreditLedgerEntry(
                    id=uuid4(),
                    business_id=business_id,
                    entry_type=entry_type,
                    delta_credits=signed_credits(entry_type, reservation.credits),
                    points_table_version=reservation.points_table_version,
                    source_type=source_type,
                    source_id=source_id,
                    reservation_id=reservation.id,
                    # Derived from the reservation, so the unique index refuses a second refund
                    # even if every check above were bypassed.
                    idempotency_key=f"refund:{reservation.id}",
                    created_by_user_id=None,
                    correlation_id=correlation_id,
                    reason=reservation.failure_code,
                    created_at=now,
                )
            )
        # The actor is the person the hold was opened for, not the worker that closed it:
        # `audit_logs` names a human, and "whose credit moved" is the question this row answers.
        self._audit(
            business_id=business_id,
            user_id=reservation.requested_by_user_id,
            action=f"entitlement.reservation.{reservation.status.value}",
            resource_id=reservation.id,
            correlation_id=correlation_id,
            details={"credits": reservation.credits, "failure_code": reservation.failure_code},
        )
        return reservation

    # --- grants --------------------------------------------------------------------------------

    async def grant(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        credits: int,
        reason: str | None,
        idempotency_key: str | None,
        correlation_id: str,
    ) -> CreditLedgerEntry:
        """Put credits into a tenant's ledger by hand. Owner only (PRD §32.1's admin grant).

        This is the *only* source of credit in this slice, and it is on purpose: store
        verification, renewal and plan mapping are Phase 3. Building consumption first and the
        source afterwards is reversible; the other order is not, because usage that went
        uncounted while payments were being taken is a debt nobody can reconstruct.
        """

        if credits < 1 or credits > self._settings.entitlement_max_grant_credits:
            raise ProblemException(
                status=422,
                code="ENTITLEMENT_GRANT_INVALID",
                title="Grant amount is not allowed",
                detail="A grant is a whole number of credits within the configured ceiling.",
                meta={"max_credits": self._settings.entitlement_max_grant_credits},
            )
        async with self._session.begin():
            await self._authorize(user_id, business_id, EntitlementAction.GRANT_CREATE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                key=idempotency_key,
                payload={"credits": credits, "reason": reason},
                correlation_id=correlation_id,
            )
            if replay is not None and replay.entry_id is not None:
                existing = await self._session.get(CreditLedgerEntry, replay.entry_id)
                if existing is not None and existing.business_id == business_id:
                    return existing
            await self._repository.lock_tenant(business_id)
            entry = CreditLedgerEntry(
                id=uuid4(),
                business_id=business_id,
                entry_type=CreditEntryType.GRANT,
                delta_credits=signed_credits(CreditEntryType.GRANT, credits),
                points_table_version=None,
                source_type=SOURCE_MANUAL_GRANT,
                source_id=None,
                reservation_id=None,
                # Namespaced so a caller-chosen key can never collide with the keys a reservation
                # derives from its own id.
                idempotency_key=None if idempotency_key is None else f"grant:{idempotency_key}",
                created_by_user_id=user_id,
                correlation_id=correlation_id,
                reason=None if reason is None else reason[:96],
                created_at=datetime.now(UTC),
            )
            self._repository.add(entry)
            await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="entitlement.grant.created",
                resource_id=entry.id,
                correlation_id=correlation_id,
                details={"credits": credits},
            )
            await self._complete_idempotent(replay, body={"entry_id": str(entry.id)})
            return entry

    # --- reads ---------------------------------------------------------------------------------

    async def read_balance(self, *, user_id: UUID, business_id: UUID) -> BalanceView:
        await self._authorize(user_id, business_id, EntitlementAction.BALANCE_READ)
        return BalanceView(
            business_id=business_id,
            balance_credits=await self._repository.balance(business_id),
            reserved_credits=await self._repository.reserved_credits(business_id),
            points_table_version=self._settings.entitlement_points_version,
        )

    async def list_ledger(
        self, *, user_id: UUID, business_id: UUID, cursor: Cursor | None, limit: int | None
    ) -> Page[CreditLedgerEntry]:
        await self._authorize(user_id, business_id, EntitlementAction.LEDGER_READ)
        page_size = resolve_limit(limit)
        rows = await self._repository.list_entries(business_id, cursor=cursor, limit=page_size)
        return build_page(rows, limit=page_size, key=lambda row: (row.created_at, row.id))

    async def list_reservations(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        cursor: Cursor | None,
        limit: int | None,
        status: ReservationStatus | None,
    ) -> Page[UsageReservation]:
        await self._authorize(user_id, business_id, EntitlementAction.LEDGER_READ)
        page_size = resolve_limit(limit)
        rows = await self._repository.list_reservations(
            business_id, cursor=cursor, limit=page_size, status=status
        )
        return build_page(rows, limit=page_size, key=lambda row: (row.created_at, row.id))

    # --- plumbing ------------------------------------------------------------------------------

    async def _authorize(self, user_id: UUID, business_id: UUID, action: EntitlementAction) -> None:
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
        key: str | None,
        payload: dict[str, object],
        correlation_id: str,
    ) -> _IdempotentGrant | None:
        if key is None:
            return None
        result = await IdempotencyService(OperationsRepository(self._session)).acquire(
            business_id=business_id,
            actor_user_id=user_id,
            operation=GRANT_OPERATION,
            key=key,
            fingerprint=request_fingerprint(payload),
            correlation_id=correlation_id,
        )
        body = result.record.response_body or {}
        entry_id = body.get("entry_id") if result.is_replay else None
        return _IdempotentGrant(
            record=result.record,
            entry_id=UUID(entry_id) if isinstance(entry_id, str) else None,
        )

    async def _complete_idempotent(
        self, request: _IdempotentGrant | None, *, body: dict[str, object]
    ) -> None:
        if request is None:
            return
        await OperationsService(self._session, self._settings).complete_idempotency(
            request.record, response_status=201, response_body=body
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
        audit_reservation(
            self._session,
            business_id=business_id,
            user_id=user_id,
            action=action,
            resource_id=resource_id,
            correlation_id=correlation_id,
            details=details,
        )


@dataclass(frozen=True, slots=True)
class _IdempotentGrant:
    record: Any
    entry_id: UUID | None


def audit_reservation(
    session: AsyncSession,
    *,
    business_id: UUID,
    user_id: UUID,
    action: str,
    resource_id: UUID,
    correlation_id: str,
    details: dict[str, object],
) -> None:
    """One audit row for one thing that happened to a hold, in the caller's transaction.

    A free function rather than a method because two writers need it — the service and the sweep
    — and the sweep has no business reaching inside the service to borrow one.
    """

    OperationsRepository(session).add(
        AuditLog(
            id=uuid4(),
            business_id=business_id,
            actor_user_id=user_id,
            action=action,
            resource_type=RESERVATION_RESOURCE_TYPE,
            resource_id=resource_id,
            correlation_id=correlation_id,
            details=details,
        )
    )


class ReservationSourceProbe(Protocol):
    """Answers, for a batch of source ids, which of them can no longer settle their own hold.

    Declared here and implemented by the module that owns the work, so entitlement depends on no
    other module's tables. The direction matters: content already depends on entitlement, and a
    query from entitlement back into `content_projects` would close that loop.
    """

    async def closed_sources(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]:
        """The subset that reached a terminal state, or that no longer exists at all."""

        ...  # pragma: no cover - protocol


class AbandonedReservationSweeper:
    """Release holds whose work can no longer settle them.

    In a healthy system this finds nothing, and that is a property rather than an accident: a
    reservation is settled inside the same transaction that makes the work terminal, so there is
    no window in which a finished job has an open hold. What this sweep covers is the case that
    window would have created if it existed — a source row that went away, or a settlement that a
    future caller forgets — and it covers it without ever guessing: a hold is released only when
    the module that owns the work says the work is over.

    It follows slice 2E's `AbandonedRunSweeper`: an age cutoff, a bounded batch, `SKIP LOCKED`,
    and `None` when there was nothing to do so the drain stops.
    """

    def __init__(
        self, session: AsyncSession, settings: Settings, probe: ReservationSourceProbe
    ) -> None:
        self._session = session
        self._settings = settings
        self._settings_batch = settings.entitlement_sweep_batch_size
        self._repository = EntitlementRepository(session)
        self._probe = probe

    async def process_next(self) -> dict[str, int] | None:
        cutoff = datetime.now(UTC) - timedelta(
            seconds=self._settings.entitlement_reservation_sweep_age_seconds
        )
        async with self._session.begin():
            candidates = await self._repository.claim_open_reservations(
                source_type=SOURCE_CONTENT_PROJECT, older_than=cutoff, limit=self._settings_batch
            )
            if not candidates:
                return None
            closed = await self._probe.closed_sources(
                tuple(reservation.source_id for reservation in candidates)
            )
            released = 0
            now = datetime.now(UTC)
            for reservation in candidates:
                if reservation.source_id not in closed:
                    continue
                reservation.status = ReservationStatus.RELEASED
                reservation.settled_at = now
                reservation.updated_at = now
                reservation.failure_code = RESERVATION_ABANDONED
                audit_reservation(
                    self._session,
                    business_id=reservation.business_id,
                    user_id=reservation.requested_by_user_id,
                    action="entitlement.reservation.released",
                    resource_id=reservation.id,
                    correlation_id=reservation.correlation_id,
                    details={
                        "credits": reservation.credits,
                        "failure_code": RESERVATION_ABANDONED,
                    },
                )
                self._repository.add(
                    CreditLedgerEntry(
                        id=uuid4(),
                        business_id=reservation.business_id,
                        entry_type=CreditEntryType.REFUND,
                        delta_credits=signed_credits(CreditEntryType.REFUND, reservation.credits),
                        points_table_version=reservation.points_table_version,
                        source_type=reservation.source_type,
                        source_id=reservation.source_id,
                        reservation_id=reservation.id,
                        idempotency_key=f"refund:{reservation.id}",
                        created_by_user_id=None,
                        correlation_id=reservation.correlation_id,
                        reason=RESERVATION_ABANDONED,
                        created_at=now,
                    )
                )
                released += 1
            if released == 0:
                return None
            # The batch is a cap, and a full batch means there may be more behind it. Reported
            # rather than implied: a sweep that silently truncates reads like a clean one.
            return {
                "examined": len(candidates),
                "released": released,
                "batch_full": int(len(candidates) >= self._settings_batch),
            }


__all__ = [
    "GRANT_OPERATION",
    "SOURCE_CONTENT_PROJECT",
    "AbandonedReservationSweeper",
    "BalanceView",
    "EntitlementService",
    "ReservationSourceProbe",
    "audit_reservation",
]
