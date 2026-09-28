from datetime import UTC, datetime, timedelta

import pytest

from cactus_juice.troca.mapping import to_troca_timestamp
from cactus_juice.troca.models import (
    ActivePowerSchedule,
    Bounds,
    CommandParameter,
    CommandStatus,
    LocationId,
    SchedulePeriod,
    SessionCommand,
    SessionCommandLocation,
    SessionCommandStatusEntry,
    SessionCommandType,
    SessionData,
    SessionStatus,
    SessionStatusEntry,
    StructureLevel,
    StructurePair,
    set_charging_profile_parameters,
)
from cactus_juice.troca.poll import POLL_EPOCH
from cactus_juice.troca.session_schedule import (
    FAILED_SCHEDULE_RETRY_INTERVAL,
    find_active_session,
    find_evse_location,
    find_latest_pushed_schedule,
    latest_command_status,
    requires_push,
)

NOW = datetime(2026, 9, 28, 4, 0, 0, tzinfo=UTC)

POOL = LocationId("pool-1", StructureLevel.POOL)
STATION = LocationId("station-1", StructureLevel.STATION)
EVSE = LocationId("evse-1", StructureLevel.EVSE)
EVSE_CONNECTOR = LocationId("connector-1", StructureLevel.EVSE_CONNECTOR)
OTHER_EVSE = LocationId("evse-2", StructureLevel.EVSE)
PAIRS = [StructurePair(POOL, STATION), StructurePair(STATION, EVSE), StructurePair(EVSE, EVSE_CONNECTOR)]


def ts(mins: float) -> str:
    return to_troca_timestamp(NOW + timedelta(minutes=mins))


def session(session_id: str, arrival_mins: int, location: LocationId | None = EVSE_CONNECTOR) -> SessionData:
    return SessionData(session_id=session_id, arrival_date=ts(arrival_mins), location=location)


def status(session_id: str, s: SessionStatus, mins: int) -> SessionStatusEntry:
    return SessionStatusEntry(session_id, s, ts(mins))


def test_find_active_session():
    sessions = [session("done", -60), session("current", -5), session("stale", -10)]
    statuses = [
        status("done", SessionStatus.CHARGING, -60),
        status("done", SessionStatus.COMPLETED, -30),
        status("stale", SessionStatus.CHARGING, -10),
        status("stale", SessionStatus.CLEARED, -9),
        status("current", SessionStatus.CHARGING, -5),
    ]
    active = find_active_session(sessions, statuses)
    assert active is not None and active.session_id == "current"

    assert find_active_session(sessions[:1], statuses) is None
    assert find_active_session([], []) is None


def test_find_active_session_no_status_is_active():
    active = find_active_session([session("new", -1)], [])
    assert active is not None and active.session_id == "new"


def test_find_active_session_status_order():
    """The latest status wins, regardless of the order they're returned in"""
    statuses = [status("s", SessionStatus.COMPLETED, -1), status("s", SessionStatus.CHARGING, -5)]
    assert find_active_session([session("s", -5)], statuses) is None


@pytest.mark.parametrize(
    "location, expected",
    [(EVSE_CONNECTOR, EVSE), (EVSE, EVSE), (LocationId("unknown", StructureLevel.EVSE_CONNECTOR), None), (None, None)],
)
def test_find_evse_location(location: LocationId | None, expected: LocationId | None):
    assert find_evse_location(session("s", 0, location), PAIRS) == expected


def schedule(kw: float) -> ActivePowerSchedule:
    return ActivePowerSchedule(ts(0), ts(60), [SchedulePeriod(ts(0), ts(60), Bounds(value=kw, unit="kW"))])


def command(command_id: str, issued_mins: int, kw: float, internal: bool = False) -> SessionCommand:
    return SessionCommand(
        command_id=command_id,
        type=SessionCommandType.SET_CHARGING_PROFILE,
        input_parameters=CommandParameter(ts(issued_mins), set_charging_profile_parameters(schedule(kw))),
        custom_data={"clientCommandId": "x"} if internal else {},
    )


def test_find_latest_pushed_schedule():
    commands = [
        command("prev-session", -30, -1),
        command("older", -10, -2),
        command("latest", -5, -3),
        command("internal", -4, -4, internal=True),
        command("other-evse", -3, -5),
        SessionCommand(command_id="stop", type=SessionCommandType.STOP_TRANSACTION),
    ]
    locations = [SessionCommandLocation(c.command_id, EVSE) for c in commands if c.command_id != "other-evse"]
    locations.append(SessionCommandLocation("other-evse", OTHER_EVSE))

    result = find_latest_pushed_schedule(commands, locations, EVSE, NOW - timedelta(minutes=20))
    assert result is not None
    assert result[0].command_id == "latest"
    assert result[1] == schedule(-3)

    assert find_latest_pushed_schedule(commands, locations, EVSE, NOW) is None
    assert find_latest_pushed_schedule(commands, [], EVSE, POLL_EPOCH) is None


def test_latest_command_status():
    statuses = [
        SessionCommandStatusEntry("a", CommandStatus.ACCEPTED, ts(-1)),
        SessionCommandStatusEntry("a", CommandStatus.PENDING, ts(-2)),
        SessionCommandStatusEntry("b", CommandStatus.ERROR, ts(0)),
    ]
    result = latest_command_status(statuses, "a")
    assert result is not None and result.status == CommandStatus.ACCEPTED
    assert latest_command_status(statuses, "c") is None


@pytest.mark.parametrize(
    "pushed_kw, status_value, status_mins, expected",
    [
        (None, None, 0, True),  # Nothing pushed yet
        (-7, None, 0, False),  # Pushed but no status yet
        (-7, CommandStatus.PENDING, 0, False),
        (-7, CommandStatus.ACCEPTED, 0, False),
        (-6, CommandStatus.ACCEPTED, 0, True),  # Schedule has changed
        (-7, CommandStatus.ERROR, 0, False),  # Failed too recently to retry
        (-7, CommandStatus.REFUSED, 0, False),
        (-7, CommandStatus.ERROR, -FAILED_SCHEDULE_RETRY_INTERVAL.total_seconds() / 60, True),
        (-6, CommandStatus.ERROR, 0, True),  # Schedule has changed - so a recent failure doesn't matter
    ],
)
def test_requires_push(pushed_kw, status_value, status_mins, expected):
    pushed = None if pushed_kw is None else (command("c", -1, pushed_kw), schedule(pushed_kw))
    s = None if status_value is None else SessionCommandStatusEntry("c", status_value, ts(status_mins))
    assert requires_push(pushed, s, schedule(-7), NOW) is expected
