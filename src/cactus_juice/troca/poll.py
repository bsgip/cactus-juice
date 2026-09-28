import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import add_ocpp_readings, upsert_ocpp_metadata
from cactus_juice.db import DatabaseConnection
from cactus_juice.troca.client import TrocaClient
from cactus_juice.troca.mapping import (
    DEFAULT_OCPP_EVSE_NB,
    rated_power_variables,
    reading_to_db,
    variables_to_metadata,
)
from cactus_juice.troca.models import (
    ConnectorType,
    LocationId,
    OcppTarget,
    ScheduleSyncMode,
    StructureLevel,
    ocpp_version_from_connector_name,
)
from cactus_juice.troca.ocpp_schedule import PushedProfile, sync_ocpp_schedule
from cactus_juice.troca.session_schedule import sync_session_schedule

logger = logging.getLogger(__name__)

POLL_EPOCH = datetime.min.replace(tzinfo=UTC)

DEFAULT_SCHEDULE_SYNC_MODE = ScheduleSyncMode.OCPP


@dataclass(slots=True)
class Pollable:
    last_poll: datetime
    poll_rate: timedelta


@dataclass(slots=True)
class ClientState:
    """Encapsulates all the state the client needs to know about the Troca server and its polling behaviour."""

    client: TrocaClient
    db: DatabaseConnection

    connector_id: str | None  # The ID of the Troca connector that will be used for comms
    readings_poll: Pollable  # Poll status of the readings
    schedule_poll: Pollable  # Poll status of the charge schedule
    metadata_poll: Pollable  # Poll status of the connected EVSE metadata
    ramp_step: timedelta

    # Troca returns its whole metering history on every poll - only readings after this are new
    last_reading_at: datetime

    schedule_sync_mode: ScheduleSyncMode = DEFAULT_SCHEDULE_SYNC_MODE
    pushed_profile: PushedProfile | None = None  # What was last pushed to the station (ScheduleSyncMode.OCPP only)

    # Configured values that would otherwise be discovered via the Troca API (None = discover on each poll)
    ocpp_connector_name: str | None = None
    ocpp_version: str | None = None
    ocpp_station_name: str | None = None
    ocpp_evse_nb: int | None = None
    evse_id: str | None = None

    def next_poll(self) -> datetime:
        """Calculates the next moment a poll/post should occur."""

        def _candidate_poll(p: Pollable | None) -> datetime | None:
            return None if p is None else p.last_poll + p.poll_rate

        candidate_times = [
            _candidate_poll(self.readings_poll),
            _candidate_poll(self.schedule_poll),
            _candidate_poll(self.metadata_poll),
        ]

        # The default should never occur - but just in case
        return min((ct for ct in candidate_times if ct is not None), default=datetime.now(UTC))

    @staticmethod
    def new_instance(
        client: TrocaClient,
        db: DatabaseConnection,
        connector_id: str | None,
        readings_poll_rate_seconds: int,
        schedule_poll_rate_seconds: int,
        metadata_poll_rate_seconds: int,
        ramp_step_seconds: int,
        schedule_sync_mode: ScheduleSyncMode = DEFAULT_SCHEDULE_SYNC_MODE,
        ocpp_connector_name: str | None = None,
        ocpp_version: str | None = None,
        ocpp_station_name: str | None = None,
        ocpp_evse_nb: int | None = None,
        evse_id: str | None = None,
    ) -> "ClientState":
        return ClientState(
            client=client,
            db=db,
            connector_id=connector_id,
            readings_poll=Pollable(POLL_EPOCH, timedelta(seconds=readings_poll_rate_seconds)),
            schedule_poll=Pollable(POLL_EPOCH, timedelta(seconds=schedule_poll_rate_seconds)),
            metadata_poll=Pollable(POLL_EPOCH, timedelta(seconds=metadata_poll_rate_seconds)),
            ramp_step=timedelta(seconds=ramp_step_seconds),
            last_reading_at=datetime.now(UTC),
            schedule_sync_mode=schedule_sync_mode,
            ocpp_connector_name=ocpp_connector_name,
            ocpp_version=ocpp_version,
            ocpp_station_name=ocpp_station_name,
            ocpp_evse_nb=ocpp_evse_nb,
            evse_id=evse_id,
        )


