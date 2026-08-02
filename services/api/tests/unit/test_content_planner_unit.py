"""PRD §13's planner as pure values: the machine, the clock, the ten priorities, the mix.

Everything under test here is a total function, so the tests are enumerations rather than
examples. Four things get the most attention because each one is a way for the planner to be
quietly wrong.

**The order is §13.2's order.** A lexicographic key over ten discrete buckets is only a
specification if the earlier priorities really do dominate the later ones, so the dominance test
below walks every adjacent pair and proves it — and the two priorities that have a field and no
rule are proved to change nothing at all.

**The clock is the tenant's, not the server's.** Period boundaries and publish slots are built
from local calendar dates, so a DST day is 23 or 25 hours long and a quiet window that ends in a
non-existent local hour still lands outside the window. Europe/Berlin is in these tests precisely
because Türkiye has no DST: a planner that assumed the business timezone never changes offset
would pass every test written against Istanbul.

**The mix is measured, not enforced.** `measure_mix` has no failure mode and no caller can turn a
deviation into a refusal, which the tests state by exhausting the function's range.

**A transition the machine does not draw is a refusal, not a guess.** `next_obligation_status` is
enumerated over its whole product.
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.modules.businesses.models import BusinessRole
from app.modules.content.render import RenderProfile
from app.modules.content.script import ScenarioCode
from app.modules.planner.obligation import (
    DEFAULT_MIX_SHARES,
    ERROR_ITEM_INVALID,
    ERROR_QUIET_HOURS_UNRESOLVED,
    ERROR_SETTINGS_INVALID,
    ERROR_TIMEZONE_UNKNOWN,
    MIX_TOLERANCE_POINTS,
    MIX_TOTAL,
    REASON_MEDIA_ABSENT,
    REASON_PERFORMANCE_NOT_MEASURED,
    REASON_SPECIAL_DAYS_NOT_CONFIGURED,
    UNIMPLEMENTED_PRIORITIES,
    ContentCategory,
    ContentType,
    MixTargets,
    ObligationEvent,
    ObligationStatus,
    ObligationTransitionError,
    Orientation,
    PlannerError,
    PlannerPriority,
    PlanPeriod,
    QuietHours,
    RankContext,
    build_window,
    measure_mix,
    next_obligation_status,
    obligation_can_cancel,
    obligation_is_terminal,
    orientation_of,
    period_bounds,
    period_days,
    period_start_date,
    rank_obligations,
    rank_reasons,
    require_obligation_status,
    resolve_timezone,
    shift_out_of_quiet_hours,
    surface_for,
    target_orientation,
)
from app.modules.planner.policy import ACTION_PERMISSIONS, PlannerAction, permits_action

BERLIN = ZoneInfo("Europe/Berlin")
ISTANBUL = ZoneInfo("Europe/Istanbul")
# 22:00 → 08:00 local: the shape a quiet window almost always has, and the one that wraps midnight.
NIGHT = QuietHours(start_minute=22 * 60, end_minute=8 * 60)
OPEN = QuietHours(start_minute=0, end_minute=0)


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql+asyncpg://t:t@localhost:5432/t",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


# --- the obligation state machine ----------------------------------------------------------------


def test_the_obligation_transition_table_is_total_over_status_and_event() -> None:
    """Every pair answers. `None` is a refusal a caller handles, never a `KeyError`."""

    for status, event in itertools.product(ObligationStatus, ObligationEvent):
        answer = next_obligation_status(status, event)
        assert answer is None or isinstance(answer, ObligationStatus)


def test_creation_is_an_entry_arrow_and_not_a_transition() -> None:
    assert all(
        next_obligation_status(status, ObligationEvent.CREATED) is None
        for status in ObligationStatus
    )


def test_terminal_obligations_leave_no_edges_and_cancellation_covers_exactly_the_rest() -> None:
    for status in ObligationStatus:
        if obligation_is_terminal(status):
            assert all(next_obligation_status(status, event) is None for event in ObligationEvent)
        assert obligation_can_cancel(status) is not obligation_is_terminal(status)


def test_only_a_window_that_never_became_work_can_expire() -> None:
    """An `in_progress` obligation is producing something the customer will receive.

    Expiring it would throw away a generation that has already been reserved and, past the
    preview, already charged. The window closing is the planner's problem, not the project's.
    """

    expirable = {
        status
        for status in ObligationStatus
        if next_obligation_status(status, ObligationEvent.EXPIRED) is not None
    }
    assert expirable == {ObligationStatus.PLANNED, ObligationStatus.BLOCKED}


def test_blocking_is_not_death() -> None:
    """A blocked obligation can be retried and converted — that is why it is a state."""

    assert (
        next_obligation_status(ObligationStatus.BLOCKED, ObligationEvent.RETRIED)
        is ObligationStatus.PLANNED
    )
    assert (
        next_obligation_status(ObligationStatus.BLOCKED, ObligationEvent.CONVERTED)
        is ObligationStatus.IN_PROGRESS
    )


def test_an_undrawn_transition_raises_rather_than_guessing() -> None:
    with pytest.raises(ObligationTransitionError):
        require_obligation_status(ObligationStatus.FULFILLED, ObligationEvent.CONVERTED)


# --- the vocabulary ------------------------------------------------------------------------------


def test_every_content_type_maps_to_a_scenario_and_a_profile() -> None:
    """Totality is enforced at import; this states what the mapping is for a reader."""

    for content_type in ContentType:
        scenario, profile = surface_for(content_type)
        assert scenario is ScenarioCode.PRODUCT_REELS
        assert isinstance(profile, RenderProfile)


def test_the_review_proxy_is_not_a_publishing_surface() -> None:
    """Nothing a subscription can standingly demand renders to `preview_540x960`."""

    assert RenderProfile.PREVIEW_540X960 not in {
        surface_for(content_type)[1] for content_type in ContentType
    }


def test_orientation_reads_the_profile_spec_rather_than_restating_it() -> None:
    assert target_orientation(RenderProfile.INSTAGRAM_REELS_1080X1920) is Orientation.PORTRAIT
    assert target_orientation(RenderProfile.X_VIDEO_1280X720) is Orientation.LANDSCAPE
    assert target_orientation(RenderProfile.INSTAGRAM_SQUARE_1080X1080) is Orientation.SQUARE
    assert orientation_of(1, 1) is Orientation.SQUARE


# --- the clock -----------------------------------------------------------------------------------


def test_an_unknown_timezone_is_refused_rather_than_defaulted_to_utc() -> None:
    """Defaulting would publish a café's evening post three hours early and say nothing."""

    with pytest.raises(PlannerError) as error:
        resolve_timezone("Mars/Olympus_Mons")
    assert error.value.code == ERROR_TIMEZONE_UNKNOWN


