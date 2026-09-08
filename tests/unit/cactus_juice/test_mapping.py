import re
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from assertical.asserts.type import assert_dict_type
from cactus_test_definitions.csipaus import (
    CSIPAusReadingLocation,
    CSIPAusReadingType,
)
from envoy_schema.server.schema.sep2.der_control_types import ActivePower
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    KindType,
    UomType,
)

from cactus_juice.error import BaseJuiceError
from cactus_juice.mapping import (
    MirrorUsagePointMrids,
    generate_hashed_mrid,
    generate_mmr_mrids,
    generate_mup_mrids,
    generate_reading_type_values,
    generate_role_flags,
    previous_post_period,
    sep2_to_value,
    value_to_sep2,
)


def assert_mrid(mrid: str, pen: int | None):
    assert isinstance(mrid, str)
    assert len(mrid) == 32
    if pen is not None:
        assert mrid.endswith(str(pen))
    assert re.search(r"[^A-F0-9]", mrid) is None, "Should only be uppercase hex chars"


def assert_mup_mrids(
    m: MirrorUsagePointMrids,
    reading_types: list[CSIPAusReadingType],
    pen: int,
    check_mmr_pens: bool = True,
):
    assert isinstance(m, MirrorUsagePointMrids)
    assert_mrid(m.mup_mrid, pen)

    assert_dict_type(CSIPAusReadingType, str, m.mmr_mrids, len(reading_types))
    for rt in reading_types:
        assert_mrid(m.mmr_mrids[rt], pen if check_mmr_pens else None)


def assert_all_different(m1: MirrorUsagePointMrids, m2: MirrorUsagePointMrids):
    assert m1.mup_mrid != m2.mup_mrid

    for key, m1_val in m1.mmr_mrids.items():
        if key in m2.mmr_mrids:
            assert m1_val != m2.mmr_mrids[key]

    for key, m2_val in m2.mmr_mrids.items():
        if key in m1.mmr_mrids:
            assert m2_val != m1.mmr_mrids[key]

    assert set(m1.mmr_mrids.items()) != set(m2.mmr_mrids.items())


def test_generate_hashed_mrid():
    mrid1 = generate_hashed_mrid("", 1234)
    assert_mrid(mrid1, 1234)

    mrid2 = generate_hashed_mrid("", 12345)
    assert_mrid(mrid2, 12345)

    mrid3 = generate_hashed_mrid("seed value", 12345)
    assert_mrid(mrid3, 12345)

    mrid4 = generate_hashed_mrid("seed value", 12345678)
    assert_mrid(mrid4, 12345678)

    mrid4_dup = generate_hashed_mrid("seed value", 12345678)
    assert_mrid(mrid4_dup, 12345678)
    assert mrid4_dup == mrid4

    mrid5 = generate_hashed_mrid("seed value 2", 12345678)
    assert_mrid(mrid5, 12345678)

    all_unique_mrids = [mrid1, mrid2, mrid3, mrid4, mrid5]
    assert len(all_unique_mrids) == len(set(all_unique_mrids))


def test_generate_mup_mrids():
    pen1 = 123
    pen2 = 124

    rts_1 = [CSIPAusReadingType.ActivePowerMaximum, CSIPAusReadingType.FrequencyMaximum]
    rts_2 = [
        CSIPAusReadingType.ActivePowerMaximum,
        CSIPAusReadingType.ActivePowerMinimum,
    ]

    mup1 = generate_mup_mrids(CSIPAusReadingLocation.Device, rts_1, pen1)
    assert_mup_mrids(mup1, rts_1, pen1)

    mup1_dup = generate_mup_mrids(CSIPAusReadingLocation.Device, rts_1, pen1)
    assert_mup_mrids(mup1_dup, rts_1, pen1)
    assert mup1 == mup1_dup

    mup1_reversed = generate_mup_mrids(CSIPAusReadingLocation.Device, list(reversed(rts_1)), pen1)
    assert_mup_mrids(mup1_reversed, rts_1, pen1)
    assert mup1 == mup1_reversed, "Should be invariant to the order they are specified"

    mup2 = generate_mup_mrids(CSIPAusReadingLocation.Device, rts_1, pen2)
    assert_mup_mrids(mup2, rts_1, pen2)

    mup3 = generate_mup_mrids(CSIPAusReadingLocation.Device, rts_2, pen1)
    assert_mup_mrids(mup3, rts_2, pen1)

    assert_all_different(mup1, mup2)
    assert_all_different(mup1, mup3)


