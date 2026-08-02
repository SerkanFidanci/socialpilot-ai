"""Add `usage_reservations` + `credit_ledger` and make the ledger append-only — slice W20.

Two tables and two triggers, and the triggers are the interesting part.

`credit_ledger` is PRD §32.4's ledger with one sketched column deliberately absent. §32.4 lists
`balance_after`; keeping a running total on each row means the writes have to be totally ordered
to produce it and stores an answer the entries already give. When such a column disagrees with
the entries there is no way to tell which was ever right. The balance is `SUM(delta_credits)`,
and ADR-017 records the reasoning.

What replaces `balance_after` as a guarantee is `trg_credit_ledger_non_negative`: PRD §32.4's
"Negatif bakiye oluşmamalıdır" enforced by the database rather than by whoever remembers to check.
It is a backstop, not the mechanism — the application takes a transaction-scoped advisory lock on
the tenant so the read-decide-write sequence is atomic, and the trigger only sees committed rows.
Without the lock two concurrent transactions could both pass it; with the lock they serialise and
it is exact. Its job is to catch a code path that forgot the lock, and it makes forgetting fatal
rather than expensive.

`trg_credit_ledger_append_only` is the other half of the same idea. "Append-only" as a convention
is a comment; as a rule that refuses `UPDATE` and `DELETE` it is a property. An edited entry is a
balance that moved with no record of the move, which is the one failure a ledger exists to
prevent. Corrections are new entries. `TRUNCATE` does not fire row triggers, so test teardown is
unaffected; a cascade from a deleted business row *would* fire it and fail, which is correct — a
tenant's entitlement history is not something to erase as a side effect.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_entitlement_ledger"
down_revision: str | None = "0016_content_projects"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TIMESTAMP_DEFAULT = sa.text("timezone('utc', now())")

_RESERVATION_STATUSES = ("reserved", "consumed", "released")
_ENTRY_TYPES = ("grant", "consume", "refund", "expire")

_NON_NEGATIVE_FUNCTION = """
CREATE FUNCTION credit_ledger_reject_negative_balance() RETURNS trigger AS $$
DECLARE
    running bigint;
BEGIN
    IF NEW.delta_credits >= 0 THEN
        RETURN NEW;
    END IF;
    SELECT COALESCE(SUM(delta_credits), 0) INTO running
    FROM credit_ledger
    WHERE business_id = NEW.business_id;
    IF running + NEW.delta_credits < 0 THEN
        RAISE EXCEPTION
            'credit_ledger balance would go negative for business %', NEW.business_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_APPEND_ONLY_FUNCTION = """
CREATE FUNCTION credit_ledger_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'credit_ledger is append-only; write a compensating entry instead'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    reservation_status = postgresql.ENUM(
        *_RESERVATION_STATUSES, name="usage_reservation_status", create_type=False
    )
    entry_type = postgresql.ENUM(*_ENTRY_TYPES, name="credit_entry_type", create_type=False)
    reservation_status.create(op.get_bind(), checkfirst=True)
    entry_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "usage_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", reservation_status, nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("points_table_version", sa.Integer(), nullable=False),
        sa.Column("point_kind", sa.String(length=48), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=96), nullable=True),
        sa.CheckConstraint("credits > 0", name="ck_usage_reservation_credits_positive"),
        sa.CheckConstraint(
            "(status = 'reserved') = (settled_at IS NULL)",
            name="ck_usage_reservation_settled_at",
        ),
        sa.UniqueConstraint("business_id", "idempotency_key", name="uq_usage_reservation_key"),
    )
    op.create_index(
        "ix_usage_reservations_business_created",
        "usage_reservations",
        ["business_id", "created_at", "id"],
    )
    op.create_index(
        "ix_usage_reservations_business_status", "usage_reservations", ["business_id", "status"]
    )
    op.create_index(
        "ix_usage_reservations_source",
        "usage_reservations",
        ["business_id", "source_type", "source_id"],
    )
    # Partial over open holds: settled history, however long, contributes nothing to the sweep.
    op.create_index(
        "ix_usage_reservations_open",
        "usage_reservations",
        ["created_at", "id"],
        postgresql_where=sa.text("status = 'reserved'"),
    )

    op.create_table(
        "credit_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entry_type", entry_type, nullable=False),
        sa.Column("delta_credits", sa.Integer(), nullable=False),
        sa.Column("points_table_version", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "reservation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("usage_reservations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=96), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_TIMESTAMP_DEFAULT,
            nullable=False,
        ),
        sa.CheckConstraint(
            "(entry_type IN ('grant', 'refund') AND delta_credits > 0) "
            "OR (entry_type IN ('consume', 'expire') AND delta_credits < 0)",
            name="ck_credit_ledger_delta_sign",
        ),
        sa.CheckConstraint(
            "entry_type <> 'consume' OR points_table_version IS NOT NULL",
            name="ck_credit_ledger_consume_versioned",
        ),
        sa.CheckConstraint(
            "entry_type <> 'consume' OR reservation_id IS NOT NULL",
            name="ck_credit_ledger_consume_reserved",
        ),
    )
    op.create_index(
        "ix_credit_ledger_business_created", "credit_ledger", ["business_id", "created_at", "id"]
    )
    op.create_index("ix_credit_ledger_reservation", "credit_ledger", ["reservation_id"])
    # A second refund for one reservation is refused by the database, not only by the service
    # that is supposed to check first.
    op.create_index(
        "uq_credit_ledger_idempotency",
        "credit_ledger",
        ["business_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.execute(sa.text(_NON_NEGATIVE_FUNCTION))
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_credit_ledger_non_negative BEFORE INSERT ON credit_ledger "
            "FOR EACH ROW EXECUTE FUNCTION credit_ledger_reject_negative_balance()"
        )
    )
    op.execute(sa.text(_APPEND_ONLY_FUNCTION))
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_credit_ledger_append_only BEFORE UPDATE OR DELETE ON credit_ledger "
            "FOR EACH ROW EXECUTE FUNCTION credit_ledger_reject_mutation()"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_credit_ledger_append_only ON credit_ledger"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_credit_ledger_non_negative ON credit_ledger"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS credit_ledger_reject_mutation()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS credit_ledger_reject_negative_balance()"))
    op.drop_index("uq_credit_ledger_idempotency", table_name="credit_ledger")
    op.drop_index("ix_credit_ledger_reservation", table_name="credit_ledger")
    op.drop_index("ix_credit_ledger_business_created", table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.drop_index("ix_usage_reservations_open", table_name="usage_reservations")
    op.drop_index("ix_usage_reservations_source", table_name="usage_reservations")
    op.drop_index("ix_usage_reservations_business_status", table_name="usage_reservations")
    op.drop_index("ix_usage_reservations_business_created", table_name="usage_reservations")
    op.drop_table("usage_reservations")
    postgresql.ENUM(name="credit_entry_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="usage_reservation_status").drop(op.get_bind(), checkfirst=True)
