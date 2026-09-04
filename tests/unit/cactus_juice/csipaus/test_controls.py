import random
from datetime import UTC, datetime, timedelta

import pytest
from assertical.asserts.generator import assert_class_instance_equality
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session

from cactus_juice.crud import DEFAULT_MAX_DATE
from cactus_juice.csipaus.controls import (
    DEFAULT_DEFAULT,
    ActiveInterval,
    IntervalBoundary,
    active_interval_to_control_values,
    calculate_schedule_values,
    drain_intermediate_end_boundaries,
    generate_interval_boundaries,
    generate_intervals,
    get_control_finished_at,
)
from cactus_juice.csipaus.dto import (
    ActiveValues,
    ControlValues,
    DefaultValues,
    HasDefaultValues,
    ScheduledControlValues,
)
from cactus_juice.model import CSIPAusControl, CSIPAusDefault


def clamp_seed(seed: int) -> int:
    return seed % 2**20


def dt(seed: int) -> datetime:
    """Shorthand for generating a date succinctly - unique values give unique instances - sort order guaranteed"""
    return datetime(2014, 1, 26, 10, 30, 5, tzinfo=UTC) + timedelta(hours=seed, seconds=seed)


def c(
    start: datetime, finish: datetime, cancelled: datetime | None = None, superseded: datetime | None = None
) -> CSIPAusControl:
    """shorthand for generating a control succinctly - unique values give unique instances"""
    seed = int(start.timestamp())
    seed = (seed << 1) + int(finish.timestamp())
    seed = (seed << 1) + int(cancelled.timestamp() if cancelled else 0)
    seed = (seed << 1) + int(superseded.timestamp() if superseded else 0)
    return generate_class_instance(
        CSIPAusControl,
        seed=clamp_seed(seed),
        started_at=start,
        finished_at=finish,
        cancelled_at=cancelled,
        superseded_at=superseded,
    )


def d(start: datetime, finish: datetime | None = None) -> CSIPAusDefault:
    """shorthand for generating a default succinctly - unique values give unique instances"""
    if finish is None:
        finish = DEFAULT_MAX_DATE
    seed = int(start.timestamp())
    seed += int(finish.timestamp())

    return generate_class_instance(
        CSIPAusDefault,
        seed=clamp_seed(seed),
        started_at=start,
        finished_at=finish,
        active_range=None,
    )


def ib(
    start: datetime,
    start_c: list[CSIPAusControl] | None = None,
    start_d: list[CSIPAusDefault] | None = None,
    finish_c: list[CSIPAusControl] | None = None,
    finish_d: list[CSIPAusDefault] | None = None,
) -> IntervalBoundary:
    """Succinct shorthand for generating an IntervalBoundary"""
    return IntervalBoundary(
        moment=start,
        started_controls=start_c or [],
        started_defaults=start_d or [],
        finished_controls=finish_c or [],
        finished_defaults=finish_d or [],
    )


def ai(
    active_from: datetime,
    active_to: datetime | None,
    controls: list[CSIPAusControl] | None = None,
    default: CSIPAusDefault | None = None,
) -> ActiveInterval:
    """Succinct shorthand for generating an ActiveInterval - a None default means the implied DEFAULT_DEFAULT"""
    return ActiveInterval(
        active_from=active_from,
        active_to=active_to,
        active_controls=controls or [],
        active_default=default if default is not None else DEFAULT_DEFAULT,
    )


def av(
    connect: bool | None = None,
    energize: bool | None = None,
    import_limit_watts: int | None = None,
    export_limit_watts: int | None = None,
    load_limit_watts: int | None = None,
    generation_limit_watts: int | None = None,
    storage_target_watts: int | None = None,
    ramp_percent_max_second_hundredths: int | None = None,
    ramp_time_seconds: int | None = None,
) -> ActiveValues:
    """Succinct shorthand for generating an ActiveValues"""
    return ActiveValues(
        connect=connect,
        energize=energize,
        import_limit_watts=import_limit_watts,
        export_limit_watts=export_limit_watts,
        load_limit_watts=load_limit_watts,
        generation_limit_watts=generation_limit_watts,
        storage_target_watts=storage_target_watts,
        ramp_percent_max_second_hundredths=ramp_percent_max_second_hundredths,
        ramp_time_seconds=ramp_time_seconds,
    )


def c_vals(
    primacy: int,
    started_at: datetime,
    connect: bool | None = None,
    energize: bool | None = None,
    import_limit_watts: int | None = None,
    export_limit_watts: int | None = None,
    load_limit_watts: int | None = None,
    generation_limit_watts: int | None = None,
    storage_target_watts: int | None = None,
    ramp_time_seconds: int | None = None,
) -> CSIPAusControl:
    """Succinct way of setting control values"""
    seed = int(connect or 0)
    seed = seed << 1 + (energize or 0)
    seed = seed << 1 + (import_limit_watts or 0)
    seed = seed << 1 + (export_limit_watts or 0)
    seed = seed << 1 + (load_limit_watts or 0)
    seed = seed << 1 + (generation_limit_watts or 0)
    seed = seed << 1 + (storage_target_watts or 0)
    seed = seed << 1 + (ramp_time_seconds or 0)
    seed += int(started_at.timestamp())
    return generate_class_instance(
        CSIPAusControl,
        seed=clamp_seed(seed),
        primacy=primacy,
        started_at=started_at,
        connect=connect,
        energize=energize,
        import_limit_watts=import_limit_watts,
        export_limit_watts=export_limit_watts,
        load_limit_watts=load_limit_watts,
        generation_limit_watts=generation_limit_watts,
        storage_target_watts=storage_target_watts,
        ramp_time_seconds=ramp_time_seconds,
    )


