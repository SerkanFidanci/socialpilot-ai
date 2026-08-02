"""The planner's four jobs: configure it, materialise demand, convert it, schedule the result.

The split into three worker services is PM decision 2 written into class names. Planning produces
**demand**; conversion produces **content** and is the only step that spends credit; scheduling
gives approved content a slot. Keeping them apart is what makes a planning mistake a row somebody
can cancel rather than a generation somebody paid for — and it is why `ObligationPlanningService`
holds no `EntitlementService` and no `ContentProjectService` at all.

Three properties are load-bearing.

**Planning is idempotent twice over.** The tenant advisory lock serialises the read-then-insert
sequence, and `(subscription_item_id, period_start)` is unique, so the second of two concurrent
runs finds the first one's rows — and if it somehow did not, the database refuses the duplicate.
Neither mechanism depends on the planner remembering to check.

**Conversion reuses everything.** It calls `ContentProjectService.create_project`, which
authorises as the person who set the standing demand up, reserves credit in the transaction that
creates the project, and wakes the sequencer. The planner adds an idempotency key derived from the
obligation — so a crash between "the project was created" and "the obligation was updated" replays
the same project instead of buying a second one — and a refusal handler that makes the obligation
say why. An obligation that could not be converted is `blocked` and **visible**; it is never
silently dropped, which was the specific failure this slice was asked to make impossible.

**Nothing here decides an order.** §13.2's ranking is `obligation.rank_obligations`, which is pure.
This file reads the facts that function needs and does what it says.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.core.pagination import Cursor, Page, build_page, resolve_limit
from app.modules.businesses.models import Business, BusinessStatus
from app.modules.businesses.repository import BusinessRepository
from app.modules.content.lifecycle import ProjectEvent, ProjectState
from app.modules.content.project_service import ContentProjectService, apply_transition
from app.modules.content.render import RenderProfile
from app.modules.content.repository import (
    PROJECT_RESOURCE_TYPE,
    ContentFactsReader,
    ContentRepository,
    ScriptFactsReader,
)
from app.modules.content.script import ScenarioCode
from app.modules.content.validation import AssetFacts
from app.modules.operations.models import AuditLog
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import (
    IdempotencyService,
    OperationsService,
    request_fingerprint,
)
from app.modules.planner.models import (
    ContentObligation,
    PlannerSettings,
    PlannerSubscriptionItem,
)
from app.modules.planner.obligation import (
    ERROR_ITEM_NOT_FOUND,
    ERROR_OBLIGATION_NOT_FOUND,
    REASON_ATTEMPTS_EXHAUSTED,
    REASON_PROJECT_ENDED,
    REASON_WINDOW_CLOSED,
    ContentCategory,
    ContentType,
    MixObservation,
    MixTargets,
    ObligationEvent,
    ObligationStatus,
    ObligationWindow,
    PlanItemStatus,
    PlannerError,
    PlanPeriod,
    QuietHours,
    RankContext,
    RankedObligation,
    build_window,
    measure_mix,
    obligation_can_cancel,
    obligation_is_terminal,
    orientation_of,
    period_days,
    rank_obligations,
    require_obligation_status,
    resolve_timezone,
    shift_out_of_quiet_hours,
    surface_for,
    target_orientation,
)
from app.modules.planner.policy import PlannerAction, permits_action
from app.modules.planner.repository import (
    ITEM_RESOURCE_TYPE,
    OBLIGATION_RESOURCE_TYPE,
    SETTINGS_RESOURCE_TYPE,
    PlannerRepository,
)

ITEM_OPERATION = "planner.item.create"

# The statuses a dispatch may still act on. Written out once so the claim, the settlement guard
# and `ck_content_obligation_due_matches_status` all mean the same set.
_CONVERTIBLE: Final = (ObligationStatus.PLANNED, ObligationStatus.BLOCKED)


# --- the shared read half ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanningProfile:
    """One tenant's planning context: its clock, its quiet window, its targets, its horizon.

    Assembled from the business row and the optional settings row. A business with no settings is
    planned with the deployment defaults rather than not at all — a planner that silently does
    nothing until somebody configures it is indistinguishable from a broken one.
    """

    business_id: UUID
    timezone_name: str
    enabled: bool
    quiet_hours: QuietHours
    mix_targets: MixTargets
    horizon_days: int

    @property
    def zone(self) -> ZoneInfo:
        """The tenant's clock. Resolved on use rather than stored, so a profile stays a value."""

        return resolve_timezone(self.timezone_name)


def build_profile(
    business: Business, settings_row: PlannerSettings | None, settings: Settings
) -> PlanningProfile:
    """Fold a business and its optional planner settings into one planning context."""

    if settings_row is None:
        return PlanningProfile(
            business_id=business.id,
            timezone_name=business.timezone,
            enabled=True,
            quiet_hours=QuietHours(
                start_minute=settings.planner_quiet_hours_start_minute,
                end_minute=settings.planner_quiet_hours_end_minute,
            ),
            mix_targets=MixTargets.default(),
            horizon_days=settings.planner_planning_horizon_days,
        )
    return PlanningProfile(
        business_id=business.id,
        timezone_name=business.timezone,
        enabled=settings_row.enabled,
        quiet_hours=QuietHours(
            start_minute=settings_row.quiet_hours_start_minute,
            end_minute=settings_row.quiet_hours_end_minute,
        ),
        mix_targets=MixTargets.from_document(settings_row.mix_targets),
        horizon_days=settings_row.planning_horizon_days,
    )


