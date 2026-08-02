"""The two tables: an append-only ledger, and the reservations that write into it.

**`credit_ledger` is append-only and the database says so.** Migration 0017 installs a trigger
that refuses `UPDATE` and `DELETE` on this table. That is not belt-and-braces over an ORM
convention: the balance is the sum of these rows, so an edited row is a balance that changed with
no record of the change, which is precisely the failure a ledger exists to prevent. A correction
is a new entry, always.

**The balance is never stored.** There is no `balance` column here and no `balance_after` column
on an entry, and PRD §32.4's sketch of one is deliberately not implemented — see
`docs/adr/ADR-017-entitlement-ledger.md`. A running total maintained beside the entries is a
second source of truth that two concurrent consumptions can silently desynchronise, and once it
disagrees with the entries there is no way to tell which one was ever right. `SUM(delta_credits)`
over an indexed, tenant-scoped set is cheap, and it cannot be wrong.

**An open reservation has already reduced the balance.** The `consume` entry is written when the
reservation opens, not when the work finishes. This is what makes two concurrent requests unable
to spend the same last credit without a distributed-transaction argument: the first request's
entry exists before the second request's sum runs. Releasing a reservation writes a compensating
`refund` entry; settling one writes nothing, because the charge already happened.

`usage_reservations` is the mutable half, and it is mutable in exactly one dimension: the status
walks `reserved → consumed | released` once and stops. Nothing about the amount can move.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.entitlement.ledger import CreditEntryType, ReservationStatus
from app.modules.identity.models import Base

# Where a reservation came from. A plain string rather than an enum so publishing (Phase 4) and
# advertising (Phase 5) can consume credits without a migration, exactly as `jobs.job_type` does.
SOURCE_CONTENT_PROJECT = "content_project"
SOURCE_MANUAL_GRANT = "manual_grant"

RESERVATION_RESOURCE_TYPE = "usage_reservation"


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


class UsageReservation(Base):
    """One hold on a tenant's credits, for one unit of work (PRD §12.7, §12.8).

    The row records what was priced and under which table version, so the charge is auditable
    after the prices move. `credits` is resolved once, when the reservation opens, and no code
    path re-derives it from `points_table_version` — that column is a label on history, not an
    input to a later calculation.

    `idempotency_key` is `NOT NULL` and unique per tenant because this is the deduplication that
    matters: a project that is created twice by a replayed request must reserve once. Making it
    optional would leave that guarantee to whoever remembers to pass one.
    """

    __tablename__ = "usage_reservations"
    __table_args__ = (
        CheckConstraint("credits > 0", name="ck_usage_reservation_credits_positive"),
        # A reservation is open exactly while it has no settlement stamp. Stated in the schema so
        # the two facts cannot drift apart in a partially-applied update.
        CheckConstraint(
            "(status = 'reserved') = (settled_at IS NULL)",
            name="ck_usage_reservation_settled_at",
        ),
        UniqueConstraint("business_id", "idempotency_key", name="uq_usage_reservation_key"),
        Index("ix_usage_reservations_business_created", "business_id", "created_at", "id"),
        # PRD §28.9 asks for `usage_reservations(entitlement_window_id, status)`. Windows arrive
        # with the subscription period in Phase 3; the tenant is the scope that exists today and
        # the shape of the index is the same.
        Index("ix_usage_reservations_business_status", "business_id", "status"),
        Index("ix_usage_reservations_source", "business_id", "source_type", "source_id"),
        # The reconciliation sweep's claim. Partial over open reservations: a tenant's settled
        # history contributes nothing to it, however long that history gets.
        Index(
            "ix_usage_reservations_open",
            "created_at",
            "id",
            postgresql_where=text("status = 'reserved'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    status: Mapped[ReservationStatus] = mapped_column(
        _enum(ReservationStatus, "usage_reservation_status")
    )
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    # Which version of PRD §12.4 priced this, and which row of it. Both stored, neither read back
    # as an input: they answer "what was this charged at", nothing else.
    points_table_version: Mapped[int] = mapped_column(Integer, nullable=False)
    point_kind: Mapped[str] = mapped_column(String(48), nullable=False)

    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # A plain UUID rather than a foreign key, like `jobs.resource_id`: the ledger must be able to
    # hold work from modules whose schema it does not depend on.
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    # Every hold is opened on behalf of somebody. `NOT NULL` because that is what makes the
    # settlement auditable: `audit_logs` requires an actor, and a release written by a background
    # sweep still has to name the person whose credit moved.
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # The join to cost. `provider_usage` carries the same correlation id for every paid call a
    # project made, so "which provider spend did this reservation cause" is one query and needs
    # no column on either table pointing at the other.
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Why a released reservation was released. NULL on an open or consumed one.
    failure_code: Mapped[str | None] = mapped_column(String(96), nullable=True)


class CreditLedgerEntry(Base):
    """One immutable movement of credit (PRD §32.4).

    `delta_credits` is signed and the sign is constrained to match `entry_type`, so the balance is
    `SUM(delta_credits)` — one expression, in one place, with no `CASE` that a second query could
    write differently. A row whose sign disagrees with its type cannot be inserted.

    What PRD §32.4 also sketches and this table does not have is `balance_after`. Maintaining a
    per-row running total requires the writes to be totally ordered, and it stores an answer that
    the entries can already give; ADR-017 records the reasoning.
    """

    __tablename__ = "credit_ledger"
    __table_args__ = (
        # The sign is a property of the type. Both halves are stated, so neither an unsigned
        # consume nor a negative grant is representable.
        CheckConstraint(
            "(entry_type IN ('grant', 'refund') AND delta_credits > 0) "
            "OR (entry_type IN ('consume', 'expire') AND delta_credits < 0)",
            name="ck_credit_ledger_delta_sign",
        ),
        # Every charge names the price list it was computed from. Without this a future pricing
        # argument has no answer for entries written before anyone thought to record one.
        CheckConstraint(
            "entry_type <> 'consume' OR points_table_version IS NOT NULL",
            name="ck_credit_ledger_consume_versioned",
        ),
        # And every charge names the reservation that authorised it. A `consume` with no
        # reservation would be a charge nothing can release or explain.
        CheckConstraint(
            "entry_type <> 'consume' OR reservation_id IS NOT NULL",
            name="ck_credit_ledger_consume_reserved",
        ),
        # Covers both reads this table serves: the cursor-paginated history and the balance sum.
        Index("ix_credit_ledger_business_created", "business_id", "created_at", "id"),
        Index("ix_credit_ledger_reservation", "reservation_id"),
        Index(
            "uq_credit_ledger_idempotency",
            "business_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = _business_id()
    entry_type: Mapped[CreditEntryType] = mapped_column(_enum(CreditEntryType, "credit_entry_type"))
    delta_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    points_table_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    # RESTRICT: the reservation is the authorisation for the charge, and a charge whose
    # authorisation was deleted is exactly the row nobody could ever explain.
    reservation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("usage_reservations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "RESERVATION_RESOURCE_TYPE",
    "SOURCE_CONTENT_PROJECT",
    "SOURCE_MANUAL_GRANT",
    "CreditLedgerEntry",
    "UsageReservation",
]
