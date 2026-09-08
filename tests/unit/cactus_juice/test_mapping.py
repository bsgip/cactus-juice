import re
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from assertical.asserts.type import assert_dict_type
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions.csipaus import (
    CSIPAusReadingLocation,
    CSIPAusReadingType,
)
from envoy_schema.server.schema.sep2.der import (
    DefaultDERControl,
    DERCapability,
    DERControlBase,
    DERControlResponse,
    DERSettings,
    DERStatus,
)
from envoy_schema.server.schema.sep2.der_control_types import ActivePower
from envoy_schema.server.schema.sep2.event import EventStatus, EventStatusType
from envoy_schema.server.schema.sep2.metering_mirror import MirrorMeterReadingListRequest, MirrorUsagePointRequest
from envoy_schema.server.schema.sep2.response import ResponseType
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    DateTimeIntervalType,
    FlowDirectionType,
    KindType,
    ServiceKind,
    UomType,
)

from cactus_juice.csipaus.dto import DefaultValues
from cactus_juice.error import BaseJuiceError
from cactus_juice.mapping import (
    POW10_BY_READING_TYPE,
    SUPPORTED_READING_TYPES,
    MirrorUsagePointMrids,
    create_location_mup,
    csipaus_controls_to_responses,
    default_dercontrols_to_values,
    dercontrol_to_csipaus_control,
    generate_hashed_mrid,
    generate_mmr_mrids,
    generate_mup_mrids,
    generate_reading_type_values,
    generate_role_flags,
    ocpp_metadata_to_sep2,
    ocpp_readings_to_submit_mmr,
    previous_post_period,
    sep2_to_value,
    value_to_sep2,
)
from cactus_juice.model import CSIPAusControl, CSIPAusControlResponse, OCPPMetadata, OCPPReading


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


def test_SUPPORTED_READING_TYPES_in_POW10_BY_READING_TYPE():
    """Every SUPPORTED_READING_TYPES must have a pow10"""
    for rt in SUPPORTED_READING_TYPES:
        assert rt in POW10_BY_READING_TYPE


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


# ---------------------------------------------------------------------------
# ocpp_metadata_to_sep2
# ---------------------------------------------------------------------------

_METADATA_MEASUREMENT_FIELDS = (
    "max_voltage_volts",
    "min_voltage_volts",
    "max_power_watts",
    "max_charge_rate_watts",
    "max_discharge_rate_watts",
    "set_grad_w",
)


def _metadata(**overrides: float | None) -> OCPPMetadata:
    """An OCPPMetadata with every nullable measurement field defaulting to None (override as needed)."""
    metadata = generate_class_instance(OCPPMetadata, seed=101)
    for field in _METADATA_MEASUREMENT_FIELDS:
        setattr(metadata, field, overrides.get(field))
    return metadata


def test_ocpp_metadata_to_sep2_without_max_power():
    """No max_power_watts -> no capability/settings, but always a DERStatus."""
    metadata = _metadata(max_power_watts=None)

    capability, settings, status = ocpp_metadata_to_sep2(metadata)

    assert capability is None
    assert settings is None
    assert isinstance(status, DERStatus)
    assert status.alarmStatus == "00"
    assert status.readingTime == int(metadata.created_at.timestamp())


def test_ocpp_metadata_to_sep2_minimal_max_power():
    """max_power_watts set but every optional rating absent - must not blow up on the None -> None helpers."""
    metadata = _metadata(max_power_watts=5000.0, set_grad_w=None)

    capability, settings, status = ocpp_metadata_to_sep2(metadata)

    assert isinstance(capability, DERCapability)
    assert isinstance(settings, DERSettings)
    assert isinstance(status, DERStatus)

    # Mandatory bits are populated from max_power_watts
    assert capability.rtgMaxW is not None
    assert settings.setMaxW is not None

    # Optionals fed by absent metadata stay None
    assert capability.rtgMaxV is None
    assert capability.rtgMinV is None
    assert capability.rtgMaxChargeRateW is None
    assert capability.rtgMaxDischargeRateW is None
    assert settings.setMaxV is None
    assert settings.setMinV is None
    assert settings.setMaxChargeRateW is None
    assert settings.setMaxDischargeRateW is None

    # set_grad_w is None -> documented hardcoded fallback
    assert settings.setGradW == 27
    assert settings.updatedTime == int(metadata.created_at.timestamp())
    assert status.readingTime == int(metadata.created_at.timestamp())


