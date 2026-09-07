import re

import pytest
from assertical.asserts.type import assert_dict_type
from cactus_test_definitions.csipaus import (
    CSIPAusReadingLocation,
    CSIPAusReadingType,
)
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    KindType,
    UomType,
)

from cactus_juice.csipaus.mup import (
    MirrorUsagePointMrids,
    generate_hashed_mrid,
    generate_mmr_mrids,
    generate_mup_mrids,
    generate_reading_type_values,
    generate_role_flags,
)
from cactus_juice.error import BaseJuiceError


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
