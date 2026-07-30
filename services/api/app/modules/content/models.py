"""Persistence for timeline revisions and render outputs.

Two tables, and the shape of the first one is the interesting decision: a timeline revision is
a **new row**, not an update. Slice 2F has to show a reviewer what changed between the version
they rejected and the version they are being asked to approve, and slice 2E has to prove that a
re-render consumed no fresh entitlement. Neither is answerable from a document that was
overwritten in place, and both are free if history is the storage model.

`render_outputs` carries two fields this slice deliberately fills with "nothing happened":
`ai_disclosure_state` and `provenance_state`. Nothing here calls a model, so the honest values
are `none` and `stripped_pending_reattach`. They exist now because a record written from the
first render is trustworthy and a column back-filled after the fact is not — see the notes on
each enum in `render.py`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.content.render import AiDisclosureState, ProvenanceState, RenderProfile
from app.modules.identity.models import Base


class RenderStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RenderTrigger(StrEnum):
    """Why a render ran — the fact entitlement accounting keys off.

    `initial` is the first render of a timeline lineage and is the one that will consume a
    generation right when slice 2E wires the ledger. `revision` is a re-render after a
    parametric patch: no provider was called and no new content was generated, so it draws on
    the revision quota instead (plan §2, PRD §12.8). Recording the reason at render time rather
    than inferring it later is what keeps that rule auditable.
    """

    INITIAL = "initial"
    REVISION = "revision"


def _enum(enum_type: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_type, name=name, values_callable=lambda values: [item.value for item in values]
    )


def _business_id() -> Mapped[UUID]:
    return mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )


class ContentTimeline(Base):
    """One immutable revision of a timeline document (PRD §18.2)."""

    __tablename__ = "content_timelines"
    __table_args__ = (
        UniqueConstraint("business_id", "root_id", "revision", name="uq_content_timeline_revision"),
        Index("ix_content_timelines_business_created", "business_id", "created_at", "id"),
        Index("ix_content_timelines_root", "business_id", "root_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    # The first revision of a lineage points at itself, so "every revision of this timeline" is
    # one indexed equality test rather than a recursive walk.
    root_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_timelines.id", ondelete="SET NULL"),
        nullable=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RenderOutput(Base):
    """One render of one timeline revision, with the objects it produced."""

    __tablename__ = "render_outputs"
    __table_args__ = (
        Index("ix_render_outputs_business_created", "business_id", "created_at", "id"),
        Index("ix_render_outputs_timeline", "business_id", "timeline_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    timeline_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("content_timelines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # The durable job that will do the work. Nullable because the job row is written in the
    # same transaction and a cancelled job may be pruned before its output record is.
    job_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    profile: Mapped[RenderProfile] = mapped_column(_enum(RenderProfile, "render_profile"))
    status: Mapped[RenderStatus] = mapped_column(_enum(RenderStatus, "render_status"))
    trigger: Mapped[RenderTrigger] = mapped_column(_enum(RenderTrigger, "render_trigger"))
    # Derived from `trigger` at creation and stored, not computed on read: the entitlement
    # ledger in slice 2E must be able to audit what the rule decided at the time.
    consumes_entitlement: Mapped[bool] = mapped_column(Boolean, nullable=False)

    master_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    preview_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    thumbnail_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)

    ai_disclosure_state: Mapped[AiDisclosureState] = mapped_column(
        _enum(AiDisclosureState, "ai_disclosure_state")
    )
    provenance_state: Mapped[ProvenanceState] = mapped_column(
        _enum(ProvenanceState, "render_provenance_state")
    )
    # Where a signed C2PA manifest will live once signing exists. Always NULL in this slice.
    provenance_manifest_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    failure_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