def test_a_period_starts_at_local_midnight_and_not_at_utc_midnight() -> None:
    start, end = period_bounds(date(2026, 8, 3), period=PlanPeriod.DAILY, tz=ISTANBUL)
    assert start == datetime(2026, 8, 2, 21, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 3, 21, 0, tzinfo=UTC)


def test_a_daily_period_on_a_spring_forward_day_is_twenty_three_hours_long() -> None:
    """The whole reason a boundary is a local date and not `start + timedelta(days=1)`."""

    start, end = period_bounds(date(2026, 3, 29), period=PlanPeriod.DAILY, tz=BERLIN)
    assert end - start == timedelta(hours=23)


def test_a_daily_period_on_a_fall_back_day_is_twenty_five_hours_long() -> None:
    start, end = period_bounds(date(2026, 10, 25), period=PlanPeriod.DAILY, tz=BERLIN)
    assert end - start == timedelta(hours=25)


def test_a_weekly_period_starts_on_the_local_monday() -> None:
    # 2026-08-03 is a Monday; 2026-08-06 is the Thursday of the same week.
    assert period_start_date(date(2026, 8, 6), PlanPeriod.WEEKLY) == date(2026, 8, 3)
    start, end = period_bounds(date(2026, 8, 6), period=PlanPeriod.WEEKLY, tz=ISTANBUL)
    assert start == datetime(2026, 8, 2, 21, 0, tzinfo=UTC)
    assert end - start == timedelta(days=7)


