"""Add tenant-safe media ingest inspection and retry state.

Revision ID: 0005_media_ingest_foundation
Revises: 0004_operational_reliability
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_media_ingest_foundation"
down_revision: str | None = "0004_operational_reliability"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

malware_status = postgresql.ENUM(
    "clean",
    "infected",
    "unavailable",
    "indeterminate",
    name="media_malware_scan_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for value in ("rejected", "quarantined"):
        op.execute(f"ALTER TYPE media_asset_status ADD VALUE IF NOT EXISTS '{value}'")
    for value in ("validating", "scanning", "ready_for_analysis", "rejected", "failed", "dead"):
        op.execute(f"ALTER TYPE media_ingest_status ADD VALUE IF NOT EXISTS '{value}'")
    malware_status.create(bind, checkfirst=True)
    op.add_column("jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE jobs SET next_attempt_at = requested_at WHERE status = 'queued'")
    op.create_index(
        "ix_jobs_ingest_claim",
        "jobs",
        ["job_type", "status", "next_attempt_at", "requested_at"],
    )
    op.create_index(
        "uq_jobs_media_ingest_resource",
        "jobs",
        ["business_id", "job_type", "resource_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("job_type = 'media.ingest'"),
    )
    op.create_table(
        "media_ingest_inspections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_byte_size", sa.Integer(), nullable=False),
        sa.Column("storage_content_type", sa.String(length=127), nullable=False),
        sa.Column("storage_sha256_checksum", sa.String(length=64), nullable=False),
        sa.Column("storage_etag", sa.String(length=512), nullable=False),
        sa.Column("detected_content_type", sa.String(length=127), nullable=False),
        sa.Column(
            "inspected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("storage_byte_size > 0", name="ck_media_ingest_inspection_size"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "asset_id", name="uq_media_ingest_inspection_asset"),
    )
    op.create_index(
        "ix_media_ingest_inspections_business_asset",
        "media_ingest_inspections",
        ["business_id", "asset_id"],
    )
    op.create_table(
        "media_malware_scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", malware_status, nullable=False),
        sa.Column("scanner_name", sa.String(length=64), nullable=False),
        sa.Column("safe_error_code", sa.String(length=96), nullable=True),
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "asset_id", name="uq_media_malware_scan_asset"),
    )
    op.create_index(
        "ix_media_malware_scans_business_asset",
        "media_malware_scans",
        ["business_id", "asset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_media_malware_scans_business_asset", table_name="media_malware_scans")
    op.drop_table("media_malware_scans")
    op.drop_index(
        "ix_media_ingest_inspections_business_asset", table_name="media_ingest_inspections"
    )
    op.drop_table("media_ingest_inspections")
    op.drop_index("uq_jobs_media_ingest_resource", table_name="jobs")
    op.drop_index("ix_jobs_ingest_claim", table_name="jobs")
    op.drop_column("jobs", "next_attempt_at")
    bind = op.get_bind()
    malware_status.drop(bind, checkfirst=True)

    op.execute(
        "ALTER TABLE media_assets ALTER COLUMN ingest_status TYPE text USING ingest_status::text"
    )
    op.execute("UPDATE media_assets SET ingest_status = 'pending' WHERE ingest_status <> 'pending'")
    op.execute("DROP TYPE media_ingest_status")
    op.execute("CREATE TYPE media_ingest_status AS ENUM ('pending')")
    op.execute(
        "ALTER TABLE media_assets ALTER COLUMN ingest_status TYPE media_ingest_status "
        "USING ingest_status::media_ingest_status"
    )

    op.execute("ALTER TABLE media_assets ALTER COLUMN status TYPE text USING status::text")
    op.execute(
        "UPDATE media_assets SET status = 'uploaded' WHERE status NOT IN ('uploading', 'uploaded')"
    )
    op.execute("DROP TYPE media_asset_status")
    op.execute("CREATE TYPE media_asset_status AS ENUM ('uploading', 'uploaded')")
    op.execute(
        "ALTER TABLE media_assets ALTER COLUMN status TYPE media_asset_status "
        "USING status::media_asset_status"
    )