class _PlannerBase:
    """Session, settings, repository and the two authorization checks every caller repeats."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = PlannerRepository(session)
        self._businesses = BusinessRepository(session)

    async def _authorize(self, user_id: UUID, business_id: UUID, action: PlannerAction) -> None:
        membership = await self._businesses.get_active_membership(business_id, user_id)
        if membership is None:
            # Another tenant's business id answers exactly like a made-up one.
            raise _not_found("BUSINESS_NOT_FOUND", "Business not found")
        if not permits_action(membership.role, action):
            raise ProblemException(
                status=403,
                code="INSUFFICIENT_PERMISSION",
                title="Forbidden",
                detail="You do not have this permission.",
            )

    async def _require_active_business(self, business_id: UUID) -> Business:
        business = await self._businesses.get_business(business_id)
        if business is None:
            raise _not_found("BUSINESS_NOT_FOUND", "Business not found")
        if business.status != BusinessStatus.ACTIVE:
            raise ProblemException(
                status=409,
                code="BUSINESS_NOT_MUTABLE",
                title="Business is not mutable",
                detail="Suspended or archived businesses cannot be changed.",
            )
        return business

    async def _profile(self, business_id: UUID) -> PlanningProfile:
        business = await self._businesses.get_business(business_id)
        if business is None:
            raise _not_found("BUSINESS_NOT_FOUND", "Business not found")
        return build_profile(
            business, await self._repository.get_settings(business_id), self._settings
        )

    def _audit(
        self,
        *,
        business_id: UUID,
        # Never `None`: `audit_logs.actor_user_id` is NOT NULL, and the rule behind that column is
        # W20's — a background job still names the person whose work moved.
        user_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        correlation_id: str,
        details: dict[str, object],
    ) -> None:
        OperationsRepository(self._session).add(
            AuditLog(
                id=uuid4(),
                business_id=business_id,
                actor_user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                correlation_id=correlation_id,
                details=details,
            )
        )


# --- the API side ---------------------------------------------------------------------------------


class PlannerConfigService(_PlannerBase):
    """Configure the planner and read what it produced. Every method opens its own transaction."""

    async def upsert_settings(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        enabled: bool,
        quiet_hours_start_minute: int,
        quiet_hours_end_minute: int,
        mix_targets: MixTargets,
        planning_horizon_days: int,
        correlation_id: str,
    ) -> PlanningProfile:
        """Write this business's planning configuration. `business.update`, one row per tenant.

        An upsert rather than a create/update pair, and therefore without an `Idempotency-Key`:
        the request states the whole configuration, so replaying it produces the same row. There
        is nothing here a second application could double.

        Returns the *effective* profile rather than the row, for the reason `read_settings` does:
        the answer to "when will you not publish" is a window and a zone, and half of that comes
        from the business rather than from this table.
        """

        # Constructed before the transaction: `QuietHours` refuses a malformed window with a
        # documented code, and doing that inside would leave an open transaction to unwind.
        window = QuietHours(
            start_minute=quiet_hours_start_minute, end_minute=quiet_hours_end_minute
        )
        async with self._session.begin():
            await self._authorize(user_id, business_id, PlannerAction.SETTINGS_WRITE)
            business = await self._require_active_business(business_id)
            now = datetime.now(UTC)
            row = await self._repository.get_settings(business_id, lock=True)
            if row is None:
                row = PlannerSettings(
                    id=uuid4(),
                    business_id=business_id,
                    enabled=enabled,
                    quiet_hours_start_minute=window.start_minute,
                    quiet_hours_end_minute=window.end_minute,
                    mix_targets=mix_targets.as_document(),
                    planning_horizon_days=planning_horizon_days,
                    updated_by_user_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
                self._repository.add(row)
            else:
                row.enabled = enabled
                row.quiet_hours_start_minute = window.start_minute
                row.quiet_hours_end_minute = window.end_minute
                row.mix_targets = mix_targets.as_document()
                row.planning_horizon_days = planning_horizon_days
                row.updated_by_user_id = user_id
                row.updated_at = now
            await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="planner.settings.updated",
                resource_type=SETTINGS_RESOURCE_TYPE,
                resource_id=row.id,
                correlation_id=correlation_id,
                # Codes and numbers only: a quiet window is a setting, not tenant prose.
                details={"enabled": enabled, "horizon_days": planning_horizon_days},
            )
            return build_profile(business, row, self._settings)

    async def read_settings(self, *, user_id: UUID, business_id: UUID) -> PlanningProfile:
        """This business's effective planning context, defaults included.

        Returns the *effective* configuration rather than the row, because "there is no row" is
        not the answer to "when will you not publish" — the deployment default is.
        """

        await self._authorize(user_id, business_id, PlannerAction.SETTINGS_READ)
        return await self._profile(business_id)

    async def create_item(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        content_type: ContentType,
        category: ContentCategory,
        period: PlanPeriod,
        publish_minute: int,
        lead_time_minutes: int,
        preference_rank: int,
        product_id: UUID,
        cta_id: UUID,
        campaign_offer_id: UUID | None,
        source_asset_ids: tuple[UUID, ...],
        idempotency_key: str | None,
        correlation_id: str,
    ) -> PlannerSubscriptionItem:
        """Register a standing demand for content. `business.update`.

        The verified references are checked here, against this tenant, exactly as
        `create_project` checks them — an item naming another business's product would otherwise
        produce an obligation that blocks on every conversion attempt forever.
        """

        async with self._session.begin():
            await self._authorize(user_id, business_id, PlannerAction.ITEM_WRITE)
            await self._require_active_business(business_id)
            replay = await self._begin_idempotent(
                business_id=business_id,
                user_id=user_id,
                key=idempotency_key,
                payload={
                    "content_type": content_type.value,
                    "category": category.value,
                    "period": period.value,
                    "publish_minute": publish_minute,
                    "lead_time_minutes": lead_time_minutes,
                    "product_id": str(product_id),
                    "cta_id": str(cta_id),
                    "campaign_offer_id": None
                    if campaign_offer_id is None
                    else str(campaign_offer_id),
                    "source_asset_ids": sorted(str(value) for value in source_asset_ids),
                },
                correlation_id=correlation_id,
            )
            if replay is not None and replay.item_id is not None:
                existing = await self._repository.get_item(business_id, replay.item_id)
                if existing is not None:
                    return existing
            if await self._repository.count_items(business_id) >= (
                self._settings.planner_max_items_per_business
            ):
                raise ProblemException(
                    status=409,
                    code="PLANNER_TOO_MANY_ITEMS",
                    title="Too many standing demands",
                    detail="This business already holds the maximum number of planner items.",
                    meta={"max_items": self._settings.planner_max_items_per_business},
                )
            await self._require_inputs(
                business_id,
                product_id=product_id,
                cta_id=cta_id,
                campaign_offer_id=campaign_offer_id,
                source_asset_ids=source_asset_ids,
            )
            now = datetime.now(UTC)
            item = PlannerSubscriptionItem(
                id=uuid4(),
                business_id=business_id,
                status=PlanItemStatus.ACTIVE,
                content_type=content_type,
                category=category,
                period=period,
                publish_minute=publish_minute,
                lead_time_minutes=lead_time_minutes,
                preference_rank=preference_rank,
                product_id=product_id,
                cta_id=cta_id,
                campaign_offer_id=campaign_offer_id,
                source_asset_ids=[str(value) for value in source_asset_ids],
                requested_by_user_id=user_id,
                # Due immediately: a tenant that has just described what it wants should see the
                # first obligation on the next planning tick, not after a whole replan interval.
                next_plan_at=now,
                created_at=now,
                updated_at=now,
            )
            self._repository.add(item)
            await self._session.flush()
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="planner.item.created",
                resource_type=ITEM_RESOURCE_TYPE,
                resource_id=item.id,
                correlation_id=correlation_id,
                details={"content_type": content_type.value, "period": period.value},
            )
            await self._complete_idempotent(replay, body={"item_id": str(item.id)})
            return item

    async def set_item_status(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        item_id: UUID,
        status: PlanItemStatus,
        correlation_id: str,
    ) -> PlannerSubscriptionItem:
        """Pause or resume a standing demand. `business.update`.

        Pausing clears `next_plan_at`, which removes the item from the planning claim's partial
        index — the schema states the same rule, so a paused item cannot be picked up even if
        this method forgot. Obligations that were already planned are left alone: they are
        commitments the tenant can withdraw one by one, and silently cancelling a fortnight of
        planned content because somebody paused an item would be a surprise, not a feature.
        """

        async with self._session.begin():
            await self._authorize(user_id, business_id, PlannerAction.ITEM_WRITE)
            await self._require_active_business(business_id)
            item = await self._repository.get_item(business_id, item_id, lock=True)
            if item is None:
                raise _not_found(ERROR_ITEM_NOT_FOUND, "Planner item not found")
            now = datetime.now(UTC)
            item.status = status
            item.next_plan_at = None if status is PlanItemStatus.PAUSED else now
            item.updated_at = now
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action=f"planner.item.{status.value}",
                resource_type=ITEM_RESOURCE_TYPE,
                resource_id=item.id,
                correlation_id=correlation_id,
                details={"status": status.value},
            )
            return item

    async def get_item(
        self, *, user_id: UUID, business_id: UUID, item_id: UUID
    ) -> PlannerSubscriptionItem:
        await self._authorize(user_id, business_id, PlannerAction.ITEM_READ)
        item = await self._repository.get_item(business_id, item_id)
        if item is None:
            raise _not_found(ERROR_ITEM_NOT_FOUND, "Planner item not found")
        return item

    async def list_items(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        cursor: Cursor | None,
        limit: int | None,
        status: PlanItemStatus | None,
    ) -> Page[PlannerSubscriptionItem]:
        await self._authorize(user_id, business_id, PlannerAction.ITEM_READ)
        page_size = resolve_limit(limit)
        rows = await self._repository.list_items(
            business_id, cursor=cursor, limit=page_size, status=status
        )
        return build_page(rows, limit=page_size, key=lambda row: (row.created_at, row.id))

    async def get_obligation(
        self, *, user_id: UUID, business_id: UUID, obligation_id: UUID
    ) -> ContentObligation:
        await self._authorize(user_id, business_id, PlannerAction.OBLIGATION_READ)
        obligation = await self._repository.get_obligation(business_id, obligation_id)
        if obligation is None:
            raise _not_found(ERROR_OBLIGATION_NOT_FOUND, "Obligation not found")
        return obligation

    async def list_obligations(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        cursor: Cursor | None,
        limit: int | None,
        status: ObligationStatus | None,
    ) -> Page[ContentObligation]:
        """§13.1's queue, read back. This is where `blocked` becomes visible to a person."""

        await self._authorize(user_id, business_id, PlannerAction.OBLIGATION_READ)
        page_size = resolve_limit(limit)
        rows = await self._repository.list_obligations(
            business_id, cursor=cursor, limit=page_size, status=status
        )
        return build_page(rows, limit=page_size, key=lambda row: (row.created_at, row.id))

    async def cancel_obligation(
        self, *, user_id: UUID, business_id: UUID, obligation_id: UUID, correlation_id: str
    ) -> ContentObligation:
        """Withdraw an obligation that has not become work. `business.update`.

        An `in_progress` obligation is refused rather than cancelled: the project it became holds
        a credit reservation, and withdrawing the queue entry would leave that project running
        with nothing pointing at it. Cancelling the *project* is the way to stop that, and it is
        `content`'s endpoint, with `content`'s refund.
        """

        async with self._session.begin():
            await self._authorize(user_id, business_id, PlannerAction.OBLIGATION_WRITE)
            await self._require_active_business(business_id)
            obligation = await self._repository.get_obligation(
                business_id, obligation_id, lock=True
            )
            if obligation is None:
                raise _not_found(ERROR_OBLIGATION_NOT_FOUND, "Obligation not found")
            if not obligation_can_cancel(obligation.status) or (
                obligation.status is ObligationStatus.IN_PROGRESS
            ):
                raise ProblemException(
                    status=409,
                    code="PLANNER_OBLIGATION_TRANSITION_NOT_ALLOWED",
                    title="Obligation cannot be cancelled",
                    detail=(
                        "Only an obligation that has not become a content project can be"
                        " cancelled; cancel the project instead."
                    ),
                    meta={"status": obligation.status.value},
                )
            _apply_obligation_event(obligation, ObligationEvent.CANCELLED, reason=None)
            self._audit(
                business_id=business_id,
                user_id=user_id,
                action="planner.obligation.cancelled",
                resource_type=OBLIGATION_RESOURCE_TYPE,
                resource_id=obligation.id,
                correlation_id=correlation_id,
                details={"status": obligation.status.value},
            )
            return obligation

    async def read_plan(self, *, user_id: UUID, business_id: UUID) -> tuple[RankedObligation, ...]:
        """§13.2's order over this tenant's convertible obligations, with every reason attached.

        The same function the dispatcher uses, over the same facts, so "why is this next?" is
        answered by the thing that actually decides rather than by a second explanation written
        to look like it.
        """

        await self._authorize(user_id, business_id, PlannerAction.PLAN_READ)
        now = datetime.now(UTC)
        contexts = await _RankContextReader(self._session, self._settings).contexts(
            business_id, now=now
        )
        return rank_obligations(
            contexts,
            now=now,
            urgent_window=timedelta(seconds=self._settings.planner_urgent_window_seconds),
        )

    async def read_mix(self, *, user_id: UUID, business_id: UUID) -> tuple[MixObservation, ...]:
        """§13.3's distribution, measured. A report — nothing here refuses anything."""

        await self._authorize(user_id, business_id, PlannerAction.PLAN_READ)
        profile = await self._profile(business_id)
        since = datetime.now(UTC) - timedelta(days=self._settings.planner_mix_window_days)
        counts = await self._repository.category_counts(business_id, since=since)
        return measure_mix(profile.mix_targets, counts)

    # --- plumbing ---------------------------------------------------------------------------

    async def _require_inputs(
        self,
        business_id: UUID,
        *,
        product_id: UUID,
        cta_id: UUID,
        campaign_offer_id: UUID | None,
        source_asset_ids: tuple[UUID, ...],
    ) -> None:
        facts = ScriptFactsReader(self._session)
        now = datetime.now(UTC)
        if await facts.product_brief(business_id, product_id) is None:
            raise _not_found("PLANNER_INPUT_NOT_FOUND", "Input not found")
        if await facts.cta_text(business_id, cta_id) is None:
            raise _not_found("PLANNER_INPUT_NOT_FOUND", "Input not found")
        if campaign_offer_id is not None:
            if await facts.campaign_brief(business_id, campaign_offer_id, now=now) is None:
                raise _not_found("PLANNER_INPUT_NOT_FOUND", "Input not found")
        if not source_asset_ids:
            return
        if len(source_asset_ids) > self._settings.script_generation_max_source_assets:
            raise ProblemException(
                status=422,
                code="PLANNER_TOO_MANY_SOURCE_ASSETS",
                title="Too many source assets",
                detail="Fewer source assets are allowed for one standing demand.",
            )
        known = await facts.known_asset_ids(business_id, source_asset_ids)
        if len(known) != len(set(source_asset_ids)):
            raise _not_found("PLANNER_INPUT_NOT_FOUND", "Input not found")

    async def _begin_idempotent(
        self,
        *,
        business_id: UUID,
        user_id: UUID,
        key: str | None,
        payload: dict[str, object],
        correlation_id: str,
    ) -> _IdempotentItem | None:
        if key is None:
            return None
        result = await IdempotencyService(OperationsRepository(self._session)).acquire(
            business_id=business_id,
            actor_user_id=user_id,
            operation=ITEM_OPERATION,
            key=key,
            fingerprint=request_fingerprint(payload),
            correlation_id=correlation_id,
        )
        body = result.record.response_body or {}
        item_id = body.get("item_id") if result.is_replay else None
        return _IdempotentItem(
            record=result.record, item_id=UUID(item_id) if isinstance(item_id, str) else None
        )

    async def _complete_idempotent(
        self, request: _IdempotentItem | None, *, body: dict[str, object]
    ) -> None:
        if request is None:
            return
        await OperationsService(self._session, self._settings).complete_idempotency(
            request.record, response_status=201, response_body=body
        )