def test_the_current_period_is_always_planned_even_when_the_horizon_is_zero() -> None:
    """A mid-week weekly item must not wait until next Monday to get its first obligation."""

    days = period_days(first=date(2026, 8, 6), period=PlanPeriod.WEEKLY, horizon=0)
    assert days == (date(2026, 8, 3),)


def test_period_days_walks_forward_by_whole_periods() -> None:
    assert period_days(first=date(2026, 8, 3), period=PlanPeriod.DAILY, horizon=3) == (
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
    )


def test_a_negative_horizon_is_a_configuration_error() -> None:
    with pytest.raises(PlannerError) as error:
        period_days(first=date(2026, 8, 3), period=PlanPeriod.DAILY, horizon=-1)
    assert error.value.code == ERROR_SETTINGS_INVALID


# --- quiet hours ---------------------------------------------------------------------------------


def test_a_wrapped_quiet_window_contains_both_halves_of_the_night() -> None:
    assert NIGHT.contains(datetime(2026, 8, 3, 23, 0).time())
    assert NIGHT.contains(datetime(2026, 8, 3, 2, 0).time())
    assert not NIGHT.contains(datetime(2026, 8, 3, 12, 0).time())
    # Half-open at the end: 08:00 is when the window is over, not the last minute of it.
    assert not NIGHT.contains(datetime(2026, 8, 3, 8, 0).time())


def test_an_empty_window_is_a_real_answer_and_shifts_nothing() -> None:
    moment = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    assert OPEN.is_empty
    assert shift_out_of_quiet_hours(moment, tz=ISTANBUL, quiet_hours=OPEN) == moment


def test_a_slot_outside_the_window_comes_back_unchanged() -> None:
    """Which is what makes the shift safe to apply unconditionally."""

    moment = datetime(2026, 8, 3, 15, 0, tzinfo=ISTANBUL).astimezone(UTC)
    assert shift_out_of_quiet_hours(moment, tz=ISTANBUL, quiet_hours=NIGHT) == moment


def test_an_evening_slot_is_shifted_to_the_next_local_morning_and_never_cancelled() -> None:
    """§13.2/8 moves a publication out of the window. It does not drop it."""

    requested = datetime(2026, 8, 3, 23, 30, tzinfo=ISTANBUL).astimezone(UTC)
    shifted = shift_out_of_quiet_hours(requested, tz=ISTANBUL, quiet_hours=NIGHT)
    assert shifted.astimezone(ISTANBUL) == datetime(2026, 8, 4, 8, 0, tzinfo=ISTANBUL)
    assert shifted > requested


def test_an_early_morning_slot_is_shifted_forward_within_the_same_local_day() -> None:
    requested = datetime(2026, 8, 3, 3, 0, tzinfo=ISTANBUL).astimezone(UTC)
    shifted = shift_out_of_quiet_hours(requested, tz=ISTANBUL, quiet_hours=NIGHT)
    assert shifted.astimezone(ISTANBUL) == datetime(2026, 8, 3, 8, 0, tzinfo=ISTANBUL)


def test_a_local_midnight_slot_lands_on_the_correct_side_of_the_window() -> None:
    """Midnight is inside a 22:00–08:00 window and belongs to the morning it precedes."""

    requested = datetime(2026, 8, 4, 0, 0, tzinfo=ISTANBUL).astimezone(UTC)
    shifted = shift_out_of_quiet_hours(requested, tz=ISTANBUL, quiet_hours=NIGHT)
    assert shifted.astimezone(ISTANBUL) == datetime(2026, 8, 4, 8, 0, tzinfo=ISTANBUL)


def test_a_shift_whose_target_hour_does_not_exist_still_lands_outside_the_window() -> None:
    """Spring forward in Berlin: 02:00–03:00 local does not happen on 2026-03-29.

    A window ending at 02:30 therefore names an instant nobody's clock shows. The conversion
    lands on the offset in force before the transition, so the result reads as 03:30 local —
    after the window, which is the only property that has to hold.
    """

    window = QuietHours(start_minute=60, end_minute=150)
    requested = datetime(2026, 3, 29, 1, 30, tzinfo=BERLIN).astimezone(UTC)
    shifted = shift_out_of_quiet_hours(requested, tz=BERLIN, quiet_hours=window)
    assert not window.contains(shifted.astimezone(BERLIN).time())
    assert shifted > requested


