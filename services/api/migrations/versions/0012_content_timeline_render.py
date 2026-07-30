"""Add the timeline revision and render output tables (PRD §18.2, §19).

`content_timelines` is append-only by design: a parametric edit writes a new revision row
rather than updating the document, so an approval flow can show what changed and an entitlement
audit can prove a re-render generated nothing new. `root_id` anchors a lineage and the first
revision points at itself, which keeps "every revision of this timeline" a single indexed
equality test.

`render_outputs` carries `ai_disclosure_state` and `provenance_state` from its first row. This
slice writes `none` and `absent` respectively — nothing here calls a model — but the columns
exist now because a record written at render time is trustworthy while a back-filled one is
not (Meta has required AI disclosure on FB/IG ads since July 2026; C2PA manifests do not
survive the re-encode this pipeline performs).

**Chain note for the merge (W11 → main).** `down_revision` is `0010_brand_catalog` because W10
had not merged when this branch was cut. W10 owns slot `0011_schema_debt`. Whoever merges
second must re-point this file's `down_revision` at `0011_schema_debt` so the chain stays
linear and single-headed. The two migrations touch disjoint tables — W10 covers
`provider_usage`, media and businesses; this one only adds `content_timelines` and
`render_outputs` — so the re-chain is a one-line change with no data-ordering dependency.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_content_timeline_render"
down_revision: str | None = "0010_brand_catalog"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TIMESTAMP_DEFAULT = sa.text("timezone('utc', now())")

_RENDER_PROFILES = (
    "instagram_reels_1080x1920",
    "instagram_story_1080x1920",
    "instagram_feed_1080x1350",
    "instagram_square_1080x1080",
    "x_video_1280x720",
    "x_vertical_1080x1920",
    "preview_540x960",
)


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    render_profile = _enum("render_profile", *_RENDER_PROFILES)
    render_status = _enum("render_status", "pending", "running", "succeeded", "failed")
    render_trigger = _enum("render_trigger", "initial", "revision")
    disclosure_state = _enum(
        "ai_disclosure_state", "none", "ai_generated", "ai_modified", "unknown"
    )
    provenance_state = _enum(
        "render_provenance_state", "absent", "stripped_pending_reattach", "attached"
    )
    bind = op.get_bind()
    for enum_type in (
        render_profile,
        render_status,
        render_trigger,
        disclosure_state,
        provenance_state,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "content_timelines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["content_timelines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "business_id", "root_id", "revision", name="uq_content_timeline_revision"
        ),
    )
    op.create_index(
        "ix_content_timelines_business_created",
        "content_timelines",
        ["business_id", "created_at", "id"],
    )
    op.create_index("ix_content_timelines_root", "content_timelines", ["business_id", "root_id"])

    op.create_table(
        "render_outputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile", render_profile, nullable=False),
        sa.Column("status", render_status, nullable=False),
        sa.Column("trigger", render_trigger, nullable=False),
        sa.Column("consumes_entitlement", sa.Boolean(), nullable=False),
        sa.Column("master_object_key", sa.String(length=512), nullable=True),
        sa.Column("preview_object_key", sa.String(length=512), nullable=True),
        sa.Column("thumbnail_object_key", sa.String(length=512), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("video_codec", sa.String(length=32), nullable=True),
        sa.Column("audio_codec", sa.String(length=32), nullable=True),
        sa.Column("ai_disclosure_state", disclosure_state, nullable=False),
        sa.Column("provenance_state", provenance_state, nullable=False),
        sa.Column("provenance_manifest_key", sa.String(length=512), nullable=True),
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
        # RESTRICT, not CASCADE: a rendered object outlives nothing. Deleting the revision that
        # produced a published video would erase the record of what was published.
        sa.ForeignKeyConstraint(["timeline_id"], ["content_timelines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_render_outputs_business_created", "render_outputs", ["business_id", "created_at", "id"]
    )
    op.create_index("ix_render_outputs_timeline", "render_outputs", ["business_id", "timeline_id"])


def downgrade() -> None:
    op.drop_index("ix_render_outputs_timeline", table_name="render_outputs")
    op.drop_index("ix_render_outputs_business_created", table_name="render_outputs")
    op.drop_table("render_outputs")
    op.drop_index("ix_content_timelines_root", table_name="content_timelines")
    op.drop_index("ix_content_timelines_business_created", table_name="content_timelines")
    op.drop_table("content_timelines")
    bind = op.get_bind()
    for name in (
        "render_provenance_state",
        "ai_disclosure_state",
        "render_trigger",
        "render_status",
        "render_profile",
    ):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