def d_vals(
    connect: bool | None = None,
    energize: bool | None = None,
    import_limit_watts: int | None = None,
    export_limit_watts: int | None = None,
    load_limit_watts: int | None = None,
    generation_limit_watts: int | None = None,
    storage_target_watts: int | None = None,
    ramp_percent_max_second_hundredths: int | None = None,
) -> CSIPAusDefault:
    """Succinct way of setting default values"""
    seed = int(connect or 0)
    seed = seed << 1 + (energize or 0)
    seed = seed << 1 + (import_limit_watts or 0)
    seed = seed << 1 + (export_limit_watts or 0)
    seed = seed << 1 + (load_limit_watts or 0)
    seed = seed << 1 + (generation_limit_watts or 0)
    seed = seed << 1 + (storage_target_watts or 0)
    seed = seed << 1 + (ramp_percent_max_second_hundredths or 0)
    return generate_class_instance(
        CSIPAusDefault,
        seed=clamp_seed(seed),
        active_range=None,
        connect=connect,
        energize=energize,
        import_limit_watts=import_limit_watts,
        export_limit_watts=export_limit_watts,
        load_limit_watts=load_limit_watts,
        generation_limit_watts=generation_limit_watts,
        storage_target_watts=storage_target_watts,
        ramp_percent_max_second_hundredths=ramp_percent_max_second_hundredths,
    )


def assert_default_equal(expected: HasDefaultValues, actual: HasDefaultValues) -> None:
    """Compares a resolved active default - handles both a real CSIPAusDefault and the implied DEFAULT_DEFAULT"""
    if expected is DEFAULT_DEFAULT:
        assert actual is DEFAULT_DEFAULT
    else:
        assert isinstance(actual, CSIPAusDefault)
        assert_class_instance_equality(CSIPAusDefault, expected, actual, ignored_properties={"active_range"})


def assert_ai_equal(expected: ActiveInterval, actual: ActiveInterval) -> None:
    assert expected.active_from == actual.active_from
    assert expected.active_to == actual.active_to
    assert_controls_equal_unordered(expected.active_controls, actual.active_controls)
    assert_default_equal(expected.active_default, actual.active_default)


CTL_1_9 = c(dt(1), dt(9))
CTL_1_10 = c(dt(1), dt(10))
CTL_1_10_CANCEL_3 = c(dt(1), dt(10), cancelled=dt(3))
CTL_1_10_SUPERSEDE_4 = c(dt(1), dt(10), superseded=dt(4))
CTL_1_11 = c(dt(1), dt(11))
CTL_2_5 = c(dt(2), dt(5))
CTL_3_5 = c(dt(3), dt(5))


CTL_1_3 = c(dt(1), dt(3))
CTL_3_10 = c(dt(3), dt(10))
CTL_5_7 = c(dt(5), dt(7))
CTL_1_MAX = c(dt(1), DEFAULT_MAX_DATE)
CTL_1_5_CANCEL_9 = c(dt(1), dt(5), cancelled=dt(9))
CTL_1_10_CANCEL_8_SUPERSEDE_4 = c(dt(1), dt(10), cancelled=dt(8), superseded=dt(4))


DEF_1_9 = d(dt(1), dt(9))
DEF_1_10 = d(dt(1), dt(10))
DEF_1_11 = d(dt(1), dt(11))
DEF_2_5 = d(dt(2), dt(5))
DEF_1_MAX = d(dt(1))
DEF_1_4 = d(dt(1), dt(4))
DEF_1_5 = d(dt(1), dt(5))
DEF_4_9 = d(dt(4), dt(9))
DEF_5_10 = d(dt(5), dt(10))
DEF_9_MAX = d(dt(9))


def assert_controls_equal_unordered(expected: list[CSIPAusControl], actual: list[CSIPAusControl]) -> None:
    """This is an ordering invariant compare"""
    assert len(expected) == len(actual)
    for e, a in zip(
        sorted(expected, key=lambda e: e.csipaus_control_id),
        sorted(actual, key=lambda e: e.csipaus_control_id),
        strict=True,
    ):
        assert_class_instance_equality(CSIPAusControl, e, a)


def assert_defaults_equal_unordered(expected: list[CSIPAusDefault], actual: list[CSIPAusDefault]) -> None:
    """This is an ordering invariant compare"""
    assert len(expected) == len(actual)
    for e, a in zip(
        sorted(expected, key=lambda e: e.csipaus_default_id),
        sorted(actual, key=lambda e: e.csipaus_default_id),
        strict=True,
    ):
        assert_class_instance_equality(CSIPAusDefault, e, a, ignored_properties={"active_range"})


