"""The pure half of entitlement: the price list, the arithmetic, and the two decision tables.

Everything here runs without a database, and everything here is about a *closed* domain. The
tests are written as exhaustive walks over enum products rather than as a handful of examples,
because the property each one asserts is totality — that no combination of inputs falls through
to a default nobody chose. A sampled test of a total function proves the samples.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from app.core.config import Settings
from app.modules.businesses.models import BusinessRole
from app.modules.businesses.policy import Permission, permits
from app.modules.content.lifecycle import ProjectState
from app.modules.content.project_service import source_outcome
from app.modules.content.render import RenderProfile
from app.modules.content.script import ScenarioCode
from app.modules.entitlement.ledger import (
    ENTRY_SIGNS,
    FAILURE_CLASSES,
    REFUND_POLICY,
    SETTLED_STATUS,
    SETTLEMENT_ENTRY,
    CreditEntryType,
    FailureClass,
    ReservationStatus,
    SettlementAction,
    SettlementOutcome,
    SourceOutcome,
    classify_failure,
    resolve_settlement,
    settlement_outcome,
    signed_credits,
)
from app.modules.entitlement.points import (
    POINT_TABLE_V1,
    POINT_TABLES,
    ContentPointKind,
    PointTable,
    PointTableError,
    point_table,
)
from app.modules.entitlement.policy import EntitlementAction, permits_action, required_permission

# --- the price list ------------------------------------------------------------------------------


def test_the_live_point_table_prices_every_kind_and_maps_every_surface() -> None:
    """Totality is the property; the constructor enforces it and this states what it covers."""

    assert set(POINT_TABLE_V1.points) == set(ContentPointKind)
    product = set(itertools.product(ScenarioCode, RenderProfile))
    assert set(POINT_TABLE_V1.surfaces) == product
    # And every surface resolves to a positive whole number of credits, walked exhaustively.
    for scenario, profile in sorted(product, key=lambda pair: (pair[0].value, pair[1].value)):
        credits = POINT_TABLE_V1.credits_for(scenario, profile)
        assert isinstance(credits, int) and not isinstance(credits, bool)
        assert credits > 0


def test_version_one_carries_the_prd_numbers_verbatim() -> None:
    """PRD §12.4's table, transcribed. A drift here is a pricing change nobody reviewed."""

    assert POINT_TABLE_V1.points == {
        ContentPointKind.X_POST: 1,
        ContentPointKind.STORY: 1,
        ContentPointKind.STATIC_POST: 2,
        ContentPointKind.CAROUSEL: 3,
        ContentPointKind.STANDARD_REELS: 5,
        ContentPointKind.PROFESSIONAL_REELS: 8,
        ContentPointKind.PREMIUM_VIDEO: 20,
        ContentPointKind.AD_CREATIVE_VARIATION: 5,
        # §12.4 writes "10+"; a price list cannot hold the open end, so the floor is the price.
        ContentPointKind.GENERATIVE_VIDEO_SCENE: 10,
    }


def test_a_table_that_does_not_price_every_kind_cannot_exist() -> None:
    with pytest.raises(PointTableError, match="prices no"):
        PointTable(
            version=99,
            points={ContentPointKind.STORY: 1},
            surfaces=dict(POINT_TABLE_V1.surfaces),
        )


def test_a_table_that_does_not_map_every_surface_cannot_exist() -> None:
    """A new render profile has to be priced before it can ship, not after it renders free."""

    partial = dict(POINT_TABLE_V1.surfaces)
    partial.pop((ScenarioCode.PRODUCT_REELS, RenderProfile.X_VIDEO_1280X720))
    with pytest.raises(PointTableError, match="does not map"):
        PointTable(version=99, points=dict(POINT_TABLE_V1.points), surfaces=partial)


def test_a_table_cannot_price_a_kind_at_zero_or_below() -> None:
    with pytest.raises(PointTableError, match="non-positive"):
        PointTable(
            version=99,
            points=dict(POINT_TABLE_V1.points) | {ContentPointKind.STORY: 0},
            surfaces=dict(POINT_TABLE_V1.surfaces),
        )


