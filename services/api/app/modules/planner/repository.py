"""Tenant-scoped persistence for the planner, plus the two claims its workers run on.

Every read and write takes `business_id`, with exactly two documented exceptions — the dispatch
claim and the scheduling claim. Both run in a worker with no user and no business behind them, so
scoping them to a tenant would mean they could only ever advance a tenant somebody named. Every
row they touch is re-read under its own tenant scope before anything is written about it.

`lock_business` is why two planning runs cannot write the same window twice. Reading "which
obligations already exist for this item" and then inserting the missing ones is two statements,
and between them another transaction can read the same answer; PostgreSQL's default isolation
sees no conflict because neither modifies a row the other read. A transaction-scoped advisory
lock serialises exactly that sequence. The unique index on `(subscription_item_id, period_start)`
is the second lock on the same door — the one that holds even if a future caller forgets this one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor, apply_cursor, fetch_size
from app.modules.content.lifecycle import ProjectState
from app.modules.content.models import ContentProject
from app.modules.planner.models import (
    ContentObligation,
    PlannerSettings,
    PlannerSubscriptionItem,
)
from app.modules.planner.obligation import (
    ContentCategory,
    ObligationStatus,
    PlanItemStatus,
)

ADVISORY_LOCK_NAMESPACE = 2_0022
"""The first key of `pg_advisory_xact_lock(int, int)`; the second is the hashed tenant id.