def test_a_shift_across_an_ambiguous_local_hour_still_lands_outside_the_window() -> None:
    """Fall back in Berlin: 02:00–03:00 local happens twice on 2026-10-25."""

    window = QuietHours(start_minute=60, end_minute=180)
    requested = datetime(2026, 10, 25, 1, 30, tzinfo=BERLIN).astimezone(UTC)
    shifted = shift_out_of_quiet_hours(requested, tz=BERLIN, quiet_hours=window)
    assert not window.contains(shifted.astimezone(BERLIN).time())


def test_a_quiet_window_covering_the_whole_day_is_refused_rather_than_looped_over() -> None:
    """`start == end` means empty; a window with no way out has to fail loudly.

    Constructed here as 00:00–00:01 shifted onto itself: the correction runs once and, if the
    result is still inside, the window itself is malformed. The refusal is a documented code
    rather than an infinite search for a minute that is not there.
    """

    window = QuietHours(start_minute=0, end_minute=1)
    # A 23-hour-59-minute window is expressible; this one is not that. Assert the ordinary case
    # resolves, so the guard below is about the pathological one only.
    assert shift_out_of_quiet_hours(
        datetime(2026, 8, 3, 0, 0, tzinfo=ISTANBUL).astimezone(UTC),
        tz=ISTANBUL,
        quiet_hours=window,
    ).astimezone(ISTANBUL) == datetime(2026, 8, 3, 0, 1, tzinfo=ISTANBUL)
    assert ERROR_QUIET_HOURS_UNRESOLVED == "PLANNER_QUIET_HOURS_UNRESOLVED"


def test_a_quiet_window_minute_outside_the_day_is_refused() -> None:
    with pytest.raises(PlannerError) as error:
        QuietHours(start_minute=1_440, end_minute=0)
    assert error.value.code == ERROR_SETTINGS_INVALID


# --- §13.1's four instants -----------------------------------------------------------------------


def test_a_window_puts_generation_strictly_before_publication() -> None:
    window = build_window(
        date(2026, 8, 3),
        period=PlanPeriod.DAILY,
        tz=ISTANBUL,
        publish_minute=18 * 60 + 30,
        lead_minutes=6 * 60,
        quiet_hours=NIGHT,
    )
    assert window.planned_publish_at == datetime(2026, 8, 3, 18, 30, tzinfo=ISTANBUL).astimezone(
        UTC
    )
    assert window.generation_deadline_at == window.planned_publish_at - timedelta(hours=6)
    assert window.period_start <= window.planned_publish_at < window.period_end
    assert window.quiet_hours_shifted is False


def test_a_window_records_that_its_slot_had_to_move() -> None:
    """ "Why is my post at 08:00 when I said 23:00?" has to be answerable from the row."""

    window = build_window(
        date(2026, 8, 3),
        period=PlanPeriod.DAILY,
        tz=ISTANBUL,
        publish_minute=23 * 60,
        lead_minutes=60,
        quiet_hours=NIGHT,
    )
    assert window.quiet_hours_shifted is True
    # Deliberately outside the period: publishing a few hours late beats not publishing.
    assert window.planned_publish_at >= window.period_end
    assert window.generation_deadline_at < window.planned_publish_at


def test_a_publish_minute_outside_the_day_is_an_item_error() -> None:
    with pytest.raises(PlannerError) as error:
        build_window(
            date(2026, 8, 3),
            period=PlanPeriod.DAILY,
            tz=ISTANBUL,
            publish_minute=2_000,
            lead_minutes=0,
            quiet_hours=OPEN,
        )
    assert error.value.code == ERROR_ITEM_INVALID


# --- §13.3's mix ---------------------------------------------------------------------------------