def test_assert_controls_equal_unordered():
    """Sanity checks"""
    C1 = c(dt(1), dt(2))
    C2 = c(dt(1), dt(3))
    C3 = c(dt(1), dt(4))
    assert_controls_equal_unordered([], [])
    assert_controls_equal_unordered([C1], [C1])
    assert_controls_equal_unordered([C1, C3, C2], [C1, C3, C2])
    assert_controls_equal_unordered([C1, C2, C3], [C1, C3, C2])
    assert_controls_equal_unordered([C1, C1, C2], [C1, C2, C1])
    assert_controls_equal_unordered([C1, C1, C2], [C1, C2, C1])  # dupes

    with pytest.raises(AssertionError):
        assert_controls_equal_unordered([C1], [])
    with pytest.raises(AssertionError):
        assert_controls_equal_unordered([C1], [C1, C1])
    with pytest.raises(AssertionError):
        assert_controls_equal_unordered([C1, C1], [C1])
    with pytest.raises(AssertionError):
        assert_controls_equal_unordered([C1, C2], [C1, C3])


def test_assert_defaults_equal_unordered():
    """Sanity checks"""
    D1 = d(dt(1), dt(2))
    D2 = d(dt(1), dt(3))
    D3 = d(dt(1), dt(4))
    assert_defaults_equal_unordered([], [])
    assert_defaults_equal_unordered([D1], [D1])
    assert_defaults_equal_unordered([D1, D1], [D1, D1])
    assert_defaults_equal_unordered([D1, D3, D2], [D1, D3, D2])
    assert_defaults_equal_unordered([D1, D2, D3], [D1, D3, D2])
    assert_defaults_equal_unordered([D1, D1, D2], [D1, D2, D1])
    assert_defaults_equal_unordered([D1, D1, D2], [D1, D2, D1])  # dupes

    with pytest.raises(AssertionError):
        assert_defaults_equal_unordered([D1], [])
    with pytest.raises(AssertionError):
        assert_defaults_equal_unordered([D1], [D1, D1])
    with pytest.raises(AssertionError):
        assert_defaults_equal_unordered([D1, D1], [D1, D2])
    with pytest.raises(AssertionError):
        assert_defaults_equal_unordered([D1, D1], [D1])
    with pytest.raises(AssertionError):
        assert_defaults_equal_unordered([D1, D2], [D1, D3])


@pytest.mark.parametrize(
    "control, expected",
    [
        (CTL_1_10, dt(10)),  # No early finish - the natural finished_at is used
        (CTL_1_10_CANCEL_3, dt(3)),  # cancelled_at brings the finish forward
        (CTL_1_10_SUPERSEDE_4, dt(4)),  # superseded_at brings the finish forward
        (CTL_1_10_CANCEL_8_SUPERSEDE_4, dt(4)),  # both set - the earliest of the three wins
        (CTL_1_5_CANCEL_9, dt(5)),  # cancelled_at AFTER finished_at is ignored
        (c(dt(1), dt(5), superseded=dt(9)), dt(5)),  # superseded_at AFTER finished_at is ignored
    ],
)
def test_get_control_finished_at(control: CSIPAusControl, expected: datetime):
    assert get_control_finished_at(control) == expected


def assert_ib_equal(expected: IntervalBoundary, actual: IntervalBoundary) -> None:
    assert expected.moment == actual.moment

    assert_controls_equal_unordered(expected.started_controls, actual.started_controls)
    assert_defaults_equal_unordered(expected.started_defaults, actual.started_defaults)
    assert_controls_equal_unordered(expected.finished_controls, actual.finished_controls)
    assert_defaults_equal_unordered(expected.finished_defaults, actual.finished_defaults)