@dataclass(frozen=True, slots=True)
class _IdempotentItem:
    record: Any
    item_id: UUID | None


def _not_found(code: str, title: str) -> ProblemException:
    return ProblemException(
        status=404, code=code, title=title, detail="The resource is not available."
    )


def _apply_obligation_event(
    obligation: ContentObligation,
    event: ObligationEvent,
    *,
    reason: str | None,
    next_attempt_at: datetime | None = None,
) -> None:
    """Move an obligation through its state machine and keep the row's invariants with it.

    One writer for the transition, the due time and the reason, because those three have to agree:
    `ck_content_obligation_due_matches_status` refuses any row that is not convertible and still
    carries a due time, and a blocked row with no reason is the silent disappearance `blocked`
    exists to prevent. Written once here rather than at each of the five call sites.
    """

    obligation.status = require_obligation_status(obligation.status, event)
    obligation.updated_at = datetime.now(UTC)
    if reason is not None:
        obligation.reason_code = reason[:96]
    if obligation.status is ObligationStatus.IN_PROGRESS:
        # The project is the durable job from here on; the obligation stops being polled and is
        # reconciled by the scheduling drain when that project ends. The last blocking reason
        # goes with it — it was answered by the conversion that just succeeded.
        obligation.reason_code = None
        obligation.next_attempt_at = None
    elif obligation_is_terminal(obligation.status):
        obligation.next_attempt_at = None
    elif next_attempt_at is not None:
        obligation.next_attempt_at = next_attempt_at


