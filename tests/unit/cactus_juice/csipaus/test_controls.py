from datetime import UTC, datetime, timedelta

import pytest
from assertical.asserts.generator import assert_class_instance_equality
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance

from cactus_juice.crud import DEFAULT_MAX_DATE
from cactus_juice.csipaus.controls import (
    DEFAULT_DEFAULT,
    ActiveInterval,
    IntervalBoundary,
    drain_intermediate_end_boundaries,
    generate_interval_boundaries,
    generate_intervals,
    get_control_finished_at,
)
from cactus_juice.csipaus.dto import HasDefaultValues
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


@pytest.mark.parametrize(
    "now, defaults, controls, expected",
    [
        #
        # The following all use shorthand functions for brevity (see above for details)
        #
        # Nothing at all - a single default-default interval then the unbounded tail from DEFAULT_MAX_DATE
        (dt(1), [], [], [ai(dt(1), DEFAULT_MAX_DATE), ai(DEFAULT_MAX_DATE, None)]),
        # Singular default - covered, then falls back to the default-default once it finishes
        (
            dt(1),
            [DEF_1_10],
            [],
            [ai(dt(1), dt(10), default=DEF_1_10), ai(dt(10), DEFAULT_MAX_DATE), ai(DEFAULT_MAX_DATE, None)],
        ),
        # Default whose started_at precedes now - clamped forward to now
        (
            dt(5),
            [DEF_1_10],
            [],
            [ai(dt(5), dt(10), default=DEF_1_10), ai(dt(10), DEFAULT_MAX_DATE), ai(DEFAULT_MAX_DATE, None)],
        ),
        # Default that never finishes (runs to DEFAULT_MAX_DATE) - no default-default gap
        (dt(1), [DEF_1_MAX], [], [ai(dt(1), DEFAULT_MAX_DATE, default=DEF_1_MAX), ai(DEFAULT_MAX_DATE, None)]),
        # Singular control
        (
            dt(1),
            [],
            [CTL_1_10],
            [ai(dt(1), dt(10), controls=[CTL_1_10]), ai(dt(10), DEFAULT_MAX_DATE), ai(DEFAULT_MAX_DATE, None)],
        ),
        # Control whose started_at precedes now - clamped forward to now
        (
            dt(5),
            [],
            [CTL_1_10],
            [ai(dt(5), dt(10), controls=[CTL_1_10]), ai(dt(10), DEFAULT_MAX_DATE), ai(DEFAULT_MAX_DATE, None)],
        ),
        # Control that never finishes (runs to DEFAULT_MAX_DATE)
        (dt(1), [], [CTL_1_MAX], [ai(dt(1), DEFAULT_MAX_DATE, controls=[CTL_1_MAX]), ai(DEFAULT_MAX_DATE, None)]),
        # Control that only starts in the future - leading interval is default-default with no controls
        (
            dt(1),
            [],
            [CTL_2_5],
            [
                ai(dt(1), dt(2)),
                ai(dt(2), dt(5), controls=[CTL_2_5]),
                ai(dt(5), DEFAULT_MAX_DATE),
                ai(DEFAULT_MAX_DATE, None),
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
                ai(dt(7), DEFAULT_MAX_DATE),
                ai(DEFAULT_MAX_DATE, None),
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
                ai(dt(10), DEFAULT_MAX_DATE),
                ai(DEFAULT_MAX_DATE, None),
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
                ai(dt(10), DEFAULT_MAX_DATE),
                ai(DEFAULT_MAX_DATE, None),
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
                ai(dt(10), DEFAULT_MAX_DATE),
                ai(DEFAULT_MAX_DATE, None),
            ],
        ),
        # now clamps a default and multiple controls onto a single shared opening boundary
        (
            dt(5),
            [DEF_1_10],
            [CTL_1_10, CTL_3_10],
            [
                ai(dt(5), dt(10), controls=[CTL_1_10, CTL_3_10], default=DEF_1_10),
                ai(dt(10), DEFAULT_MAX_DATE),
                ai(DEFAULT_MAX_DATE, None),
            ],
        ),
        # Cancelled control finishes early
        (
            dt(1),
            [],
            [CTL_1_10_CANCEL_3],
            [
                ai(dt(1), dt(3), controls=[CTL_1_10_CANCEL_3]),
                ai(dt(3), DEFAULT_MAX_DATE),
                ai(DEFAULT_MAX_DATE, None),
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
                ai(dt(10), DEFAULT_MAX_DATE, default=DEF_9_MAX),
                ai(DEFAULT_MAX_DATE, None),
            ],
        ),
    ],
)
def test_generate_intervals(
    now: datetime, defaults: list[CSIPAusDefault], controls: list[CSIPAusControl], expected: list[ActiveInterval]
):
    actual = generate_intervals(now, defaults, controls)

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