def test_generate_reading_type_values_bad_value():
    with pytest.raises(BaseJuiceError):
        generate_reading_type_values("not a valid value")  # type: ignore


def test_generate_reading_type_values():
    all_values: list[tuple] = []
    for rt in CSIPAusReadingType:
        all_values.append(generate_reading_type_values(rt))

    assert len(all_values) == len(set(all_values)), "For catching copy paste errors"


def test_generate_role_flags_bad_value():
    with pytest.raises(BaseJuiceError):
        generate_role_flags("not a valid value")  # type: ignore


def test_generate_role_flags_values():
    all_values = []
    for loc in CSIPAusReadingLocation:
        all_values.append(generate_role_flags(loc))

    assert len(all_values) == len(set(all_values)), "For catching copy paste errors"


def test_generate_mmr_mrids_basic():
    """Test that generate_mmr_mrids produces a consistent 32-character MRID with PEN suffix if mmr mrids not
    specified"""
    mup_mrid = "ABC123456789012345678901234567890"
    rts = [CSIPAusReadingType.ActivePowerAverage]
    pen = 12345678

    result = generate_mmr_mrids(mup_mrid, rts, pen)

    assert isinstance(result, dict)
    assert list(result.keys()) == rts
    mrid = result[rts[0]]
    assert mrid.endswith(str(pen))

    result2 = generate_mmr_mrids(mup_mrid, rts, pen)
    assert result == result2


@pytest.mark.parametrize(
    "dt, post_rate, expected",
    [
        # Basic alignment - partway through a period rounds down to the boundary
        (
            datetime(2024, 3, 15, 13, 22, 33, tzinfo=UTC),
            timedelta(minutes=5),
            datetime(2024, 3, 15, 13, 20, 0, tzinfo=UTC),
        ),
        (
            datetime(2024, 3, 15, 13, 22, 33, tzinfo=UTC),
            timedelta(minutes=15),
            datetime(2024, 3, 15, 13, 15, 0, tzinfo=UTC),
        ),
        (
            datetime(2024, 3, 15, 13, 22, 33, tzinfo=UTC),
            timedelta(hours=1),
            datetime(2024, 3, 15, 13, 0, 0, tzinfo=UTC),
        ),
        # Sub-minute microseconds are also discarded
        (
            datetime(2024, 3, 15, 13, 20, 0, 500_000, tzinfo=UTC),
            timedelta(minutes=5),
            datetime(2024, 3, 15, 13, 20, 0, tzinfo=UTC),
        ),
        # Exactly on a boundary returns that same instant
        (
            datetime(2024, 3, 15, 13, 15, 0, tzinfo=UTC),
            timedelta(minutes=15),
            datetime(2024, 3, 15, 13, 15, 0, tzinfo=UTC),
        ),
        # Just before midnight with a large period aligns back to the prior boundary
        (
            datetime(2024, 3, 15, 23, 59, 59, tzinfo=UTC),
            timedelta(hours=6),
            datetime(2024, 3, 15, 18, 0, 0, tzinfo=UTC),
        ),
        # Exactly midnight returns midnight
        (
            datetime(2024, 3, 15, 0, 0, 0, tzinfo=UTC),
            timedelta(minutes=5),
            datetime(2024, 3, 15, 0, 0, 0, tzinfo=UTC),
        ),
        # A full day period always collapses to local midnight
        (
            datetime(2024, 3, 15, 17, 45, 12, tzinfo=UTC),
            timedelta(days=1),
            datetime(2024, 3, 15, 0, 0, 0, tzinfo=UTC),
        ),
        # Boundaries align to midnight in dt's own (non-UTC) timezone
        (
            datetime(2024, 3, 15, 13, 22, 33, tzinfo=timezone(timedelta(hours=10))),
            timedelta(minutes=15),
            datetime(2024, 3, 15, 13, 15, 0, tzinfo=timezone(timedelta(hours=10))),
        ),
    ],
)
def test_previous_post_period(dt: datetime, post_rate: timedelta, expected: datetime):
    result = previous_post_period(dt, post_rate)

    assert result == expected
    assert result.tzinfo == dt.tzinfo, "tzinfo should be preserved"
    assert result <= dt, "boundary is never in the future"


def test_previous_post_period_named_timezone_midnight_alignment():
    """Boundaries are aligned to local midnight of a named tz, not UTC midnight."""
    tz = ZoneInfo("Australia/Brisbane")  # UTC+10, no DST
    dt = datetime(2024, 6, 15, 0, 20, 0, tzinfo=tz)

    result = previous_post_period(dt, timedelta(minutes=15))

    assert result == datetime(2024, 6, 15, 0, 15, 0, tzinfo=tz)