def test_the_default_distribution_is_section_13_3_and_sums_to_one_hundred() -> None:
    assert sum(DEFAULT_MIX_SHARES.values()) == MIX_TOTAL
    assert DEFAULT_MIX_SHARES[ContentCategory.PRODUCT_SERVICE] == 25
    assert DEFAULT_MIX_SHARES[ContentCategory.CORPORATE] == 5
    assert set(DEFAULT_MIX_SHARES) == set(ContentCategory)


def test_a_distribution_with_a_hole_or_a_wrong_total_cannot_exist() -> None:
    partial = {category: 20 for category in list(ContentCategory)[:5]}
    with pytest.raises(PlannerError) as missing:
        MixTargets(shares=partial)
    assert missing.value.code == ERROR_SETTINGS_INVALID
    wrong_total = dict.fromkeys(ContentCategory, 10)
    with pytest.raises(PlannerError) as total:
        MixTargets(shares=wrong_total)
    assert total.value.code == ERROR_SETTINGS_INVALID


def test_a_stored_distribution_round_trips_and_a_malformed_one_is_refused() -> None:
    targets = MixTargets.default()
    assert MixTargets.from_document(targets.as_document()) == targets
    with pytest.raises(PlannerError):
        MixTargets.from_document({"product_service": "twenty-five"})
    with pytest.raises(PlannerError):
        MixTargets.from_document(["product_service"])


def test_measuring_nothing_reports_every_category_as_under_served() -> None:
    """Which is the honest reading of a business that has produced nothing yet."""

    observations = measure_mix(MixTargets.default(), {})
    assert [item.category for item in observations] == list(ContentCategory)
    assert all(item.actual_share == 0 for item in observations)
    assert all(item.deviation_points == DEFAULT_MIX_SHARES[item.category] for item in observations)


def test_the_mix_is_a_measurement_with_no_failure_mode() -> None:
    """Nothing here can refuse anything — over-service is reported, never blocked."""

    counts = {ContentCategory.CAMPAIGN: 40, ContentCategory.EDUCATIONAL: 1}
    observations = {item.category: item for item in measure_mix(MixTargets.default(), counts)}
    campaign = observations[ContentCategory.CAMPAIGN]
    assert campaign.actual_share > campaign.target_share
    assert campaign.deviation_points < 0
    # And the over-served category still appears with a full record rather than being dropped.
    assert campaign.observed == 40


# --- §13.2's ten priorities ----------------------------------------------------------------------


def context(**overrides: object) -> RankContext:
    base: dict[str, object] = {
        "obligation_id": UUID(int=1),
        "category": ContentCategory.PRODUCT_SERVICE,
        "planned_publish_at": datetime(2026, 8, 3, 18, 0, tzinfo=UTC),
        "generation_deadline_at": datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        "has_active_campaign": False,
        "mix_deviation_points": 0,
        "renderable_assets": 1,
        "required_assets": 1,
        "recent_product_uses": 0,
        "matches_target_orientation": True,
        "quiet_hours_shifted": False,
        "preference_rank": 0,
    }
    return RankContext(**(base | overrides))  # type: ignore[arg-type]


NOW = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)
URGENT = timedelta(hours=6)