@pytest.mark.parametrize(
    "new_boundary, upcoming_end_boundaries, expected_intermediates, expected_next, expected_removed_indexes",
    [
        #
        # The following all use shorthand functions for brevity (see above for details)
        #
        (dt(1), [], [], IntervalBoundary(dt(1), [], [], [], []), []),
        # Single controls/defaults
        (dt(10), [(dt(9), None, CTL_1_9)], [ib(dt(9), finish_c=[CTL_1_9])], ib(dt(10)), [0]),
        (dt(10), [(dt(10), None, CTL_1_10)], [], ib(dt(10), finish_c=[CTL_1_10]), [0]),
        (dt(10), [(dt(11), None, CTL_1_11)], [], ib(dt(10)), []),
        (dt(10), [(dt(9), DEF_1_9, None)], [ib(dt(9), finish_d=[DEF_1_9])], ib(dt(10)), [0]),
        (dt(10), [(dt(10), DEF_1_10, None)], [], ib(dt(10), finish_d=[DEF_1_10]), [0]),
        (dt(10), [(dt(11), DEF_1_11, None)], [], ib(dt(10)), []),
        # combination - both preceding new_boundary
        (
            dt(10),
            [(dt(9), DEF_1_9, None), (dt(9), None, CTL_1_9), (dt(11), DEF_1_11, None)],
            [ib(dt(9), finish_c=[CTL_1_9], finish_d=[DEF_1_9])],
            ib(dt(10)),
            [0, 1],
        ),
        # combination - both aligning with new_boundary
        (
            dt(10),
            [(dt(10), DEF_1_10, None), (dt(10), None, CTL_1_10), (dt(11), DEF_1_11, None)],
            [],
            ib(dt(10), finish_c=[CTL_1_10], finish_d=[DEF_1_10]),
            [0, 1],
        ),
        # multiple combinations - with some aligning with new_boundary
        (
            dt(10),
            [
                (dt(5), DEF_2_5, None),
                (dt(5), None, CTL_2_5),
                (dt(5), None, CTL_3_5),
                (dt(9), DEF_1_9, None),
                (dt(10), None, CTL_1_10),
                (dt(11), DEF_1_11, None),
            ],
            [ib(dt(5), finish_c=[CTL_2_5, CTL_3_5], finish_d=[DEF_2_5]), ib(dt(9), finish_d=[DEF_1_9])],
            ib(dt(10), finish_c=[CTL_1_10]),
            [0, 1, 2, 3, 4],
        ),
        # multiple entry tuple - technically shouldn't happen but no reason to NOT support
        (
            dt(10),
            [(dt(9), DEF_1_9, CTL_1_9), (dt(10), DEF_1_10, CTL_1_10), (dt(11), DEF_1_11, CTL_1_11)],
            [ib(dt(9), finish_c=[CTL_1_9], finish_d=[DEF_1_9])],
            ib(dt(10), finish_c=[CTL_1_10], finish_d=[DEF_1_10]),
            [0, 1],
        ),
        # multiple distinct end times all strictly before new_boundary - each gets its own intermediate
        # and the returned "next" boundary is a fresh empty one
        (
            dt(20),
            [(dt(5), None, CTL_2_5), (dt(9), DEF_1_9, None), (dt(11), None, CTL_1_11)],
            [ib(dt(5), finish_c=[CTL_2_5]), ib(dt(9), finish_d=[DEF_1_9]), ib(dt(11), finish_c=[CTL_1_11])],
            ib(dt(20)),
            [0, 1, 2],
        ),
        # new_boundary == DEFAULT_MAX_DATE with an entry finishing exactly there - this is the real
        # end-of-run drain: the closing boundary is popped out of the intermediates, not freshly made
        (
            DEFAULT_MAX_DATE,
            [(dt(9), None, CTL_1_9), (DEFAULT_MAX_DATE, DEF_1_MAX, None)],
            [ib(dt(9), finish_c=[CTL_1_9])],
            ib(DEFAULT_MAX_DATE, finish_d=[DEF_1_MAX]),
            [0, 1],
        ),
    ],
)
def test_drain_intermediate_end_boundaries(
    new_boundary: datetime,
    upcoming_end_boundaries: list[tuple[datetime, CSIPAusDefault | None, CSIPAusControl | None]],
    expected_intermediates: list[IntervalBoundary],
    expected_next: IntervalBoundary,
    expected_removed_indexes: list[int],
):
    original_upcoming_end_boundaries = list(upcoming_end_boundaries)

    actual = drain_intermediate_end_boundaries(new_boundary, upcoming_end_boundaries)
    assert isinstance(actual, tuple) and len(actual) == 2
    actual_intermediates, actual_next = actual

    # Value check
    assert_ib_equal(expected_next, actual_next)
    assert len(expected_intermediates) == len(actual_intermediates)
    for e, a in zip(expected_intermediates, actual_intermediates, strict=True):
        assert_ib_equal(e, a)

    # Type check
    assert_list_type(IntervalBoundary, actual_intermediates, count=len(expected_intermediates))
    assert isinstance(actual_next, IntervalBoundary)

    # Ensure removal occurred
    assert len(original_upcoming_end_boundaries) == len(upcoming_end_boundaries) + len(expected_removed_indexes)
    for removed_idx in expected_removed_indexes:
        assert original_upcoming_end_boundaries[removed_idx] not in upcoming_end_boundaries

    # Invariant: intermediates are strictly ascending, unique, and always strictly before new_boundary
    # (any ending that lands exactly on new_boundary is returned as actual_next instead)
    intermediate_moments = [b.moment for b in actual_intermediates]
    assert intermediate_moments == sorted(intermediate_moments)
    assert len(set(intermediate_moments)) == len(intermediate_moments)
    assert all(moment < new_boundary for moment in intermediate_moments)