def test_ocpp_metadata_to_sep2_full_values():
    """Every metadata field supplied (incl. a non-integer set_grad_w) maps without error."""
    metadata = _metadata(
        max_voltage_volts=253.0,
        min_voltage_volts=207.0,
        max_power_watts=10000.0,
        max_charge_rate_watts=7000.0,
        max_discharge_rate_watts=6000.0,
        set_grad_w=12.7,
    )

    capability, settings, status = ocpp_metadata_to_sep2(metadata)

    assert isinstance(capability, DERCapability)
    assert isinstance(settings, DERSettings)
    assert isinstance(status, DERStatus)

    assert capability.rtgMaxV is not None
    assert capability.rtgMinV is not None
    assert capability.rtgMaxChargeRateW is not None
    assert capability.rtgMaxDischargeRateW is not None
    assert settings.setMaxV is not None
    assert settings.setMinV is not None
    assert settings.setMaxChargeRateW is not None
    assert settings.setMaxDischargeRateW is not None

    # float set_grad_w is truncated to int
    assert settings.setGradW == 12
    assert isinstance(settings.setGradW, int)
    assert status.alarmStatus == "00"


def test_ocpp_metadata_to_sep2_negative_values():
    """Negative ratings should still round-trip through the int() conversions without raising."""
    metadata = _metadata(max_power_watts=-4200.0, max_charge_rate_watts=-1000.0, set_grad_w=-5.0)

    capability, settings, _ = ocpp_metadata_to_sep2(metadata)

    assert isinstance(capability, DERCapability)
    assert isinstance(settings, DERSettings)
    assert capability.rtgMaxW is not None
    assert settings.setGradW == -5


# ---------------------------------------------------------------------------
# create_location_mup
# ---------------------------------------------------------------------------

_VALID_LFDI = "0F" * 20  # 40 hex chars - deviceLFDI is validated as hex by the schema


@pytest.mark.parametrize(
    "location, expected_flow",
    [
        (CSIPAusReadingLocation.Site, FlowDirectionType.FORWARD),
        (CSIPAusReadingLocation.Device, FlowDirectionType.REVERSE),
    ],
)
def test_create_location_mup_builds_request(location: CSIPAusReadingLocation, expected_flow: FlowDirectionType):
    mrids = generate_mup_mrids(location, SUPPORTED_READING_TYPES, 12345678)

    mup = create_location_mup(location, mrids, _VALID_LFDI)

    assert isinstance(mup, MirrorUsagePointRequest)
    assert mup.mRID == mrids.mup_mrid
    assert mup.deviceLFDI == _VALID_LFDI
    assert mup.serviceCategoryKind == ServiceKind.ELECTRICITY
    assert re.fullmatch(r"[0-9A-F]{4}", mup.roleFlags), "roleFlags should be 4 uppercase hex chars"

    assert mup.mirrorMeterReadings is not None
    assert len(mup.mirrorMeterReadings) == len(SUPPORTED_READING_TYPES)
    mrids_seen = {mmr.mRID for mmr in mup.mirrorMeterReadings}
    assert mrids_seen == set(mrids.mmr_mrids.values()), "each supported reading type gets its own MMR"
    for mmr in mup.mirrorMeterReadings:
        assert mmr.readingType is not None
        assert mmr.readingType.flowDirection == expected_flow


