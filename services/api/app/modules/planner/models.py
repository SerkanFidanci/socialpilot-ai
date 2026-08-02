"""Persistence for PRD §13: what a tenant standingly wants, and what that turned into.

Three tables, and the shape of the middle one is the decision worth reading.

`planner_subscription_items` is a **stand-in**. PRD §12.2's real subscription item — plan, quality
tier, billing — is Phase 3, blocked behind K1. What §13.1 needs from it is much smaller: something
that says "this business wants one Instagram Reel a day, about this product, with this call to
action". So this slice models exactly that and nothing else, seeded by hand exactly as W20 seeded
credit by hand, and keeps §13.1's own column name (`subscription_item_id`) on the obligation so
Phase 3 re-points a foreign key rather than renaming a field the PRD names.

`content_obligations` is §13.1's example, column for column, plus the four things a queue entry
needs that an example does not show: what it became (`project_id`), why it could not become that
(`blocked_code`), when to look at it again (`next_attempt_at`, which is both the claim's ordering
key and its lease), and how many times that has been tried.

`planner_settings` is one row per business: the quiet window, the §13.3 targets, and the horizon.
It is optional — a business with no row is planned with the deployment defaults — because the
alternative is a planner that silently does nothing until somebody configures it.

**The link to a project points this way on purpose.** A project can exist without an obligation:
that is every project slices 2A–2F produce, and it stays true. An obligation has at most one
project. Putting the reference on the obligation therefore keeps the dependency one-directional —
`planner` reads `content`, and `content` does not know this module exists — which is the same rule
`entitlement` and `content` already follow through `ReservationSourceProbe`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.identity.models import Base
from app.modules.planner.obligation import (
    ContentCategory,
    ContentType,
    ObligationStatus,
    PlanItemStatus,
    PlanPeriod,
)


def _enum(enum_type: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_type, name=name, values_callable=lambda values: [item.value for item in values]
    )


def _business_id() -> Mapped[UUID]:
    return mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )


class PlannerSettings(Base):
    """One business's planning configuration (§13.2/8, §13.2/9, §13.3).

    Written with `business.update` and read with `business.read`: this is a setting about the
    business, not a piece of content, so it sits on the same side of PRD §4's line as a rename.

    The quiet window is stored as minutes past *local* midnight rather than as a timestamp,
    because "we do not post between 22:00 and 08:00" is a fact about the tenant's wall clock that
    survives a DST transition unchanged. Which instants that maps to is computed at planning
    time from the business's own timezone.
    """

    __tablename__ = "planner_settings"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_planner_settings_business"),
        # An empty window is `start == end`; anything else is a real interval, possibly wrapping
        # midnight, which is what a quiet window normally does.
        CheckConstraint(
            "quiet_hours_start_minute BETWEEN 0 AND 1439"
            " AND quiet_hours_end_minute BETWEEN 0 AND 1439",
            name="ck_planner_settings_quiet_window",
        ),
        CheckConstraint(
            "planning_horizon_days BETWEEN 0 AND 60", name="ck_planner_settings_horizon"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    # Off by default is the wrong default here and on is the wrong one too, so this is explicit:
    # a settings row exists only because somebody made one, and `enabled` is what they said.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    quiet_hours_start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    quiet_hours_end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    # §13.3's distribution as `{category: share}`. JSONB rather than seven columns because it is
    # read and written as one document and §13.3 explicitly allows the set of shares to differ by
    # sector; `MixTargets` is what refuses a document that is not total or does not sum to 100.
    mix_targets: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    planning_horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlannerSubscriptionItem(Base):
    """A standing demand for content — Phase 3's subscription item, in the small.

    What it holds is exactly what turning an obligation into a project needs: the surface, the
    category it counts towards in §13.3's mix, the cadence, when in the local day it should be
    published, how long before that generation has to start, and the verified records the script
    will be written from. Nothing about money, plans or renewal: those are §12.2's and they are
    Phase 3's.

    `requested_by_user_id` is the person every obligation derived from this item acts as. A
    background conversion still spends somebody's credit, and `audit_logs` names a human — the
    same rule W20 applied to a sweep-written refund.
    """

    __tablename__ = "planner_subscription_items"
    __table_args__ = (
        Index("ix_planner_items_business_created", "business_id", "created_at", "id"),
        # The planning drain's claim. Partial over the items that are still producing, so a
        # tenant that paused everything contributes nothing to it.
        Index(
            "ix_planner_items_due",
            "next_plan_at",
            "id",
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint("publish_minute BETWEEN 0 AND 1439", name="ck_planner_item_publish_minute"),
        # A lead time longer than the period would put every generation deadline before the
        # previous period's publish slot, which is a schedule nobody could satisfy.
        CheckConstraint("lead_time_minutes BETWEEN 0 AND 10080", name="ck_planner_item_lead_time"),
        CheckConstraint("preference_rank BETWEEN 0 AND 999", name="ck_planner_item_preference"),
        # A paused item is never looked at, and a live one always is — the same rule
        # `content_projects` states between a terminal state and its due time, and for the same
        # reason: the claim's partial index must not be able to disagree with the row.
        CheckConstraint(
            "(status = 'paused') = (next_plan_at IS NULL)",
            name="ck_planner_item_due_matches_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    status: Mapped[PlanItemStatus] = mapped_column(_enum(PlanItemStatus, "planner_item_status"))
    content_type: Mapped[ContentType] = mapped_column(_enum(ContentType, "planner_content_type"))
    category: Mapped[ContentCategory] = mapped_column(
        _enum(ContentCategory, "planner_content_category")
    )
    period: Mapped[PlanPeriod] = mapped_column(_enum(PlanPeriod, "planner_plan_period"))
    publish_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    lead_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # §13.2/9's "kullanıcı tercihleri", as a number the tenant sets. Zero is highest.
    preference_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The verified records a project opened from this item is built on. `RESTRICT` for the same
    # reason `content_projects` uses it: deleting a product must not leave a standing demand
    # pointing at nothing.
    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    cta_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("approved_ctas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    campaign_offer_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campaign_offers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_asset_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    requested_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # When the planning drain should materialise this item's next windows. Both the ordering key
    # of its claim and its lease, exactly as `content_projects.next_check_at` is for the
    # sequencer: pushed forward inside the claim, so a worker that dies mid-pass releases the item
    # instead of holding it. `NULL` while paused.
    next_plan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ContentObligation(Base):
    """PRD §13.1's queue entry: what should be published, when, and by when it must exist.

    `(subscription_item_id, period_start)` is unique, and that constraint is the whole of the
    planner's idempotency. A second run over the same window finds the row it wrote; two
    concurrent runs serialise on the tenant advisory lock and, if one ever slipped past it, the
    index refuses the duplicate. Neither depends on the planner remembering to check.

    `next_attempt_at` plays the part `content_projects.next_check_at` plays for the sequencer: it
    is the ordering key of the dispatch claim *and* the lease, pushed forward inside the claim so
    a worker that dies mid-conversion releases the obligation instead of holding it. It is `NULL`
    in every status that is not convertible — including `in_progress`, which is not terminal but
    has handed the work to a project that is now the durable job. That is what keeps everything
    but the live queue out of the partial index.
    """

    __tablename__ = "content_obligations"
    __table_args__ = (
        # §13.1's natural key. One standing demand produces one obligation per window, forever.
        UniqueConstraint(
            "subscription_item_id", "period_start", name="uq_content_obligation_period"
        ),
        # PRD §28.9 names this index by these columns.
        Index(
            "ix_content_obligations_business_planned",
            "business_id",
            "planned_publish_at",
            "status",
        ),
        Index("ix_content_obligations_business_created", "business_id", "created_at", "id"),
        # The dispatcher's claim. Partial over the statuses that can still become work, so a
        # tenant with a year of fulfilled obligations contributes nothing to it.
        Index(
            "ix_content_obligations_due",
            "next_attempt_at",
            "id",
            postgresql_where=text("status IN ('planned', 'blocked')"),
        ),
        CheckConstraint("period_start < period_end", name="ck_content_obligation_window"),
        CheckConstraint(
            "generation_deadline_at <= planned_publish_at",
            name="ck_content_obligation_deadline",
        ),
        # Exactly the *convertible* statuses carry a due time, and it is written as the same set
        # the claim's partial index is written over so the two cannot disagree. `in_progress` is
        # not terminal and still carries none: once an obligation has become a project, that
        # project is the durable job, and polling the queue entry as well would be a second
        # clock over the same work.
        CheckConstraint(
            "(status IN ('planned', 'blocked')) = (next_attempt_at IS NOT NULL)",
            name="ck_content_obligation_due_matches_status",
        ),
        # Every blocked row explains itself. Not an equivalence, because `expired` and `cancelled`
        # carry a reason too — but a blocked obligation with no code would be exactly the silent
        # disappearance this status exists to prevent.
        CheckConstraint(
            "status <> 'blocked' OR reason_code IS NOT NULL",
            name="ck_content_obligation_blocked_has_code",
        ),
        CheckConstraint("attempts >= 0", name="ck_content_obligation_attempts"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    # §13.1 names this column. It points at this slice's stand-in; Phase 3 re-points it at the
    # real subscription item without the field changing its meaning or its name.
    subscription_item_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("planner_subscription_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_type: Mapped[ContentType] = mapped_column(_enum(ContentType, "planner_content_type"))
    # Copied from the item rather than joined, and deliberately: §13.3's mix is measured over
    # what was actually planned, so re-categorising a standing demand next month must not rewrite
    # the distribution of the weeks that already happened.
    category: Mapped[ContentCategory] = mapped_column(
        _enum(ContentCategory, "planner_content_category")
    )
    status: Mapped[ObligationStatus] = mapped_column(
        _enum(ObligationStatus, "content_obligation_status")
    )

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generation_deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Whether §13.2/8 moved the slot the tenant asked for. Stored because "why is my post at
    # 08:00 when I said 23:00?" has to be answerable from the row rather than by re-deriving it
    # against a quiet window that may have been edited since.
    quiet_hours_shifted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    project_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_projects.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Why this obligation is where it is: a documented code, never prose and never tenant text —
    # an insufficient balance, a product that is no longer this tenant's, a window that closed, a
    # project that ended. It is the field that makes `blocked` visible instead of silent, and it
    # outlives the blocking: an expired row keeps the last reason it could not be converted.
    reason_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["ContentObligation", "PlannerSettings", "PlannerSubscriptionItem"]
