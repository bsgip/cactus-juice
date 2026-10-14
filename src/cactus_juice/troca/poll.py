import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import add_ocpp_readings, upsert_ocpp_metadata
from cactus_juice.db import DatabaseConnection
from cactus_juice.error import PollError
from cactus_juice.troca.client import TrocaApiError, TrocaClient
from cactus_juice.troca.mapping import (
    DEFAULT_OCPP_EVSE_NB,
    evse_to_metadata,
    find_evse,
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
from cactus_juice.troca.ocpp_metering import configure_metering
from cactus_juice.troca.ocpp_prices import PushedPrices, sync_ocpp_prices
from cactus_juice.troca.ocpp_schedule import PushedProfile, sync_ocpp_schedule
from cactus_juice.troca.session_schedule import sync_session_schedule

logger = logging.getLogger(__name__)

POLL_EPOCH = datetime.min.replace(tzinfo=UTC)

DEFAULT_SCHEDULE_SYNC_MODE = ScheduleSyncMode.OCPP

# GetBaseReport request (see poll_metadata) - the requestId only matters for correlating the station's report
BASE_REPORT_REQUEST_ID = 4101
BASE_REPORT_TYPE = "FullInventory"


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

    # The latest reading received so far - each poll only asks Troca for readings after this
    last_reading_at: datetime

    schedule_sync_mode: ScheduleSyncMode = DEFAULT_SCHEDULE_SYNC_MODE
    pushed_profile: PushedProfile | None = None  # What was last pushed to the station (ScheduleSyncMode.OCPP only)
    pushed_prices: PushedPrices | None = None  # The dynamic prices last pushed to the station
    metering_configured_station: str | None = None  # The station whose sampled meter values were last configured
    base_report_requested_station: str | None = None  # The station last asked to resend its device model report

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
    metering_data = await state.client.get_metering_data(after=state.last_reading_at, orderby="timestamp")

    # last_reading_at is sent truncated to whole seconds - re-filtering stops a sub second reading being re-added
    readings = [reading_to_db(r) for r in metering_data]
    new_readings = [r for r in readings if r.reading_start > state.last_reading_at]

    await add_ocpp_readings(session, new_readings)
    if new_readings:
        state.last_reading_at = max(r.reading_start for r in new_readings)


async def poll_metadata(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Updates the DB with the latest rated power of the EVSE. The first poll of a station also configures which
    meter values it samples.

    The ratings Troca reports on the EVSE (from the station's device model report) are preferred. If they're
    missing, the station is asked (once) to resend its report - and in the meantime the station is asked directly
    (via the OCPP passthrough) for its rated power. Some stations (eg the Trialog simulator) never report ratings
    to Troca, so the direct request is all that will ever work for them."""

    target = await resolve_ocpp_target(state)
    if target is None:
        return

    if state.metering_configured_station != target.station_name:
        await configure_metering(state.client, target)
        state.metering_configured_station = target.station_name

    evse_nb = state.ocpp_evse_nb if state.ocpp_evse_nb is not None else DEFAULT_OCPP_EVSE_NB
    evse = find_evse(await state.client.get_evses(), target, evse_nb, state.evse_id)
    metadata = None if evse is None else evse_to_metadata(evse)

    if metadata is None:
        if state.base_report_requested_station != target.station_name:
            await request_base_report(state.client, target)
            state.base_report_requested_station = target.station_name

        results = await state.client.get_variables(target, rated_power_variables(evse_nb))
        metadata = variables_to_metadata(results)
        if metadata is None:
            logger.warning(f"Station {target.station_name} didn't report a usable rated power: {results}")
            return

    await upsert_ocpp_metadata(session, metadata)


async def request_base_report(client: TrocaClient, target: OcppTarget) -> None:
    """Asks the station to resend its device model report to Troca. Not every station supports this - a refusal
    is logged (rather than raised) as the rated power can still be requested directly from the station."""
    try:
        status = await client.get_base_report(target, BASE_REPORT_REQUEST_ID, BASE_REPORT_TYPE)
        logger.info(f"Requested a {BASE_REPORT_TYPE} report from station {target.station_name}: {status}")
    except TrocaApiError as exc:
        logger.warning(f"Station {target.station_name} didn't accept GetBaseReport: {exc}")


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


async def poll_prices(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Keeps the charging station's price schedule in sync with the CSIP-Aus dynamic prices (regardless of
    state.schedule_sync_mode - prices always go via the OCPP passthrough)."""

    target = await resolve_ocpp_target(state)
    if target is None:
        return
    state.pushed_prices = await sync_ocpp_prices(state.client, target, session, now, state.pushed_prices)


def poll_required(pollable: Pollable, now: datetime) -> bool:
    return now >= (pollable.last_poll + pollable.poll_rate)


async def run_polls(state: ClientState, min_wait: timedelta = timedelta(seconds=1)) -> datetime:
    """Runs every required poll of server, updating state as required. Will manage the creation of db sessions

    Returns the next "wakeup" time for the next set of polls

    Will not poll/post resources that have been polled recently. A failure polling one resource is logged and
    does not prevent the others in the same call from being polled - once they have all run, any failures are
    raised as a single PollError (carrying the next "wakeup" time)."""

    now = datetime.now(UTC)
    failures: list[tuple[str, Exception]] = []

    if poll_required(state.readings_poll, now):
        try:
            async with state.db.session_maker() as session:
                await poll_readings(state, session, now)
                await session.commit()
        except Exception as exc:
            logger.exception("Failed polling Troca readings.")
            failures.append(("Failed polling Troca readings", exc))
        finally:
            state.readings_poll.last_poll = now

    # Metadata before schedules - the schedule can't be built until the EVSE's rated power is known
    if poll_required(state.metadata_poll, now):
        try:
            async with state.db.session_maker() as session:
                await poll_metadata(state, session, now)
                await session.commit()
        except Exception as exc:
            logger.exception("Failed polling Troca session metadata.")
            failures.append(("Failed polling Troca session metadata", exc))
        finally:
            state.metadata_poll.last_poll = now

    if poll_required(state.schedule_poll, now):
        try:
            async with state.db.session_maker() as session:
                await poll_schedules(state, session, now)
                await session.commit()
        except Exception as exc:
            logger.exception("Failed polling/pushing the Troca charging schedule.")
            failures.append(("Failed polling/pushing the Troca charging schedule", exc))

        # Prices share the schedule's poll rate - but a failure in one doesn't prevent the other
        try:
            async with state.db.session_maker() as session:
                await poll_prices(state, session, now)
                await session.commit()
        except Exception as exc:
            logger.exception("Failed pushing the Troca price schedule.")
            failures.append(("Failed pushing the Troca price schedule", exc))
        finally:
            state.schedule_poll.last_poll = now

    # Figure out our next call to this function
    next_wake = max(state.next_poll(), datetime.now(UTC) + min_wait)
    if failures:
        raise PollError(failures, next_wake)
    return next_wake
