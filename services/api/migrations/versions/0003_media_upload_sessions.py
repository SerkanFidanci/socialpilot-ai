"""Create tenant-scoped media assets and upload sessions.

Revision ID: 0003_media_upload_sessions
Revises: 0002_identity_and_businesses
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_media_upload_sessions"
down_revision: str | None = "0002_identity_and_businesses"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

asset_status = postgresql.ENUM(
    "uploading", "uploaded", name="media_asset_status", create_type=False
)
ingest_status = postgresql.ENUM("pending", name="media_ingest_status", create_type=False)
session_status = postgresql.ENUM(
    "created",
    "completed",
    "cancelled",
    "expired",
    name="media_upload_session_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    asset_status.create(bind, checkfirst=True)
    ingest_status.create(bind, checkfirst=True)
    session_status.create(bind, checkfirst=True)
    op.create_table(
        "media_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_object_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=127), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256_checksum", sa.String(length=64), nullable=False),
        sa.Column("status", asset_status, nullable=False),
        sa.Column("ingest_status", ingest_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_object_key"),
    )
    op.create_index("ix_media_assets_business_status", "media_assets", ["business_id", "status"])
    op.create_table(
        "media_upload_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_upload_id", sa.String(length=128), nullable=False),
        sa.Column("expected_part_count", sa.Integer(), nullable=False),
        sa.Column("status", session_status, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_upload_id"),
        sa.UniqueConstraint("business_id", "asset_id", name="uq_media_upload_session_asset"),
    )
    op.create_index(
        "ix_media_upload_sessions_business_status",
        "media_upload_sessions",
        ["business_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_media_upload_sessions_business_status", table_name="media_upload_sessions")
    op.drop_table("media_upload_sessions")
    op.drop_index("ix_media_assets_business_status", table_name="media_assets")
    op.drop_table("media_assets")
    bind = op.get_bind()
    session_status.drop(bind, checkfirst=True)
    ingest_status.drop(bind, checkfirst=True)
    asset_status.drop(bind, checkfirst=True)