# --- the worker side: planning ------------------------------------------------------------------


class ObligationPlanningService(_PlannerBase):
    """Materialise §13.1 obligations for one standing demand's upcoming windows.

    It holds no entitlement service and no project service, and that constructor is the claim:
    planning cannot spend money because it has nothing to spend it with.

    One transaction, unlike the sequencer's two, and for a reason rather than by omission: nothing
    here calls a provider or waits on anything outside PostgreSQL, so there is no point at which a
    transaction would be held open across somebody else's timeout.
    """

    async def process_next(self) -> dict[str, int] | None:
        """Plan one standing demand. `None` when nothing is due, so the drain stops."""

        async with self._session.begin():
            now = datetime.now(UTC)
            item = await self._repository.claim_next_plannable_item(now=now)
            if item is None:
                return None
            # The lease first, so a crash below releases the item after
            # `PLANNER_PLAN_LEASE_SECONDS` rather than leaving it claimed forever.
            item.next_plan_at = now + timedelta(seconds=self._settings.planner_plan_lease_seconds)
            item.updated_at = now
            business = await self._businesses.get_business(item.business_id)
            if business is None or business.status is not BusinessStatus.ACTIVE:
                # A suspended tenant keeps its configuration and stops producing. Nothing is
                # cancelled: the obligations already planned are still commitments.
                item.next_plan_at = now + timedelta(
                    seconds=self._settings.planner_replan_interval_seconds
                )
                return {"planned": 0, "skipped": 1}
            profile = build_profile(
                business,
                await self._repository.get_settings(item.business_id),
                self._settings,
            )
            if not profile.enabled:
                item.next_plan_at = now + timedelta(
                    seconds=self._settings.planner_replan_interval_seconds
                )
                return {"planned": 0, "skipped": 1}
            try:
                created = await self._plan_item(item, profile, now=now)
                refused = False
            except PlannerError:
                # A documented refusal — an unresolvable timezone, a stored distribution that is
                # not one. Caught *here* rather than allowed out, because letting it out rolls
                # back the lease as well and the drain re-claims the same item immediately: one
                # broken row would become a hot loop. The item rests a full interval instead, and
                # the count says something was skipped rather than that nothing was due.
                created, refused = 0, True
            item.next_plan_at = now + timedelta(
                seconds=self._settings.planner_replan_interval_seconds
            )
            return {"planned": created, "skipped": int(refused)}

    async def _plan_item(
        self, item: PlannerSubscriptionItem, profile: PlanningProfile, *, now: datetime
    ) -> int:
        """Insert the missing obligations for this item's windows inside the horizon.

        The advisory lock is taken **before** the existing period starts are read. Taken after,
        it would serialise two runs that had both already decided to insert the same window —
        which is to say it would serialise nothing that matters.
        """

        await self._repository.lock_business(item.business_id)
        local_today = now.astimezone(profile.zone).date()
        days = period_days(first=local_today, period=item.period, horizon=profile.horizon_days)
        if not days:
            return 0
        existing = await self._repository.planned_period_starts(
            item.business_id, item.id, not_before=_window_for(item, days[0], profile).period_start
        )
        created = 0
        for day in days:
            window = _window_for(item, day, profile)
            if window.period_start in existing:
                continue
            self._repository.add(
                ContentObligation(
                    id=uuid4(),
                    business_id=item.business_id,
                    subscription_item_id=item.id,
                    content_type=item.content_type,
                    # Copied, not joined: re-categorising a standing demand next month must not
                    # rewrite the §13.3 distribution of the weeks that already happened.
                    category=item.category,
                    status=ObligationStatus.PLANNED,
                    period_start=window.period_start,
                    period_end=window.period_end,
                    planned_publish_at=window.planned_publish_at,
                    generation_deadline_at=window.generation_deadline_at,
                    quiet_hours_shifted=window.quiet_hours_shifted,
                    project_id=None,
                    reason_code=None,
                    attempts=0,
                    # Convertible from the moment generation is meant to start. Earlier would
                    # spend credit before §13.1's deadline says the work is due; later would miss
                    # a deadline that is already in the past when the obligation is created.
                    next_attempt_at=min(window.generation_deadline_at, window.planned_publish_at),
                    correlation_id=f"planner:{item.id}",
                    created_at=now,
                    updated_at=now,
                )
            )
            created += 1
            if created >= self._settings.planner_max_obligations_per_run:
                # A cap, reported rather than implied. A horizon wide enough to hit this is a
                # configuration mistake, and the next pass continues from where this one stopped.
                break
        if created:
            await self._session.flush()
        return created