def test_create_location_mup_site_and_device_differ():
    """Site vs Device must not collapse to the same role flags / flow direction (copy-paste guard)."""
    mrids = generate_mup_mrids(CSIPAusReadingLocation.Site, SUPPORTED_READING_TYPES, 12345678)

    site = create_location_mup(CSIPAusReadingLocation.Site, mrids, _VALID_LFDI)
    device = create_location_mup(CSIPAusReadingLocation.Device, mrids, _VALID_LFDI)

    assert site.roleFlags != device.roleFlags
    assert site.mirrorMeterReadings is not None and device.mirrorMeterReadings is not None
    site_rt = site.mirrorMeterReadings[0].readingType
    device_rt = device.mirrorMeterReadings[0].readingType
    assert site_rt is not None and device_rt is not None
    assert site_rt.flowDirection != device_rt.flowDirection


def test_create_location_mup_no_reading_types():
    """An empty mmr_mrids map still yields a valid request with no readings."""
    mrids = MirrorUsagePointMrids(mup_mrid=generate_hashed_mrid("mup", 1), mmr_mrids={})

    mup = create_location_mup(CSIPAusReadingLocation.Site, mrids, _VALID_LFDI)

    assert mup.mirrorMeterReadings == []


def test_create_location_mup_bad_location():
    mrids = generate_mup_mrids(CSIPAusReadingLocation.Site, SUPPORTED_READING_TYPES, 1)
    with pytest.raises(BaseJuiceError):
        create_location_mup("not a location", mrids, _VALID_LFDI)  # type: ignore


def test_create_location_mup_bad_reading_type():
    mrids = MirrorUsagePointMrids(
        mup_mrid=generate_hashed_mrid("mup", 1),
        mmr_mrids={"not a reading type": generate_hashed_mrid("mmr", 1)},  # type: ignore
    )
    with pytest.raises(BaseJuiceError):
        create_location_mup(CSIPAusReadingLocation.Site, mrids, _VALID_LFDI)


# ---------------------------------------------------------------------------
# ocpp_readings_to_submit_mmr
# ---------------------------------------------------------------------------

_RF = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_RT = datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)

_READING_MEASUREMENT_FIELDS = (
    "frequency_hz",
    "import_active_power_watts",
    "export_active_power_watts",
    "import_reactive_power_var",
    "export_reactive_power_var",
    "soc_percent",
    "voltage_volts",
)


def _reading(**overrides: float | None) -> OCPPReading:
    """An OCPPReading with every nullable measurement field defaulting to None (override as needed)."""
    reading = generate_class_instance(OCPPReading, seed=202)
    for field in _READING_MEASUREMENT_FIELDS:
        setattr(reading, field, overrides.get(field))
    return reading


def _site_mrids() -> MirrorUsagePointMrids:
    return generate_mup_mrids(CSIPAusReadingLocation.Site, SUPPORTED_READING_TYPES, 12345678)


def test_ocpp_readings_to_submit_mmr_empty():
    mrids = _site_mrids()
    assert ocpp_readings_to_submit_mmr(_RF, _RT, [], mrids, mrids) == (None, None)


def test_ocpp_readings_to_submit_mmr_averages_and_shape():
    mrids = _site_mrids()
    readings = [
        _reading(import_active_power_watts=80.0, export_active_power_watts=40.0, import_reactive_power_var=8.0),
        _reading(import_active_power_watts=120.0, export_active_power_watts=40.0, import_reactive_power_var=4.0),
    ]

    site, device = ocpp_readings_to_submit_mmr(_RF, _RT, readings, mrids, mrids)

    assert device is None, "device level readings are never mapped"
    assert isinstance(site, MirrorMeterReadingListRequest)
    assert site.mirrorMeterReadings is not None

    by_mrid = {mmr.mRID: mmr for mmr in site.mirrorMeterReadings}
    active = by_mrid[mrids.mmr_mrids[CSIPAusReadingType.ActivePowerAverage]]
    reactive = by_mrid[mrids.mmr_mrids[CSIPAusReadingType.ReactivePowerAverage]]
    assert active.reading is not None and reactive.reading is not None
    assert active.reading.timePeriod is not None

    # avg import 100 - avg export 40 = 60, pow10 == 0
    assert active.reading.value == 60
    # avg import var 6 - avg export var 0 = 6
    assert reactive.reading.value == 6
    assert active.reading.timePeriod.duration == int((_RT - _RF).total_seconds())
    assert active.reading.timePeriod.start == int(_RF.timestamp())


