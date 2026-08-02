"""Add `render_qc_reports` (PRD §19.4) — slice 2D.

The table follows `voiceover_assets`: the row is written `pending` before any measurement runs,
so the columns have to make sense for a run that never settled. What is different is the two
judgement columns. `verdict` and `recommended_path` are `NOT NULL` and are written
`needs_review`/`human_review` on the pending row: a run killed mid-measurement must read as
unreviewed rather than as approved, and a nullable column would have delegated that decision to
whichever query happened to read it. The check constraint below states the same rule a second
time — a pending run may not claim to have passed — so it survives a future writer that forgets.

`thresholds` is a JSONB snapshot rather than a version number alone. A version says which ruleset
ran; only the snapshot says what it compared against, and without that two reports written a
month apart cannot be compared and a threshold changed by accident leaves no trace.

`render_id` is RESTRICT, not CASCADE: the report is the evidence about the output, and deleting
the output must not silently erase what was found in it. There is deliberately no unique
constraint on `render_id` — only a partial unique index over runs still `pending`, so one run at
a time is a database fact while a future re-run against changed thresholds stays possible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_render_qc_reports"
down_revision: str | None = "0014_voiceover_assets"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TIMESTAMP_DEFAULT = sa.text("timezone('utc', now())")

_RUN_STATUSES = ("pending", "completed", "failed")
_VERDICTS = ("passed", "needs_review", "failed")
_PATHS = (
    "none",
    "retry_render",
    "alternative_scene",
    "alternative_provider",
    "human_review",
    "request_new_media",
)


def upgrade() -> None:
    run_status = postgresql.ENUM(*_RUN_STATUSES, name="qc_run_status", create_type=False)
    run_status.create(op.get_bind(), checkfirst=True)
    verdict = postgresql.ENUM(*_VERDICTS, name="qc_verdict", create_type=False)
    verdict.create(op.get_bind(), checkfirst=True)
    remediation = postgresql.ENUM(*_PATHS, name="qc_remediation_path", create_type=False)
    remediation.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "render_qc_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", run_status, nullable=False),
        sa.Column("verdict", verdict, nullable=False),
        sa.Column("recommended_path", remediation, nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        sa.Column("measurement", postgresql.JSONB(), nullable=False),
        sa.Column("qc_version", sa.Integer(), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(), nullable=False),
        sa.Column("route_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("provider_usage_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_code", sa.String(length=96), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["render_id"], ["render_outputs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_usage_id"], ["provider_usage.id"], ondelete="SET NULL"),
        # Fail-closed, stated in the schema: a run that has not finished cannot claim a passing
        # verdict. The service already writes `needs_review` on the pending row; this is the
        # guarantee that survives a future writer who does not.
        sa.CheckConstraint(
            "status <> 'pending' OR verdict <> 'passed'",
            name="ck_render_qc_pending_is_not_passed",
        ),
        # A ruleset version is what makes one report comparable with another.
        sa.CheckConstraint("qc_version >= 1", name="ck_render_qc_version_positive"),
    )
    op.create_index(
        "ix_render_qc_reports_business_created",
        "render_qc_reports",
        ["business_id", "created_at", "id"],
    )
    op.create_index(
        "ix_render_qc_reports_business_render", "render_qc_reports", ["business_id", "render_id"]
    )
    op.create_index(
        "ix_render_qc_reports_business_verdict", "render_qc_reports", ["business_id", "verdict"]
    )
    op.create_index(
        "uq_render_qc_report_pending",
        "render_qc_reports",
        ["render_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_render_qc_report_pending", table_name="render_qc_reports")
    op.drop_index("ix_render_qc_reports_business_verdict", table_name="render_qc_reports")
    op.drop_index("ix_render_qc_reports_business_render", table_name="render_qc_reports")
    op.drop_index("ix_render_qc_reports_business_created", table_name="render_qc_reports")
    op.drop_table("render_qc_reports")
    postgresql.ENUM(name="qc_remediation_path").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="qc_verdict").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="qc_run_status").drop(op.get_bind(), checkfirst=True)
