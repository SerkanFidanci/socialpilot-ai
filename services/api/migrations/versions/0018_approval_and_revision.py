"""Add `content_approvals` + `content_revisions` and reopen `preview_ready` — slice 2F.

Two tables, five new enum types, five new project columns, and one reshaping that is the reason
this migration is not simply additive: **`preview_ready` stops being a terminal state.** Slice 2E
made it terminal because approval did not exist; PRD §21 puts a decision after the preview, so the
sequencer now passes through that state and the terminal set becomes `approved`, `failed` and
`cancelled`. Everything that encoded the old set has to move with it — the partial claim index,
the constraint tying a due time to terminality, and the constraint requiring a preview to name its
render.

`content_project_state` and `content_project_event` are **replaced rather than extended**.
`ALTER TYPE ... ADD VALUE` is the cheaper move and this repository has used it before (`0005`,
`0011`), but it cannot be used here: Alembic runs a migration inside one transaction, and
PostgreSQL refuses to *use* an enum value added in the transaction that added it — which is
exactly what the new check constraints and the new index predicate do. Rewriting those predicates
to compare `state::text` would dodge the error and cost more than it saved: the claim's partial
index only helps if its predicate matches the query's, and slice 2E measured what happens when it
does not. Swapping the type keeps everything in one transaction and, unlike `ADD VALUE`, gives the
downgrade something honest to do.

Two backfills, and both are load-bearing. Every project already sitting in `preview_ready` is
given a due time, because the state it is in is no longer terminal and the reshaped constraint
would otherwise reject rows the previous migration wrote. Each is also stamped with
`preview_delivered_at`: that project already consumed its credit, and a project whose delivery is
not recorded would, on a later failed revision, ask the ledger to refund a preview the customer
already has — which the ledger correctly refuses as a contradiction, leaving the project stuck.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_approval_and_revision"
down_revision: str | None = "0017_entitlement_ledger"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TIMESTAMP_DEFAULT = sa.text("timezone('utc', now())")

# PRD §20's states as of slice 2F, and as of slice 2E. Both are written out because the type is
# swapped in each direction, and a reversal that guessed its own target would be a reversal
# nobody could check.
_STATES_2E = (
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
_STATES_2F = (*_STATES_2E, "waiting_approval", "revision_requested", "approved", "cancelled")

_EVENTS_2E = (
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
_EVENTS_2F = (
    *_EVENTS_2E,
    "approval_required",
    "auto_approved",
    "approved",
    "rejected",
    "revision_scoped_to_script",
    "revision_scoped_to_voice",
    "revision_scoped_to_timeline",
    "cancelled",
)

_APPROVAL_POLICIES = (
    "always",
    "campaign_only",
    "price_or_discount_only",
    "ads_only",
    "first_n_contents",
    "low_confidence_only",
    "never_within_guardrails",
)
_APPROVAL_DECISIONS = ("approved", "auto_approved", "rejected")
_REJECTION_REASONS = (
    "wrong_product",
    "wrong_price",
    "wrong_cut",
    "off_brand_tone",
    "unsuitable_voice",
    "unsuitable_music",
    "wrong_length",
    "low_quality",
    "new_concept",
    "other",
)
_REVISION_CLASSES = ("minor", "major")
_REVISION_SCOPES = ("script", "voice", "timeline")

# The states a project is over in, before and after this migration. Written as SQL fragments so
# the three places that need them — two check constraints and one index predicate — cannot
# disagree about which states are finished.
_TERMINAL_2E = "'preview_ready', 'failed'"
_TERMINAL_2F = "'approved', 'failed', 'cancelled'"
# Where a render must already exist. `waiting_approval` and `approved` join `preview_ready`
# because both are states a person reached by looking at a rendered video.
_RENDERED_2E = "'preview_ready'"
_RENDERED_2F = "'preview_ready', 'waiting_approval', 'approved'"

_STATE_COLUMNS = (
    ("content_projects", "state"),
    ("content_project_transitions", "from_state"),
    ("content_project_transitions", "to_state"),
)
_EVENT_COLUMNS = (("content_project_transitions", "event"),)


def upgrade() -> None:
    _drop_state_dependants()
    _drop_event_dependants()
    _swap_enum("content_project_state", _STATES_2F, columns=_STATE_COLUMNS)
    _swap_enum("content_project_event", _EVENTS_2F, columns=_EVENT_COLUMNS)
    # Before the reshaped constraint exists, not after: `preview_ready` is a live state now, and
    # a live state must carry a due time. The sequencer picks these up and applies §21.1's policy
    # to them, which is the correct treatment of a preview produced before approval existed.
    op.execute(
        sa.text(
            "UPDATE content_projects SET next_check_at = timezone('utc', now()) "
            "WHERE state = 'preview_ready'"
        )
    )
    _create_state_dependants(terminal=_TERMINAL_2F, rendered=_RENDERED_2F)
    _create_event_dependants()

    for name, values in (
        ("content_approval_policy", _APPROVAL_POLICIES),
        ("content_approval_decision", _APPROVAL_DECISIONS),
        ("content_rejection_reason", _REJECTION_REASONS),
        ("content_revision_class", _REVISION_CLASSES),
        ("content_revision_scope", _REVISION_SCOPES),
    ):
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)

    _add_project_columns()

    op.create_table(
        "content_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("decision", _existing("content_approval_decision"), nullable=False),
        sa.Column("policy", _existing("content_approval_policy"), nullable=False),
        sa.Column("rejection_reason", _existing("content_rejection_reason"), nullable=True),
        # The tenant's own words (§21.2). Unbounded `Text` rather than a short varchar because a
        # customer explaining what is wrong with their video is not writing an identifier; the
        # length ceiling belongs at the API boundary, where a limit can carry an error code.
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("render_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(["render_id"], ["render_outputs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "sequence", name="uq_content_approval_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_content_approval_sequence"),
        # A reason belongs to a rejection and to nothing else, in both directions: an approval
        # carrying a rejection reason and a rejection carrying none are equally unreadable.
        sa.CheckConstraint(
            "(decision = 'rejected') = (rejection_reason IS NOT NULL)",
            name="ck_content_approval_reason_matches_decision",
        ),
        # §21.2 makes the free note mandatory for `other`: a closed set with an escape hatch that
        # explains nothing is a closed set with a hole in it.
        sa.CheckConstraint(
            "rejection_reason IS DISTINCT FROM 'other' OR note IS NOT NULL",
            name="ck_content_approval_other_has_note",
        ),
        # A note explains a rejection. Allowing one on an approval would open a second,
        # unreviewed place for tenant prose to accumulate.
        sa.CheckConstraint(
            "note IS NULL OR decision = 'rejected'", name="ck_content_approval_note_is_rejection"
        ),
        # Exactly the automatic decisions have no actor. `audit_logs` names a human for
        # everything a human did, and this column says which decisions those were.
        sa.CheckConstraint(
            "(decision = 'auto_approved') = (actor_user_id IS NULL)",
            name="ck_content_approval_actor_matches_decision",
        ),
    )
    op.create_index(
        "ix_content_approvals_project",
        "content_approvals",
        ["business_id", "project_id", "sequence"],
    )
    op.create_index(
        "ix_content_approvals_business_created",
        "content_approvals",
        ["business_id", "created_at", "id"],
    )

    op.create_table(
        "content_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("revision_class", _existing("content_revision_class"), nullable=False),
        sa.Column("scope", _existing("content_revision_scope"), nullable=False),
        sa.Column("quota_cost", sa.Integer(), nullable=False),
        sa.Column("quota_used_after", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["content_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_id"], ["content_approvals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "sequence", name="uq_content_revision_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_content_revision_sequence"),
        # A revision that costs nothing would be an unbounded loop wearing a receipt.
        sa.CheckConstraint("quota_cost >= 1", name="ck_content_revision_cost"),
        sa.CheckConstraint("quota_used_after >= quota_cost", name="ck_content_revision_running"),
    )
    op.create_index(
        "ix_content_revisions_project",
        "content_revisions",
        ["business_id", "project_id", "sequence"],
    )


def downgrade() -> None:
    # Before a single object is dropped: can the reversal keep its data? A project in a state
    # slice 2E cannot express has no honest reversal — dropping the value would rewrite what
    # happened to somebody's content. Refuse instead of guess, the same answer `0011` gives.
    _refuse_downgrade_that_cannot_keep_its_data()

    op.drop_index("ix_content_revisions_project", table_name="content_revisions")
    op.drop_table("content_revisions")
    op.drop_index("ix_content_approvals_business_created", table_name="content_approvals")
    op.drop_index("ix_content_approvals_project", table_name="content_approvals")
    op.drop_table("content_approvals")

    for name in (
        "content_revision_scope",
        "content_revision_class",
        "content_rejection_reason",
        "content_approval_decision",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

    _drop_project_columns()
    # Dropped only after the project column that uses it is gone.
    postgresql.ENUM(name="content_approval_policy").drop(op.get_bind(), checkfirst=True)

    _drop_state_dependants()
    _drop_event_dependants()
    _swap_enum("content_project_event", _EVENTS_2E, columns=_EVENT_COLUMNS)
    _swap_enum("content_project_state", _STATES_2E, columns=_STATE_COLUMNS)
    # `preview_ready` is terminal again, and slice 2E's constraint requires a terminal state to
    # carry no due time. Undone before that constraint is recreated, for the same reason the
    # upgrade's backfill happens before its own.
    op.execute(
        sa.text(
            "UPDATE content_projects SET next_check_at = NULL "
            "WHERE state IN ('preview_ready', 'failed')"
        )
    )
    _create_state_dependants(terminal=_TERMINAL_2E, rendered=_RENDERED_2E)
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
    not have. Nullability and NOT NULL survive an `ALTER COLUMN ... TYPE` untouched; none of these
    columns carries a server default, so none is dropped and none is silently re-added.
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
        sa.text(
            "SELECT count(*) FROM content_projects WHERE state::text IN "
            "('waiting_approval', 'revision_requested', 'approved', 'cancelled')"
        )
    )
    remaining = int(stranded.scalar_one())
    if remaining:
        raise RuntimeError(
            f"downgrade would discard {remaining} project(s) in a slice 2F state; "
            "resolve or delete them before reversing 0018"
        )


def _drop_state_dependants() -> None:
    """Remove everything whose definition mentions a `content_project_state` literal."""

    op.drop_constraint("ck_content_project_due_matches_state", "content_projects", type_="check")
    op.drop_constraint("ck_content_project_preview_has_render", "content_projects", type_="check")
    op.drop_index("ix_content_projects_due", table_name="content_projects")


def _create_state_dependants(*, terminal: str, rendered: str) -> None:
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


def _add_project_columns() -> None:
    # Added with a server default so existing rows get one, then stripped of it: the application
    # always supplies both values, and a lingering default would let a future insert that forgot
    # them look deliberate.
    op.add_column(
        "content_projects",
        sa.Column(
            "approval_policy",
            _existing("content_approval_policy"),
            nullable=False,
            # `always` for rows that predate the column — not the deployment default. A project
            # opened before approval existed carries no recorded intent, and asking a person is
            # the answer that cannot publish something nobody chose to publish.
            server_default=sa.text("'always'"),
        ),
    )
    op.alter_column("content_projects", "approval_policy", server_default=None)
    op.add_column(
        "content_projects",
        sa.Column("revision_quota", sa.Integer(), nullable=False, server_default=sa.text("3")),
    )
    op.alter_column("content_projects", "revision_quota", server_default=None)
    op.add_column(
        "content_projects",
        sa.Column("revisions_requested", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "content_projects",
        sa.Column("revision_quota_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "content_projects",
        sa.Column("preview_delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_content_project_revision_counters",
        "content_projects",
        "revisions_requested >= 0 AND revision_quota_used >= 0 AND revision_quota >= 0",
    )
    # §12.3's allowance, stated in the schema. The service refuses a revision whose cost would
    # cross the line; this makes crossing it inexpressible even if the service did not.
    op.create_check_constraint(
        "ck_content_project_revision_within_quota",
        "content_projects",
        "revision_quota_used <= revision_quota",
    )
    # A project already sitting on a preview has delivered it — the ledger consumed its credit at
    # that moment — and this is the column every later settlement reads.
    op.execute(
        sa.text(
            "UPDATE content_projects SET preview_delivered_at = updated_at "
            "WHERE state = 'preview_ready'"
        )
    )


def _drop_project_columns() -> None:
    op.drop_constraint(
        "ck_content_project_revision_within_quota", "content_projects", type_="check"
    )
    op.drop_constraint("ck_content_project_revision_counters", "content_projects", type_="check")
    op.drop_column("content_projects", "preview_delivered_at")
    op.drop_column("content_projects", "revision_quota_used")
    op.drop_column("content_projects", "revisions_requested")
    op.drop_column("content_projects", "revision_quota")
    op.drop_column("content_projects", "approval_policy")