def _window_for(
    item: PlannerSubscriptionItem, day: date, profile: PlanningProfile
) -> ObligationWindow:
    """§13.1's four instants for one of this item's local period days."""

    return build_window(
        day,
        period=item.period,
        tz=profile.zone,
        publish_minute=item.publish_minute,
        lead_minutes=item.lead_time_minutes,
        quiet_hours=profile.quiet_hours,
    )


# --- the worker side: conversion -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ClaimedObligation:
    """One leased obligation, lifted out of the claim transaction as values only."""

    business_id: UUID
    obligation_id: UUID
    item_id: UUID
    user_id: UUID
    correlation_id: str
    attempts: int
    scenario_code: ScenarioCode
    profile: RenderProfile
    product_id: UUID
    cta_id: UUID
    campaign_offer_id: UUID | None
    source_asset_ids: tuple[UUID, ...]


class ObligationDispatchService(_PlannerBase):
    """Turn the highest-ranked convertible obligation into a content project.

    This is the only place in the planner that spends credit, and it does not spend it itself:
    `ContentProjectService.create_project` reserves inside the transaction that creates the
    project (W20), so "an obligation was converted but the credit was not held" is not a state
    this code can produce. A refusal for want of credit therefore leaves **no project row at
    all** — the whole transaction goes with the `402` — and the obligation records why.

    Three transactions, in the sequencer's own shape: claim and lease, work with nothing open,
    settle. The middle step calls a service that opens its own transaction, which is exactly why
    the first one has to close.
    """

    async def process_next(self) -> dict[str, object] | None:
        claimed = await self._claim()
        if claimed is None:
            return None
        outcome = await self._convert(claimed)
        # A claim that only read leaves an autobegun transaction behind; the settlement opens its
        # own, so release the read snapshot here rather than inside a confusing error three lines
        # down. A no-op when `create_project` already committed.
        await self._session.rollback()
        return await self._settle(claimed, outcome)

    async def _claim(self) -> _ClaimedObligation | None:
        async with self._session.begin():
            now = datetime.now(UTC)
            business_id = await self._repository.next_dispatch_business(now=now)
            if business_id is None:
                return None
            # Serialises this tenant's dispatch, so two workers cannot rank the same candidate
            # set and both pick its winner. Taken before the set is read, for the reason
            # `_plan_item` takes it before reading period starts.
            await self._repository.lock_business(business_id)
            contexts = await _RankContextReader(self._session, self._settings).contexts(
                business_id, now=now
            )
            if not contexts:
                return None
            ranked = rank_obligations(
                contexts,
                now=now,
                urgent_window=timedelta(seconds=self._settings.planner_urgent_window_seconds),
            )
            obligation = await self._repository.get_obligation(
                business_id, ranked[0].obligation_id, lock=True
            )
            if obligation is None:
                return None
            item = await self._repository.get_item(business_id, obligation.subscription_item_id)
            if item is None:  # pragma: no cover - the foreign key makes this unreachable
                return None
            obligation.attempts += 1
            obligation.next_attempt_at = now + timedelta(
                seconds=self._settings.planner_dispatch_lease_seconds
            )
            obligation.updated_at = now
            scenario_code, profile = surface_for(obligation.content_type)
            return _ClaimedObligation(
                business_id=business_id,
                obligation_id=obligation.id,
                item_id=item.id,
                # The person who set the standing demand up. A background conversion still spends
                # somebody's credit, and `create_project` authorises as them — so a member who
                # lost their `content.generate` permission stops producing content, which is the
                # correct answer rather than a background job with no owner.
                user_id=item.requested_by_user_id,
                correlation_id=obligation.correlation_id,
                attempts=obligation.attempts,
                scenario_code=scenario_code,
                profile=profile,
                product_id=item.product_id,
                cta_id=item.cta_id,
                campaign_offer_id=item.campaign_offer_id,
                source_asset_ids=tuple(UUID(value) for value in item.source_asset_ids),
            )

    async def _convert(self, claimed: _ClaimedObligation) -> _Conversion:
        try:
            project = await ContentProjectService(self._session, self._settings).create_project(
                user_id=claimed.user_id,
                business_id=claimed.business_id,
                scenario_code=claimed.scenario_code,
                profile=claimed.profile,
                product_id=claimed.product_id,
                cta_id=claimed.cta_id,
                campaign_offer_id=claimed.campaign_offer_id,
                source_asset_ids=claimed.source_asset_ids,
                # Derived from the obligation, never random: a crash between "the project was
                # created" and "the obligation was updated" replays the same project instead of
                # buying a second one. This is the whole of "obligation → proje idempotent".
                idempotency_key=_conversion_key(claimed.obligation_id),
                correlation_id=claimed.correlation_id,
            )
        except ProblemException as error:
            if error.status >= 500:
                return _Conversion(project_id=None, code=error.code[:96], transient=True)
            # A 4xx is the content module saying this request can never succeed as stated —
            # not enough credit, a product that is no longer this tenant's, a suspended
            # business. The obligation blocks and says which, and it stays convertible: a
            # tenant who tops up their balance sees the next pass pick it up again.
            return _Conversion(project_id=None, code=error.code[:96], transient=False)
        return _Conversion(project_id=project.id, code=None, transient=False)

    async def _settle(self, claimed: _ClaimedObligation, outcome: _Conversion) -> dict[str, object]:
        async with self._session.begin():
            obligation = await self._repository.get_obligation(
                claimed.business_id, claimed.obligation_id, lock=True
            )
            if obligation is None:  # pragma: no cover - deleted mid-flight
                return {"converted": 0, "blocked": 0}
            if obligation.status not in _CONVERTIBLE:
                # Somebody withdrew the window while the conversion was running. The claim's lease
                # does not lock the row across the middle transaction — it cannot, because
                # `create_project` opens its own — so this race is reachable and is handled rather
                # than forced: `require_obligation_status` would refuse the transition and take
                # the task down with it, leaving a project that already exists and already holds
                # credit. The project stands; cancelling it is the customer's own endpoint, with
                # its own refund.
                return {"converted": 0, "blocked": 0, "withdrawn": 1}
            now = datetime.now(UTC)
            if outcome.project_id is not None:
                obligation.project_id = outcome.project_id
                _apply_obligation_event(obligation, ObligationEvent.CONVERTED, reason=None)
                self._audit(
                    business_id=claimed.business_id,
                    user_id=claimed.user_id,
                    action="planner.obligation.converted",
                    resource_type=OBLIGATION_RESOURCE_TYPE,
                    resource_id=obligation.id,
                    correlation_id=claimed.correlation_id,
                    details={"project_id": str(outcome.project_id)},
                )
                return {"converted": 1, "blocked": 0}
            exhausted = claimed.attempts >= self._settings.planner_dispatch_max_attempts
            if outcome.transient and not exhausted:
                # Still worth another go. The obligation stays `planned` and comes back after a
                # backoff bounded by the lease, so a provider outage costs delay rather than a
                # window.
                obligation.next_attempt_at = now + timedelta(
                    seconds=min(
                        2**claimed.attempts * self._settings.planner_dispatch_retry_seconds,
                        self._settings.planner_dispatch_lease_seconds,
                    )
                )
                obligation.updated_at = now
                return {"converted": 0, "blocked": 0, "retrying": 1}
            code = (
                REASON_ATTEMPTS_EXHAUSTED
                if outcome.transient
                else (outcome.code or REASON_ATTEMPTS_EXHAUSTED)
            )
            event = (
                ObligationEvent.BLOCKED
                if obligation.status is ObligationStatus.PLANNED
                else ObligationEvent.RETRIED
            )
            if event is ObligationEvent.RETRIED:
                # Already blocked and blocked again, possibly for a different reason. The state
                # machine draws `blocked -> planned -> blocked` rather than a self-loop, so this
                # takes both edges: the row ends blocked with the newest code, and the machine
                # stays a machine.
                _apply_obligation_event(obligation, ObligationEvent.RETRIED, reason=None)
            _apply_obligation_event(
                obligation,
                ObligationEvent.BLOCKED,
                reason=code,
                next_attempt_at=now
                + timedelta(seconds=self._settings.planner_blocked_retry_seconds),
            )
            self._audit(
                business_id=claimed.business_id,
                user_id=claimed.user_id,
                action="planner.obligation.blocked",
                resource_type=OBLIGATION_RESOURCE_TYPE,
                resource_id=obligation.id,
                correlation_id=claimed.correlation_id,
                details={"reason_code": code},
            )
            return {"converted": 0, "blocked": 1}


