"""Add the planner's three tables and reopen `approved` — slice 2G.

Three new tables, five new enum types, one new project column, and one reshaping that is the
reason this migration is not simply additive: **`approved` stops being a terminal state.** Slice 2F
made it terminal because there was no scheduler; PRD §20 draws `--> SCHEDULED` after the approval,
so `scheduled` is now the state a project ends in and `approved` is a state it passes through.
Everything that encoded the old terminal set has to move with it — the partial claim index, the
constraint tying a due time to terminality, and the constraint requiring a rendered state to name
its render.

This is the same shape `0018` had, one state later, and it is deliberate: each slice that puts
something *after* what used to be the end reopens exactly one state and moves the three objects
that spelled the old set out.

`content_project_state` and `content_project_event` are **replaced rather than extended**, for the
reason `0018` records: `ALTER TYPE ... ADD VALUE` cannot be followed, in the same transaction, by a
constraint or index predicate that *uses* the new value — and both of the new predicates below do.
Swapping the type keeps everything in one transaction and gives the downgrade something honest to
do.

One backfill, and it is load-bearing. Every project already sitting in `approved` is given a due
time, because the state it is in is no longer terminal and the reshaped constraint would otherwise
reject rows `0018` wrote. Those projects are then picked up by the scheduling drain and given a
publication slot, which is the correct treatment of content approved before a planner existed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_content_planner"
down_revision: str | None = "0018_approval_and_revision"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TIMESTAMP_DEFAULT = sa.text("timezone('utc', now())")

# PRD §20's states as of slice 2G, and as of slice 2F. Both are written out because the type is
# swapped in each direction, and a reversal that guessed its own target would be a reversal nobody
# could check.
_STATES_2F = (
    "planned",
    "waiting_media",
    "analyzing",
    "scripting",
    "voice_generation",
    "timeline_building",
    "rendering",
    "quality_check",
    "preview_ready",
    "failed",
    "retrying",
    "waiting_approval",
    "revision_requested",
    "approved",
    "cancelled",
)
_STATES_2G = (*_STATES_2F, "scheduled")

_EVENTS_2F = (
    "created",
    "media_required",
    "media_attached",
    "analysis_started",
    "analysis_complete",
    "script_ready",
    "voiceover_ready",
    "timeline_ready",
    "render_succeeded",
    "qc_passed",
    "qc_needs_review",
    "qc_failed",
    "retry_requested",
    "retry_started",
    "step_failed",
    "approval_required",
    "auto_approved",
    "approved",
    "rejected",
    "revision_scoped_to_script",
    "revision_scoped_to_voice",
    "revision_scoped_to_timeline",
    "cancelled",
)
_EVENTS_2G = (*_EVENTS_2F, "scheduled")

# The planner's own vocabularies (PRD §13.1, §13.3).
_CONTENT_TYPES = (
    "instagram_reels",
    "instagram_story",
    "instagram_feed",
    "instagram_square",
    "x_video",
    "x_vertical",
)
_CONTENT_CATEGORIES = (
    "product_service",
    "educational",
    "brand_story",
    "social_proof",
    "entertainment",
    "campaign",
    "corporate",
)
_PLAN_PERIODS = ("daily", "weekly")
_ITEM_STATUSES = ("active", "paused")
_OBLIGATION_STATUSES = (
    "planned",
    "in_progress",
    "blocked",
    "fulfilled",
    "cancelled",
    "expired",
)

# The states a project is over in, before and after this migration. Written as SQL fragments so the
# three places that need them — two check constraints and one index predicate — cannot disagree.
_TERMINAL_2F = "'approved', 'failed', 'cancelled'"
_TERMINAL_2G = "'scheduled', 'failed', 'cancelled'"
# Where a render must already exist. `scheduled` joins the list for the reason `approved` did: it
# is a state somebody reached by looking at a rendered video.
_RENDERED_2F = "'preview_ready', 'waiting_approval', 'approved'"
_RENDERED_2G = "'preview_ready', 'waiting_approval', 'approved', 'scheduled'"

_STATE_COLUMNS = (
    ("content_projects", "state"),
    ("content_project_transitions", "from_state"),
    ("content_project_transitions", "to_state"),
)
_EVENT_COLUMNS = (("content_project_transitions", "event"),)

# §13.3's example distribution, as the default a pre-existing settings row would carry. There are
# none to carry it — the table is created here — but the literal is written once and read by the
# application from `DEFAULT_MIX_SHARES`, so the two are checked against each other by the suite.
_DEFAULT_MIX = (
    '{"product_service": 25, "educational": 20, "brand_story": 15, "social_proof": 15,'
    ' "entertainment": 10, "campaign": 10, "corporate": 5}'
)


def upgrade() -> None:
    _drop_state_dependants()
    _drop_event_dependants()
    _swap_enum("content_project_state", _STATES_2G, columns=_STATE_COLUMNS)
    _swap_enum("content_project_event", _EVENTS_2G, columns=_EVENT_COLUMNS)
    op.add_column(
        "content_projects",
        sa.Column("scheduled_publish_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Before the reshaped constraint exists, not after: `approved` is a live state now, and a live
    # state must carry a due time. The scheduling drain picks these up and gives each one a slot,
    # which is the correct treatment of content approved before a planner existed.
    op.execute(
        sa.text(
            "UPDATE content_projects SET next_check_at = timezone('utc', now()) "
            "WHERE state = 'approved'"
        )
    )
    _create_state_dependants(terminal=_TERMINAL_2G, rendered=_RENDERED_2G, scheduled=True)
    _create_event_dependants()

    for name, values in (
        ("planner_content_type", _CONTENT_TYPES),
        ("planner_content_category", _CONTENT_CATEGORIES),
        ("planner_plan_period", _PLAN_PERIODS),
        ("planner_item_status", _ITEM_STATUSES),
        ("content_obligation_status", _OBLIGATION_STATUSES),
    ):
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)

    _create_settings()
    _create_items()
    _create_obligations()


def downgrade() -> None:
    # Before a single object is dropped: can the reversal keep its data? A project in a state
    # slice 2F cannot express has no honest reversal — dropping the value would rewrite what
    # happened to somebody's content. Refuse instead of guess, the answer `0011` and `0018` give.
    _refuse_downgrade_that_cannot_keep_its_data()

    op.drop_index("ix_content_obligations_due", table_name="content_obligations")
    op.drop_index("ix_content_obligations_business_created", table_name="content_obligations")
    op.drop_index("ix_content_obligations_business_planned", table_name="content_obligations")
    op.drop_table("content_obligations")
    op.drop_index("ix_planner_items_due", table_name="planner_subscription_items")
    op.drop_index("ix_planner_items_business_created", table_name="planner_subscription_items")
    op.drop_table("planner_subscription_items")
    op.drop_table("planner_settings")

    for name in (
        "content_obligation_status",
        "planner_item_status",
        "planner_plan_period",
        "planner_content_category",
        "planner_content_type",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

    _drop_state_dependants()
    _drop_event_dependants()
    _swap_enum("content_project_event", _EVENTS_2F, columns=_EVENT_COLUMNS)
    _swap_enum("content_project_state", _STATES_2F, columns=_STATE_COLUMNS)
    op.drop_column("content_projects", "scheduled_publish_at")
    # `approved` is terminal again, and slice 2F's constraint requires a terminal state to carry
    # no due time. Undone before that constraint is recreated, exactly as the upgrade's backfill
    # happens before its own.
    op.execute(
        sa.text(
            "UPDATE content_projects SET next_check_at = NULL "
            "WHERE state IN ('approved', 'failed', 'cancelled')"
        )
    )
    _create_state_dependants(terminal=_TERMINAL_2F, rendered=_RENDERED_2F, scheduled=False)
    _create_event_dependants()


# --- helpers -----------------------------------------------------------------------------------
# The upgrade and the downgrade perform the *same* operations with different value lists. Two
# hand-written copies of a four-statement type swap is how a reversal comes to differ from the
# thing it reverses.


def _existing(name: str) -> postgresql.ENUM:
    """A reference to a type this migration already created; never a second CREATE TYPE."""

    return postgresql.ENUM(name=name, create_type=False)


def _swap_enum(name: str, values: tuple[str, ...], *, columns: tuple[tuple[str, str], ...]) -> None:
    """Replace an enum type in place, moving every column that uses it onto the new one.

    The rename-create-cast-drop sequence is the only fully transactional way to *remove* an enum
    value, and it is symmetric, so one helper serves both directions. `USING x::text::y` is safe
    precisely because the caller has already refused any row holding a value the target type does
    not have.
    """

    op.execute(sa.text(f"ALTER TYPE {name} RENAME TO {name}_swap"))
    postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=False)
    for table, column in columns:
        op.execute(
            sa.text(
                f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {name} "
                f"USING {column}::text::{name}"
            )
        )
    op.execute(sa.text(f"DROP TYPE {name}_swap"))


def _refuse_downgrade_that_cannot_keep_its_data() -> None:
    stranded = op.get_bind().execute(
        sa.text("SELECT count(*) FROM content_projects WHERE state::text = 'scheduled'")
    )
    remaining = int(stranded.scalar_one())
    if remaining:
        raise RuntimeError(
            f"downgrade would discard {remaining} scheduled project(s); "
            "resolve or cancel them before reversing 0019"
        )


def _drop_state_dependants() -> None:
    """Remove everything whose definition mentions a `content_project_state` literal."""

    op.drop_constraint("ck_content_project_due_matches_state", "content_projects", type_="check")
    op.drop_constraint("ck_content_project_preview_has_render", "content_projects", type_="check")
    # `IF EXISTS` because both directions run this unconditionally and only one of them has ever
    # created it: on the way up the constraint does not exist yet, on the way down it does.
    op.execute(
        sa.text(
            "ALTER TABLE content_projects "
            "DROP CONSTRAINT IF EXISTS ck_content_project_scheduled_has_time"
        )
    )
    op.drop_index("ix_content_projects_due", table_name="content_projects")


def _create_state_dependants(*, terminal: str, rendered: str, scheduled: bool) -> None:
    op.create_check_constraint(
        "ck_content_project_due_matches_state",
        "content_projects",
        f"(state IN ({terminal})) = (next_check_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_content_project_preview_has_render",
        "content_projects",
        f"state NOT IN ({rendered}) OR render_id IS NOT NULL",
    )
    if scheduled:
        # A scheduled project with no time on it would be a calendar entry with no date. Stated
        # in the schema so it is inexpressible, not merely unwritten.
        op.create_check_constraint(
            "ck_content_project_scheduled_has_time",
            "content_projects",
            "state <> 'scheduled' OR scheduled_publish_at IS NOT NULL",
        )
    op.create_index(
        "ix_content_projects_due",
        "content_projects",
        ["next_check_at", "id"],
        postgresql_where=sa.text(f"state NOT IN ({terminal})"),
    )


def _drop_event_dependants() -> None:
    op.drop_constraint(
        "ck_content_project_transition_origin", "content_project_transitions", type_="check"
    )


def _create_event_dependants() -> None:
    op.create_check_constraint(
        "ck_content_project_transition_origin",
        "content_project_transitions",
        "from_state IS NOT NULL OR event = 'created'",
    )


def _create_settings() -> None:
    op.create_table(
        "planner_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # Minutes past *local* midnight, not a timestamp: "we do not post between 22:00 and 08:00"
        # is a fact about the tenant's wall clock and survives a DST transition unchanged.
        sa.Column(
            "quiet_hours_start_minute",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1320"),
        ),
        sa.Column(
            "quiet_hours_end_minute", sa.Integer(), nullable=False, server_default=sa.text("480")
        ),
        sa.Column(
            "mix_targets",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT_MIX}'::jsonb"),
        ),
        sa.Column(
            "planning_horizon_days", sa.Integer(), nullable=False, server_default=sa.text("7")
        ),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("business_id", name="uq_planner_settings_business"),
        sa.CheckConstraint(
            "quiet_hours_start_minute BETWEEN 0 AND 1439"
            " AND quiet_hours_end_minute BETWEEN 0 AND 1439",
            name="ck_planner_settings_quiet_window",
        ),
        sa.CheckConstraint(
            "planning_horizon_days BETWEEN 0 AND 60", name="ck_planner_settings_horizon"
        ),
    )


def _create_items() -> None:
    op.create_table(
        "planner_subscription_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", _existing("planner_item_status"), nullable=False),
        sa.Column("content_type", _existing("planner_content_type"), nullable=False),
        sa.Column("category", _existing("planner_content_category"), nullable=False),
        sa.Column("period", _existing("planner_plan_period"), nullable=False),
        sa.Column("publish_minute", sa.Integer(), nullable=False),
        sa.Column("lead_time_minutes", sa.Integer(), nullable=False),
        sa.Column("preference_rank", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cta_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_offer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_asset_ids", postgresql.JSONB(), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("next_plan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        # `RESTRICT` for the reason `content_projects` uses it: deleting a product must not leave
        # a standing demand pointing at nothing.
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cta_id"], ["approved_ctas.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["campaign_offer_id"], ["campaign_offers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "publish_minute BETWEEN 0 AND 1439", name="ck_planner_item_publish_minute"
        ),
        sa.CheckConstraint(
            "lead_time_minutes BETWEEN 0 AND 10080", name="ck_planner_item_lead_time"
        ),
        sa.CheckConstraint("preference_rank BETWEEN 0 AND 999", name="ck_planner_item_preference"),
        sa.CheckConstraint(
            "(status = 'paused') = (next_plan_at IS NULL)",
            name="ck_planner_item_due_matches_status",
        ),
    )
    op.create_index(
        "ix_planner_items_business_created",
        "planner_subscription_items",
        ["business_id", "created_at", "id"],
    )
    op.create_index(
        "ix_planner_items_due",
        "planner_subscription_items",
        ["next_plan_at", "id"],
        postgresql_where=sa.text("status = 'active'"),
    )


def _create_obligations() -> None:
    op.create_table(
        "content_obligations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_type", _existing("planner_content_type"), nullable=False),
        sa.Column("category", _existing("planner_content_category"), nullable=False),
        sa.Column("status", _existing("content_obligation_status"), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_publish_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "quiet_hours_shifted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_code", sa.String(length=96), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["subscription_item_id"],
            ["planner_subscription_items.id"],
            ondelete="CASCADE",
        ),
        # `RESTRICT`: the obligation is the record of what this window became, and deleting the
        # project would leave a fulfilled obligation pointing at nothing.
        sa.ForeignKeyConstraint(["project_id"], ["content_projects.id"], ondelete="RESTRICT"),
        # §13.1's natural key, and the whole of the planner's idempotency: one standing demand
        # produces one obligation per window, forever.
        sa.UniqueConstraint(
            "subscription_item_id", "period_start", name="uq_content_obligation_period"
        ),
        sa.CheckConstraint("period_start < period_end", name="ck_content_obligation_window"),
        sa.CheckConstraint(
            "generation_deadline_at <= planned_publish_at",
            name="ck_content_obligation_deadline",
        ),
        # Exactly the convertible statuses carry a due time, written over the same set as the
        # partial index below so the constraint and the claim cannot disagree.
        sa.CheckConstraint(
            "(status IN ('planned', 'blocked')) = (next_attempt_at IS NOT NULL)",
            name="ck_content_obligation_due_matches_status",
        ),
        sa.CheckConstraint(
            "status <> 'blocked' OR reason_code IS NOT NULL",
            name="ck_content_obligation_blocked_has_code",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_content_obligation_attempts"),
    )
    # PRD §28.9 names this index by these columns.
    op.create_index(
        "ix_content_obligations_business_planned",
        "content_obligations",
        ["business_id", "planned_publish_at", "status"],
    )
    op.create_index(
        "ix_content_obligations_business_created",
        "content_obligations",
        ["business_id", "created_at", "id"],
    )
    op.create_index(
        "ix_content_obligations_due",
        "content_obligations",
        ["next_attempt_at", "id"],
        postgresql_where=sa.text("status IN ('planned', 'blocked')"),
    )
