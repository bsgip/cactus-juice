from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from assertical.fake.generator import generate_class_instance

from cactus_juice.error import PollError
from cactus_juice.model import OCPPMetadata, OCPPReading
from cactus_juice.troca import poll
from cactus_juice.troca.client import TrocaApiError
from cactus_juice.troca.mapping import to_troca_timestamp
from cactus_juice.troca.models import (
    Connector,
    ConnectorType,
    Evse,
    EvseUid,
    GetVariableResult,
    LocationId,
    MeteringReading,
    OcppComponent,
    OcppTarget,
    OcppVariable,
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
    metering_data: list[MeteringReading] = field(default_factory=list)
    evses: list[Evse] = field(default_factory=list)
    base_reports: list[str] = field(default_factory=list)  # The station of each GetBaseReport request
    metering_queries: list[dict] = field(default_factory=list)

    async def get_connectors(self) -> list[Connector]:
        return self.connectors

    async def get_stations(self) -> list[Station]:
        return self.stations

    async def get_evses(self) -> list[Evse]:
        return self.evses

    async def get_base_report(self, target: OcppTarget, request_id: int, report_base: str) -> str:
        self.base_reports.append(target.station_name)
        return "Accepted"

    async def get_metering_data(self, **kwargs) -> list[MeteringReading]:
        self.metering_queries.append(kwargs)
        return self.metering_data


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


async def test_poll_metadata_configures_metering_once_per_station(monkeypatch: pytest.MonkeyPatch):
    configured: list[str] = []
    fail = False

    async def configure_metering(client, target):
        if fail:
            raise ConnectionError("boom")
        configured.append(target.station_name)

    async def get_variables(target, requests):
        return []  # No usable rated power - poll_metadata stops before touching the DB

    monkeypatch.setattr(poll, "configure_metering", configure_metering)
    client = FakeTrocaClient([connector("qocppConnector2.1")], [station("FR*TRI*E123")])
    client.get_variables = get_variables  # ty: ignore[unresolved-attribute]
    state = client_state(client)

    await poll.poll_metadata(state, None, NOW)  # ty: ignore[invalid-argument-type]
    await poll.poll_metadata(state, None, NOW)  # ty: ignore[invalid-argument-type]
    assert configured == ["FR*TRI*E123"]

    # A different station gets configured too - and a failure is retried on the next poll
    state.ocpp_station_name = "CS2"
    fail = True
    with pytest.raises(ConnectionError):
        await poll.poll_metadata(state, None, NOW)  # ty: ignore[invalid-argument-type]
    assert state.metering_configured_station == "FR*TRI*E123"

    fail = False
    await poll.poll_metadata(state, None, NOW)  # ty: ignore[invalid-argument-type]
    assert configured == ["FR*TRI*E123", "CS2"]


async def test_poll_readings_only_requests_new(monkeypatch: pytest.MonkeyPatch):
    added: list[list[OCPPReading]] = []

    async def add_ocpp_readings(session, readings):
        added.append(readings)

    monkeypatch.setattr(poll, "add_ocpp_readings", add_ocpp_readings)
    client = FakeTrocaClient()
    state = client_state(client)
    state.last_reading_at = NOW

    # Out of order (as Troca's default ordering is) - plus one that Troca shouldn't have returned
    client.metering_data = [
        generate_class_instance(MeteringReading, seed=1, timestamp=to_troca_timestamp(NOW + timedelta(seconds=20))),
        generate_class_instance(MeteringReading, seed=2, timestamp=to_troca_timestamp(NOW)),
        generate_class_instance(MeteringReading, seed=3, timestamp=to_troca_timestamp(NOW + timedelta(seconds=10))),
    ]
    await poll.poll_readings(state, None, NOW)  # ty: ignore[invalid-argument-type]

    assert client.metering_queries == [{"after": NOW, "orderby": "timestamp"}]
    assert [r.reading_start for r in added[0]] == [NOW + timedelta(seconds=20), NOW + timedelta(seconds=10)]
    assert state.last_reading_at == NOW + timedelta(seconds=20)

    # Nothing new - last_reading_at is retained for the next poll
    client.metering_data = []
    await poll.poll_readings(state, None, NOW)  # ty: ignore[invalid-argument-type]
    assert client.metering_queries[-1] == {"after": NOW + timedelta(seconds=20), "orderby": "timestamp"}
    assert state.last_reading_at == NOW + timedelta(seconds=20)


def evse(station_name: str, max_charge_kw: float | None, max_discharge_kw: float | None) -> Evse:
    return generate_class_instance(
        Evse,
        evse_uid=EvseUid(station_name=station_name, evse_nb=1),
        max_charge_power=max_charge_kw,
        max_discharge_power=max_discharge_kw,
    )


@pytest.fixture
def metadata_client(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeTrocaClient, list[OCPPMetadata], list[str]]:
    """A client for poll_metadata - returns (client, every upserted metadata, every GetVariables station)"""
    upserted: list[OCPPMetadata] = []
    get_variables_calls: list[str] = []

    async def configure_metering(client, target):
        pass

    async def upsert_ocpp_metadata(session, metadata):
        upserted.append(metadata)

    monkeypatch.setattr(poll, "configure_metering", configure_metering)
    monkeypatch.setattr(poll, "upsert_ocpp_metadata", upsert_ocpp_metadata)

    client = FakeTrocaClient([connector("qocppConnector2.1")], [station("HALO")])

    async def get_variables(target, requests):
        get_variables_calls.append(target.station_name)
        return [
            GetVariableResult(
                attribute_status="Accepted",
                component=OcppComponent(name="ElectricalFeed"),
                variable=OcppVariable(name="Power"),
                attribute_value="40",
            )
        ]

    client.get_variables = get_variables  # ty: ignore[unresolved-attribute]
    return client, upserted, get_variables_calls


async def test_poll_metadata_from_evse_ratings(metadata_client):
    client, upserted, get_variables_calls = metadata_client
    client.evses = [evse("OTHER", 22, 22), evse("HALO", 7.4, 7.4)]

    await poll.poll_metadata(client_state(client), None, NOW)  # ty: ignore[invalid-argument-type]

    assert [(m.max_power_watts, m.max_charge_rate_watts, m.max_discharge_rate_watts) for m in upserted] == [
        (7400, 7400, 7400)
    ]
    assert get_variables_calls == [] and client.base_reports == []


async def test_poll_metadata_falls_back_to_station(metadata_client):
    """Without EVSE ratings, the station is asked for its report (once) and its rated power directly"""
    client, upserted, get_variables_calls = metadata_client
    client.evses = [evse("HALO", None, None)]
    state = client_state(client)

    await poll.poll_metadata(state, None, NOW)  # ty: ignore[invalid-argument-type]
    await poll.poll_metadata(state, None, NOW)  # ty: ignore[invalid-argument-type]

    assert client.base_reports == ["HALO"]
    assert get_variables_calls == ["HALO", "HALO"]
    assert [m.max_power_watts for m in upserted] == [40000, 40000]


async def test_poll_metadata_base_report_refused(metadata_client):
    """A station that refuses GetBaseReport still has its rated power requested directly"""
    client, upserted, get_variables_calls = metadata_client

    async def get_base_report(target, request_id, report_base):
        client.base_reports.append(target.station_name)
        raise TrocaApiError(502, "POST", "/GetBaseReport", "CallError")

    client.get_base_report = get_base_report
    state = client_state(client)

    await poll.poll_metadata(state, None, NOW)  # ty: ignore[invalid-argument-type]
    await poll.poll_metadata(state, None, NOW)  # ty: ignore[invalid-argument-type]

    assert client.base_reports == ["HALO"]
    assert [m.max_power_watts for m in upserted] == [40000, 40000]


class FakeSession:
    async def commit(self) -> None:
        pass


class FakeDatabaseConnection:
    @asynccontextmanager
    async def session_maker(self):
        yield FakeSession()


async def test_run_polls_raises_failures_after_running_every_poll(monkeypatch: pytest.MonkeyPatch):
    """A failing poll shouldn't stop the others from running - but must still be raised so it gets reported"""
    called: list[str] = []

    def fake_poll(name: str, error: Exception | None = None):
        async def _poll(state, session, now):
            called.append(name)
            if error is not None:
                raise error

        return _poll

    monkeypatch.setattr(
        poll, "poll_readings", fake_poll("readings", TrocaApiError(503, "GET", "/metering", "unavailable"))
    )
    monkeypatch.setattr(poll, "poll_metadata", fake_poll("metadata"))
    monkeypatch.setattr(poll, "poll_schedules", fake_poll("schedules", ConnectionError()))
    monkeypatch.setattr(poll, "poll_prices", fake_poll("prices"))

    state = client_state(FakeTrocaClient())
    state.db = FakeDatabaseConnection()  # ty: ignore[invalid-assignment]

    with pytest.raises(PollError) as exc_info:
        await poll.run_polls(state)

    assert called == ["readings", "metadata", "schedules", "prices"]
    assert [type(e) for _, e in exc_info.value.failures] == [TrocaApiError, ConnectionError]
    assert "GET /metering -> 503: unavailable" in str(exc_info.value)
    assert "ConnectionError" in str(exc_info.value)
    assert exc_info.value.next_poll_at >= state.next_poll()
    assert state.readings_poll.last_poll != POLL_EPOCH
    assert state.schedule_poll.last_poll != POLL_EPOCH


async def test_run_polls_keeps_reporting_failure_until_that_poll_succeeds(monkeypatch: pytest.MonkeyPatch):
    """Regression - a failing (slow cadence) metadata poll must still be reported by subsequent calls where only
    other polls (eg readings) were due and succeeded, otherwise the task health immediately flips back to healthy"""
    metadata_error: Exception | None = TrocaApiError(504, "POST", "/SetVariables", "Gateway Time-out")
    called: list[str] = []

    async def poll_readings(state, session, now):
        called.append("readings")

    async def poll_metadata(state, session, now):
        called.append("metadata")
        if metadata_error is not None:
            raise metadata_error

    async def noop(state, session, now):
        pass

    monkeypatch.setattr(poll, "poll_readings", poll_readings)
    monkeypatch.setattr(poll, "poll_metadata", poll_metadata)
    monkeypatch.setattr(poll, "poll_schedules", noop)
    monkeypatch.setattr(poll, "poll_prices", noop)

    state = client_state(FakeTrocaClient())
    state.db = FakeDatabaseConnection()  # ty: ignore[invalid-assignment]

    with pytest.raises(PollError, match="Gateway Time-out"):
        await poll.run_polls(state)
    assert called == ["readings", "metadata"]

    # Only readings are due (and succeed) - the metadata failure is still outstanding
    called.clear()
    state.readings_poll.last_poll = POLL_EPOCH
    with pytest.raises(PollError, match="Gateway Time-out"):
        await poll.run_polls(state)
    assert called == ["readings"]

    # Once metadata succeeds, the failure is cleared
    called.clear()
    metadata_error = None
    state.metadata_poll.last_poll = POLL_EPOCH
    await poll.run_polls(state)
    assert called == ["metadata"]
    assert state.failures == {}