@pytest.mark.parametrize(
    "now, defaults, controls, expected",
    [
        #
        # The following all use shorthand functions for brevity (see above for details)
        #
        (dt(1), [], [], [ib(dt(1)), ib(DEFAULT_MAX_DATE)]),
        # Singular
        (dt(1), [DEF_1_10], [], [ib(dt(1), start_d=[DEF_1_10]), ib(dt(10), finish_d=[DEF_1_10]), ib(DEFAULT_MAX_DATE)]),
        (dt(5), [DEF_1_10], [], [ib(dt(5), start_d=[DEF_1_10]), ib(dt(10), finish_d=[DEF_1_10]), ib(DEFAULT_MAX_DATE)]),
        (dt(1), [], [CTL_1_10], [ib(dt(1), start_c=[CTL_1_10]), ib(dt(10), finish_c=[CTL_1_10]), ib(DEFAULT_MAX_DATE)]),
        (dt(5), [], [CTL_1_10], [ib(dt(5), start_c=[CTL_1_10]), ib(dt(10), finish_c=[CTL_1_10]), ib(DEFAULT_MAX_DATE)]),
        # Testing control superseded/deleted
        (
            dt(1),
            [],
            [CTL_1_10_CANCEL_3],
            [ib(dt(1), start_c=[CTL_1_10_CANCEL_3]), ib(dt(3), finish_c=[CTL_1_10_CANCEL_3]), ib(DEFAULT_MAX_DATE)],
        ),
        (
            dt(2),
            [],
            [CTL_1_10_CANCEL_3],
            [ib(dt(2), start_c=[CTL_1_10_CANCEL_3]), ib(dt(3), finish_c=[CTL_1_10_CANCEL_3]), ib(DEFAULT_MAX_DATE)],
        ),
        (
            dt(1),
            [],
            [CTL_1_10_SUPERSEDE_4],
            [
                ib(dt(1), start_c=[CTL_1_10_SUPERSEDE_4]),
                ib(dt(4), finish_c=[CTL_1_10_SUPERSEDE_4]),
                ib(DEFAULT_MAX_DATE),
            ],
        ),
        (
            dt(2),
            [],
            [CTL_1_10_SUPERSEDE_4],
            [
                ib(dt(2), start_c=[CTL_1_10_SUPERSEDE_4]),
                ib(dt(4), finish_c=[CTL_1_10_SUPERSEDE_4]),
                ib(DEFAULT_MAX_DATE),
            ],
        ),
        # Testing collisions on boundaries
        (
            dt(1),
            [DEF_1_10, DEF_1_11, DEF_1_9, DEF_2_5],
            [CTL_1_10, CTL_1_10_CANCEL_3, CTL_2_5, CTL_3_5],
            [
                ib(dt(1), start_c=[CTL_1_10, CTL_1_10_CANCEL_3], start_d=[DEF_1_10, DEF_1_11, DEF_1_9]),
                ib(dt(2), start_c=[CTL_2_5], start_d=[DEF_2_5]),
                ib(dt(3), start_c=[CTL_3_5], finish_c=[CTL_1_10_CANCEL_3]),
                ib(dt(5), finish_c=[CTL_2_5, CTL_3_5], finish_d=[DEF_2_5]),
                ib(dt(9), finish_d=[DEF_1_9]),
                ib(dt(10), finish_c=[CTL_1_10], finish_d=[DEF_1_10]),
                ib(dt(11), finish_d=[DEF_1_11]),
                ib(DEFAULT_MAX_DATE),
            ],
        ),
        # Entity running all the way to DEFAULT_MAX_DATE - its finish lands on the closing boundary
        (dt(1), [DEF_1_MAX], [], [ib(dt(1), start_d=[DEF_1_MAX]), ib(DEFAULT_MAX_DATE, finish_d=[DEF_1_MAX])]),
        (dt(1), [], [CTL_1_MAX], [ib(dt(1), start_c=[CTL_1_MAX]), ib(DEFAULT_MAX_DATE, finish_c=[CTL_1_MAX])]),
        # Entity that only starts in the future - the leading "now" boundary is still emitted but empty
        (
            dt(1),
            [],
            [CTL_2_5],
            [
                ib(dt(1)),
                ib(dt(2), start_c=[CTL_2_5]),
                ib(dt(5), finish_c=[CTL_2_5]),
                ib(DEFAULT_MAX_DATE),
            ],
        ),
        # Two non-overlapping controls with a gap of no coverage in between
        (
            dt(1),
            [],
            [CTL_1_3, CTL_5_7],
            [
                ib(dt(1), start_c=[CTL_1_3]),
                ib(dt(3), finish_c=[CTL_1_3]),
                ib(dt(5), start_c=[CTL_5_7]),
                ib(dt(7), finish_c=[CTL_5_7]),
                ib(DEFAULT_MAX_DATE),
            ],
        ),
    ],
)
def test_generate_interval_boundaries(
    now: datetime, defaults: list[CSIPAusDefault], controls: list[CSIPAusControl], expected: list[IntervalBoundary]
):
    actual = generate_interval_boundaries(now, defaults, controls)

    assert_list_type(IntervalBoundary, actual, count=len(expected))

    # Invariant: boundaries are strictly ascending with a unique moment each (the whole point is to
    # collapse simultaneous starts/finishes into one shared boundary)
    moments = [b.moment for b in actual]
    assert moments == sorted(moments)
    assert len(set(moments)) == len(moments)

    for idx, (e, a) in enumerate(zip(expected, actual, strict=True)):
        try:
            assert_ib_equal(e, a)
        except Exception as exc:
            raise Exception(f"Exception at idx {idx}") from exc


