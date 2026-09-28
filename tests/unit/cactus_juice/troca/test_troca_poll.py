from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from cactus_juice.troca import poll
from cactus_juice.troca.models import (
    Connector,
    ConnectorType,
    LocationId,
    OcppTarget,
    ScheduleSyncMode,
    Station,
    StructureLevel,
)
from cactus_juice.troca.poll import (
    POLL_EPOCH,
    ClientState,
    Pollable,
    poll_required,
    poll_schedules,
    resolve_ocpp_target,
)

NOW = datetime(2026, 9, 28, 4, 0, 0, tzinfo=UTC)
CONNECTOR_ID = "d6ccd51d-4b07-463f-89e3-f4eb7a7a1e58"


@dataclass
class FakeTrocaClient:
    connectors: list[Connector] = field(default_factory=list)
    stations: list[Station] = field(default_factory=list)

    async def get_connectors(self) -> list[Connector]:
        return self.connectors

    async def get_stations(self) -> list[Station]:
        return self.stations


def connector(name: str, connector_id: str = CONNECTOR_ID, t: ConnectorType = ConnectorType.Q_OCPP) -> Connector:
    return Connector(name=name, connector_id=connector_id, connector_type=t)


def station(name: str) -> Station:
    return Station(station_id=name, name=name)


def client_state(client: FakeTrocaClient, mode: ScheduleSyncMode = ScheduleSyncMode.OCPP) -> ClientState:
    return ClientState.new_instance(client, None, CONNECTOR_ID, 1, 1, 1, 1, mode)  # ty: ignore[invalid-argument-type]


def test_poll_required_from_epoch():
    """Regression - a freshly created Pollable must be comparable against an aware datetime"""
    assert poll_required(Pollable(POLL_EPOCH, timedelta(seconds=10)), NOW)
    assert not poll_required(Pollable(NOW, timedelta(seconds=10)), NOW + timedelta(seconds=9))


@pytest.mark.parametrize(
    "connectors, stations, expected",
    [
        (
            [connector("qocppConnector1.6", "other"), connector("qocppConnector2.1")],
            [station("FR*TRI*E123")],
            OcppTarget("qocppConnector2.1", "2.1", "FR*TRI*E123"),
        ),
        (
            [connector("qocppConnector2.0.1")],
            [station("A"), station("B")],
            OcppTarget("qocppConnector2.0.1", "2.0.1", "A"),
        ),
        ([connector("qocppConnector1.6", "other")], [station("A")], None),  # No matching connector
        ([connector("linky", t=ConnectorType.LINKY)], [station("A")], None),  # Not OCPP
        ([connector("qocppConnector")], [station("A")], None),  # No version
        ([connector("qocppConnector2.1")], [], None),  # No station
    ],
)
async def test_resolve_ocpp_target(connectors: list[Connector], stations: list[Station], expected: OcppTarget | None):
    assert await resolve_ocpp_target(client_state(FakeTrocaClient(connectors, stations))) == expected


async def test_poll_schedules_dispatch(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    async def sync_session_schedule(client, session, now, evse_location):
        calls.append(f"session {evse_location}")

    async def sync_ocpp_schedule(client, target, session, now, last):
        calls.append(f"ocpp {target.station_name}")
        return "pushed"

    monkeypatch.setattr(poll, "sync_session_schedule", sync_session_schedule)
    monkeypatch.setattr(poll, "sync_ocpp_schedule", sync_ocpp_schedule)
    client = FakeTrocaClient([connector("qocppConnector2.1")], [station("FR*TRI*E123")])

    state = client_state(client, ScheduleSyncMode.OCPP)
    await poll_schedules(state, None, NOW)  # ty: ignore[invalid-argument-type]
    assert calls == ["ocpp FR*TRI*E123"] and state.pushed_profile == "pushed"

    calls.clear()
    state = client_state(client, ScheduleSyncMode.TROCA_SESSION)
    await poll_schedules(state, None, NOW)  # ty: ignore[invalid-argument-type]
    assert calls == ["session None"] and state.pushed_profile is None

    # A configured EVSE skips discovery
    calls.clear()
    state.evse_id = "evse-1"
    await poll_schedules(state, None, NOW)  # ty: ignore[invalid-argument-type]
    assert calls == [f"session {LocationId('evse-1', StructureLevel.EVSE)}"]


@dataclass
class CountingFakeTrocaClient(FakeTrocaClient):
    calls: int = 0

    async def get_connectors(self) -> list[Connector]:
        self.calls += 1
        return await super().get_connectors()

    async def get_stations(self) -> list[Station]:
        self.calls += 1
        return await super().get_stations()


@pytest.mark.parametrize(
    "overrides, expected, expected_calls",
    [
        ({}, OcppTarget("qocppConnector2.1", "2.1", "FR*TRI*E123"), 2),
        (
            {"ocpp_connector_name": "qocppConnector2.0.1", "ocpp_station_name": "CS1"},
            OcppTarget("qocppConnector2.0.1", "2.0.1", "CS1"),
            0,
        ),
        (
            {"ocpp_connector_name": "myConnector", "ocpp_version": "2.1", "ocpp_station_name": "CS1"},
            OcppTarget("myConnector", "2.1", "CS1"),
            0,
        ),
        ({"ocpp_version": "2.0.1"}, OcppTarget("qocppConnector2.1", "2.0.1", "FR*TRI*E123"), 2),
        ({"ocpp_station_name": "CS1"}, OcppTarget("qocppConnector2.1", "2.1", "CS1"), 1),
        ({"ocpp_connector_name": "myConnector", "ocpp_station_name": "CS1"}, None, 0),  # Version undeterminable
    ],
)
async def test_resolve_ocpp_target_configured(overrides: dict, expected: OcppTarget | None, expected_calls: int):
    """Configured values are used as is - only the rest are discovered"""
    client = CountingFakeTrocaClient([connector("qocppConnector2.1")], [station("FR*TRI*E123")])
    state = client_state(client)
    for k, v in overrides.items():
        setattr(state, k, v)

    assert await resolve_ocpp_target(state) == expected
    assert client.calls == expected_calls