def test_the_configured_version_is_a_registered_one() -> None:
    """`core` cannot import the registry to validate this, so the suite does it instead."""

    configured = Settings(
        database_url="postgresql+asyncpg://t:t@localhost:5432/t",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
    ).entitlement_points_version
    assert configured in POINT_TABLES
    assert point_table(configured).version == configured


def test_an_unregistered_version_is_refused_rather_than_guessed() -> None:
    with pytest.raises(PointTableError, match="no point table is registered"):
        point_table(max(POINT_TABLES) + 1)


# --- entry arithmetic ----------------------------------------------------------------------------


def test_every_entry_type_has_a_direction() -> None:
    assert set(ENTRY_SIGNS) == set(CreditEntryType)


@pytest.mark.parametrize("entry_type", list(CreditEntryType))
def test_the_sign_of_an_entry_belongs_to_its_type(entry_type: CreditEntryType) -> None:
    delta = signed_credits(entry_type, 7)
    assert abs(delta) == 7
    positive = entry_type in {CreditEntryType.GRANT, CreditEntryType.REFUND}
    assert (delta > 0) is positive


@pytest.mark.parametrize("credits", [0, -1, -100])
def test_an_entry_that_moves_nothing_is_refused(credits: int) -> None:
    with pytest.raises(ValueError, match="positive number of credits"):
        signed_credits(CreditEntryType.GRANT, credits)


def test_a_balance_is_the_sum_of_its_entries_and_nothing_else() -> None:
    """The whole derivation, in one line, over a sequence with every entry type in it."""

    entries = [
        (CreditEntryType.GRANT, 100),
        (CreditEntryType.CONSUME, 5),
        (CreditEntryType.CONSUME, 20),
        (CreditEntryType.REFUND, 5),
        (CreditEntryType.EXPIRE, 30),
    ]
    assert sum(signed_credits(kind, amount) for kind, amount in entries) == 50


# --- the settlement tables -----------------------------------------------------------------------


def test_resolve_settlement_is_total_over_status_and_outcome() -> None:
    for status, outcome in itertools.product(ReservationStatus, SettlementOutcome):
        assert isinstance(resolve_settlement(status, outcome), SettlementAction)


@pytest.mark.parametrize(
    ("status", "outcome", "expected"),
    [
        (ReservationStatus.RESERVED, SettlementOutcome.CONSUME, SettlementAction.APPLY),
        (ReservationStatus.RESERVED, SettlementOutcome.RELEASE, SettlementAction.APPLY),
        # A retried settlement writes nothing rather than failing: settlement runs inside a
        # transaction that can be replayed.
        (
            ReservationStatus.CONSUMED,
            SettlementOutcome.CONSUME,
            SettlementAction.ALREADY_APPLIED,
        ),
        (
            ReservationStatus.RELEASED,
            SettlementOutcome.RELEASE,
            SettlementAction.ALREADY_APPLIED,
        ),
        # Two callers disagreeing about whether the work delivered. In an append-only ledger a
        # silent second entry would be permanent, so this is loud.
        (ReservationStatus.CONSUMED, SettlementOutcome.RELEASE, SettlementAction.CONFLICT),
        (ReservationStatus.RELEASED, SettlementOutcome.CONSUME, SettlementAction.CONFLICT),
    ],
)
def test_replay_and_contradiction_are_different_answers(
    status: ReservationStatus, outcome: SettlementOutcome, expected: SettlementAction
) -> None:
    assert resolve_settlement(status, outcome) is expected


def test_a_settled_reservation_can_never_be_re_settled_into_a_second_entry() -> None:
    """The property behind the table: only an open hold produces `APPLY`."""

    for status, outcome in itertools.product(ReservationStatus, SettlementOutcome):
        applies = resolve_settlement(status, outcome) is SettlementAction.APPLY
        assert applies is (status is ReservationStatus.RESERVED)