@pytest.mark.parametrize(
    "now, defaults, controls, expected",
    [
        #
        # The following all use shorthand functions for brevity (see above for details)
        #
        # Nothing at all - a single default-default interval then the unbounded tail from DEFAULT_MAX_DATE
        (dt(1), [], [], [ai(dt(1), None)]),
        # Singular default - covered, then falls back to the default-default once it finishes
        (
            dt(1),
            [DEF_1_10],
            [],
            [ai(dt(1), dt(10), default=DEF_1_10), ai(dt(10), None)],
        ),
        # Default whose started_at precedes now - clamped forward to now
        (
            dt(5),
            [DEF_1_10],
            [],
            [ai(dt(5), dt(10), default=DEF_1_10), ai(dt(10), None)],
        ),
        # Default that never finishes (runs to DEFAULT_MAX_DATE) - no default-default gap
        (dt(1), [DEF_1_MAX], [], [ai(dt(1), None, default=DEF_1_MAX)]),
        # Singular control
        (
            dt(1),
            [],
            [CTL_1_10],
            [ai(dt(1), dt(10), controls=[CTL_1_10]), ai(dt(10), None)],
        ),
        # Control whose started_at precedes now - clamped forward to now
        (
            dt(5),
            [],
            [CTL_1_10],
            [ai(dt(5), dt(10), controls=[CTL_1_10]), ai(dt(10), None)],
        ),
        # Control that never finishes (runs to DEFAULT_MAX_DATE)
        (dt(1), [], [CTL_1_MAX], [ai(dt(1), None, controls=[CTL_1_MAX])]),
        # Control that only starts in the future - leading interval is default-default with no controls
        (
            dt(1),
            [],
            [CTL_2_5],
            [
                ai(dt(1), dt(2)),
                ai(dt(2), dt(5), controls=[CTL_2_5]),
                ai(dt(5), None),
            ],
        ),
        # Two non-overlapping controls - the gap between them is a bare default-default interval
        (
            dt(1),
            [],
            [CTL_1_3, CTL_5_7],
            [
                ai(dt(1), dt(3), controls=[CTL_1_3]),
                ai(dt(3), dt(5)),
                ai(dt(5), dt(7), controls=[CTL_5_7]),
                ai(dt(7), None),
            ],
        ),
        # Overlapping controls - middle interval carries both
        (
            dt(1),
            [],
            [CTL_1_10, CTL_2_5],
            [
                ai(dt(1), dt(2), controls=[CTL_1_10]),
                ai(dt(2), dt(5), controls=[CTL_1_10, CTL_2_5]),
                ai(dt(5), dt(10), controls=[CTL_1_10]),
                ai(dt(10), None),
            ],
        ),
        # A control overlapping a default - both resolved together on the shared interval
        (
            dt(1),
            [DEF_1_10],
            [CTL_2_5],
            [
                ai(dt(1), dt(2), default=DEF_1_10),
                ai(dt(2), dt(5), controls=[CTL_2_5], default=DEF_1_10),
                ai(dt(5), dt(10), default=DEF_1_10),
                ai(dt(10), None),
            ],
        ),
        # Back-to-back defaults - each interval resolves to exactly the one that is active
        (
            dt(1),
            [DEF_1_5, DEF_5_10],
            [],
            [
                ai(dt(1), dt(5), default=DEF_1_5),
                ai(dt(5), dt(10), default=DEF_5_10),
                ai(dt(10), None),
            ],
        ),
        # now clamps a default and multiple controls onto a single shared opening boundary
        (
            dt(5),
            [DEF_1_10],
            [CTL_1_10, CTL_3_10],
            [
                ai(dt(5), dt(10), controls=[CTL_1_10, CTL_3_10], default=DEF_1_10),
                ai(dt(10), None),
            ],
        ),
        # Cancelled control finishes early
        (
            dt(1),
            [],
            [CTL_1_10_CANCEL_3],
            [
                ai(dt(1), dt(3), controls=[CTL_1_10_CANCEL_3]),
                ai(dt(3), None),
            ],
        ),
        # Everything at once - overlapping controls (one cancelled early) across three back-to-back defaults
        (
            dt(1),
            [DEF_1_4, DEF_4_9, DEF_9_MAX],
            [CTL_1_10, CTL_1_10_CANCEL_3, CTL_2_5, CTL_3_5],
            [
                ai(dt(1), dt(2), controls=[CTL_1_10, CTL_1_10_CANCEL_3], default=DEF_1_4),
                ai(dt(2), dt(3), controls=[CTL_1_10, CTL_1_10_CANCEL_3, CTL_2_5], default=DEF_1_4),
                ai(dt(3), dt(4), controls=[CTL_1_10, CTL_2_5, CTL_3_5], default=DEF_1_4),
                ai(dt(4), dt(5), controls=[CTL_1_10, CTL_2_5, CTL_3_5], default=DEF_4_9),
                ai(dt(5), dt(9), controls=[CTL_1_10], default=DEF_4_9),
                ai(dt(9), dt(10), controls=[CTL_1_10], default=DEF_9_MAX),
                ai(dt(10), None, default=DEF_9_MAX),
            ],
        ),
    ],
)
def test_generate_intervals(
    now: datetime, defaults: list[CSIPAusDefault], controls: list[CSIPAusControl], expected: list[ActiveInterval]
):
    # Act
    actual = generate_intervals(now, defaults, controls)

    # Assert
    assert_list_type(ActiveInterval, actual, count=len(expected))

    # Structural invariants: first interval opens at now, the sequence is contiguous with no gaps/overlaps,
    # every interval bar the last is bounded and non-empty, and only the final interval is unbounded
    assert actual[0].active_from == now
    assert actual[-1].active_to is None
    for earlier, later in zip(actual, actual[1:], strict=False):
        assert earlier.active_to == later.active_from
        assert earlier.active_to is not None and earlier.active_from < earlier.active_to

    for idx, (e, a) in enumerate(zip(expected, actual, strict=True)):
        try:
            assert_ai_equal(e, a)
        except Exception as exc:
            raise Exception(f"Exception at idx {idx}") from exc