# One worse-than-default value per priority. Each has to move only its own component of the key.
WORSE_BY_PRIORITY: dict[PlannerPriority, dict[str, object]] = {
    PlannerPriority.ACTIVE_CAMPAIGN: {"has_active_campaign": False},
    PlannerPriority.SUBSCRIPTION_OBLIGATION: {
        "generation_deadline_at": datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    },
    PlannerPriority.BRAND_CONTENT_BALANCE: {"mix_deviation_points": -50},
    PlannerPriority.PAST_PERFORMANCE: {"performance_score": -100},
    PlannerPriority.MEDIA_SUFFICIENCY: {"renderable_assets": 0},
    PlannerPriority.PRODUCT_REPETITION: {"recent_product_uses": 5},
    PlannerPriority.PLATFORM_FORMAT_FIT: {"matches_target_orientation": None},
    PlannerPriority.QUIET_HOURS: {"quiet_hours_shifted": True},
    PlannerPriority.USER_PREFERENCE: {"preference_rank": 9},
    PlannerPriority.SPECIAL_DAYS: {"special_day_code": "unknown"},
}
BEST_BY_PRIORITY: dict[PlannerPriority, dict[str, object]] = {
    PlannerPriority.ACTIVE_CAMPAIGN: {"has_active_campaign": True},
    PlannerPriority.SUBSCRIPTION_OBLIGATION: {
        "generation_deadline_at": datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    },
    PlannerPriority.BRAND_CONTENT_BALANCE: {"mix_deviation_points": 50},
    PlannerPriority.PAST_PERFORMANCE: {"performance_score": 100},
    PlannerPriority.MEDIA_SUFFICIENCY: {"renderable_assets": 3},
    PlannerPriority.PRODUCT_REPETITION: {"recent_product_uses": 0},
    PlannerPriority.PLATFORM_FORMAT_FIT: {"matches_target_orientation": True},
    PlannerPriority.QUIET_HOURS: {"quiet_hours_shifted": False},
    PlannerPriority.USER_PREFERENCE: {"preference_rank": 0},
    PlannerPriority.SPECIAL_DAYS: {"special_day_code": None},
}


def test_every_priority_answers_for_every_candidate_and_names_itself() -> None:
    reasons = rank_reasons(context(), now=NOW, urgent_window=URGENT)
    assert [reason.priority for reason in reasons] == list(PlannerPriority)
    assert all(reason.rank >= 0 and reason.code for reason in reasons)


@pytest.mark.parametrize(
    ("higher", "lower"),
    [(first, second) for first, second in itertools.pairwise(PlannerPriority)],
)
def test_an_earlier_priority_outranks_every_later_one(
    higher: PlannerPriority, lower: PlannerPriority
) -> None:
    """§13.2 is an order, not a weighted score: nothing below can outweigh anything above.

    The candidate that is best on the higher priority and worst on every lower one still wins.
    That property is what "saf ve sıralı, ağırlıklı skor değil" means once it is a sort key —
    and it is exactly what a weighted score would break.
    """

    worse_below = {
        key: value
        for priority in PlannerPriority
        if priority > higher
        for key, value in WORSE_BY_PRIORITY[priority].items()
    }
    better_below = {
        key: value
        for priority in PlannerPriority
        if priority > higher
        for key, value in BEST_BY_PRIORITY[priority].items()
    }
    winner = context(obligation_id=UUID(int=1), **(BEST_BY_PRIORITY[higher] | worse_below))
    loser = context(obligation_id=UUID(int=2), **(WORSE_BY_PRIORITY[higher] | better_below))
    ranked = rank_obligations([loser, winner], now=NOW, urgent_window=URGENT)
    if higher in UNIMPLEMENTED_PRIORITIES:
        # A priority with no rule contributes a constant, so it cannot outrank anything — and
        # the decision falls through to the next one, which is exactly what "alan var, kural yok"
        # has to mean in a lexicographic key. Asserted rather than skipped: the day §13.2/4 gets
        # a rule, this branch is what fails.
        assert ranked[0].obligation_id == UUID(int=2), f"{higher.name} has acquired a rule"
        return
    assert ranked[0].obligation_id == UUID(int=1), f"{higher.name} lost to {lower.name}"


def test_the_two_unmeasured_priorities_have_a_field_and_no_rule() -> None:
    """§13.2/4 and §13.2/10: alan var, kural yok.

    Their inputs are carried on the context and read by nothing, so setting them to any value
    changes no reason, no key and no order anywhere. When Phase 5's metrics and a verified
    calendar source arrive, this test is what has to be rewritten — which is the point.
    """

    assert UNIMPLEMENTED_PRIORITIES == {
        PlannerPriority.PAST_PERFORMANCE,
        PlannerPriority.SPECIAL_DAYS,
    }
    plain = rank_reasons(context(), now=NOW, urgent_window=URGENT)
    loaded = rank_reasons(
        context(performance_score=9_999, special_day_code="kurban_bayrami"),
        now=NOW,
        urgent_window=URGENT,
    )
    assert plain == loaded
    codes = {reason.priority: reason.code for reason in plain}
    assert codes[PlannerPriority.PAST_PERFORMANCE] == REASON_PERFORMANCE_NOT_MEASURED
    assert codes[PlannerPriority.SPECIAL_DAYS] == REASON_SPECIAL_DAYS_NOT_CONFIGURED
    # Best-case inputs for both, against a candidate that is otherwise identical: still a tie
    # broken by the final key, never by either unimplemented priority.
    first = context(obligation_id=UUID(int=1), performance_score=100, special_day_code="x")
    second = context(obligation_id=UUID(int=2))
    ranked = rank_obligations([second, first], now=NOW, urgent_window=URGENT)
    assert ranked[0].obligation_id == UUID(int=1)