def test_every_settlement_outcome_has_a_status_and_an_entry_rule() -> None:
    assert set(SETTLED_STATUS) == set(SettlementOutcome)
    assert set(SETTLEMENT_ENTRY) == set(SettlementOutcome)
    # Consuming writes nothing: the charge was written when the hold opened.
    assert SETTLEMENT_ENTRY[SettlementOutcome.CONSUME] is None
    assert SETTLEMENT_ENTRY[SettlementOutcome.RELEASE] is CreditEntryType.REFUND


def test_every_failure_class_has_a_refund_answer() -> None:
    assert set(REFUND_POLICY) == set(FailureClass)


@pytest.mark.parametrize("code", [*FAILURE_CLASSES, "SOMETHING_NOBODY_MAPPED", "", None])
def test_classify_failure_is_total_over_any_code_at_all(code: str | None) -> None:
    assert isinstance(classify_failure(code), FailureClass)


def test_an_unmapped_failure_code_is_unclassified_rather_than_an_error() -> None:
    assert classify_failure("PROJECT_INVENTED_TOMORROW") is FailureClass.UNCLASSIFIED
    assert classify_failure(None) is FailureClass.UNCLASSIFIED


def test_settlement_outcome_is_total_over_outcome_and_every_failure_code() -> None:
    codes: list[str | None] = [*FAILURE_CLASSES, "PROJECT_INVENTED_TOMORROW", None]
    for outcome, code in itertools.product(SourceOutcome, codes):
        answer = settlement_outcome(outcome, code)
        if outcome is SourceOutcome.RUNNING:
            assert answer is None
        else:
            assert isinstance(answer, SettlementOutcome)


def test_only_delivered_work_is_ever_charged() -> None:
    """PRD §12.7/§12.8: the credit is consumed when a preview exists, and otherwise released."""

    codes: list[str | None] = [*FAILURE_CLASSES, "PROJECT_INVENTED_TOMORROW", None]
    for outcome, code in itertools.product(SourceOutcome, codes):
        answer = settlement_outcome(outcome, code)
        consumed = answer is SettlementOutcome.CONSUME
        assert consumed is (outcome is SourceOutcome.DELIVERED)


def test_a_stale_failure_code_on_delivered_work_does_not_hand_the_credit_back() -> None:
    """A project that failed a check, retried and then produced a preview still carries the code."""

    assert (
        settlement_outcome(SourceOutcome.DELIVERED, "PROJECT_RENDER_FAILED")
        is SettlementOutcome.CONSUME
    )


# --- the content project's side of the boundary ---------------------------------------------------


def test_source_outcome_is_total_over_the_project_state_machine() -> None:
    """`ProjectState × delivered`, walked whole. Slice 2F added the second dimension."""

    for state, delivered in itertools.product(ProjectState, (False, True)):
        assert isinstance(source_outcome(state, preview_delivered=delivered), SourceOutcome)


def test_a_project_that_never_previewed_delivers_only_from_preview_ready() -> None:
    for state in ProjectState:
        outcome = source_outcome(state, preview_delivered=False)
        assert (outcome is SourceOutcome.DELIVERED) is (
            state in {ProjectState.PREVIEW_READY, ProjectState.APPROVED}
        )
        assert (outcome is SourceOutcome.ABANDONED) is (
            state in {ProjectState.FAILED, ProjectState.CANCELLED}
        )


def test_a_delivered_preview_stays_delivered_wherever_the_project_ends_up() -> None:
    """The rule slice 2F had to add, and the reason it is a second argument rather than a state.

    §21 puts a revision loop *after* the preview. A project can now reach `preview_ready` — where
    PRD §12.7 consumes the credit — then be rejected, revised, and end in `failed` or `cancelled`.
    Reading only the final state would ask the ledger to release a hold it already consumed,
    which `resolve_settlement` correctly calls a `CONFLICT`; the project would be stuck having
    done nothing wrong. The customer keeps the preview they were given in every one of those
    endings, so every one of them is `DELIVERED`.
    """

    for state in ProjectState:
        assert source_outcome(state, preview_delivered=True) is SourceOutcome.DELIVERED

    for state in (ProjectState.FAILED, ProjectState.CANCELLED, ProjectState.REVISION_REQUESTED):
        decision = settlement_outcome(
            source_outcome(state, preview_delivered=True), "PROJECT_RENDER_FAILED"
        )
        assert decision is SettlementOutcome.CONSUME
        # And the settlement that already happened at `preview_ready` is simply replayed, which
        # writes nothing at all rather than refunding a second time.
        assert (
            resolve_settlement(ReservationStatus.CONSUMED, decision)
            is SettlementAction.ALREADY_APPLIED
        )