async def resolve_ocpp_target(state: ClientState) -> OcppTarget | None:
    """Works out where OCPP passthrough messages should be sent. Any of the connector name, OCPP version and
    station name configured on state are used as is - the rest are discovered via the Troca API (the connector name
    from state.connector_id, the OCPP version from the connector name and the station as the only station
    registered with Troca)."""

    connector_name = state.ocpp_connector_name
    if connector_name is None:
        connectors = await state.client.get_connectors()
        connector = next((c for c in connectors if c.connector_id == state.connector_id), None)
        if connector is None or connector.connector_type != ConnectorType.Q_OCPP:
            logger.error(f"Troca connector {state.connector_id} isn't a known OCPP connector.")
            return None
        connector_name = connector.name

    ocpp_version = state.ocpp_version or ocpp_version_from_connector_name(connector_name)
    if ocpp_version is None:
        logger.error(f"Unable to determine the OCPP version from connector name '{connector_name}'.")
        return None

    station_name = state.ocpp_station_name
    if station_name is None:
        stations = await state.client.get_stations()
        if not stations:
            logger.info("No charging stations registered with Troca.")
            return None
        if len(stations) > 1:
            logger.warning(f"{len(stations)} Troca charging stations found - only {stations[0].name} will be used.")
        station_name = stations[0].name

    return OcppTarget(connector_name, ocpp_version, station_name)


async def poll_readings(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Takes a snapshot of any new OCPP readings via troca and pushes them into the DB"""
    readings = [reading_to_db(r) for r in await state.client.get_metering_data()]
    new_readings = [r for r in readings if r.reading_start > state.last_reading_at]

    await add_ocpp_readings(session, new_readings)
    if new_readings:
        state.last_reading_at = max(r.reading_start for r in new_readings)


async def poll_metadata(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Asks the charging station directly (via Troca's OCPP passthrough) for its rated power and updates the DB
    with the latest values."""

    target = await resolve_ocpp_target(state)
    if target is None:
        return

    evse_nb = state.ocpp_evse_nb if state.ocpp_evse_nb is not None else DEFAULT_OCPP_EVSE_NB
    results = await state.client.get_variables(target, rated_power_variables(evse_nb))
    metadata = variables_to_metadata(results)
    if metadata is None:
        logger.warning(f"Station {target.station_name} didn't report a usable rated power: {results}")
        return

    await upsert_ocpp_metadata(session, metadata)


async def poll_schedules(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Keeps the charging station's schedule in sync with the full upcoming schedule of CSIP-Aus controls, via
    whichever mechanism state.schedule_sync_mode selects."""

    if state.schedule_sync_mode == ScheduleSyncMode.TROCA_SESSION:
        evse_location = None if state.evse_id is None else LocationId(state.evse_id, StructureLevel.EVSE)
        await sync_session_schedule(state.client, session, now, evse_location)
        return

    target = await resolve_ocpp_target(state)
    if target is None:
        return
    state.pushed_profile = await sync_ocpp_schedule(state.client, target, session, now, state.pushed_profile)


def poll_required(pollable: Pollable, now: datetime) -> bool:
    return now >= (pollable.last_poll + pollable.poll_rate)


async def run_polls(state: ClientState, min_wait: timedelta = timedelta(seconds=1)) -> datetime:
    """Runs every required poll of server, updating state as required. Will manage the creation of db sessions

    Returns the next "wakeup" time for the next set of polls

    Will not poll/post resources that have been polled recently. A failure polling one resource is logged and
    does not prevent the others in the same call from being polled."""

    now = datetime.now(UTC)

    if poll_required(state.readings_poll, now):
        try:
            async with state.db.session_maker() as session:
                await poll_readings(state, session, now)
                await session.commit()
        except Exception:
            logger.exception("Failed polling Troca readings.")
        finally:
            state.readings_poll.last_poll = now

    # Metadata before schedules - the schedule can't be built until the EVSE's rated power is known
    if poll_required(state.metadata_poll, now):
        try:
            async with state.db.session_maker() as session:
                await poll_metadata(state, session, now)
                await session.commit()
        except Exception:
            logger.exception("Failed polling Troca session metadata.")
        finally:
            state.metadata_poll.last_poll = now

    if poll_required(state.schedule_poll, now):
        try:
            async with state.db.session_maker() as session:
                await poll_schedules(state, session, now)
                await session.commit()
        except Exception:
            logger.exception("Failed polling/pushing the Troca charging schedule.")
        finally:
            state.schedule_poll.last_poll = now

    # Figure out our next call to this function
    return max(state.next_poll(), datetime.now(UTC) + min_wait)
