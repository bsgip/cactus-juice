from datetime import UTC, datetime, timedelta

import pytest
from assertical.fake.generator import generate_class_instance

from cactus_juice.csipaus.dto import ActiveValues, ScheduledControlValues
from cactus_juice.troca.mapping import (
    MAX_SCHEDULE_PERIODS,
    SCHEDULE_HORIZON,
    ocpp_profiles_match,
    rated_power_watts,
    reading_to_db,
    schedule_to_ocpp_profile,
    schedule_to_troca,
    schedules_match,
    to_troca_timestamp,
    values_to_charge_watts,
    values_to_ocpp_limits,
    variables_to_metadata,
)
from cactus_juice.troca.models import (
    ActivePowerSchedule,
    Bounds,
    GetVariableResult,
    MeteringReading,
    OcppChargingProfile,
    OcppChargingProfilePurpose,
    OcppChargingSchedule,
    OcppChargingSchedulePeriod,
    SchedulePeriod,
    SessionCommand,
    SessionData,
    numeric_value,
    parse_set_charging_profile_parameters,
    set_charging_profile_parameters,
)

NOW = datetime(2026, 9, 28, 4, 0, 0, tzinfo=UTC)
MAX_POWER = 40000.0


def values(**kwargs) -> ActiveValues:
    """ActiveValues with everything unset, other than kwargs"""
    return generate_class_instance(ActiveValues, optional_is_none=True, **kwargs)


def scv(start_mins: int, end_mins: int | None, v: ActiveValues) -> ScheduledControlValues:
    return ScheduledControlValues(
        active_from=NOW + timedelta(minutes=start_mins),
        active_to=None if end_mins is None else NOW + timedelta(minutes=end_mins),
        values=v,
    )


@pytest.mark.parametrize(
    "v, expected",
    [
        (values(), MAX_POWER),
        (values(import_limit_watts=7000), 7000),
        (values(import_limit_watts=7000, load_limit_watts=5000), 5000),
        (values(import_limit_watts=99000), MAX_POWER),
        (values(import_limit_watts=-10), 0),
        (values(export_limit_watts=0, generation_limit_watts=0), MAX_POWER),
        (values(import_limit_watts=7000, connect=False), 0),
        (values(import_limit_watts=7000, energize=False), 0),
        (values(import_limit_watts=7000, connect=True, energize=True), 7000),
    ],
)
def test_values_to_charge_watts(v: ActiveValues, expected: float):
    assert values_to_charge_watts(v, MAX_POWER) == expected


def test_schedule_to_troca_full_schedule():
    """Sign is inverted + kW, equal adjacent setpoints merge and the final open-ended period hits the horizon"""
    raw = [
        scv(0, 10, values(import_limit_watts=7000)),
        scv(10, 20, values(import_limit_watts=7000, export_limit_watts=0)),  # Same setpoint as previous
        scv(20, 30, values(connect=False)),
        scv(30, None, values()),
    ]

    result = schedule_to_troca(raw, MAX_POWER, NOW)

    assert result is not None
    assert result.start_time == "2026-09-28T04:00:00Z"
    assert result.end_time == to_troca_timestamp(NOW + SCHEDULE_HORIZON)
    assert [(p.start_time, p.end_time, p.global_) for p in result.periods] == [
        ("2026-09-28T04:00:00Z", "2026-09-28T04:20:00Z", Bounds(value=-7.0, unit="kW")),
        ("2026-09-28T04:20:00Z", "2026-09-28T04:30:00Z", Bounds(value=0.0, unit="kW")),
        ("2026-09-28T04:30:00Z", result.end_time, Bounds(value=-40.0, unit="kW")),
    ]


def test_schedule_to_troca_empty():
    assert schedule_to_troca([], MAX_POWER, NOW) is None


def test_schedule_to_troca_truncates():
    raw = [scv(i, i + 1, values(import_limit_watts=1000 * (i % 2))) for i in range(MAX_SCHEDULE_PERIODS + 10)]
    result = schedule_to_troca(raw, MAX_POWER, NOW)
    assert result is not None
    assert len(result.periods) == MAX_SCHEDULE_PERIODS
    assert result.end_time == to_troca_timestamp(NOW + timedelta(minutes=MAX_SCHEDULE_PERIODS))


