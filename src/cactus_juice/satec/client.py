import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from cactus_juice.crud import SATEC_READING_VALUE_COLUMNS, fetch_satec_configs, insert_satec_readings
from cactus_juice.db import DatabaseConnection
from cactus_juice.model import SatecConfig, SatecReading
from cactus_juice.satec.meter import PROFILES, Reading, SatecMeter, build_client

logger = logging.getLogger(__name__)

MIN_DATE = datetime(1000, 1, 1, tzinfo=UTC)  # We just want a TZ aware "minimum" date - forces an immediate first poll

# The columns on SatecReading that come directly out of a satec.meter.Reading.values dict (everything on
# SatecReading EXCEPT the PK/satec_config_id/reading_start/created_at).
READING_VALUE_COLUMNS = tuple(c for c in SATEC_READING_VALUE_COLUMNS if c not in ("satec_config_id", "reading_start"))


@dataclass(slots=True)
class PolledMeter:
    """Tracks the live SatecMeter connection + polling schedule for a single SatecConfig row."""

    satec_config_id: int
    changed_at: datetime  # SatecConfig.changed_at as of when this PolledMeter was last (re)built
    poll_rate: timedelta
    include_phases: bool
    include_energy: bool
    meter: SatecMeter
    last_poll: datetime = MIN_DATE
    connected: bool = False

    @staticmethod
    def new_instance(config: SatecConfig) -> "PolledMeter":
        client = build_client(
            host=config.host,
            port=config.port,
            port_tcp=config.port_tcp,
            baud=config.baud,
            parity=config.parity,
            timeout=config.timeout_seconds,
        )
        meter = SatecMeter(PROFILES[config.model], client, unit=config.unit, float_mode=config.float_mode)
        return PolledMeter(
            satec_config_id=config.satec_config_id,
            changed_at=config.changed_at,
            poll_rate=timedelta(seconds=config.poll_rate_seconds),
            include_phases=config.include_phases,
            include_energy=config.include_energy,
            meter=meter,
        )


@dataclass(slots=True)
class ClientState:
    """Manages a PolledMeter (connection + schedule) for every currently registered SatecConfig row."""

    db: DatabaseConnection
    meters: dict[int, PolledMeter] = field(default_factory=dict)

    def next_poll(self) -> datetime:
        """Calculates the next moment a poll should occur across every currently tracked meter."""
        return min((pm.last_poll + pm.poll_rate for pm in self.meters.values()), default=datetime.now(UTC))

    @staticmethod
    def new_instance(db: DatabaseConnection) -> "ClientState":
        return ClientState(db=db)


def poll_required(pm: PolledMeter, now: datetime) -> bool:
    return now >= (pm.last_poll + pm.poll_rate)


def reconcile_meters(state: ClientState, configs: Sequence[SatecConfig]) -> None:
    """Adds/rebuilds/removes entries in state.meters so it exactly matches the supplied set of SatecConfig rows.

    A config whose changed_at has moved on since we last saw it is torn down and rebuilt from scratch (its
    connection details may have changed) - closing the stale Modbus connection first. A config that has been
    deleted has its connection closed and is dropped entirely."""

    seen_ids: set[int] = set()
    for config in configs:
        seen_ids.add(config.satec_config_id)
        existing = state.meters.get(config.satec_config_id)
        if existing is not None and existing.changed_at == config.changed_at:
            continue

        if existing is not None:
            logger.info(f"SatecConfig id={config.satec_config_id} has changed - rebuilding its connection.")
            existing.meter.close()

        try:
            state.meters[config.satec_config_id] = PolledMeter.new_instance(config)
        except Exception:
            logger.exception(f"Failed building a connection for SatecConfig id={config.satec_config_id}")
            state.meters.pop(config.satec_config_id, None)

    for removed_id in set(state.meters) - seen_ids:
        logger.info(f"SatecConfig id={removed_id} no longer exists - closing its connection.")
        state.meters.pop(removed_id).meter.close()


def poll_meter_sync(pm: PolledMeter) -> Reading:
    """Runs the actual (blocking) Modbus I/O for a single meter. Intended to be run via asyncio.to_thread since
    pymodbus's synchronous clients block the calling thread for the duration of the read.

    Connects lazily (on first use, or after a previous failure dropped the connection) and leaves the connection
    open afterwards so a subsequent poll can reuse it."""

    if not pm.connected:
        if not pm.meter.connect():
            raise OSError(f"Could not open Modbus connection for SatecConfig id={pm.satec_config_id}")
        pm.connected = True

    try:
        return pm.meter.sample(phases=pm.include_phases, energy=pm.include_energy)
    except Exception:
        # Leave the connection closed - the next due poll will reconnect from scratch.
        pm.meter.close()
        pm.connected = False
        raise


def reading_to_model(satec_config_id: int, reading: Reading) -> SatecReading:
    return SatecReading(
        satec_config_id=satec_config_id,
        reading_start=datetime.fromtimestamp(reading.timestamp, tz=UTC),
        **{col: reading.values.get(col) for col in READING_VALUE_COLUMNS},
    )


async def run_polls(state: ClientState, min_wait: timedelta = timedelta(seconds=1)) -> datetime:
    """Runs every required poll of every registered SatecConfig, updating state as required.

    Returns the next "wakeup" time for the next set of polls.

    Will not poll meters that have been polled recently (per their own poll_rate_seconds). A failure polling
    one meter is logged and does not prevent the others in the same call from being polled."""

    now = datetime.now(UTC)

    async with state.db.session_maker() as session:
        configs = await fetch_satec_configs(session)
    reconcile_meters(state, configs)

    due = [pm for pm in state.meters.values() if poll_required(pm, now)]
    if not due:
        return max(state.next_poll(), datetime.now(UTC) + min_wait)

    readings: list[SatecReading] = []
    for pm in due:
        try:
            reading = await asyncio.to_thread(poll_meter_sync, pm)
            readings.append(reading_to_model(pm.satec_config_id, reading))
        except Exception:
            logger.exception(f"Failed polling SatecConfig id={pm.satec_config_id}")
        finally:
            pm.last_poll = now

    if readings:
        logger.info(f"Writing {len(readings)} SatecReading(s)")
        async with state.db.session_maker() as session:
            await insert_satec_readings(session, readings)
            await session.commit()

    return max(state.next_poll(), datetime.now(UTC) + min_wait)


async def close(state: ClientState) -> None:
    """Closes every currently tracked meter connection - intended for use during a graceful shutdown."""
    for pm in state.meters.values():
        pm.meter.close()
    state.meters.clear()