def test_previous_post_period_across_dst_boundary():
    """Alignment is anchored to local midnight so it is unaffected by a DST transition earlier in the day."""
    tz = ZoneInfo("Australia/Sydney")
    # 2024-04-07 03:00 local, DST ends at 03:00 -> 02:00 that morning in Sydney
    dt = datetime(2024, 4, 7, 13, 7, 0, tzinfo=tz)

    result = previous_post_period(dt, timedelta(minutes=5))

    assert result == datetime(2024, 4, 7, 13, 5, 0, tzinfo=tz)


def test_previous_post_period_requires_timezone_aware():
    with pytest.raises(ValueError):
        previous_post_period(datetime(2024, 3, 15, 13, 22, 33), timedelta(minutes=5))


@pytest.mark.parametrize("post_rate", [timedelta(0), timedelta(minutes=-5)])
def test_previous_post_period_rejects_non_positive_rate(post_rate: timedelta):
    with pytest.raises(ValueError):
        previous_post_period(datetime(2024, 3, 15, 13, 22, 33, tzinfo=UTC), post_rate)


@pytest.mark.parametrize(
    "post_rate",
    [
        timedelta(minutes=7),  # does not divide 60
        timedelta(hours=5),  # does not divide 24
        timedelta(seconds=7),  # does not divide the day
    ],
)
def test_previous_post_period_rejects_rate_not_dividing_day(post_rate: timedelta):
    with pytest.raises(ValueError):
        previous_post_period(datetime(2024, 3, 15, 13, 22, 33, tzinfo=UTC), post_rate)


def test_generate_reading_type_values_fuzzy_match():
    """Test that all combinations of units and qualifiers exist and are consistent"""

    units_to_uoms = {
        "ActivePower": UomType.REAL_POWER_WATT,
        "ReactivePower": UomType.REACTIVE_POWER_VAR,
        "Frequency": UomType.FREQUENCY_HZ,
        "VoltageSinglePhase": UomType.VOLTAGE,
    }
    qualifiers = {
        "Average": DataQualifierType.AVERAGE,
        "Instantaneous": DataQualifierType.STANDARD,
        "Maximum": DataQualifierType.MAXIMUM,
        "Minimum": DataQualifierType.MINIMUM,
    }

    for unit, expected_uom in units_to_uoms.items():
        for qualifier, expected_qualifier in qualifiers.items():
            enum_name = f"{unit}{qualifier}"
            rt = CSIPAusReadingType[enum_name]

            # Act
            uom, kind, data_qualifier = generate_reading_type_values(rt)

            # Assert
            assert isinstance(data_qualifier, DataQualifierType)
            assert data_qualifier == expected_qualifier

            assert isinstance(uom, UomType)
            assert uom == expected_uom

            assert kind == KindType.POWER


@pytest.mark.parametrize(
    "v, pow10, expected",
    [
        (0, 0, 0),
        (10.3, 0, 10),
        (821.2, 1, 82),
        (4731.3, 3, 4),
        (4731.3, -1, 47313),
        (4731.3, -2, 473130),
    ],
)
def test_value_to_sep2(v: float, pow10: int, expected: int):
    actual = value_to_sep2(v, pow10)
    assert isinstance(actual, int)
    assert actual == expected
    assert value_to_sep2(-v, pow10) == -expected


@pytest.mark.parametrize(
    "sep2_val, expected",
    [
        (None, None),
        (ActivePower(multiplier=0, value=0), 0.0),
        (ActivePower(multiplier=-2, value=0), 0.0),
        (ActivePower(multiplier=3, value=0), 0.0),
        (ActivePower(multiplier=0, value=123), 123.0),
        (ActivePower(multiplier=-1, value=123), 12.3),
        (ActivePower(multiplier=1, value=123), 1230.0),
        (ActivePower(multiplier=2, value=123), 12300.0),
        (ActivePower(multiplier=-2, value=123), 1.23),
        (ActivePower(multiplier=-2, value=-456), -4.56),
        (ActivePower(multiplier=1, value=-456), -4560.0),
        (ActivePower(multiplier=0, value=-456), -456.0),
    ],
)
def test_sep2_to_value(sep2_val: ActivePower | None, expected: float | None):
    actual = sep2_to_value(sep2_val)
    if expected is None:
        assert actual is None
    else:
        assert isinstance(actual, float) or isinstance(actual, int)
        assert actual == pytest.approx(expected)