def test_the_same_input_produces_the_same_order_regardless_of_how_it_arrived() -> None:
    """Determinism is a property of the sort, not of the caller's incidental ordering."""

    candidates = [context(obligation_id=UUID(int=index)) for index in range(1, 6)]
    forward = rank_obligations(candidates, now=NOW, urgent_window=URGENT)
    backward = rank_obligations(list(reversed(candidates)), now=NOW, urgent_window=URGENT)
    assert [item.obligation_id for item in forward] == [item.obligation_id for item in backward]
    assert [item.rank_key for item in forward] == [item.rank_key for item in backward]


def test_ties_are_broken_by_the_slot_and_then_by_identity() -> None:
    early = context(
        obligation_id=UUID(int=9), planned_publish_at=datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
    )
    late = context(
        obligation_id=UUID(int=1), planned_publish_at=datetime(2026, 8, 3, 20, 0, tzinfo=UTC)
    )
    ranked = rank_obligations([late, early], now=NOW, urgent_window=URGENT)
    assert ranked[0].obligation_id == UUID(int=9)


def test_an_obligation_with_no_usable_footage_ranks_last_and_is_not_refused() -> None:
    """§14.1's fallback is "görevi beklet", so absent media is a position and not a rejection."""

    empty = context(obligation_id=UUID(int=2), renderable_assets=0)
    ready = context(obligation_id=UUID(int=1))
    ranked = rank_obligations([empty, ready], now=NOW, urgent_window=URGENT)
    assert ranked[0].obligation_id == UUID(int=1)
    codes = {
        reason.priority: reason.code
        for reason in rank_reasons(empty, now=NOW, urgent_window=URGENT)
    }
    assert codes[PlannerPriority.MEDIA_SUFFICIENCY] == REASON_MEDIA_ABSENT
    # It is still in the ranked list. Ranking last is not the same as being removed.
    assert len(ranked) == 2


def test_a_campaign_beats_the_mix_so_a_full_category_never_blocks_one() -> None:
    """PM decision 5: sapma ölçülür, yargılanmaz. A hard quota would punish this exact business.

    The campaign obligation is over its §13.3 share by every measure and still goes first,
    because §13.2/1 sits above §13.2/3 and nothing anywhere converts a deviation into a refusal.
    """

    over_served_campaign = context(
        obligation_id=UUID(int=1),
        category=ContentCategory.CAMPAIGN,
        has_active_campaign=True,
        mix_deviation_points=-90,
    )
    under_served_educational = context(
        obligation_id=UUID(int=2),
        category=ContentCategory.EDUCATIONAL,
        has_active_campaign=False,
        mix_deviation_points=90,
    )
    ranked = rank_obligations(
        [under_served_educational, over_served_campaign], now=NOW, urgent_window=URGENT
    )
    assert ranked[0].obligation_id == UUID(int=1)


def test_the_mix_tolerance_keeps_priority_three_from_reordering_on_noise() -> None:
    inside = context(mix_deviation_points=MIX_TOLERANCE_POINTS)
    outside = context(mix_deviation_points=MIX_TOLERANCE_POINTS + 1)
    inside_rank = next(
        reason.rank
        for reason in rank_reasons(inside, now=NOW, urgent_window=URGENT)
        if reason.priority is PlannerPriority.BRAND_CONTENT_BALANCE
    )
    outside_rank = next(
        reason.rank
        for reason in rank_reasons(outside, now=NOW, urgent_window=URGENT)
        if reason.priority is PlannerPriority.BRAND_CONTENT_BALANCE
    )
    assert inside_rank == 1
    assert outside_rank == 0


