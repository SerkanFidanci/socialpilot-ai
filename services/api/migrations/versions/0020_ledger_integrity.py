"""Move the ledger's invariants out of the callers and into the schema — slice W23.

Slice W20 built a ledger whose integrity was correct but *conditional*: the balance could not go
negative and one reservation could not be refunded twice **as long as every writer took the tenant
advisory lock first and derived its idempotency key the canonical way**. `EntitlementService` does
both. An independent verification round showed that nothing else has to, and found three ways to
write money into the ledger without going through it.

The three, and what each one gets here:

**Two concurrent transactions could both pass the non-negative trigger.** The trigger *reads* the
balance, and a read under READ COMMITTED cannot see the other transaction's uncommitted entry, so
two `consume -5` writes against a balance of 5 both saw 5 and both committed. Reading harder does
not fix that; the writes have to be ordered. So the trigger now takes
`pg_advisory_xact_lock(20020, hashtext(business_id))` — *the same lock the application takes*,
which makes it a no-op on the service path and a genuine barrier on every other path — before it
computes the sum. The second writer waits for the first to commit, then sums a set that contains
it. `entitlement.repository.ADVISORY_LOCK_NAMESPACE` is the Python side of that constant, and an
integration test reads the installed function body back to keep the two from drifting apart.

**The lock alone was not enough, and finding out why is why this migration also adds a table.**
Waiting for a lock does not move a transaction's snapshot. A `REPEATABLE READ` writer takes its
snapshot when its `INSERT` begins — *before* the trigger asks for the lock — so it would queue
behind the winner, acquire the lock, and then sum a set that still did not contain the winner's
row. Both committed and the balance went to `-5` again, this time politely. The same holds for any
mix of isolation levels, because `SERIALIZABLE`'s conflict detection only covers transactions that
are all serialisable.

`entitlement_ledger_anchors` closes it. One row per tenant, holding nothing — `last_write_at` is
not data and nothing reads it — and every ledger insert stamps it. A stamp is an ordinary row
update, so `READ COMMITTED` blocks on it and then re-reads, while `REPEATABLE READ` and
`SERIALIZABLE` raise `could not serialize access due to concurrent update` against a row the
winner already changed. The invariant stops depending on which isolation level a future writer
happened to pick. The anchor deliberately does **not** hold a balance: W20 derives the balance
from the entries and ADR-017 records why, and a counter beside the entries would be the second
source of truth that decision exists to refuse.

**A second refund could be written for one reservation** with a different idempotency key, which
is money created from nothing. `uq_credit_ledger_reservation_entry` makes "one entry of each type
per reservation" a property of the table rather than of the key the writer chose. Refunds are
pinned twice over: `ck_credit_ledger_refund_reserved` refuses a refund that names no reservation
(otherwise the unique index has a `NULL` shaped hole in it), and the insert guard refuses a refund
whose amount is not the amount the reservation holds. Partial refunds do not exist today and the
constraint is deliberately the degenerate form — equality, not "the sum of refunds is within the
hold" — because the sum form would be machinery for a case with no producer.

**A second reservation could be opened for one unit of work.**
`uq_usage_reservations_standing_source` allows one *standing* hold per `(business, source_type,
source_id)`. Released is not standing: a hold that was refunded frees the slot, so a cancelled
project can be started again, which is the behaviour slice 2F needs. A consumed one still stands,
which is decision K4 ("a pure re-render consumes no new entitlement") expressed in the schema
rather than trusted to the caller.

The insert guard also refuses an entry that names *another tenant's* reservation. Tenant isolation
is enforced in every repository query already; here it stops being a property of the queries.

**Existing rows are not rewritten.** Each index has a guard that counts what would violate it and
raises with the count. A migration that repaired a ledger by editing or deleting entries would be
doing the one thing `trg_credit_ledger_append_only` exists to prevent, and the numbers it silently
fixed would be exactly the numbers nobody could reconstruct afterwards. On a database written only
by `EntitlementService` — which is every database we have — all three guards count zero.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0020_ledger_integrity"
down_revision: str | None = "0019_content_planner"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

# Must equal `app.modules.entitlement.repository.ADVISORY_LOCK_NAMESPACE`. A literal because a
# trigger body cannot import Python, so the two are one fact written twice;
# `test_the_trigger_locks_the_same_thing_the_application_locks` reads the installed function
# definition back and fails if they ever drift apart.
_ADVISORY_LOCK_NAMESPACE = 2_0020

_INSERT_GUARD_FUNCTION = f"""
CREATE OR REPLACE FUNCTION credit_ledger_guard_insert() RETURNS trigger AS $$
DECLARE
    running bigint;
    held integer;