@dataclass(frozen=True, slots=True)
class _Conversion:
    project_id: UUID | None
    code: str | None
    transient: bool


def _conversion_key(obligation_id: UUID) -> str:
    """The idempotency key of one obligation's one project. One window, one generation."""

    return f"obligation:{obligation_id}:generation"


# --- the worker side: scheduling and reconciliation -----------------------------------------------


class ProjectSchedulingService(_PlannerBase):
    """Give approved content a publication slot, and keep the plan honest about reality.

    Three jobs in one drain, in priority order, because all three are the same statement: what the
    planner believes has to match what happened.

    1. `APPROVED --> SCHEDULED`. The slot is the obligation's `planned_publish_at` when there is
       an obligation and a fresh one when there is not — a project created by hand still has to
       be given a time, or `approved` would be a state nothing ever leaves. Either way it is
       pushed out of the quiet window (§13.2/8) and never into the past.
    2. An obligation whose project failed or was withdrawn is cancelled. Leaving it `in_progress`
       would make the planner believe that window is being served.
    3. An obligation whose window closed while it was still waiting expires. §13.1's window is a
       commitment to publish *inside* it; once it has gone there is nothing left to be early for.
    """

    async def process_next(self) -> dict[str, int] | None:
        scheduled = await self._schedule_one()
        if scheduled is not None:
            return scheduled
        reconciled = await self._reconcile_batch()
        if reconciled is not None:
            return reconciled
        return await self._expire_batch()

    async def _schedule_one(self) -> dict[str, int] | None:
        async with self._session.begin():
            projects = await self._repository.claim_schedulable_projects(limit=1)
            if not projects:
                return None
            # Already locked by the claim, and its own `business_id` is the tenant scope every
            # read below uses — so there is nothing a second, "tenant-scoped" re-read would check.
            project = projects[0]
            business = await self._businesses.get_business(project.business_id)
            if business is None:  # pragma: no cover - the foreign key makes this unreachable
                return None
            profile = build_profile(
                business,
                await self._repository.get_settings(project.business_id),
                self._settings,
            )
            if project.state is not ProjectState.APPROVED:  # pragma: no cover - claim predicate
                return {"scheduled": 0}
            obligation = await self._repository.obligation_for_project(
                project.business_id, project.id, lock=True
            )
            now = datetime.now(UTC)
            try:
                slot = self._slot_for(obligation, profile, now=now)
            except PlannerError:
                # Same reasoning as the planning drain: a project whose tenant has an
                # unresolvable timezone stays `approved` rather than taking the task down with
                # it. It is reported as a skip, so a drain that finds nothing schedulable and a
                # drain that could not compute a slot do not read alike.
                return {"scheduled": 0, "skipped": 1}
            # Written *before* the transition, so the row is never briefly a scheduled project
            # with no time on it — `ck_content_project_scheduled_has_time` would refuse it, and
            # the autoflush of any statement in between would find that out the hard way.
            project.scheduled_publish_at = slot
            apply_transition(
                project,
                event=ProjectEvent.SCHEDULED,
                reason=None,
                # Nobody acted. The approval that made this possible names its own actor.
                actor_user_id=None,
                sequence=await _next_transition_sequence(
                    self._session, project.business_id, project.id
                ),
                session_add=self._repository.add,
                poll_seconds=0,
            )
            if obligation is not None:
                _apply_obligation_event(obligation, ObligationEvent.FULFILLED, reason=None)
            self._audit(
                business_id=project.business_id,
                # `audit_logs` names a human for everything, including what a background job did
                # on their behalf — W20's rule, applied here to the person who asked for the
                # content. Nobody pressed anything; whose content moved is still the question the
                # row answers.
                user_id=project.requested_by_user_id,
                action="content.project.scheduled",
                resource_type=PROJECT_RESOURCE_TYPE,
                resource_id=project.id,
                correlation_id=project.correlation_id,
                details={
                    "scheduled_publish_at": slot.isoformat(),
                    "obligation_id": None if obligation is None else str(obligation.id),
                },
            )
            return {"scheduled": 1}

    def _slot_for(
        self, obligation: ContentObligation | None, profile: PlanningProfile, *, now: datetime
    ) -> datetime:
        """When this content goes out. Never in the past, never inside the quiet window.

        A planned slot that has already passed — the approval took longer than the window allowed
        — is moved to now rather than kept: publishing at a time that has gone is not publishing
        on time, it is publishing immediately with a misleading record of when.
        """

        requested = (
            now + timedelta(seconds=self._settings.planner_manual_publish_delay_seconds)
            if obligation is None
            else max(obligation.planned_publish_at, now)
        )
        return shift_out_of_quiet_hours(requested, tz=profile.zone, quiet_hours=profile.quiet_hours)

    async def _reconcile_batch(self) -> dict[str, int] | None:
        async with self._session.begin():
            obligations = await self._repository.claim_settled_obligations(
                limit=self._settings.planner_batch_size
            )
            if not obligations:
                return None
            for obligation in obligations:
                _apply_obligation_event(
                    obligation, ObligationEvent.CANCELLED, reason=REASON_PROJECT_ENDED
                )
            return {
                "reconciled": len(obligations),
                "batch_full": int(len(obligations) >= self._settings.planner_batch_size),
            }

    async def _expire_batch(self) -> dict[str, int] | None:
        async with self._session.begin():
            obligations = await self._repository.claim_expired_obligations(
                now=datetime.now(UTC), limit=self._settings.planner_batch_size
            )
            if not obligations:
                return None
            for obligation in obligations:
                _apply_obligation_event(
                    obligation, ObligationEvent.EXPIRED, reason=REASON_WINDOW_CLOSED
                )
            return {
                "expired": len(obligations),
                "batch_full": int(len(obligations) >= self._settings.planner_batch_size),
            }