def test_set_charging_profile_parameters_matches_observed_request():
    """The exact parameters shape of a set_charging_profile request that Troca accepted (taken from its logs)"""
    schedule = ActivePowerSchedule(
        start_time="2026-09-27T13:24:18Z",
        end_time="2027-09-27T13:24:18Z",
        periods=[
            SchedulePeriod("2026-09-27T13:24:18Z", "2026-09-27T13:34:18Z", Bounds(value=-7.0, unit="kW")),
            SchedulePeriod("2026-09-27T13:34:18Z", "2027-09-27T13:24:18Z", Bounds(value=0, unit="kW")),
        ],
    )
    params = set_charging_profile_parameters(schedule)
    assert params == {
        "schedule": {
            "Active power": {
                "recurrency": False,
                "schedule": {
                    "valueType": "Active power",
                    "startTime": "2026-09-27T13:24:18Z",
                    "endTime": "2027-09-27T13:24:18Z",
                    "periods": [
                        {
                            "startTime": "2026-09-27T13:24:18Z",
                            "endTime": "2026-09-27T13:34:18Z",
                            "global": {"value": -7.0, "unit": "kW"},
                        },
                        {
                            "startTime": "2026-09-27T13:34:18Z",
                            "endTime": "2027-09-27T13:24:18Z",
                            "global": {"value": 0, "unit": "kW"},
                        },
                    ],
                },
            }
        }
    }
    assert parse_set_charging_profile_parameters(params) == schedule


def test_session_command_parsing():
    """Our command vs Troca's internal derivative of it (as returned from GET /sessions/commands)"""
    client = SessionCommand.from_dict(
        {
            "commandId": "f5efb5e6-615c-4c91-898f-85b8ce98619c",
            "customData": {},
            "inputParameters": {
                "parameters": set_charging_profile_parameters(
                    ActivePowerSchedule("a", "b", [SchedulePeriod("a", "b", Bounds(value=1))])
                ),
                "timestamp": "2026-09-27T13:24:18.000Z",
            },
            "type": "set_charging_profile",
        }
    )
    internal = SessionCommand.from_dict(
        {
            "commandId": "a83862a8-5d02-4470-84d8-9ff3dfb89e1b",
            "customData": {"clientCommandId": "f5efb5e6-615c-4c91-898f-85b8ce98619c"},
            "inputParameters": {"parameters": {"periods": [], "unit": "kW"}, "timestamp": "2026-09-27T13:24:18.000Z"},
            "type": "set_charging_profile",
        }
    )
    assert client.is_client_command
    assert not internal.is_client_command
    assert client.input_parameters is not None and internal.input_parameters is not None
    assert parse_set_charging_profile_parameters(client.input_parameters.parameters) is not None
    assert parse_set_charging_profile_parameters(internal.input_parameters.parameters) is None


def test_session_data_scalar_or_object_values():
    """Spec says TEmsValue objects, live server returns bare numbers"""
    observed = SessionData.from_dict(
        {
            "sessionId": "c321f6ba-653e-49dc-98a6-174e45f5b3b0",
            "arrivalDate": "2026-09-28T04:32:45Z",
            "location": {"level": "evseConnector", "locationId": "9c5605a7-9f2d-41ef-82e0-17e9a3e0ed18"},
            "meterStart": 1,
            "meterStop": -0.001,
            "userToken": {"expiryTime": "", "tokenIssuer": "", "type": "unknown", "value": ""},
            "authorizationMethod": "unknown",
        }
    )
    assert numeric_value(observed.meter_start) == 1.0
    assert numeric_value(observed.meter_stop) == -0.001

    spec = SessionData.from_dict({"sessionId": "a", "meterStart": {"value": 2.5, "unit": "kWh"}})
    assert numeric_value(spec.meter_start) == 2.5
    assert numeric_value(spec.meter_stop) is None


def _schedule(*periods: tuple[int, int, float]) -> ActivePowerSchedule:
    sp = [
        SchedulePeriod(
            to_troca_timestamp(NOW + timedelta(minutes=s)),
            to_troca_timestamp(NOW + timedelta(minutes=e)),
            Bounds(value=v, unit="kW"),
        )
        for s, e, v in periods
    ]
    return ActivePowerSchedule(sp[0].start_time, sp[-1].end_time, sp)