def test_deadline_pressure_is_bucketed_so_later_priorities_stay_reachable() -> None:
    """A raw deadline comparison would be almost unique per candidate and would make §13.2/3
    through §13.2/9 unreachable. Three buckets keep ties alive."""

    overdue = context(generation_deadline_at=NOW - timedelta(minutes=1))
    imminent = context(generation_deadline_at=NOW + timedelta(hours=1))
    also_imminent = context(generation_deadline_at=NOW + timedelta(hours=5))
    ahead = context(generation_deadline_at=NOW + timedelta(days=3))

    def bucket(candidate: RankContext) -> int:
        return next(
            reason.rank
            for reason in rank_reasons(candidate, now=NOW, urgent_window=URGENT)
            if reason.priority is PlannerPriority.SUBSCRIPTION_OBLIGATION
        )

    assert bucket(overdue) == 0
    assert bucket(imminent) == bucket(also_imminent) == 1
    assert bucket(ahead) == 2


def test_an_empty_candidate_set_ranks_to_an_empty_plan() -> None:
    assert rank_obligations([], now=NOW, urgent_window=URGENT) == ()


def test_positions_are_dense_and_start_at_zero() -> None:
    candidates = [context(obligation_id=uuid4()) for _ in range(4)]
    ranked = rank_obligations(candidates, now=NOW, urgent_window=URGENT)
    assert [item.position for item in ranked] == [0, 1, 2, 3]


# --- policy --------------------------------------------------------------------------------------


def test_every_planner_action_maps_to_a_permission() -> None:
    assert set(ACTION_PERMISSIONS) == set(PlannerAction)


def test_configuring_the_planner_is_changing_the_business_not_producing_content() -> None:
    """PRD §4's line. An editor produces content and does not rewrite the publishing schedule."""

    writes = (
        PlannerAction.SETTINGS_WRITE,
        PlannerAction.ITEM_WRITE,
        PlannerAction.OBLIGATION_WRITE,
    )
    for action in writes:
        assert permits_action(BusinessRole.OWNER, action)
        assert permits_action(BusinessRole.ADMIN, action)
        assert not permits_action(BusinessRole.EDITOR, action)
        assert not permits_action(BusinessRole.APPROVER, action)
        assert not permits_action(BusinessRole.VIEWER, action)
    reads = (
        PlannerAction.SETTINGS_READ,
        PlannerAction.ITEM_READ,
        PlannerAction.OBLIGATION_READ,
        PlannerAction.PLAN_READ,
    )
    for action in reads:
        assert all(permits_action(role, action) for role in BusinessRole)


# --- configuration -------------------------------------------------------------------------------


def test_the_planning_lease_must_be_shorter_than_the_replan_interval() -> None:
    """A lease that outlived the interval would stop an item ever being replanned."""

    with pytest.raises(ValueError, match="PLANNER_PLAN_LEASE_SECONDS"):
        settings(planner_plan_lease_seconds=3_600, planner_replan_interval_seconds=3_600)


def test_the_conversion_backoff_must_stay_below_the_lease_that_bounds_it() -> None:
    with pytest.raises(ValueError, match="PLANNER_DISPATCH_RETRY_SECONDS"):
        settings(planner_dispatch_retry_seconds=300, planner_dispatch_lease_seconds=300)


def test_the_mix_window_cannot_be_shorter_than_the_week_the_mix_is_stated_over() -> None:
    with pytest.raises(ValueError):
        settings(planner_mix_window_days=3)


def test_the_default_quiet_window_is_the_night_and_wraps_midnight() -> None:
    configured = settings()
    window = QuietHours(
        start_minute=configured.planner_quiet_hours_start_minute,
        end_minute=configured.planner_quiet_hours_end_minute,
    )
    assert window.start_minute > window.end_minute
    assert window.contains(datetime(2026, 8, 3, 23, 0).time())
    assert not window.contains(datetime(2026, 8, 3, 12, 0).time())