def test_the_composed_rule_charges_exactly_one_project_state() -> None:
    """`ProjectState × failure code`, walked whole. This is the "which failure refunds" matrix."""

    codes: list[str | None] = [*FAILURE_CLASSES, "PROJECT_INVENTED_TOMORROW", None]
    charged: set[tuple[str, str | None]] = set()
    held: set[tuple[str, str | None]] = set()
    released: set[tuple[str, str | None]] = set()
    for state, code in itertools.product(ProjectState, codes):
        answer = settlement_outcome(source_outcome(state, preview_delivered=False), code)
        bucket = {
            None: held,
            SettlementOutcome.CONSUME: charged,
            SettlementOutcome.RELEASE: released,
        }[answer]
        bucket.add((state.value, code))
    assert len(charged) + len(held) + len(released) == len(list(ProjectState)) * len(codes)
    # Two states charge, and both mean the same thing: a preview exists. `approved` is only
    # reachable through `preview_ready`, so nothing is charged twice — `resolve_settlement`
    # answers `ALREADY_APPLIED` for the second pass.
    assert {state for state, _ in charged} == {
        ProjectState.PREVIEW_READY.value,
        ProjectState.APPROVED.value,
    }
    # Two states release, and both mean the same thing: no preview was ever produced. The
    # cancellation codes slice 2F introduces are not in `FAILURE_CLASSES` and therefore classify
    # as `UNCLASSIFIED`, which refunds — the correct answer, and the one this loop covers by
    # including an unmapped code.
    assert {state for state, _ in released} == {
        ProjectState.FAILED.value,
        ProjectState.CANCELLED.value,
    }
    for code in ("PROJECT_CANCELLED", "PROJECT_ABANDONED"):
        assert classify_failure(code) is FailureClass.UNCLASSIFIED
        assert (
            settlement_outcome(
                source_outcome(ProjectState.CANCELLED, preview_delivered=False), code
            )
            is SettlementOutcome.RELEASE
        )


# --- policy ---------------------------------------------------------------------------------------


def test_every_entitlement_action_maps_to_a_central_permission() -> None:
    for action in EntitlementAction:
        assert isinstance(required_permission(action), Permission)


def test_creating_credit_is_the_owner_alone() -> None:
    """An admin may do everything to a business except end it — and except mint credit."""

    for role in BusinessRole:
        allowed = permits_action(role, EntitlementAction.GRANT_CREATE)
        assert allowed is (role is BusinessRole.OWNER)


def test_reading_the_ledger_is_ordinary_business_read() -> None:
    """A viewer who cannot start a generation still needs to see why one was refused."""

    for action in (EntitlementAction.BALANCE_READ, EntitlementAction.LEDGER_READ):
        assert required_permission(action) is Permission.BUSINESS_READ
        assert permits_action(BusinessRole.VIEWER, action)
        # Slice 2F gave the approver `business.read` so it can see what it is signing off, and
        # this read rides on that permission. What it did *not* get is any way to move credit:
        # granting is still the owner alone, asserted directly above.
        assert permits_action(BusinessRole.APPROVER, action)
        assert not permits_action(BusinessRole.APPROVER, EntitlementAction.GRANT_CREATE)


def test_the_grant_permission_is_in_the_central_table_for_every_role() -> None:
    """`permits` raises for a role missing from the table; that is the invariant being pinned."""

    for role in BusinessRole:
        assert isinstance(permits(role, Permission.ENTITLEMENT_GRANT), bool)


def test_there_is_no_action_whose_effect_is_only_to_spend() -> None:
    """Spending happens inside the operation that needs it; a second path would be a weaker one."""

    names: list[Any] = [action.value for action in EntitlementAction]
    assert not [name for name in names if "consume" in name or "reserve" in name]
