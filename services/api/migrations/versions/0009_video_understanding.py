"""Add tenant-scoped normalized scene-understanding records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_video_understanding"
down_revision: str | None = "0008_scene_speech_hardening"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    status = postgresql.ENUM(
        "pending",
        "completed",
        "failed",
        "dead",
        name="scene_understanding_status",
        create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "media_scene_understandings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("visual_description", sa.Text(), nullable=False),
        sa.Column("transcript_context", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("labels", postgresql.JSONB(), nullable=False),
        sa.Column("objects", postgresql.JSONB(), nullable=False),
        sa.Column("actions", postgresql.JSONB(), nullable=False),
        sa.Column("visible_text", postgresql.JSONB(), nullable=False),
        sa.Column("dominant_topics", postgresql.JSONB(), nullable=False),
        sa.Column("safety_flags", postgresql.JSONB(), nullable=False),
        sa.Column("quality_signals", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["media_scenes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", "scene_id", name="uq_scene_understanding_scene"),
    )
    op.create_index(
        "ix_scene_understandings_business_asset",
        "media_scene_understandings",
        ["business_id", "asset_id"],
    )
    op.create_index("ix_scene_understandings_asset", "media_scene_understandings", ["asset_id"])
    op.create_index(
        "uq_jobs_video_understanding_resource",
        "jobs",
        ["business_id", "job_type", "resource_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("job_type = 'media.video_understanding'"),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_jobs_video_understanding_resource")
    op.drop_index("ix_scene_understandings_asset", table_name="media_scene_understandings")
    op.drop_index("ix_scene_understandings_business_asset", table_name="media_scene_understandings")
    op.drop_table("media_scene_understandings")
    postgresql.ENUM(name="scene_understanding_status").drop(op.get_bind(), checkfirst=True)
