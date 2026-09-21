from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from cactus_juice.model import SatecConfig, SatecReading
from cactus_juice.satec.client import (
    MIN_DATE,
    READING_VALUE_COLUMNS,
    ClientState,
    PolledMeter,
    poll_meter_sync,
    poll_required,
    reading_to_model,
    reconcile_meters,
)
from cactus_juice.satec.meter import Reading


@dataclass
class FakeSatecMeter:
    """Duck-types cactus_juice.satec.meter.SatecMeter without touching real Modbus I/O."""

    connect_result: bool = True
    sample_result: Reading | None = None
    raise_on_sample: Exception | None = None

    connect_calls: int = field(default=0, init=False)
    close_calls: int = field(default=0, init=False)
    sample_calls: list[tuple[bool, bool]] = field(default_factory=list, init=False)

    def connect(self) -> bool:
        self.connect_calls += 1
        return self.connect_result

    def close(self) -> None:
        self.close_calls += 1

    def sample(self, phases: bool = False, energy: bool = False) -> Reading:
        self.sample_calls.append((phases, energy))
        if self.raise_on_sample is not None:
            raise self.raise_on_sample
        assert self.sample_result is not None
        return self.sample_result


def make_polled_meter(
    satec_config_id: int = 1,
    changed_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    poll_rate: timedelta = timedelta(seconds=10),
    last_poll: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    include_phases: bool = False,
    include_energy: bool = False,
    meter: FakeSatecMeter | None = None,
) -> PolledMeter:
    return PolledMeter(
        satec_config_id=satec_config_id,
        changed_at=changed_at,
        poll_rate=poll_rate,
        include_phases=include_phases,
        include_energy=include_energy,
        meter=meter if meter is not None else FakeSatecMeter(),  # ty: ignore[invalid-argument-type]
        last_poll=last_poll,
    )


def make_satec_config(satec_config_id: int, changed_at: datetime, **overrides) -> SatecConfig:
    values = {
        "label": f"meter {satec_config_id}",
        "poll_rate_seconds": 10.0,
        "model": "em133",
        "host": None,
        "port": "/dev/ttyUSB0",
        "port_tcp": 502,
        "unit": 1,
        "baud": 19200,
        "parity": "N",
        "timeout_seconds": 1.0,
        "include_phases": False,
        "include_energy": False,
        "float_mode": False,
        **overrides,
    }
    config = SatecConfig(satec_config_id=satec_config_id, changed_at=changed_at, **values)
    return config


@pytest.mark.parametrize(
    "now, expected",
    [
        (datetime(2026, 1, 1, 0, 0, 9, tzinfo=UTC), False),
        (datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC), True),
        (datetime(2026, 1, 1, 0, 0, 11, tzinfo=UTC), True),
    ],
)
def test_poll_required(now: datetime, expected: bool):
    pm = make_polled_meter(last_poll=datetime(2026, 1, 1, tzinfo=UTC), poll_rate=timedelta(seconds=10))
    assert poll_required(pm, now) is expected


def test_reading_to_model_maps_present_values_and_defaults_missing_to_none():
    values = {col: float(i) for i, col in enumerate(READING_VALUE_COLUMNS) if i % 2 == 0}
    reading = Reading(timestamp=1_700_000_000.0, serial=123, values=values)

    model = reading_to_model(satec_config_id=7, reading=reading)

    assert isinstance(model, SatecReading)
    assert model.satec_config_id == 7
    assert model.reading_start == datetime.fromtimestamp(1_700_000_000.0, tz=UTC)
    for col in READING_VALUE_COLUMNS:
        assert getattr(model, col) == values.get(col)


def test_reconcile_meters_adds_new_configs():
    state = ClientState.new_instance(db=None)  # ty: ignore[invalid-argument-type]
    config = make_satec_config(1, datetime(2026, 1, 1, tzinfo=UTC))

    reconcile_meters(state, [config])

    assert set(state.meters) == {1}
    assert state.meters[1].changed_at == config.changed_at
    assert state.meters[1].poll_rate == timedelta(seconds=config.poll_rate_seconds)


def test_reconcile_meters_removes_deleted_configs():
    state = ClientState.new_instance(db=None)  # ty: ignore[invalid-argument-type]
    fake = FakeSatecMeter()
    state.meters[1] = make_polled_meter(satec_config_id=1, meter=fake)

    reconcile_meters(state, [])

    assert state.meters == {}
    assert fake.close_calls == 1


def test_reconcile_meters_leaves_unchanged_configs_alone():
    state = ClientState.new_instance(db=None)  # ty: ignore[invalid-argument-type]
    fake = FakeSatecMeter()
    changed_at = datetime(2026, 1, 1, tzinfo=UTC)
    existing = make_polled_meter(satec_config_id=1, changed_at=changed_at, meter=fake)
    existing.last_poll = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)  # would be lost if rebuilt
    state.meters[1] = existing

    reconcile_meters(state, [make_satec_config(1, changed_at)])

    assert state.meters[1] is existing
    assert fake.close_calls == 0
    assert state.meters[1].last_poll == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)


def test_reconcile_meters_rebuilds_changed_configs():
    state = ClientState.new_instance(db=None)  # ty: ignore[invalid-argument-type]
    fake = FakeSatecMeter()
    old_changed_at = datetime(2026, 1, 1, tzinfo=UTC)
    new_changed_at = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    existing = make_polled_meter(satec_config_id=1, changed_at=old_changed_at, meter=fake)
    existing.last_poll = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    state.meters[1] = existing

    reconcile_meters(state, [make_satec_config(1, new_changed_at, poll_rate_seconds=99.0)])

    assert fake.close_calls == 1  # the stale connection was torn down
    rebuilt = state.meters[1]
    assert rebuilt is not existing
    assert rebuilt.changed_at == new_changed_at
    assert rebuilt.poll_rate == timedelta(seconds=99.0)
    assert rebuilt.last_poll == MIN_DATE  # a freshly built PolledMeter always starts out due for an immediate poll


def test_poll_meter_sync_connects_lazily_once():
    reading = Reading(timestamp=1.0, serial=1, values={})
    fake = FakeSatecMeter(sample_result=reading)
    pm = make_polled_meter(meter=fake, include_phases=True, include_energy=False)

    first = poll_meter_sync(pm)
    second = poll_meter_sync(pm)

    assert first is reading
    assert second is reading
    assert fake.connect_calls == 1  # only connected once - reused for the second poll
    assert fake.sample_calls == [(True, False), (True, False)]


def test_poll_meter_sync_raises_when_connect_fails():
    fake = FakeSatecMeter(connect_result=False)
    pm = make_polled_meter(meter=fake)

    with pytest.raises(OSError):
        poll_meter_sync(pm)

    assert pm.connected is False


def test_poll_meter_sync_closes_and_resets_on_sample_failure():
    fake = FakeSatecMeter(raise_on_sample=OSError("boom"))
    pm = make_polled_meter(meter=fake)
    pm.connected = True  # simulate an already-open connection from a prior successful poll

    with pytest.raises(OSError, match="boom"):
        poll_meter_sync(pm)

    assert pm.connected is False
    assert fake.close_calls == 1
    assert fake.connect_calls == 0  # it was already connected - no reconnect attempted this call