def test_generate_intervals_raises_for_overlapping_defaults():
    """Two defaults active over the same instant is a data-integrity violation (the DB enforces it with an
    exclusion constraint). If it somehow reaches this code it must fail loudly rather than silently drop one."""
    with pytest.raises(ValueError):
        generate_intervals(dt(1), [DEF_1_10, DEF_2_5], [])


def test_active_interval_to_control_values_default_vals():
    """Assign unique values for defaults - ensure they appear on the right ActiveValues. Attempt to catch weird
    copy paste issues."""
    for seed in [101, 202]:
        d = generate_class_instance(CSIPAusDefault, seed=seed, active_range=None)
        ai = ActiveInterval(active_from=dt(1), active_to=dt(2), active_controls=[], active_default=d)
        actual = active_interval_to_control_values(ai)
        assert actual.values.ramp_time_seconds is None, "Cant be set via default"
        assert_class_instance_equality(DefaultValues, d, actual.values)


def test_active_interval_to_control_values_control_vals():
    """Assign unique values for controls - ensure they appear on the right ActiveValues. Attempt to catch weird
    copy paste issues."""
    for seed in [101, 202]:
        c = generate_class_instance(CSIPAusControl, seed=seed)
        ai = ActiveInterval(active_from=dt(1), active_to=dt(2), active_controls=[c], active_default=DEFAULT_DEFAULT)
        actual = active_interval_to_control_values(ai)
        assert actual.values.ramp_percent_max_second_hundredths is None, "Cant be set via control"
        assert_class_instance_equality(ControlValues, c, actual.values)


@pytest.mark.parametrize(
    "controls, defaults, expected_values",
    [
        #
        # The following all use shorthand functions for brevity (see above for details)
        #
        # Empty interval
        ([], DEFAULT_DEFAULT, av()),
        ([c_vals(1, dt(1))], DEFAULT_DEFAULT, av()),
        ([c_vals(1, dt(1)), c_vals(1, dt(1))], DEFAULT_DEFAULT, av()),
        ([c_vals(2, dt(1)), c_vals(1, dt(1))], DEFAULT_DEFAULT, av()),
        # Default failover (full coverage for fields is in other tests)
        (
            [],
            d_vals(import_limit_watts=1, load_limit_watts=2),
            av(import_limit_watts=1, load_limit_watts=2),
        ),
        (
            [],
            d_vals(connect=True, export_limit_watts=1, generation_limit_watts=2, ramp_percent_max_second_hundredths=3),
            av(connect=True, export_limit_watts=1, generation_limit_watts=2, ramp_percent_max_second_hundredths=3),
        ),
        # Multiple controls (with primacy) (full coverage for fields is in other tests)
        (
            [
                # From lowest priority to highest priority
                c_vals(30, dt(1), connect=True, energize=True, import_limit_watts=1, export_limit_watts=2),
                c_vals(20, dt(1), import_limit_watts=3, export_limit_watts=4),
                c_vals(10, dt(1), import_limit_watts=5, energize=False, export_limit_watts=6, generation_limit_watts=7),
                c_vals(10, dt(2), import_limit_watts=8),
            ],
            DEFAULT_DEFAULT,
            av(connect=True, energize=False, import_limit_watts=8, export_limit_watts=6, generation_limit_watts=7),
        ),
        # Combo of everything
        (
            [
                # From lowest priority to highest priority
                c_vals(30, dt(1), export_limit_watts=0),
                c_vals(30, dt(2), connect=False, energize=False, import_limit_watts=1, export_limit_watts=2),
                c_vals(10, dt(1), import_limit_watts=3, energize=False, generation_limit_watts=4),
                c_vals(10, dt(2), import_limit_watts=0),
                c_vals(2, dt(1)),
                c_vals(1, dt(1), connect=True, energize=True),
                c_vals(0, dt(1)),
            ],
            d_vals(connect=False, energize=True, import_limit_watts=6, storage_target_watts=7),
            av(
                connect=True,
                energize=True,
                import_limit_watts=0,
                export_limit_watts=2,
                generation_limit_watts=4,
                storage_target_watts=7,
            ),
        ),
    ],
)
def test_active_interval_to_control_values(
    controls: list[CSIPAusControl], defaults: HasDefaultValues, expected_values: ActiveValues
):

    # Arrange
    active_from = datetime.now(UTC) + timedelta(hours=len(controls))
    active_to = datetime.now(UTC) + timedelta(hours=len(controls) + 2)

    # Act (we will shuffle the control list multiple ways to ensure that its invariant to ordering)
    actuals: list[ScheduledControlValues] = []

    # As is
    actuals.append(
        active_interval_to_control_values(
            ActiveInterval(
                active_from=active_from, active_to=active_to, active_controls=controls, active_default=defaults
            )
        )
    )

    # Reversed
    controls.reverse()
    actuals.append(
        active_interval_to_control_values(
            ActiveInterval(
                active_from=active_from, active_to=active_to, active_controls=controls, active_default=defaults
            )
        )
    )

    # shuffled a few times
    for _ in range(10):
        random.shuffle(controls)
        actuals.append(
            active_interval_to_control_values(
                ActiveInterval(
                    active_from=active_from, active_to=active_to, active_controls=controls, active_default=defaults
                )
            )
        )

    # Assert
    for actual in actuals:
        assert isinstance(actual, ScheduledControlValues)
        assert actual.active_from == active_from
        assert actual.active_to == active_to
        assert isinstance(actual.values, ActiveValues)
        assert actual.values == expected_values


