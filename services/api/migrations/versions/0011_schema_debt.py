"""Close four items of accumulated schema debt (W10).

1. ``provider_usage`` — the durable cost-attribution table ADR-007 describes but no migration
   ever created. Columns are exactly the ADR-007 shape (tenant/job/asset/run, capability,
   provider/model, estimated + actual integer-minor cost, currency, duration, outcome,
   correlation id). Token counts, prompts, signed URLs and raw responses have no column here.
2. ``media_upload_sessions.storage_upload_id`` widens from ``String(128)`` to ``String(512)`` so
   it holds a real provider multipart ``UploadId`` directly. This retires the server-owned
   ``_control/`` object the narrow column had forced (ADR-008 + its W10 amendment). ``VARCHAR``
   widening never rewrites or loses data; the downgrade shrinks back and fails loudly (never
   truncates) if any value is longer than 128.
3. ``media_ingest_status`` gains ``ready_for_photo_analysis`` — the unreachable seam for the
   future HEIC/HEIF photo pipeline (K6). No code path produces it yet.
4. ``business_role`` gains ``approver`` (PRD §4). The role is added; it holds no permission until
   the Phase 2 approval resources exist.

Downgrade reverses all four. The two enum reversals recreate the type without the added value,
following the technique migration 0005 established; both added values are unreachable, so no row
ever holds them and the recreation cannot lose data.

Revision ID: 0011_schema_debt
Revises: 0010_brand_catalog
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_schema_debt"
down_revision: str | None = "0010_brand_catalog"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TIMESTAMP_DEFAULT = sa.text("timezone('utc', now())")


def upgrade() -> None:
    # 2. Widen storage_upload_id to hold the real provider UploadId (removes the control object).
    op.alter_column(
        "media_upload_sessions",
        "storage_upload_id",
        existing_type=sa.String(length=128),
        type_=sa.String(length=512),
        existing_nullable=False,
    )

    # 3 + 4. Additive enum values. IF NOT EXISTS keeps an up -> down -> up cycle idempotent.
    op.execute("ALTER TYPE media_ingest_status ADD VALUE IF NOT EXISTS 'ready_for_photo_analysis'")
    op.execute("ALTER TYPE business_role ADD VALUE IF NOT EXISTS 'approver'")

    # 1. The provider_usage table. job_id/asset_id are plain UUIDs (like jobs.resource_id), not
    # foreign keys; capability is a plain string, not an enum, so a new capability needs no
    # migration. Only business_id is FK-constrained — tenant integrity is mandatory.
    op.create_table(
        "provider_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        # Integer minor units (ADR-007). Never a float: a cost is a count.
        sa.Column("estimated_cost_minor", sa.BigInteger(), nullable=False),
        sa.Column("actual_cost_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.CheckConstraint(
            "estimated_cost_minor >= 0", name="ck_provider_usage_estimated_non_negative"
        ),
        sa.CheckConstraint("actual_cost_minor >= 0", name="ck_provider_usage_actual_non_negative"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_provider_usage_business_created", "provider_usage", ["business_id", "created_at"]
    )
    op.create_index(
        "ix_provider_usage_business_capability", "provider_usage", ["business_id", "capability"]
    )


def downgrade() -> None:
    op.drop_index("ix_provider_usage_business_capability", table_name="provider_usage")
    op.drop_index("ix_provider_usage_business_created", table_name="provider_usage")
    op.drop_table("provider_usage")

    # Reverse the additive enum values by recreating the type without them. Both values are
    # unreachable, so the UPDATE guards below never move a real row.
    op.execute("ALTER TABLE business_members ALTER COLUMN role TYPE text USING role::text")
    op.execute("UPDATE business_members SET role = 'viewer' WHERE role = 'approver'")
    op.execute("DROP TYPE business_role")
    op.execute("CREATE TYPE business_role AS ENUM ('owner', 'admin', 'editor', 'viewer')")
    op.execute(
        "ALTER TABLE business_members ALTER COLUMN role TYPE business_role "
        "USING role::business_role"
    )

    op.execute(
        "ALTER TABLE media_assets ALTER COLUMN ingest_status TYPE text USING ingest_status::text"
    )
    op.execute(
        "UPDATE media_assets SET ingest_status = 'pending' "
        "WHERE ingest_status = 'ready_for_photo_analysis'"
    )
    op.execute("DROP TYPE media_ingest_status")
    op.execute(
        "CREATE TYPE media_ingest_status AS ENUM "
        "('pending', 'validating', 'scanning', 'ready_for_analysis', 'rejected', 'failed', 'dead')"
    )
    op.execute(
        "ALTER TABLE media_assets ALTER COLUMN ingest_status TYPE media_ingest_status "
        "USING ingest_status::media_ingest_status"
    )

    # Shrink storage_upload_id back. VARCHAR shrink preserves every value that still fits and
    # raises (never truncates) on any longer one; dev-only data never exceeds 128.
    op.alter_column(
        "media_upload_sessions",
        "storage_upload_id",
        existing_type=sa.String(length=512),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