YEAR = int(SCHEDULE_HORIZON.total_seconds() // 60)


@pytest.mark.parametrize(
    "applied, desired, now_offset_mins, expected",
    [
        (_schedule((0, 10, -7), (10, YEAR, 0)), _schedule((0, 10, -7), (10, YEAR, 0)), 0, True),
        # Applied was pushed earlier - its first period is partially elapsed and its horizon is a little shorter
        (_schedule((-5, 10, -7), (10, YEAR - 5, 0)), _schedule((0, 10, -7), (10, YEAR, 0)), 0, True),
        # Applied's first period has fully elapsed
        (_schedule((-20, -10, -3), (-10, 10, -7), (10, YEAR, 0)), _schedule((0, 10, -7), (10, YEAR, 0)), 0, True),
        # Split vs merged periods are equivalent
        (_schedule((0, 5, -7), (5, 10, -7), (10, YEAR, 0)), _schedule((0, 10, -7), (10, YEAR, 0)), 0, True),
        (_schedule((0, 10, -7), (10, YEAR, 0)), _schedule((0, 10, -6), (10, YEAR, 0)), 0, False),
        (_schedule((0, 10, -7), (10, YEAR, 0)), _schedule((0, 11, -7), (11, YEAR, 0)), 0, False),
        (_schedule((0, 10, -7), (10, YEAR, 0)), _schedule((0, 10, -7), (10, 20, -1), (20, YEAR, 0)), 0, False),
        # Applied is running out of horizon
        (_schedule((0, 10, -7), (10, YEAR // 3, 0)), _schedule((0, 10, -7), (10, YEAR, 0)), 0, False),
        # Applied has completely elapsed
        (_schedule((-20, -10, -7)), _schedule((0, 10, -7), (10, YEAR, 0)), 0, False),
    ],
)
def test_schedules_match(applied: ActivePowerSchedule, desired: ActivePowerSchedule, now_offset_mins, expected):
    assert schedules_match(applied, desired, NOW + timedelta(minutes=now_offset_mins)) is expected


def _gvr(component: str, variable: str, status: str, value: str | None) -> GetVariableResult:
    raw: dict = {"attributeStatus": status, "component": {"name": component}, "variable": {"name": variable}}
    if value is not None:
        raw["attributeValue"] = value
    return GetVariableResult.from_dict(raw)


@pytest.mark.parametrize(
    "results, expected",
    [
        ([], None),
        # What the Trialog simulator actually returns - "40" is assumed to be kW
        ([_gvr("EVSE", "Power", "UnknownVariable", None), _gvr("ElectricalFeed", "Power", "Accepted", "40")], 40000),
        ([_gvr("EVSE", "Power", "Accepted", "7400"), _gvr("ElectricalFeed", "Power", "Accepted", "40")], 7400),
        ([_gvr("EVSE", "Power", "Accepted", "0"), _gvr("ElectricalFeed", "Power", "Accepted", "22000")], 22000),
        ([_gvr("ElectricalFeed", "Power", "Accepted", "not a number")], None),
        ([_gvr("ElectricalFeed", "ACVoltage", "Accepted", "400")], None),
    ],
)
def test_rated_power_watts(results: list[GetVariableResult], expected: float | None):
    assert rated_power_watts(results) == expected

    metadata = variables_to_metadata(results)
    if expected is None:
        assert metadata is None
    else:
        assert metadata is not None and metadata.max_power_watts == expected


@pytest.mark.parametrize(
    "raw, expected_import, expected_export, expected_voltage",
    [
        ({}, None, None, None),
        ({"instantaneousActivePower": {"global": {"value": 7, "unit": "kW"}}}, 7000, 0, None),
        ({"instantaneousActivePower": {"global": {"value": -3.5}}}, 0, 3500, None),  # Default unit is kW
        ({"instantaneousActivePower": {"global": {"value": 500, "unit": "W"}}}, 500, 0, None),
        ({"rmsVoltage": {"global": {"value": 231, "unit": "V"}}}, None, None, 231),
    ],
)
def test_reading_to_db(raw: dict, expected_import, expected_export, expected_voltage):
    r = reading_to_db(MeteringReading.from_dict({"timestamp": "2026-09-28T04:35:00Z", **raw}))
    assert r.reading_start == datetime(2026, 9, 28, 4, 35, tzinfo=UTC)
    assert r.import_active_power_watts == expected_import
    assert r.export_active_power_watts == expected_export
    assert r.voltage_volts == expected_voltage


@pytest.mark.parametrize(
    "v, expected",
    [
        (values(), (MAX_POWER, None)),
        (values(import_limit_watts=7000), (7000, None)),
        (values(import_limit_watts=7000, load_limit_watts=5000), (5000, None)),
        (values(import_limit_watts=99000), (99000, None)),  # Unlike setpoints, limits don't need capping
        (values(import_limit_watts=-10), (0, None)),
        (values(export_limit_watts=2000), (MAX_POWER, -2000)),
        (values(export_limit_watts=2000, generation_limit_watts=1500), (MAX_POWER, -1500)),
        (values(export_limit_watts=0), (MAX_POWER, 0)),
        (values(import_limit_watts=7000, export_limit_watts=2000, connect=False), (0, 0)),
        (values(energize=False), (0, 0)),
    ],
)
def test_values_to_ocpp_limits(v: ActiveValues, expected: tuple):
    assert values_to_ocpp_limits(v, MAX_POWER) == expected


def test_schedule_to_ocpp_profile():
    raw = [
        scv(0, 10, values(import_limit_watts=7000, export_limit_watts=2000)),
        scv(10, 20, values(import_limit_watts=7000, export_limit_watts=2000, storage_target_watts=5)),  # merges
        scv(20, 30, values(connect=False)),
        scv(30, None, values()),
    ]

    profile = schedule_to_ocpp_profile(
        raw,
        MAX_POWER,
        NOW + timedelta(microseconds=500),
        9001,
        0,
        OcppChargingProfilePurpose.CHARGING_STATION_MAX_PROFILE,
    )

    assert profile is not None
    # Same shape as the SetChargingProfile payload the station accepted (2026-09-28)
    assert profile.to_dict() == {
        "id": 9001,
        "stackLevel": 0,
        "chargingProfilePurpose": "ChargingStationMaxProfile",
        "chargingProfileKind": "Absolute",
        "chargingSchedule": [
            {
                "id": 1,
                "chargingRateUnit": "W",
                "startSchedule": "2026-09-28T04:00:00Z",
                "duration": int(SCHEDULE_HORIZON.total_seconds()),
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 7000.0, "dischargeLimit": -2000.0},
                    {"startPeriod": 1200, "limit": 0.0, "dischargeLimit": 0.0},
                    {"startPeriod": 1800, "limit": MAX_POWER},
                ],
            }
        ],
    }


def test_schedule_to_ocpp_profile_empty_and_truncated():
    purpose = OcppChargingProfilePurpose.CHARGING_STATION_MAX_PROFILE
    assert schedule_to_ocpp_profile([], MAX_POWER, NOW, 1, 0, purpose) is None

    raw = [scv(i, i + 1, values(import_limit_watts=1000 * (i % 2))) for i in range(MAX_SCHEDULE_PERIODS + 10)]
    profile = schedule_to_ocpp_profile(raw, MAX_POWER, NOW, 1, 0, purpose)
    assert profile is not None
    assert len(profile.charging_schedule[0].charging_schedule_period) == MAX_SCHEDULE_PERIODS
    assert profile.charging_schedule[0].duration == MAX_SCHEDULE_PERIODS * 60


def _profile(start_mins: int, *periods: tuple[int, float, float | None], duration_mins: int) -> OcppChargingProfile:
    return OcppChargingProfile(
        id=1,
        stack_level=0,
        charging_profile_purpose=OcppChargingProfilePurpose.CHARGING_STATION_MAX_PROFILE,
        charging_profile_kind="Absolute",
        charging_schedule=[
            OcppChargingSchedule(
                id=1,
                charging_rate_unit="W",
                start_schedule=to_troca_timestamp(NOW + timedelta(minutes=start_mins)),
                duration=duration_mins * 60,
                charging_schedule_period=[
                    OcppChargingSchedulePeriod(start_period=s * 60, limit=lim, discharge_limit=dis)
                    for s, lim, dis in periods
                ],
            )
        ],
    )


@pytest.mark.parametrize(
    "applied, desired, expected",
    [
        (
            _profile(0, (0, 7000, None), (10, 0, 0), duration_mins=YEAR),
            _profile(0, (0, 7000, None), (10, 0, 0), duration_mins=YEAR),
            True,
        ),
        # Pushed 5 mins earlier - equivalent from now onwards
        (
            _profile(-5, (0, 7000, None), (15, 0, 0), duration_mins=YEAR - 5),
            _profile(0, (0, 7000, None), (10, 0, 0), duration_mins=YEAR),
            True,
        ),
        # Split vs merged periods are equivalent
        (
            _profile(0, (0, 7000, None), (5, 7000, None), (10, 0, 0), duration_mins=YEAR),
            _profile(0, (0, 7000, None), (10, 0, 0), duration_mins=YEAR),
            True,
        ),
        (
            _profile(0, (0, 7000, None), (10, 0, 0), duration_mins=YEAR),
            _profile(0, (0, 7000, -1), (10, 0, 0), duration_mins=YEAR),
            False,
        ),
        (
            _profile(0, (0, 7000, None), (10, 0, 0), duration_mins=YEAR),
            _profile(0, (0, 6000, None), (10, 0, 0), duration_mins=YEAR),
            False,
        ),
        (
            _profile(0, (0, 7000, None), (10, 0, 0), duration_mins=YEAR),
            _profile(0, (0, 7000, None), (11, 0, 0), duration_mins=YEAR),
            False,
        ),
        # Applied is running out of horizon
        (
            _profile(0, (0, 7000, None), (10, 0, 0), duration_mins=YEAR // 3),
            _profile(0, (0, 7000, None), (10, 0, 0), duration_mins=YEAR),
            False,
        ),
    ],
)
def test_ocpp_profiles_match(applied: OcppChargingProfile, desired: OcppChargingProfile, expected: bool):
    assert ocpp_profiles_match(applied, desired, NOW) is expected
