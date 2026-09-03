from datetime import UTC, datetime, timedelta

import pytest
from assertical.asserts.generator import assert_class_instance_equality
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance

from cactus_juice.crud import DEFAULT_MAX_DATE
from cactus_juice.csipaus.controls import (
    IntervalBoundary,
    drain_intermediate_end_boundaries,
    generate_interval_boundaries,
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


CTL_1_9 = c(dt(1), dt(9))
CTL_1_10 = c(dt(1), dt(10))
CTL_1_10_CANCEL_3 = c(dt(1), dt(10), cancelled=dt(3))
CTL_1_10_SUPERSEDE_4 = c(dt(1), dt(10), superseded=dt(4))
CTL_1_11 = c(dt(1), dt(11))
CTL_2_5 = c(dt(2), dt(5))
CTL_3_5 = c(dt(3), dt(5))


DEF_1_9 = d(dt(1), dt(9))
DEF_1_10 = d(dt(1), dt(10))
DEF_1_11 = d(dt(1), dt(11))
DEF_2_5 = d(dt(2), dt(5))


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
    ],
)
def test_generate_interval_boundaries(
    now: datetime, defaults: list[CSIPAusDefault], controls: list[CSIPAusControl], expected: list[IntervalBoundary]
):
    actual = generate_interval_boundaries(now, defaults, controls)

    assert_list_type(IntervalBoundary, actual, count=len(expected))
    for idx, (e, a) in enumerate(zip(expected, actual, strict=True)):
        try:
            assert_ib_equal(e, a)
        except Exception as exc:
            raise Exception(f"Exception at idx {idx}") from exc