def test_active_interval_to_control_max_date():

    # Arrange
    active_from = datetime.now(UTC)

    # Act (we will shuffle the control list multiple ways to ensure that its invariant to ordering)
    actual = active_interval_to_control_values(
        ActiveInterval(active_from=active_from, active_to=None, active_controls=[], active_default=d_vals())
    )

    # Assert
    assert actual.active_from == active_from
    assert actual.active_to is None


@pytest.mark.parametrize(
    "now, expected",
    [
        # Deliberately miss everything
        (
            datetime(2030, 1, 1, tzinfo=UTC),
            [
                ScheduledControlValues(
                    datetime(2030, 1, 1, tzinfo=UTC),
                    None,
                    av(
                        connect=True,  # from Default
                        energize=True,  # from Default
                        import_limit_watts=3001,  # from Default
                        export_limit_watts=3002,  # from Default
                        load_limit_watts=3003,  # from Default
                        generation_limit_watts=3004,  # from Default
                        storage_target_watts=3005,  # from Default
                        ramp_percent_max_second_hundredths=31,  # from Default
                    ),
                ),
            ],
        ),
        # Include everything
        (
            datetime(2010, 1, 1, tzinfo=UTC),
            [
                ScheduledControlValues(datetime(2010, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC), av()),
                ScheduledControlValues(
                    datetime(2026, 1, 1, tzinfo=UTC),
                    datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    av(
                        connect=True,  # from Default
                        energize=False,  # from Default
                        ramp_time_seconds=700,
                        import_limit_watts=701,
                        export_limit_watts=702,
                        load_limit_watts=703,
                        generation_limit_watts=704,
                        storage_target_watts=705,
                        ramp_percent_max_second_hundredths=11,  # from Default
                    ),
                ),
                ScheduledControlValues(
                    datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
                    av(
                        connect=False,  # from Default
                        energize=True,  # from Default
                        ramp_time_seconds=700,
                        import_limit_watts=701,
                        export_limit_watts=702,
                        load_limit_watts=703,
                        generation_limit_watts=704,
                        storage_target_watts=705,
                        ramp_percent_max_second_hundredths=21,  # from Default
                    ),
                ),
                ScheduledControlValues(
                    datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
                    datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
                    av(
                        connect=True,  # from Default
                        energize=True,  # from Default
                        ramp_time_seconds=500,
                        import_limit_watts=301,
                        export_limit_watts=302,
                        load_limit_watts=303,
                        generation_limit_watts=304,
                        storage_target_watts=305,
                        ramp_percent_max_second_hundredths=31,  # from Default
                    ),
                ),
                ScheduledControlValues(
                    datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
                    None,
                    av(
                        connect=True,  # from Default
                        energize=True,  # from Default
                        import_limit_watts=3001,  # from Default
                        export_limit_watts=3002,  # from Default
                        load_limit_watts=3003,  # from Default
                        generation_limit_watts=3004,  # from Default
                        storage_target_watts=3005,  # from Default
                        ramp_percent_max_second_hundredths=31,  # from Default
                    ),
                ),
            ],
        ),
    ],
)
async def test_calculate_schedule_values(pg_base_config, now: datetime, expected: list[ScheduledControlValues]):
    """Checks the high level behaviour all comes together with a real DB - more granular testing is done in the other
    unit tests"""

    async with generate_async_session(pg_base_config) as session:
        actual = await calculate_schedule_values(session, now)
        assert actual == expected
        assert_list_type(ScheduledControlValues, actual, count=len(expected))
