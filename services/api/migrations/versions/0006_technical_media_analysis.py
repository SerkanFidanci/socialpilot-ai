"""Create technical media analysis and derivative records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_technical_media_analysis"
down_revision: str | None = "0005_media_ingest_foundation"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

analysis_status = postgresql.ENUM(
    "pending",
    "running",
    "completed",
    "failed",
    "dead",
    name="technical_analysis_status",
    create_type=False,
)
derivative_status = postgresql.ENUM(
    "pending", "ready", "failed", name="media_derivative_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    analysis_status.create(bind, checkfirst=True)
    derivative_status.create(bind, checkfirst=True)
    op.create_table(
        "media_technical_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", analysis_status, nullable=False),
        sa.Column("safe_error_code", sa.String(96)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", "asset_id", name="uq_media_technical_analysis_asset"),
    )
    op.create_index(
        "ix_media_technical_analyses_business_status",
        "media_technical_analyses",
        ["business_id", "status"],
    )
    op.create_table(
        "media_technical_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("container_format", sa.String(64), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("video_codec", sa.String(64)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("display_aspect_ratio", sa.String(32)),
        sa.Column("frame_rate_numerator", sa.Integer()),
        sa.Column("frame_rate_denominator", sa.Integer()),
        sa.Column("bit_rate", sa.Integer()),
        sa.Column("rotation_degrees", sa.Integer(), nullable=False),
        sa.Column("has_audio", sa.Boolean(), nullable=False),
        sa.Column("audio_codec", sa.String(64)),
        sa.Column("audio_sample_rate", sa.Integer()),
        sa.Column("audio_channel_count", sa.Integer()),
        sa.Column("stream_count", sa.Integer(), nullable=False),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", "asset_id", name="uq_media_technical_metadata_asset"),
    )
    op.create_index(
        "ix_media_technical_metadata_business_asset",
        "media_technical_metadata",
        ["business_id", "asset_id"],
    )
    op.create_table(
        "media_derivatives",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("storage_object_key", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(127), nullable=False),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("sha256_checksum", sa.String(64)),
        sa.Column("status", derivative_status, nullable=False),
        sa.Column("safe_error_code", sa.String(96)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", "asset_id", "kind", name="uq_media_derivative_kind"),
        sa.UniqueConstraint("storage_object_key"),
    )
    op.create_index(
        "ix_media_derivatives_business_asset", "media_derivatives", ["business_id", "asset_id"]
    )
    op.create_index(
        "uq_jobs_technical_analysis_resource",
        "jobs",
        ["business_id", "job_type", "resource_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("job_type = 'media.technical_analysis'"),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_technical_analysis_resource", table_name="jobs")
    op.drop_index("ix_media_derivatives_business_asset", table_name="media_derivatives")
    op.drop_table("media_derivatives")
    op.drop_index(
        "ix_media_technical_metadata_business_asset", table_name="media_technical_metadata"
    )
    op.drop_table("media_technical_metadata")
    op.drop_index(
        "ix_media_technical_analyses_business_status", table_name="media_technical_analyses"
    )
    op.drop_table("media_technical_analyses")
    bind = op.get_bind()
    derivative_status.drop(bind, checkfirst=True)
    analysis_status.drop(bind, checkfirst=True)
