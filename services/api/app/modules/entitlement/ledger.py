"""The pure half of entitlement: entry arithmetic and the two tables that decide a settlement.

Nothing here touches a session, a clock or a provider. Three rules live in this file and each one
is written as a total function over a closed domain, because the alternative — an `if` chain at
the call site — is how a combination nobody thought about becomes free credit or a double charge.

**Signs belong to the entry type, not to the caller.** `signed_credits` is the only place a
magnitude becomes a delta. A ledger row stores that delta, and the balance is its sum; there is
no second expression anywhere that has to agree about which types are negative.

**What a finished job does to its reservation is a table over two dimensions.** How the work
ended (`SourceOutcome`) and, when it failed, what kind of failure it was (`FailureClass`). Both
are closed, the product is enumerated, and an unmapped failure code is not an undefined
combination — it is `UNCLASSIFIED`, which is a case with an answer.

**What may be applied to a reservation is a table over its status.** Settling something already
released is not the same event as settling something already settled: the first is a conflict and
the second is a replay. Collapsing them would either make retries fail or make double-refunds
silent, and the ledger is append-only, so a silent double-refund is permanent.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class CreditEntryType(StrEnum):
    """The closed set of things that can appear in the ledger (PRD §32.4).

    `refund` is credit going back to the tenant. Today it has exactly one producer — a released
    reservation, PRD §12.7's `RESERVED --> RELEASED` — because there is no support surface yet
    that can refund an already-consumed generation (§12.7's `CONSUMED --> REFUNDED`). The entry
    type is the same either way; *why* is on the reservation the entry points at.
    """

    GRANT = "grant"
    CONSUME = "consume"
    REFUND = "refund"
    EXPIRE = "expire"


class ReservationStatus(StrEnum):
    """PRD §12.7's lifecycle, minus the states this slice has no producer for.

    `AVAILABLE` and `EXPIRED` are properties of a *window*, not of a reservation, and
    `ROLLED_OVER` needs the billing period Phase 3 brings. What remains is the part §12.8
    describes step by step: open a reservation, do the work, consume it or let it go.
    """

    RESERVED = "reserved"
    CONSUMED = "consumed"
    RELEASED = "released"


class SourceOutcome(StrEnum):
    """How the work a reservation was opened for ended, in terms entitlement can reason about.

    Deliberately not the content project's state machine. The ledger will price publishing and
    advertising work too, and every one of those has its own states; what entitlement needs to
    know is only whether the thing the credit was for exists. The module that owns the state
    machine maps its terminal states onto these three.
    """

    DELIVERED = "delivered"
    ABANDONED = "abandoned"
    RUNNING = "running"


class SettlementOutcome(StrEnum):
    """What to do with an open reservation."""

    CONSUME = "consume"
    RELEASE = "release"


class SettlementAction(StrEnum):
    """Whether a settlement may be applied to a reservation in a given status."""

    APPLY = "apply"
    ALREADY_APPLIED = "already_applied"
    CONFLICT = "conflict"


class FailureClass(StrEnum):
    """Why the work did not deliver. The dimension PRD §12.8's refund rule is written over."""

    # Ours: a provider failed, a worker died, an encode broke, a step timed out.
    TECHNICAL = "technical"
    # The customer's material could not carry the request — no usable scene, speech longer than
    # the footage. Real work ran, and it produced nothing publishable.
    INPUT = "input"
    # A code no policy names, or no code at all. Present so the function is total: an unmapped
    # failure is a case with an answer, not a hole.
    UNCLASSIFIED = "unclassified"


ENTRY_SIGNS: Final[dict[CreditEntryType, int]] = {
    CreditEntryType.GRANT: 1,
    CreditEntryType.REFUND: 1,
    CreditEntryType.CONSUME: -1,
    CreditEntryType.EXPIRE: -1,
}
"""The only place an entry type becomes a direction. The database repeats it as a constraint."""


def signed_credits(entry_type: CreditEntryType, credits: int) -> int:
    """Turn a positive magnitude into the ledger's signed delta.

    A magnitude of zero is refused rather than stored: an entry that moves nothing is a bug that
    would otherwise sit in the history looking like a decision somebody made.
    """

    if credits <= 0:
        raise ValueError("a ledger entry moves a positive number of credits")
    return ENTRY_SIGNS[entry_type] * credits


# PRD §12.8's refund rule, as a table over `FailureClass`.
#
# Every class releases today, and that is not a placeholder. §12.7 draws `RESERVED --> CONSUMED`
# from "ön izleme başarıyla hazır" and §12.8 repeats it: the credit is consumed when a preview
# exists. No preview, no charge — including when the customer's own footage was the problem,
# because we cannot bill for an output nobody received. The table exists so that the day one
# class stops being refundable, the change is a line here rather than a condition somewhere in a
# worker, and so the reason each class refunds is written down next to it.
REFUND_POLICY: Final[dict[FailureClass, SettlementOutcome]] = {
    FailureClass.TECHNICAL: SettlementOutcome.RELEASE,
    FailureClass.INPUT: SettlementOutcome.RELEASE,
    FailureClass.UNCLASSIFIED: SettlementOutcome.RELEASE,
}

# The documented failure codes a content project can stop on, classified. Written as data so the
# classification is auditable beside `docs/architecture/error-handling.md`, and so a code that is
# added there without a class here surfaces as `UNCLASSIFIED` rather than as a crash.
FAILURE_CLASSES: Final[dict[str, FailureClass]] = {
    "PROJECT_STATE_TIMEOUT": FailureClass.TECHNICAL,
    "PROJECT_SCRIPT_FAILED": FailureClass.TECHNICAL,
    "PROJECT_VOICEOVER_FAILED": FailureClass.TECHNICAL,
    "PROJECT_TIMELINE_REJECTED": FailureClass.TECHNICAL,
    "PROJECT_RENDER_FAILED": FailureClass.TECHNICAL,
    "PROJECT_RENDER_ATTEMPTS_EXHAUSTED": FailureClass.TECHNICAL,
    "SCRIPT_GENERATION_ABANDONED": FailureClass.TECHNICAL,
    "VOICEOVER_ABANDONED": FailureClass.TECHNICAL,
    "ENTITLEMENT_RESERVATION_ABANDONED": FailureClass.TECHNICAL,
    "PROJECT_SOURCE_NOT_ANALYZED": FailureClass.INPUT,
    "PROJECT_NO_USABLE_SCENE": FailureClass.INPUT,
    "PROJECT_TIMELINE_TOO_SHORT_FOR_VOICEOVER": FailureClass.INPUT,
    "PROJECT_TIMELINE_TOO_LONG": FailureClass.INPUT,
}


def classify_failure(failure_code: str | None) -> FailureClass:
    """Total over every string and over `None`. Anything unmapped is `UNCLASSIFIED`."""

    if failure_code is None:
        return FailureClass.UNCLASSIFIED
    return FAILURE_CLASSES.get(failure_code, FailureClass.UNCLASSIFIED)


def settlement_outcome(
    outcome: SourceOutcome, failure_code: str | None
) -> SettlementOutcome | None:
    """What a finished job does to its reservation. `None` means "not finished, hold it".

    `DELIVERED` consumes whatever the failure code says. A project that failed a check, retried
    and then produced a preview still carries the code of the attempt that failed — the record of
    a bad first render, not a reason to hand back the credit for the good second one.
    """

    if outcome is SourceOutcome.RUNNING:
        return None
    if outcome is SourceOutcome.DELIVERED:
        return SettlementOutcome.CONSUME
    return REFUND_POLICY[classify_failure(failure_code)]


# What may be done to a reservation, by the status it is already in. Total over the product.
#
# The distinction that matters is between a replay and a contradiction. A settlement runs inside
# the transaction that made the work terminal, and that transaction can be retried; applying the
# *same* outcome twice must therefore be a success that writes nothing. Applying the *opposite*
# outcome is something else entirely — it says two callers disagree about whether the work
# delivered — and in an append-only ledger a silent second entry is not recoverable.
_SETTLEMENT_TABLE: Final[dict[tuple[ReservationStatus, SettlementOutcome], SettlementAction]] = {
    (ReservationStatus.RESERVED, SettlementOutcome.CONSUME): SettlementAction.APPLY,
    (ReservationStatus.RESERVED, SettlementOutcome.RELEASE): SettlementAction.APPLY,
    (ReservationStatus.CONSUMED, SettlementOutcome.CONSUME): SettlementAction.ALREADY_APPLIED,
    (ReservationStatus.CONSUMED, SettlementOutcome.RELEASE): SettlementAction.CONFLICT,
    (ReservationStatus.RELEASED, SettlementOutcome.RELEASE): SettlementAction.ALREADY_APPLIED,
    (ReservationStatus.RELEASED, SettlementOutcome.CONSUME): SettlementAction.CONFLICT,
}


def resolve_settlement(current: ReservationStatus, outcome: SettlementOutcome) -> SettlementAction:
    """Total over `ReservationStatus × SettlementOutcome`; a missing pair is a code fault."""

    action = _SETTLEMENT_TABLE.get((current, outcome))
    if action is None:  # pragma: no cover - the table is asserted complete by the unit suite
        raise KeyError(f"no settlement rule for {current.value}/{outcome.value}")
    return action


# The status a settled reservation lands in, per outcome. Separate from the table above so the
# two questions — "may I?" and "into what?" — cannot answer each other.
SETTLED_STATUS: Final[dict[SettlementOutcome, ReservationStatus]] = {
    SettlementOutcome.CONSUME: ReservationStatus.CONSUMED,
    SettlementOutcome.RELEASE: ReservationStatus.RELEASED,
}

# The entry a settlement writes, per outcome. `CONSUME` writes nothing: the `consume` entry was
# written when the reservation opened, which is what makes an open reservation reduce the balance
# and therefore what makes two concurrent requests unable to spend the same credit.
SETTLEMENT_ENTRY: Final[dict[SettlementOutcome, CreditEntryType | None]] = {
    SettlementOutcome.CONSUME: None,
    SettlementOutcome.RELEASE: CreditEntryType.REFUND,
}

# --- documented error codes --------------------------------------------------------------------

ERROR_INSUFFICIENT_CREDITS = "ENTITLEMENT_INSUFFICIENT_CREDITS"
ERROR_RESERVATION_CONFLICT = "ENTITLEMENT_RESERVATION_CONFLICT"
ERROR_RESERVATION_NOT_FOUND = "ENTITLEMENT_RESERVATION_NOT_FOUND"
ERROR_LEDGER_WOULD_GO_NEGATIVE = "ENTITLEMENT_LEDGER_WOULD_GO_NEGATIVE"

# The failure code a swept reservation carries. Distinct from every settled failure above for the
# same reason slice 2E's abandoned-run codes are: nobody observed this one end.
RESERVATION_ABANDONED = "ENTITLEMENT_RESERVATION_ABANDONED"


__all__ = [
    "ENTRY_SIGNS",
    "ERROR_INSUFFICIENT_CREDITS",
    "ERROR_LEDGER_WOULD_GO_NEGATIVE",
    "ERROR_RESERVATION_CONFLICT",
    "ERROR_RESERVATION_NOT_FOUND",
    "FAILURE_CLASSES",
    "REFUND_POLICY",
    "RESERVATION_ABANDONED",
    "SETTLED_STATUS",
    "SETTLEMENT_ENTRY",
    "CreditEntryType",
    "FailureClass",
    "ReservationStatus",
    "SettlementAction",
    "SettlementOutcome",
    "SourceOutcome",
    "classify_failure",
    "resolve_settlement",
    "settlement_outcome",
    "signed_credits",
]