def test_ocpp_readings_to_submit_mmr_no_matching_mrids():
    """Readings present but the MUP has no MMR mrids -> nothing to submit."""
    empty = MirrorUsagePointMrids(mup_mrid=generate_hashed_mrid("mup", 1), mmr_mrids={})
    readings = [_reading(import_active_power_watts=100.0)]

    assert ocpp_readings_to_submit_mmr(_RF, _RT, readings, empty, empty) == (None, None)


def test_ocpp_readings_to_submit_mmr_frequency_uses_its_own_mrid():
    mrids = _site_mrids()
    readings = [
        _reading(
            import_active_power_watts=100.0,
            export_active_power_watts=0.0,
            import_reactive_power_var=10.0,
            export_reactive_power_var=0.0,
            voltage_volts=240.0,
            frequency_hz=50.0,
        )
    ]

    site, _ = ocpp_readings_to_submit_mmr(_RF, _RT, readings, mrids, mrids)
    assert site is not None
    assert site.mirrorMeterReadings is not None

    mrids_seen = [mmr.mRID for mmr in site.mirrorMeterReadings]
    assert len(mrids_seen) == len(set(mrids_seen)), "every reading type should map to a distinct MMR mRID"
    assert mrids.mmr_mrids[CSIPAusReadingType.FrequencyAverage] in mrids_seen


# ---------------------------------------------------------------------------
# default_dercontrols_to_values
# ---------------------------------------------------------------------------


def _dderc(primacy: int, *, set_grad_w: int | None = None, **base_kwargs: object) -> tuple[int, DefaultDERControl]:
    base = generate_class_instance(DERControlBase, optional_is_none=True)
    base = base.model_copy(update=dict(base_kwargs))
    dderc = generate_class_instance(DefaultDERControl, optional_is_none=True, DERControlBase_=base, setGradW=set_grad_w)
    return (primacy, dderc)


def test_default_dercontrols_to_values_empty():
    result = default_dercontrols_to_values([])

    assert result == DefaultValues(
        connect=None,
        energize=None,
        import_limit_watts=None,
        export_limit_watts=None,
        load_limit_watts=None,
        generation_limit_watts=None,
        storage_target_watts=None,
        ramp_percent_max_second_hundredths=None,
    )


def test_default_dercontrols_to_values_field_mapping():
    """Each DERControlBase field must land in its own DefaultValues slot (copy-paste guard)."""
    entry = _dderc(
        1,
        set_grad_w=77,
        opModConnect=True,
        opModEnergize=False,
        opModImpLimW=ActivePower(multiplier=0, value=111),
        opModExpLimW=ActivePower(multiplier=0, value=222),
        opModLoadLimW=ActivePower(multiplier=0, value=333),
        opModGenLimW=ActivePower(multiplier=0, value=444),
        opModStorageTargetW=ActivePower(multiplier=0, value=555),
    )

    result = default_dercontrols_to_values([entry])

    assert result == DefaultValues(
        connect=True,
        energize=False,
        import_limit_watts=111,
        export_limit_watts=222,
        load_limit_watts=333,
        generation_limit_watts=444,
        storage_target_watts=555,
        ramp_percent_max_second_hundredths=77,
    )


