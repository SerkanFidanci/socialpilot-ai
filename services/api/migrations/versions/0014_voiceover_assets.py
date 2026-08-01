"""Add `voiceover_assets` (PRD §28.5, §14.8) — slice 2C.

The table follows `content_scripts`: the row is written in `pending` with its route snapshot
*before* the first provider call (ADR-007), so the columns have to make sense for a run that
never settled. `total_duration_ms`, `drift_ms`, `completed_at` and `provider_usage_id` are
therefore nullable, and `status` says which of the three states the run reached.

`segments` is JSONB rather than a child table. PRD §28.5 names one table; the per-line records
are written and read as a set in one transaction; and their shape is a contract
(`VoiceoverSegment.as_document`) rather than a query surface. Everything a later slice filters
or joins on — status, measured total, drift, voice profile version, route and usage references —
is a real column for exactly that reason.

`script_id` is RESTRICT: the script is the record of what was said and the audio is the record
of how it was said, so deleting one must not orphan the other.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_voiceover_assets"
down_revision: str | None = "0013_script_generation"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TIMESTAMP_DEFAULT = sa.text("timezone('utc', now())")

_VOICEOVER_STATUSES = ("pending", "generated", "failed")


def upgrade() -> None:
    voiceover_status = postgresql.ENUM(
        *_VOICEOVER_STATUSES, name="voiceover_status", create_type=False
    )
    voiceover_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "voiceover_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("script_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", voiceover_status, nullable=False),
        sa.Column("voice_profile_code", sa.String(length=64), nullable=False),
        sa.Column("voice_profile_version", sa.Integer(), nullable=False),
        sa.Column("voice_profile", postgresql.JSONB(), nullable=False),
        sa.Column("audio_format", sa.String(length=16), nullable=False),
        sa.Column("segments", postgresql.JSONB(), nullable=False),
        sa.Column("total_duration_ms", sa.Integer(), nullable=True),
        sa.Column("target_duration_ms", sa.Integer(), nullable=True),
        sa.Column("drift_ms", sa.Integer(), nullable=True),
        sa.Column("route_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("provider_usage_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_code", sa.String(length=96), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["script_id"], ["content_scripts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["provider_usage_id"], ["provider_usage.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        # A measured duration is a duration; a negative one would mean the probe was not the
        # source. Total is checked rather than each segment because the segments live in JSONB.
        sa.CheckConstraint(
            "total_duration_ms IS NULL OR total_duration_ms >= 0",
            name="ck_voiceover_total_duration_non_negative",
        ),
    )
    op.create_index(
        "ix_voiceover_assets_business_created",
        "voiceover_assets",
        ["business_id", "created_at", "id"],
    )
    op.create_index(
        "ix_voiceover_assets_business_script", "voiceover_assets", ["business_id", "script_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_voiceover_assets_business_script", table_name="voiceover_assets")
    op.drop_index("ix_voiceover_assets_business_created", table_name="voiceover_assets")
    op.drop_table("voiceover_assets")
    postgresql.ENUM(name="voiceover_status").drop(op.get_bind(), checkfirst=True)
