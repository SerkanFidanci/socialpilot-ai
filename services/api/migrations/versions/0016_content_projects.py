"""Add `content_projects` + `content_project_transitions` and reshape the QC claim — slice 2E.

Two tables and one column, and the column is the interesting one.

`content_projects` is PRD §20's state machine made durable. It is deliberately *not* paired with
a row in `jobs`: a sequencer's state is its result, and keeping the same fact in two tables gives
a crashed worker two answers to "where is this project". Everything the job pattern guarantees is
still here — a status, a step timeout measured from `state_entered_at`, attempt counters, a
correlation id, and a terminal `failed` state as the dead letter.

`content_project_transitions` is §20's closing sentence ("her durum geçişi transactional olarak
kaydedilmelidir"). `sequence` is unique per project so the history has an order independent of
timestamp resolution, and `from_state` is nullable only for the `[*] --> PLANNED` entry.

`render_outputs.qc_claimed_at` is the answer to the measurement slice 2D left behind. Automatic
QC claimed "a succeeded render with no report", which is an anti-join across two tables; at 200k
renders that cost 134 ms per tick, and an index did not help because nothing told the planner
that unreported renders are always the newest. The correlation is now stated as a predicate on
the render row itself, with a partial index over it — in steady state the index holds the empty
set. The backfill below closes the only gap this could open: every render that already carries a
report is marked claimed, so migrating does not re-run QC over the entire history.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_content_projects"
down_revision: str | None = "0015_render_qc_reports"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TIMESTAMP_DEFAULT = sa.text("timezone('utc', now())")

_STATES = (
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
)
_EVENTS = (
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
)


def upgrade() -> None:
    state = postgresql.ENUM(*_STATES, name="content_project_state", create_type=False)
    state.create(op.get_bind(), checkfirst=True)
    event = postgresql.ENUM(*_EVENTS, name="content_project_event", create_type=False)
    event.create(op.get_bind(), checkfirst=True)
    scenario = postgresql.ENUM(name="content_scenario_code", create_type=False)
    profile = postgresql.ENUM(name="render_profile", create_type=False)
    remediation = postgresql.ENUM(name="qc_remediation_path", create_type=False)

    op.create_table(
        "content_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_code", scenario, nullable=False),
        sa.Column("profile", profile, nullable=False),
        sa.Column("state", state, nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("campaign_offer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cta_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_asset_ids", postgresql.JSONB(), nullable=False),
        sa.Column("script_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("voiceover_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("render_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("qc_report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("render_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("step_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "requires_human_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("recommended_path", remediation, nullable=False),
        sa.Column("state_entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=96), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["campaign_offer_id"], ["campaign_offers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cta_id"], ["approved_ctas.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["script_id"], ["content_scripts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["voiceover_id"], ["voiceover_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["timeline_id"], ["content_timelines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["render_id"], ["render_outputs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["qc_report_id"], ["render_qc_reports.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        # The render loop is bounded in code by `LIFECYCLE_MAX_RENDER_ATTEMPTS`; these say the
        # counters can only ever count forward, so a bug that decremented one would be refused
        # by the database rather than silently buying itself another render.
        sa.CheckConstraint("render_attempts >= 0", name="ck_content_project_render_attempts"),
        sa.CheckConstraint("step_attempts >= 0", name="ck_content_project_step_attempts"),
        # A project that claims a preview must name the render it is a preview of. Stated in the
        # schema because `preview_ready` is the state slice 2F will act on.
        sa.CheckConstraint(
            "state <> 'preview_ready' OR render_id IS NOT NULL",
            name="ck_content_project_preview_has_render",
        ),
        # Terminal states are never claimed, so they must not carry a due time; live states must.
        sa.CheckConstraint(
            "(state IN ('preview_ready', 'failed')) = (next_check_at IS NULL)",
            name="ck_content_project_due_matches_state",
        ),
    )
    op.create_index(
        "ix_content_projects_business_created",
        "content_projects",
        ["business_id", "created_at", "id"],
    )
    op.create_index(
        "ix_content_projects_business_state", "content_projects", ["business_id", "state"]
    )
    op.create_index(
        "ix_content_projects_due",
        "content_projects",
        ["next_check_at", "id"],
        postgresql_where=sa.text("state NOT IN ('preview_ready', 'failed')"),
    )

    op.create_table(
        "content_project_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_state", state, nullable=True),
        sa.Column("to_state", state, nullable=False),
        sa.Column("event", event, nullable=False),
        sa.Column("reason", sa.String(length=96), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["content_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "project_id", "sequence", name="uq_content_project_transition_sequence"
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_content_project_transition_sequence"),
        # The only entry without a predecessor is §20's entry arrow into `planned`.
        sa.CheckConstraint(
            "from_state IS NOT NULL OR event = 'created'",
            name="ck_content_project_transition_origin",
        ),
    )
    op.create_index(
        "ix_content_project_transitions_project",
        "content_project_transitions",
        ["business_id", "project_id", "sequence"],
    )

    op.add_column(
        "render_outputs",
        sa.Column("qc_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Every render that already has a report has been claimed by definition. Without this the
    # new predicate would offer the whole history to automatic QC on the next tick.
    op.execute(
        sa.text(
            "UPDATE render_outputs SET qc_claimed_at = reports.created_at "
            "FROM (SELECT render_id, min(created_at) AS created_at FROM render_qc_reports "
            "GROUP BY render_id) AS reports "
            "WHERE reports.render_id = render_outputs.id"
        )
    )
    op.create_index(
        "ix_render_outputs_awaiting_qc",
        "render_outputs",
        ["completed_at", "id"],
        postgresql_where=sa.text("status = 'succeeded' AND qc_claimed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_render_outputs_awaiting_qc", table_name="render_outputs")
    op.drop_column("render_outputs", "qc_claimed_at")
    op.drop_index(
        "ix_content_project_transitions_project", table_name="content_project_transitions"
    )
    op.drop_table("content_project_transitions")
    op.drop_index("ix_content_projects_due", table_name="content_projects")
    op.drop_index("ix_content_projects_business_state", table_name="content_projects")
    op.drop_index("ix_content_projects_business_created", table_name="content_projects")
    op.drop_table("content_projects")
    postgresql.ENUM(name="content_project_event").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="content_project_state").drop(op.get_bind(), checkfirst=True)