def test_default_dercontrols_to_values_applies_multiplier():
    entry = _dderc(1, opModImpLimW=ActivePower(multiplier=2, value=3))

    result = default_dercontrols_to_values([entry])

    assert result.import_limit_watts == 300
    assert isinstance(result.import_limit_watts, int)


def test_default_dercontrols_to_values_lower_primacy_wins():
    """Lower primacy == higher priority, so it should overwrite a higher-primacy value."""
    high_primacy = _dderc(10, opModConnect=True, opModImpLimW=ActivePower(multiplier=0, value=100))
    low_primacy = _dderc(1, opModConnect=False, opModImpLimW=ActivePower(multiplier=0, value=999))

    # order of the input iterable should not matter
    result = default_dercontrols_to_values([high_primacy, low_primacy])
    result_reversed = default_dercontrols_to_values([low_primacy, high_primacy])

    assert result == result_reversed
    assert result.connect is False
    assert result.import_limit_watts == 999


def test_default_dercontrols_to_values_merges_unset_fields():
    """A lower-priority control still contributes fields the higher-priority one leaves unset."""
    high_primacy = _dderc(10, opModConnect=True)  # only connect
    low_primacy = _dderc(1, opModEnergize=True, opModExpLimW=ActivePower(multiplier=0, value=250))

    result = default_dercontrols_to_values([high_primacy, low_primacy])

    assert result.connect is True  # only set by the low-priority control
    assert result.energize is True
    assert result.export_limit_watts == 250


def test_default_dercontrols_to_values_setgradw_is_optional():
    assert default_dercontrols_to_values([_dderc(1, set_grad_w=None)]).ramp_percent_max_second_hundredths is None
    assert default_dercontrols_to_values([_dderc(1, set_grad_w=15)]).ramp_percent_max_second_hundredths == 15


# ---------------------------------------------------------------------------
# dercontrol_to_csipaus_control
# ---------------------------------------------------------------------------


def _derc(
    *,
    mrid: str = "DERC-MRID-1",
    start: int = 1_700_000_000,
    duration: int = 3600,
    reply_to: str | None = None,
    response_required: str | None = None,
    status: EventStatusType = EventStatusType.Active,
    **base_kwargs: object,
) -> DERControlResponse:
    """A DERControlResponse with an all-None DERControlBase (override individual opMod* fields as needed)."""
    event_status = generate_class_instance(EventStatus, optional_is_none=True, currentStatus=int(status))
    base = generate_class_instance(DERControlBase, optional_is_none=True)
    base = base.model_copy(update=dict(base_kwargs))
    return generate_class_instance(
        DERControlResponse,
        optional_is_none=True,
        mRID=mrid,
        EventStatus_=event_status,
        DERControlBase_=base,
        interval=DateTimeIntervalType(start=start, duration=duration),
        replyTo=reply_to,
        responseRequired=response_required,
    )


@pytest.mark.parametrize(
    "reply_to, response_required, expected_reply_to",
    [
        (None, None, None),
        ("/foo", None, None),
        (None, "03", None),
        ("/foo", "00", None),
        ("/foo", "03", "/foo"),
        ("/foo", "01", "/foo"),
        ("/foo", "FF", "/foo"),
    ],
)
def test_dercontrol_to_csipaus_control_field_mapping(
    reply_to: str | None, response_required: str | None, expected_reply_to: str | None
):
    """Each DERControlResponse field must land in its own CSIPAusControl column (copy-paste guard)."""
    derc = _derc(
        mrid="control-abc",
        start=1_700_000_000,
        duration=1800,
        status=EventStatusType.Active,
        rampTms=42,
        reply_to=reply_to,
        response_required=response_required,
        opModConnect=True,
        opModEnergize=False,
        opModImpLimW=ActivePower(multiplier=0, value=111),
        opModExpLimW=ActivePower(multiplier=0, value=222),
        opModLoadLimW=ActivePower(multiplier=0, value=333),
        opModGenLimW=ActivePower(multiplier=0, value=444),
        opModStorageTargetW=ActivePower(multiplier=0, value=555),
    )

    control = dercontrol_to_csipaus_control(derc, primacy=7)

    assert isinstance(control, CSIPAusControl)
    assert control.primacy == 7
    assert control.mrid == "control-abc"
    assert control.started_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert control.duration_seconds == 1800
    assert control.cancelled_at is None
    assert control.superseded_at is None
    assert control.reply_to == expected_reply_to

    assert control.ramp_time_seconds == 42
    assert control.connect is True
    assert control.energize is False
    assert control.import_limit_watts == 111
    assert control.export_limit_watts == 222
    assert control.load_limit_watts == 333
    assert control.generation_limit_watts == 444
    assert control.storage_target_watts == 555


