"""Create scene and normalized transcript records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_scene_speech_analysis"
down_revision: str | None = "0006_technical_media_analysis"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

transcript_status = postgresql.ENUM(
    "completed", "no_speech", "failed", "dead", name="transcript_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    transcript_status.create(bind, checkfirst=True)
    op.create_table(
        "media_scenes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_index", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", "asset_id", "scene_index", name="uq_media_scene_index"),
    )
    op.create_index("ix_media_scenes_business_asset", "media_scenes", ["business_id", "asset_id"])
    op.create_table(
        "transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("full_text", sa.String(20_000), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("status", transcript_status, nullable=False),
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
        sa.UniqueConstraint("business_id", "asset_id", name="uq_transcript_asset"),
    )
    op.create_index("ix_transcripts_business_asset", "transcripts", ["business_id", "asset_id"])
    op.create_table(
        "transcript_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(4_000), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("speaker_label", sa.String(128)),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("transcript_id", "segment_index", name="uq_transcript_segment_index"),
    )
    op.create_index(
        "ix_transcript_segments_transcript_index",
        "transcript_segments",
        ["transcript_id", "segment_index"],
    )
    op.create_index(
        "uq_jobs_scene_speech_resource",
        "jobs",
        ["business_id", "job_type", "resource_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("job_type = 'media.scene_speech_analysis'"),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_scene_speech_resource", table_name="jobs")
    op.drop_index("ix_transcript_segments_transcript_index", table_name="transcript_segments")
    op.drop_table("transcript_segments")
    op.drop_index("ix_transcripts_business_asset", table_name="transcripts")
    op.drop_table("transcripts")
    op.drop_index("ix_media_scenes_business_asset", table_name="media_scenes")
    op.drop_table("media_scenes")
    transcript_status.drop(op.get_bind(), checkfirst=True)