async def _next_transition_sequence(
    session: AsyncSession, business_id: UUID, project_id: UUID
) -> int:
    """Borrowed from `ContentRepository` rather than reimplemented, for the obvious reason."""

    return await ContentRepository(session).next_transition_sequence(business_id, project_id)


# --- the facts §13.2 ranks on ---------------------------------------------------------------------


class _RankContextReader:
    """Read everything the ten priorities look at, for one tenant, in five queries.

    Separate from the services because two of them need exactly this — the dispatcher, to decide
    what to convert, and the plan endpoint, to explain what it would convert. A second reader
    would be a second answer to "why is this next?" waiting to disagree with the first.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = PlannerRepository(session)
        self._facts = ContentFactsReader(session)
        self._script_facts = ScriptFactsReader(session)

    async def contexts(self, business_id: UUID, *, now: datetime) -> tuple[RankContext, ...]:
        obligations = await self._repository.due_obligations(
            business_id, now=now, limit=self._settings.planner_candidate_limit
        )
        if not obligations:
            return ()
        items = await self._repository.items_by_id(
            business_id, tuple({row.subscription_item_id for row in obligations})
        )
        settings_row = await self._repository.get_settings(business_id)
        business = await BusinessRepository(self._session).get_business(business_id)
        if business is None:  # pragma: no cover - the foreign key makes this unreachable
            return ()
        profile = build_profile(business, settings_row, self._settings)
        deviations = {
            observation.category: observation.deviation_points
            for observation in measure_mix(
                profile.mix_targets,
                await self._repository.category_counts(
                    business_id,
                    since=now - timedelta(days=self._settings.planner_mix_window_days),
                ),
            )
        }
        repetition = await self._repository.recent_product_uses(
            business_id,
            since=now - timedelta(days=self._settings.planner_repetition_window_days),
        )
        # One query for every candidate's footage rather than one per candidate. The union is
        # bounded by the candidate limit times the per-item source ceiling, both configured.
        facts = await self._facts.asset_facts(
            business_id,
            tuple({UUID(value) for item in items.values() for value in item.source_asset_ids}),
        )
        contexts: list[RankContext] = []
        for obligation in obligations:
            item = items.get(obligation.subscription_item_id)
            if item is None:  # pragma: no cover - the foreign key makes this unreachable
                continue
            renderable = [
                entry
                for value in item.source_asset_ids
                if (entry := facts.get(UUID(value))) is not None and entry.renderable
            ]
            contexts.append(
                RankContext(
                    obligation_id=obligation.id,
                    category=obligation.category,
                    planned_publish_at=obligation.planned_publish_at,
                    generation_deadline_at=obligation.generation_deadline_at,
                    has_active_campaign=await self._campaign_is_active(
                        business_id, item.campaign_offer_id, now=now
                    ),
                    mix_deviation_points=deviations.get(obligation.category, 0),
                    renderable_assets=len(renderable),
                    required_assets=self._settings.planner_min_renderable_assets,
                    recent_product_uses=repetition.get(item.product_id, 0),
                    matches_target_orientation=_orientation_match(
                        renderable, surface_for(obligation.content_type)[1]
                    ),
                    quiet_hours_shifted=obligation.quiet_hours_shifted,
                    preference_rank=item.preference_rank,
                    # §13.2/4 and §13.2/10: carried as `None` because no source exists. See
                    # `UNIMPLEMENTED_PRIORITIES`.
                    performance_score=None,
                    special_day_code=None,
                )
            )
        return tuple(contexts)

    async def _campaign_is_active(
        self, business_id: UUID, campaign_offer_id: UUID | None, *, now: datetime
    ) -> bool:
        """§13.2/1, answered by W04's own verdict rather than by a second definition of active."""

        if campaign_offer_id is None:
            return False
        brief = await self._script_facts.campaign_brief(business_id, campaign_offer_id, now=now)
        return brief is not None and brief.active


def _orientation_match(renderable: Sequence[AssetFacts], profile: RenderProfile) -> bool | None:
    """Whether any usable source is already the shape the surface wants (§13.2/7).

    `None` when nothing measurable was found — no renderable asset carries dimensions — which the
    rule reads as "unknown" and ranks last. That is a different answer from "landscape footage for
    a vertical Reel", which is merely cropped, and the two must not collapse into one bucket.
    """

    wanted = target_orientation(profile)
    measured = [
        orientation_of(entry.width, entry.height)
        for entry in renderable
        if entry.width is not None and entry.height is not None
    ]
    if not measured:
        return None
    return wanted in measured


__all__ = [
    "ITEM_OPERATION",
    "ObligationDispatchService",
    "ObligationPlanningService",
    "PlannerConfigService",
    "PlanningProfile",
    "ProjectSchedulingService",
    "build_profile",
]
