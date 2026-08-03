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

**Since W23 the same lock is also taken by `credit_ledger`'s insert trigger**, so a writer that
never called `lock_tenant` still serialises against one that did. That makes the ordering rule
below binding rather than advisory: *tenant lock first, reservation row lock second, everywhere*.
A path that locks reservation rows first and only reaches the ledger afterwards would take the two
in the opposite order from `reserve` and `settle` and could deadlock against them — which is why
`claim_reservations` takes the tenant lock itself instead of leaving it to the trigger, and why
the sweep now reads its candidates without locking them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor, apply_cursor, fetch_size
from app.modules.entitlement.ledger import ReservationStatus
from app.modules.entitlement.models import CreditLedgerEntry, UsageReservation

ADVISORY_LOCK_NAMESPACE = 2_0020
"""The first key of `pg_advisory_xact_lock(int, int)`; the second is the hashed tenant id.

A constant of its own so entitlement's locks cannot collide with a future subsystem that also
reaches for advisory locks. Two tenants whose ids hash to the same 32-bit value serialise against
each other unnecessarily — harmless, and far cheaper than the alternative of not locking.

Migration 0020 repeats this number as a literal inside `credit_ledger_guard_insert()`, because a
trigger body cannot import Python. The two must stay equal — a trigger locking a *different* key
would serialise raw writers against each other and against nobody else — so an integration test
reads the installed function definition back and compares.
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
        """The reservation a settlement is about, found by what the work is.

        `uq_usage_reservations_standing_source` allows at most one hold that still stands, but a
        unit of work whose hold was refunded may open a new one, so a source can carry a released
        hold *and* a standing one. The settlement is about the standing one: open first, then
        consumed, then released, and within a status the most recent. Ordering by age alone would
        hand a restarted project the refunded hold it already finished with, and settle the wrong
        one.
        """

        standing_first = case(
            (UsageReservation.status == ReservationStatus.RESERVED, 0),
            (UsageReservation.status == ReservationStatus.CONSUMED, 1),
            else_=2,
        )
        statement: Select[tuple[UsageReservation]] = (
            select(UsageReservation)
            .where(
                UsageReservation.business_id == business_id,
                UsageReservation.source_type == source_type,
                UsageReservation.source_id == source_id,
            )
            .order_by(standing_first, UsageReservation.created_at.desc(), UsageReservation.id)
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        return cast(UsageReservation | None, await self._session.scalar(statement))

    async def standing_reservation_for_source(
        self, business_id: UUID, source_type: str, source_id: UUID
    ) -> UsageReservation | None:
        """The hold this unit of work already carries, if it still stands.

        Separate from `reservation_for_source` because the questions differ: settlement asks
        "which hold is this outcome about", and `reserve` asks "may a hold be opened at all". A
        released hold answers the first and not the second.
        """

        statement: Select[tuple[UsageReservation]] = (
            select(UsageReservation)
            .where(
                UsageReservation.business_id == business_id,
                UsageReservation.source_type == source_type,
                UsageReservation.source_id == source_id,
                UsageReservation.status != ReservationStatus.RELEASED,
            )
            .order_by(UsageReservation.created_at, UsageReservation.id)
            .limit(1)
        )
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

    async def stale_open_reservations(
        self, *, source_type: str, older_than: datetime, limit: int
    ) -> list[StaleReservation]:
        """Open reservations older than a cutoff, oldest first and **not locked**.

        Cross-tenant on purpose: this is a maintenance sweep, not a tenant operation, and it runs
        in a worker with no user behind it.

        Deliberately unlocked, and deliberately not ORM rows. Locking here would take reservation
        rows before the tenant lock — the opposite of the order `reserve` and `settle` use, and
        since W23 the ledger's insert trigger takes the tenant lock too, so the sweep would end up
        holding row locks while waiting for a lock a settlement holds while waiting for those
        rows. This read only names candidates; `claim_reservations` is what claims them, in the
        right order.
        """

        statement = (
            select(UsageReservation.id, UsageReservation.business_id, UsageReservation.source_id)
            .where(
                UsageReservation.status == ReservationStatus.RESERVED,
                UsageReservation.source_type == source_type,
                UsageReservation.created_at < older_than,
            )
            .order_by(UsageReservation.created_at, UsageReservation.id)
            .limit(limit)
        )
        rows = await self._session.execute(statement)
        return [
            StaleReservation(id=row.id, business_id=row.business_id, source_id=row.source_id)
            for row in rows
        ]

    async def claim_reservations(
        self, business_id: UUID, reservation_ids: Sequence[UUID]
    ) -> list[UsageReservation]:
        """Take this tenant's ledger lock, then lock the named holds that are still open.

        The order is the module's rule and the reason this method exists at all. `SKIP LOCKED`
        keeps two sweepers from fighting over the same batch, exactly as the media and project
        drains do, and the status filter is re-applied under the lock because a hold that settled
        between the candidate read and here is no longer the sweep's business.
        """

        if not reservation_ids:
            return []
        await self.lock_tenant(business_id)
        statement: Select[tuple[UsageReservation]] = (
            select(UsageReservation)
            .where(
                UsageReservation.business_id == business_id,
                UsageReservation.id.in_(reservation_ids),
                UsageReservation.status == ReservationStatus.RESERVED,
            )
            .order_by(UsageReservation.created_at, UsageReservation.id)
            .with_for_update(skip_locked=True)
        )
        rows = await self._session.scalars(statement)
        return list(rows)


@dataclass(frozen=True, slots=True)
class StaleReservation:
    """A candidate for the sweep: enough to ask the owning module, and nothing more.

    Not a `UsageReservation`, because loading one would put a row the sweep has not locked into
    the session's identity map, and the locked read that follows would then be answered from that
    stale copy rather than from the database.
    """

    id: UUID
    business_id: UUID
    source_id: UUID


__all__ = ["ADVISORY_LOCK_NAMESPACE", "EntitlementRepository", "StaleReservation"]
