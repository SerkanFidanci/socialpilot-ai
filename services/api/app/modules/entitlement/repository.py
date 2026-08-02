"""Tenant-scoped persistence for the ledger, plus the lock that makes a balance check binding.

Every method takes `business_id` and constrains its statement with it. Another tenant's real
reservation id therefore produces *no row*, so `ENTITLEMENT_RESERVATION_NOT_FOUND` falls out of
the query rather than out of a comparison somebody has to remember to write.

**`lock_tenant` is the reason a race cannot spend the same credit twice.** Reading a balance and
then writing an entry is two statements, and between them another transaction can read the same
balance. PostgreSQL's default isolation will not stop that: neither transaction modifies a row
the other read, so there is no conflict to detect. A transaction-scoped advisory lock keyed on
the tenant serialises exactly the sequence that has to be atomic, and releases at commit or
rollback with no cleanup path to forget.

The lock is taken on the tenant rather than on the `businesses` row for one reason: a row lock
would also block every unrelated write to that business — a rename, a membership change — for as
long as a reservation transaction runs. The advisory namespace makes the contention specific to
entitlement, which is the only place this ordering matters.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor, apply_cursor, fetch_size
from app.modules.entitlement.ledger import ReservationStatus
from app.modules.entitlement.models import CreditLedgerEntry, UsageReservation

ADVISORY_LOCK_NAMESPACE = 2_0020
"""The first key of `pg_advisory_xact_lock(int, int)`; the second is the hashed tenant id.

A constant of its own so entitlement's locks cannot collide with a future subsystem that also
reaches for advisory locks. Two tenants whose ids hash to the same 32-bit value serialise against
each other unnecessarily — harmless, and far cheaper than the alternative of not locking.
"""


class EntitlementRepository:
    """Tenant-scoped reads and writes over `credit_ledger` and `usage_reservations`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, instance: object) -> None:
        self._session.add(instance)

    async def lock_tenant(self, business_id: UUID) -> None:
        """Serialise this tenant's balance arithmetic for the rest of the transaction.

        Must be called *before* the balance is read by any code path that intends to write an
        entry based on it. Called after, it locks nothing that matters.
        """

        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
            {"namespace": ADVISORY_LOCK_NAMESPACE, "key": str(business_id)},
        )

    async def balance(self, business_id: UUID) -> int:
        """The tenant's spendable credit: the sum of its entries, and nothing else.

        Open reservations are already subtracted, because a reservation writes its `consume`
        entry when it opens. There is no second term here and no stored total to reconcile with.
        """

        statement = select(func.coalesce(func.sum(CreditLedgerEntry.delta_credits), 0)).where(
            CreditLedgerEntry.business_id == business_id
        )
        return int(await self._session.scalar(statement) or 0)

    async def reserved_credits(self, business_id: UUID) -> int:
        """How much of the spend so far is held by work that has not finished.

        Informational only: it is *not* subtracted from `balance`, which already excludes it.
        Reporting it separately is what lets a caller explain a refusal ("you have 3 left and 5
        are held by a running project") without inviting anyone to do the arithmetic twice.
        """

        statement = select(func.coalesce(func.sum(UsageReservation.credits), 0)).where(
            UsageReservation.business_id == business_id,
            UsageReservation.status == ReservationStatus.RESERVED,
        )
        return int(await self._session.scalar(statement) or 0)

    async def get_reservation(
        self, business_id: UUID, reservation_id: UUID, *, lock: bool = False
    ) -> UsageReservation | None:
        statement: Select[tuple[UsageReservation]] = select(UsageReservation).where(
            UsageReservation.business_id == business_id, UsageReservation.id == reservation_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(UsageReservation | None, await self._session.scalar(statement))

    async def reservation_by_key(
        self, business_id: UUID, idempotency_key: str, *, lock: bool = False
    ) -> UsageReservation | None:
        """The reservation a replayed request already opened, if there is one."""

        statement: Select[tuple[UsageReservation]] = select(UsageReservation).where(
            UsageReservation.business_id == business_id,
            UsageReservation.idempotency_key == idempotency_key,
        )
        if lock:
            statement = statement.with_for_update()
        return cast(UsageReservation | None, await self._session.scalar(statement))

    async def reservation_for_source(
        self, business_id: UUID, source_type: str, source_id: UUID, *, lock: bool = False
    ) -> UsageReservation | None:
        """The reservation opened for one unit of work, found by what the work is."""

        statement: Select[tuple[UsageReservation]] = (
            select(UsageReservation)
            .where(
                UsageReservation.business_id == business_id,
                UsageReservation.source_type == source_type,
                UsageReservation.source_id == source_id,
            )
            .order_by(UsageReservation.created_at, UsageReservation.id)
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        return cast(UsageReservation | None, await self._session.scalar(statement))

    async def list_entries(
        self, business_id: UUID, *, cursor: Cursor | None, limit: int
    ) -> list[CreditLedgerEntry]:
        statement: Select[tuple[CreditLedgerEntry]] = select(CreditLedgerEntry).where(
            CreditLedgerEntry.business_id == business_id
        )
        statement = apply_cursor(
            statement,
            created_at=CreditLedgerEntry.created_at,
            identifier=CreditLedgerEntry.id,
            cursor=cursor,
        ).limit(fetch_size(limit))
        rows = await self._session.scalars(statement)
        return list(rows)

    async def list_reservations(
        self,
        business_id: UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        status: ReservationStatus | None,
    ) -> list[UsageReservation]:
        statement: Select[tuple[UsageReservation]] = select(UsageReservation).where(
            UsageReservation.business_id == business_id
        )
        if status is not None:
            statement = statement.where(UsageReservation.status == status)
        statement = apply_cursor(
            statement,
            created_at=UsageReservation.created_at,
            identifier=UsageReservation.id,
            cursor=cursor,
        ).limit(fetch_size(limit))
        rows = await self._session.scalars(statement)
        return list(rows)

    async def claim_open_reservations(
        self, *, source_type: str, older_than: datetime, limit: int
    ) -> list[UsageReservation]:
        """Open reservations older than a cutoff, locked for update, newest last.

        Cross-tenant on purpose: this is a maintenance sweep, not a tenant operation, and it runs
        in a worker with no user behind it. `SKIP LOCKED` keeps two sweepers from fighting over
        the same batch, exactly as the media and project drains do.
        """

        statement: Select[tuple[UsageReservation]] = (
            select(UsageReservation)
            .where(
                UsageReservation.status == ReservationStatus.RESERVED,
                UsageReservation.source_type == source_type,
                UsageReservation.created_at < older_than,
            )
            .order_by(UsageReservation.created_at, UsageReservation.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        rows = await self._session.scalars(statement)
        return list(rows)


__all__ = ["ADVISORY_LOCK_NAMESPACE", "EntitlementRepository"]