BEGIN
    -- PRD §32.4: "Negatif bakiye oluşmamalıdır". Only a charge can break it, so only a charge pays
    -- for the two serialisation steps below. A grant or a refund adds credit; whether it lands
    -- before or after a concurrent charge changes which of two safe answers that charge gets, and
    -- the one it gets when it does not see the credit yet is the conservative one. Checked before
    -- the coherence rules further down so that an entry which is both oversized and incoherent is
    -- reported as the overdraft it is.
    IF NEW.delta_credits < 0 THEN
        -- Every charge against one tenant serialises here, whoever the writer is. The application
        -- already holds this exact lock on its own path, and an advisory lock is re-entrant within
        -- a transaction, so this costs that path nothing. What it buys is that a writer which
        -- never heard of the lock cannot compute a balance another in-flight writer is about to
        -- invalidate: the sum runs only once the transaction ahead of it has committed.
        PERFORM pg_advisory_xact_lock({_ADVISORY_LOCK_NAMESPACE}, hashtext(NEW.business_id::text));

        -- And stamp the tenant's anchor, because waiting for a lock does not move a snapshot. A
        -- REPEATABLE READ writer would otherwise queue behind the winner and then sum a set taken
        -- before the winner existed. Updating a row the winner already updated is the one conflict
        -- every isolation level notices: READ COMMITTED waits and re-reads, stricter ones abort.
        UPDATE entitlement_ledger_anchors SET last_write_at = clock_timestamp()
        WHERE business_id = NEW.business_id;
        IF NOT FOUND THEN
            -- A tenant whose first ledger entry this is. The upsert takes the same row lock, so
            -- two first writes race exactly as two later ones do.
            INSERT INTO entitlement_ledger_anchors (business_id, last_write_at)
            VALUES (NEW.business_id, clock_timestamp())
            ON CONFLICT (business_id) DO UPDATE SET last_write_at = clock_timestamp();
        END IF;

        SELECT COALESCE(SUM(delta_credits), 0) INTO running
        FROM credit_ledger
        WHERE business_id = NEW.business_id;
        IF running + NEW.delta_credits < 0 THEN
            RAISE EXCEPTION
                'credit_ledger balance would go negative for business %', NEW.business_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.reservation_id IS NOT NULL THEN
        SELECT credits INTO held
        FROM usage_reservations
        WHERE id = NEW.reservation_id AND business_id = NEW.business_id;
        IF held IS NULL THEN
            RAISE EXCEPTION
                'credit_ledger entry names a reservation of another business (%)',
                NEW.reservation_id
                USING ERRCODE = 'check_violation';
        END IF;
        -- Only when the sign is already the one the type demands, so that a wrong-signed row is
        -- still reported by `ck_credit_ledger_delta_sign` rather than by this message.
        IF NEW.entry_type = 'refund' AND NEW.delta_credits > 0 AND NEW.delta_credits <> held THEN
            RAISE EXCEPTION
                'credit_ledger refund must return the % credits reservation % holds', held,
                NEW.reservation_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Slice 0017's version, restored on downgrade.
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