Deliberately *not* entitlement's namespace. A conversion takes this lock and then, inside
`create_project`, entitlement takes its own — always in that order, never the reverse — so the two
subsystems cannot form a cycle. Sharing one namespace would instead make every planning run
serialise against every unrelated credit grant for the same tenant.
"""

SETTINGS_RESOURCE_TYPE = "planner_settings"
ITEM_RESOURCE_TYPE = "planner_subscription_item"
OBLIGATION_RESOURCE_TYPE = "content_obligation"

_LIVE_OBLIGATION_STATUSES = (ObligationStatus.PLANNED, ObligationStatus.BLOCKED)


class PlannerRepository:
    """Tenant-scoped reads and writes over the three planner tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, instance: object) -> None:
        self._session.add(instance)

    async def lock_business(self, business_id: UUID) -> None:
        """Serialise this tenant's planning for the rest of the transaction.

        Must be taken *before* the existing obligations are read by any path that intends to
        insert one. Taken afterwards it locks nothing that matters.
        """

        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
            {"namespace": ADVISORY_LOCK_NAMESPACE, "key": str(business_id)},
        )

    # --- settings -------------------------------------------------------------------------------

    async def get_settings(
        self, business_id: UUID, *, lock: bool = False
    ) -> PlannerSettings | None:
        statement = select(PlannerSettings).where(PlannerSettings.business_id == business_id)
        if lock:
            statement = statement.with_for_update()
        return cast(PlannerSettings | None, await self._session.scalar(statement))

    # --- standing demand ------------------------------------------------------------------------

    async def get_item(
        self, business_id: UUID, item_id: UUID, *, lock: bool = False
    ) -> PlannerSubscriptionItem | None:
        statement = select(PlannerSubscriptionItem).where(
            PlannerSubscriptionItem.business_id == business_id,
            PlannerSubscriptionItem.id == item_id,
        )
        if lock:
            statement = statement.with_for_update()
        return cast(PlannerSubscriptionItem | None, await self._session.scalar(statement))

    async def list_items(
        self,
        business_id: UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        status: PlanItemStatus | None = None,
    ) -> list[PlannerSubscriptionItem]:
        statement: Select[tuple[PlannerSubscriptionItem]] = select(PlannerSubscriptionItem).where(
            PlannerSubscriptionItem.business_id == business_id
        )
        if status is not None:
            statement = statement.where(PlannerSubscriptionItem.status == status)
        paged = apply_cursor(
            statement,
            created_at=PlannerSubscriptionItem.created_at,
            identifier=PlannerSubscriptionItem.id,
            cursor=cursor,
        ).limit(fetch_size(limit))
        return list((await self._session.scalars(paged)).all())

    async def items_by_id(
        self, business_id: UUID, item_ids: Sequence[UUID]
    ) -> dict[UUID, PlannerSubscriptionItem]:
        """Tenant-scoped lookup for a batch of standing demands; unknown ids are simply absent."""

        if not item_ids:
            return {}
        statement: Select[tuple[PlannerSubscriptionItem]] = select(PlannerSubscriptionItem).where(
            PlannerSubscriptionItem.business_id == business_id,
            PlannerSubscriptionItem.id.in_(tuple(item_ids)),
        )
        return {row.id: row for row in (await self._session.scalars(statement)).all()}

    async def count_items(self, business_id: UUID) -> int:
        statement = select(func.count()).where(PlannerSubscriptionItem.business_id == business_id)
        return int(await self._session.scalar(statement) or 0)

    # --- obligations ----------------------------------------------------------------------------

    async def get_obligation(
        self, business_id: UUID, obligation_id: UUID, *, lock: bool = False
    ) -> ContentObligation | None:
        statement = select(ContentObligation).where(
            ContentObligation.business_id == business_id, ContentObligation.id == obligation_id
        )
        if lock:
            statement = statement.with_for_update()
        return cast(ContentObligation | None, await self._session.scalar(statement))

    async def list_obligations(
        self,
        business_id: UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        status: ObligationStatus | None = None,
    ) -> list[ContentObligation]:
        statement: Select[tuple[ContentObligation]] = select(ContentObligation).where(
            ContentObligation.business_id == business_id
        )
        if status is not None:
            statement = statement.where(ContentObligation.status == status)
        paged = apply_cursor(
            statement,
            created_at=ContentObligation.created_at,
            identifier=ContentObligation.id,
            cursor=cursor,
        ).limit(fetch_size(limit))
        return list((await self._session.scalars(paged)).all())

    async def planned_period_starts(
        self, business_id: UUID, item_id: UUID, *, not_before: datetime
    ) -> frozenset[datetime]:
        """The windows this standing demand already has an obligation for.

        Every status counts, including the terminal ones. A window whose obligation was
        cancelled must not be re-planned on the next tick — that would resurrect exactly what
        somebody withdrew, and the unique index would refuse it anyway.
        """

        statement = select(ContentObligation.period_start).where(
            ContentObligation.business_id == business_id,
            ContentObligation.subscription_item_id == item_id,
            ContentObligation.period_start >= not_before,
        )
        return frozenset((await self._session.scalars(statement)).all())

    async def due_obligations(
        self, business_id: UUID, *, now: datetime, limit: int
    ) -> list[ContentObligation]:
        """One tenant's convertible obligations, for ranking. Read-only, no lease taken.

        Ordering is by planned publication so the set is stable; §13.2's order is applied to it
        afterwards, by `rank_obligations`, which is pure and does not know about SQL.
        """

        statement: Select[tuple[ContentObligation]] = (
            select(ContentObligation)
            .where(
                ContentObligation.business_id == business_id,
                ContentObligation.status.in_(_LIVE_OBLIGATION_STATUSES),
                ContentObligation.next_attempt_at.is_not(None),
                ContentObligation.next_attempt_at <= now,
                # §13.1's window is a commitment to publish *inside* it. Once it has closed there
                # is nothing left to be early for, and the expiry sweep is what closes the row.
                ContentObligation.period_end > now,
            )
            .order_by(ContentObligation.planned_publish_at, ContentObligation.id)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def category_counts(
        self, business_id: UUID, *, since: datetime
    ) -> dict[ContentCategory, int]:
        """How many obligations this tenant has in each §13.3 category since `since`.

        Counted over obligations rather than over delivered projects on purpose: the mix is a
        statement about what the business *publishes*, and an obligation that is waiting to be
        converted is already part of that plan. Counting only finished work would make the
        deviation lag a week behind the schedule it is supposed to steer.
        """

        statement = (
            select(ContentObligation.category, func.count())
            .where(
                ContentObligation.business_id == business_id,
                ContentObligation.period_start >= since,
                ContentObligation.status != ObligationStatus.CANCELLED,
            )
            .group_by(ContentObligation.category)
        )
        return {
            category: int(count)
            for category, count in (await self._session.execute(statement)).all()
        }

    async def recent_product_uses(self, business_id: UUID, *, since: datetime) -> dict[UUID, int]:
        """How often each product appeared in this tenant's recent projects (§13.2/6).

        Read from `content_projects` rather than from obligations because repetition is about
        what the audience saw, and a manually created project is just as visible as a planned
        one. Cancelled projects are excluded: nothing was ever published from them.
        """

        statement = (
            select(ContentProject.product_id, func.count())
            .where(
                ContentProject.business_id == business_id,
                ContentProject.created_at >= since,
                ContentProject.product_id.is_not(None),
                ContentProject.state != ProjectState.CANCELLED,
            )
            .group_by(ContentProject.product_id)
        )
        return {
            product_id: int(count)
            for product_id, count in (await self._session.execute(statement)).all()
            if product_id is not None
        }

    async def obligation_for_project(
        self, business_id: UUID, project_id: UUID, *, lock: bool = False
    ) -> ContentObligation | None:
        statement = select(ContentObligation).where(
            ContentObligation.business_id == business_id,
            ContentObligation.project_id == project_id,
        )
        if lock:
            statement = statement.with_for_update()
        return cast(ContentObligation | None, await self._session.scalar(statement))

    # --- the claims (not tenant-scoped; see the module docstring) -------------------------------

    async def claim_next_plannable_item(self, *, now: datetime) -> PlannerSubscriptionItem | None:
        """Take one standing demand whose next planning pass has come due, with `SKIP LOCKED`.

        The predicate matches `ix_planner_items_due` exactly, so a tenant that paused everything
        contributes nothing to the scan. The item *is* the durable job here — there is no paired
        `jobs` row, for the reason `content_projects` has none: a planner's state is its result.
        """

        statement = (
            select(PlannerSubscriptionItem)
            .where(
                PlannerSubscriptionItem.status == PlanItemStatus.ACTIVE,
                PlannerSubscriptionItem.next_plan_at.is_not(None),
                PlannerSubscriptionItem.next_plan_at <= now,
            )
            .order_by(PlannerSubscriptionItem.next_plan_at, PlannerSubscriptionItem.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return cast(PlannerSubscriptionItem | None, await self._session.scalar(statement))

    async def next_dispatch_business(self, *, now: datetime) -> UUID | None:
        """Whose turn it is to have an obligation converted, and nothing more.

        The tenant is chosen by earliest due time; *which* of that tenant's obligations goes
        first is §13.2's question and is answered by `rank_obligations` afterwards, over the whole
        candidate set. Claiming the earliest-due row directly would be a second, silent priority
        order competing with the one the PRD specifies.
        """

        statement = (
            select(ContentObligation.business_id)
            .where(
                ContentObligation.status.in_(_LIVE_OBLIGATION_STATUSES),
                ContentObligation.next_attempt_at.is_not(None),
                ContentObligation.next_attempt_at <= now,
                ContentObligation.period_end > now,
            )
            .order_by(ContentObligation.next_attempt_at, ContentObligation.id)
            .limit(1)
        )
        return cast(UUID | None, await self._session.scalar(statement))

    async def claim_expired_obligations(
        self, *, now: datetime, limit: int
    ) -> list[ContentObligation]:
        """Obligations whose window closed while they were still waiting to become work."""

        statement: Select[tuple[ContentObligation]] = (
            select(ContentObligation)
            .where(
                ContentObligation.status.in_(_LIVE_OBLIGATION_STATUSES),
                ContentObligation.period_end <= now,
            )
            .order_by(ContentObligation.period_end, ContentObligation.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def claim_schedulable_projects(self, *, limit: int) -> list[ContentProject]:
        """Approved projects with no publication slot yet — the scheduling drain's work list.

        A project reaches `approved` from `preview_ready` whether or not a planner put it there,
        so this deliberately does not join on `content_obligations`: a manually created project
        that somebody approved still has to be given a time, or `approved` would be a state
        nothing ever leaves.
        """

        statement: Select[tuple[ContentProject]] = (
            select(ContentProject)
            .where(
                ContentProject.state == ProjectState.APPROVED,
                ContentProject.scheduled_publish_at.is_(None),
            )
            .order_by(ContentProject.state_entered_at, ContentProject.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def claim_settled_obligations(self, *, limit: int) -> list[ContentObligation]:
        """`in_progress` obligations whose project has already ended one way or another.

        The reconciliation half of the scheduling drain: an obligation must not sit pointing at
        a project that failed or was withdrawn, because the planner would then believe that
        window is being served.
        """

        statement: Select[tuple[ContentObligation]] = (
            select(ContentObligation)
            .join(ContentProject, ContentProject.id == ContentObligation.project_id)
            .where(
                ContentObligation.status == ObligationStatus.IN_PROGRESS,
                ContentProject.state.in_((ProjectState.FAILED, ProjectState.CANCELLED)),
            )
            .order_by(ContentObligation.updated_at, ContentObligation.id)
            .with_for_update(of=ContentObligation, skip_locked=True)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())


__all__ = [
    "ADVISORY_LOCK_NAMESPACE",
    "ITEM_RESOURCE_TYPE",
    "OBLIGATION_RESOURCE_TYPE",
    "SETTINGS_RESOURCE_TYPE",
    "PlannerRepository",
]