def test_dercontrol_to_csipaus_control_all_optionals_none():
    """An all-None DERControlBase must not raise and leaves every optional control value unset."""
    control = dercontrol_to_csipaus_control(_derc(), primacy=1)

    assert control.ramp_time_seconds is None
    assert control.connect is None
    assert control.energize is None
    assert control.import_limit_watts is None
    assert control.export_limit_watts is None
    assert control.load_limit_watts is None
    assert control.generation_limit_watts is None
    assert control.storage_target_watts is None


def test_dercontrol_to_csipaus_control_applies_multiplier():
    control = dercontrol_to_csipaus_control(_derc(opModExpLimW=ActivePower(multiplier=2, value=3)), primacy=1)

    assert control.export_limit_watts == 300


@pytest.mark.parametrize(
    "status, expect_cancelled, expect_superseded",
    [
        (EventStatusType.Scheduled, False, False),
        (EventStatusType.Active, False, False),
        (EventStatusType.Cancelled, True, False),
        (EventStatusType.CancelledWithRandomization, True, False),
        (EventStatusType.Superseded, False, True),
    ],
)
def test_dercontrol_to_csipaus_control_status_flags(
    status: EventStatusType, expect_cancelled: bool, expect_superseded: bool
):
    before = datetime.now(UTC)
    control = dercontrol_to_csipaus_control(_derc(status=status), primacy=1)
    after = datetime.now(UTC)

    if expect_cancelled:
        assert control.cancelled_at is not None
        assert before <= control.cancelled_at <= after
    else:
        assert control.cancelled_at is None

    if expect_superseded:
        assert control.superseded_at is not None
        assert before <= control.superseded_at <= after
    else:
        assert control.superseded_at is None


# ---------------------------------------------------------------------------
# csipaus_controls_to_responses
# ---------------------------------------------------------------------------

_EDEV_LFDI = "AA" * 20


def _control(
    seed: int = 301,
    cancelled_at: datetime | None = None,
    superseded_at: datetime | None = None,
    reply_to: str | None = "/foo",
) -> CSIPAusControl:
    """A CSIPAusControl with distinct auto-generated timestamps (override cancelled_at / superseded_at as needed)."""
    return generate_class_instance(
        CSIPAusControl, seed=seed, cancelled_at=cancelled_at, superseded_at=superseded_at, reply_to=reply_to
    )


def _by_status(responses: list[CSIPAusControlResponse]) -> dict[int, CSIPAusControlResponse]:
    by_status = {r.response_status: r for r in responses}
    assert len(by_status) == len(responses), "each status should appear at most once per control"
    return by_status


def test_csipaus_controls_to_responses_empty():
    assert csipaus_controls_to_responses([], _EDEV_LFDI) == []