def _refuse_to_index_over_rows_that_break_the_rule() -> None:
    """Count what the new constraints would reject, and stop rather than repair it.

    Three counts, three sentences in the error. A ledger with such rows in it has a real problem
    and the fix is a compensating entry written by somebody who knows what happened — not a
    migration guessing which of two refunds was the real one.
    """

    bind = op.get_bind()
    duplicate_entries = int(
        bind.execute(
            sa.text(
                "SELECT COALESCE(SUM(rows - 1), 0) FROM (SELECT count(*) AS rows FROM"
                " credit_ledger WHERE reservation_id IS NOT NULL"
                " GROUP BY reservation_id, entry_type HAVING count(*) > 1) AS duplicated"
            )
        ).scalar_one()
    )
    unbacked_refunds = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM credit_ledger"
                " WHERE entry_type = 'refund' AND reservation_id IS NULL"
            )
        ).scalar_one()
    )
    duplicate_holds = int(
        bind.execute(
            sa.text(
                "SELECT COALESCE(SUM(rows - 1), 0) FROM (SELECT count(*) AS rows FROM"
                " usage_reservations WHERE status <> 'released'"
                " GROUP BY business_id, source_type, source_id HAVING count(*) > 1) AS duplicated"
            )
        ).scalar_one()
    )
    problems = []
    if duplicate_entries:
        problems.append(
            f"{duplicate_entries} ledger entry/entries duplicate a (reservation, entry_type) pair"
        )
    if unbacked_refunds:
        problems.append(f"{unbacked_refunds} refund entry/entries name no reservation")
    if duplicate_holds:
        problems.append(
            f"{duplicate_holds} standing reservation(s) share a (business, source_type, source_id)"
        )
    if problems:
        raise RuntimeError(
            "0020 refuses to enforce the ledger's invariants over rows that already break them: "
            + "; ".join(problems)
            + ". Resolve them with compensating entries before migrating; this migration will not "
            "edit or delete ledger rows."
        )


def upgrade() -> None:
    _refuse_to_index_over_rows_that_break_the_rule()

    # One row per tenant, and it holds nothing. `last_write_at` is written by the insert guard and
    # read by nobody: the row exists so that appending to the ledger is also an *update*, which is
    # the only kind of conflict a snapshot older than the lock can still be made to notice.
    op.create_table(
        "entitlement_ledger_anchors",
        sa.Column(
            "business_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "last_write_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
    )
    # Updated in place on every charge and never widened. The spare page room is what lets those
    # updates stay HOT — a new row version in the same page, with no index maintenance — instead
    # of migrating and dragging the primary key along. SQLAlchemy has no table-level storage
    # parameter option, so this is the one part of the table the model cannot declare.
    op.execute(sa.text("ALTER TABLE entitlement_ledger_anchors SET (fillfactor = 70)"))
    # Existing tenants get theirs now; the guard creates one for a tenant that appears later, so
    # no other module has to know this table is here.
    op.execute(
        sa.text(
            "INSERT INTO entitlement_ledger_anchors (business_id) SELECT id FROM businesses"
            " ON CONFLICT DO NOTHING"
        )
    )

    # A refund with no reservation is credit appearing from nowhere, and it is also the `NULL` that
    # would let the unique index below be satisfied twice. Grants are the only entry type allowed
    # to create credit, and they name no reservation by design.
    op.create_check_constraint(
        "ck_credit_ledger_refund_reserved",
        "credit_ledger",
        "entry_type <> 'refund' OR reservation_id IS NOT NULL",
    )
    # One entry of each kind per reservation: one charge, one refund, and no second of either.
    # `uq_credit_ledger_idempotency` already refused a *replay*; this refuses the same write under
    # a key the writer made up.
    op.create_index(
        "uq_credit_ledger_reservation_entry",
        "credit_ledger",
        ["reservation_id", "entry_type"],
        unique=True,
        postgresql_where=sa.text("reservation_id IS NOT NULL"),
    )
    # One standing hold per unit of work. `released` is excluded so a refunded hold frees the slot
    # — otherwise a cancelled project could never be started again.
    op.create_index(
        "uq_usage_reservations_standing_source",
        "usage_reservations",
        ["business_id", "source_type", "source_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'released'"),
    )

    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_credit_ledger_non_negative ON credit_ledger"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS credit_ledger_reject_negative_balance()"))
    op.execute(sa.text(_INSERT_GUARD_FUNCTION))
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_credit_ledger_insert_guard BEFORE INSERT ON credit_ledger "
            "FOR EACH ROW EXECUTE FUNCTION credit_ledger_guard_insert()"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_credit_ledger_insert_guard ON credit_ledger"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS credit_ledger_guard_insert()"))
    op.execute(sa.text(_NON_NEGATIVE_FUNCTION))
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_credit_ledger_non_negative BEFORE INSERT ON credit_ledger "
            "FOR EACH ROW EXECUTE FUNCTION credit_ledger_reject_negative_balance()"
        )
    )
    op.drop_index("uq_usage_reservations_standing_source", table_name="usage_reservations")
    op.drop_index("uq_credit_ledger_reservation_entry", table_name="credit_ledger")
    op.drop_constraint("ck_credit_ledger_refund_reserved", "credit_ledger", type_="check")
    op.drop_table("entitlement_ledger_anchors")