def test_csipaus_controls_to_responses_running_control():
    """A control that was neither cancelled nor superseded -> received, started, completed."""
    control = _control(cancelled_at=None, superseded_at=None)

    responses = csipaus_controls_to_responses([control], _EDEV_LFDI)

    by_status = _by_status(responses)
    assert set(by_status) == {
        ResponseType.EVENT_RECEIVED,
        ResponseType.EVENT_STARTED,
        ResponseType.EVENT_COMPLETED,
    }

    for r in responses:
        assert r.control is control
        assert r.end_device_lfdi == _EDEV_LFDI
        assert r.sent_at is None

    assert by_status[ResponseType.EVENT_RECEIVED].not_before == control.created_at
    assert by_status[ResponseType.EVENT_STARTED].not_before == control.started_at
    assert by_status[ResponseType.EVENT_COMPLETED].not_before == control.finished_at


def test_csipaus_controls_to_responses_cancelled_control():
    """A cancelled control emits EVENT_CANCELLED (at cancelled_at) and no EVENT_COMPLETED."""
    cancelled_at = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    control = _control(cancelled_at=cancelled_at, superseded_at=None)

    responses = csipaus_controls_to_responses([control], _EDEV_LFDI)

    by_status = _by_status(responses)
    assert set(by_status) == {
        ResponseType.EVENT_RECEIVED,
        ResponseType.EVENT_STARTED,
        ResponseType.EVENT_CANCELLED,
    }
    assert by_status[ResponseType.EVENT_CANCELLED].not_before == cancelled_at


def test_csipaus_controls_to_responses_superseded_control():
    """A superseded control emits EVENT_SUPERSEDED (at superseded_at) and no EVENT_COMPLETED."""
    superseded_at = datetime(2026, 6, 2, 9, 30, 0, tzinfo=UTC)
    control = _control(cancelled_at=None, superseded_at=superseded_at)

    responses = csipaus_controls_to_responses([control], _EDEV_LFDI)

    by_status = _by_status(responses)
    assert set(by_status) == {
        ResponseType.EVENT_RECEIVED,
        ResponseType.EVENT_STARTED,
        ResponseType.EVENT_SUPERSEDED,
    }
    assert by_status[ResponseType.EVENT_SUPERSEDED].not_before == superseded_at


def test_csipaus_controls_to_responses_no_reply_to():
    """A control with no reply_to is skipped."""
    control = _control(reply_to=None)

    responses = csipaus_controls_to_responses([control], _EDEV_LFDI)
    assert len(responses) == 0


def test_csipaus_controls_to_responses_cancelled_and_superseded_control():
    """Both timestamps set -> both terminal responses, still no EVENT_COMPLETED."""
    control = _control(
        cancelled_at=datetime(2026, 7, 3, 1, 0, 0, tzinfo=UTC),
        superseded_at=datetime(2026, 7, 3, 2, 0, 0, tzinfo=UTC),
    )

    responses = csipaus_controls_to_responses([control], _EDEV_LFDI)

    assert set(_by_status(responses)) == {
        ResponseType.EVENT_RECEIVED,
        ResponseType.EVENT_STARTED,
        ResponseType.EVENT_CANCELLED,
        ResponseType.EVENT_SUPERSEDED,
    }


def test_csipaus_controls_to_responses_multiple_controls_flattened_in_order():
    """Responses for every control are returned, control-by-control in input order."""
    running = _control(seed=401, cancelled_at=None, superseded_at=None)
    cancelled = _control(seed=402, cancelled_at=datetime(2026, 8, 4, tzinfo=UTC), superseded_at=None)

    responses = csipaus_controls_to_responses([running, cancelled], _EDEV_LFDI)

    assert len(responses) == 6
    assert all(r.control is running for r in responses[:3])
    assert all(r.control is cancelled for r in responses[3:])
    assert {r.response_status for r in responses[:3]} == {
        ResponseType.EVENT_RECEIVED,
        ResponseType.EVENT_STARTED,
        ResponseType.EVENT_COMPLETED,
    }
    assert {r.response_status for r in responses[3:]} == {
        ResponseType.EVENT_RECEIVED,
        ResponseType.EVENT_STARTED,
        ResponseType.EVENT_CANCELLED,
    }
