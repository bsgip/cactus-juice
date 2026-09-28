import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import add_ocpp_readings, fetch_ocpp_metadata, upsert_ocpp_metadata
from cactus_juice.csipaus.controls import calculate_schedule_values
from cactus_juice.db import DatabaseConnection
from cactus_juice.troca.client import TrocaClient
from cactus_juice.troca.mapping import (
    RATED_POWER_VARIABLES,
    parse_troca_timestamp,
    reading_to_db,
    schedule_to_troca,
    schedules_match,
    to_troca_timestamp,
    variables_to_metadata,
)
from cactus_juice.troca.models import (
    ActivePowerSchedule,
    CommandStatus,
    ConnectorType,
    LocationId,
    SessionCommand,
    SessionCommandLocation,
    SessionCommandStatusEntry,
    SessionCommandType,
    SessionData,
    SessionStatus,
    SessionStatusEntry,
    StructureLevel,
    StructurePair,
    parse_set_charging_profile_parameters,
)

logger = logging.getLogger(__name__)

# Session statuses that mean the session is over - its schedule no longer needs to be kept in sync.
INACTIVE_SESSION_STATUSES = (SessionStatus.COMPLETED, SessionStatus.INVALID, SessionStatus.CLEARED)

# If Troca reports a pushed schedule as error/refused, an identical schedule won't be re-pushed until this long
# after that failure - retrying on every schedule poll would just spam the station with the same bad request.
FAILED_SCHEDULE_RETRY_INTERVAL = timedelta(minutes=5)

# Pulls the OCPP version off the end of an OCPP connector's name, eg "qocppConnector2.1" -> "2.1"
OCPP_VERSION_PATTERN = re.compile(r"(\d+(?:\.\d+)+)$")

POLL_EPOCH = datetime.min.replace(tzinfo=UTC)


@dataclass(slots=True)
class Pollable:
    last_poll: datetime
    poll_rate: timedelta


@dataclass(slots=True)
class ClientState:
    """Encapsulates all the state the client needs to know about the Troca server and its polling behaviour."""

    client: TrocaClient
    db: DatabaseConnection

    connector_id: str  # The ID of the Troca connector that will be used for comms
    readings_poll: Pollable  # Poll status of the readings
    schedule_poll: Pollable  # Poll status of the charge schedule
    metadata_poll: Pollable  # Poll status of the connected EVSE metadata
    ramp_step: timedelta

    # Troca returns its whole metering history on every poll - only readings after this are new
    last_reading_at: datetime

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
        connector_id: str,
        readings_poll_rate_seconds: int,
        schedule_poll_rate_seconds: int,
        metadata_poll_rate_seconds: int,
        ramp_step_seconds: int,
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
        )


async def poll_readings(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Takes a snapshot of any new OCPP readings via troca and pushes them into the DB"""
    readings = [reading_to_db(r) for r in await state.client.get_metering_data()]
    new_readings = [r for r in readings if r.reading_start > state.last_reading_at]

    await add_ocpp_readings(session, new_readings)
    if new_readings:
        state.last_reading_at = max(r.reading_start for r in new_readings)
    state.readings_poll.last_poll = now


def find_active_session(sessions: Iterable[SessionData], statuses: Iterable[SessionStatusEntry]) -> SessionData | None:
    """Finds the currently active session - ie the most recently arrived session whose latest status isn't one of
    INACTIVE_SESSION_STATUSES. A session with no status history at all is assumed to be active."""

    latest_status: dict[str, SessionStatusEntry] = {}
    for s in statuses:
        existing = latest_status.get(s.session_id)
        if existing is None or parse_troca_timestamp(s.timestamp) >= parse_troca_timestamp(existing.timestamp):
            latest_status[s.session_id] = s

    active = [
        s
        for s in sessions
        if s.session_id not in latest_status or latest_status[s.session_id].status not in INACTIVE_SESSION_STATUSES
    ]
    if len(active) > 1:
        # Troca has no way to filter sessions by connector - best effort is to take the most recent arrival
        logger.warning(f"{len(active)} active Troca sessions found - using the most recent arrival.")

    return max(active, key=lambda s: s.arrival_date or "", default=None)


def find_evse_location(session_data: SessionData, pairs: Iterable[StructurePair]) -> LocationId | None:
    """Session commands must target an EVSE, but a session's location is (normally) its EVSE connector - this
    walks the structure hierarchy up to the owning EVSE."""

    location = session_data.location
    if location is None:
        return None
    if location.level == StructureLevel.EVSE:
        return location

    for p in pairs:
        if p.low_level_structure == location and p.high_level_structure.level == StructureLevel.EVSE:
            return p.high_level_structure
    return None


def find_latest_pushed_schedule(
    commands: Iterable[SessionCommand],
    locations: Iterable[SessionCommandLocation],
    evse_location: LocationId,
    since: datetime,
) -> tuple[SessionCommand, ActivePowerSchedule] | None:
    """Finds the most recently pushed client (ie not Troca-internal) schedule for evse_location, issued at/after
    since (so a schedule from a previous session isn't mistaken for the current one's)."""

    command_ids_at_evse = {loc.command_id for loc in locations if loc.location_id == evse_location}

    candidates: list[tuple[datetime, SessionCommand, ActivePowerSchedule]] = []
    for c in commands:
        if c.type != SessionCommandType.SET_CHARGING_PROFILE or not c.is_client_command:
            continue
        if c.command_id not in command_ids_at_evse or c.input_parameters is None:
            continue

        # createdAt is unreliable (observed 2 hours behind) - the timestamp we sent in inputParameters is used
        issued_at = parse_troca_timestamp(c.input_parameters.timestamp)
        schedule = parse_set_charging_profile_parameters(c.input_parameters.parameters)
        if issued_at < since or schedule is None:
            continue
        candidates.append((issued_at, c, schedule))

    if not candidates:
        return None

    _, command, schedule = max(candidates, key=lambda c: c[0])
    return command, schedule


def latest_command_status(
    statuses: Iterable[SessionCommandStatusEntry], command_id: str
) -> SessionCommandStatusEntry | None:
    matching = [s for s in statuses if s.command_id == command_id]
    return max(matching, key=lambda s: parse_troca_timestamp(s.timestamp), default=None)


def requires_push(
    pushed: tuple[SessionCommand, ActivePowerSchedule] | None,
    status: SessionCommandStatusEntry | None,
    desired: ActivePowerSchedule,
    now: datetime,
) -> bool:
    """Decides whether desired needs to be pushed, given the last schedule pushed for this session (and its
    status - None meaning Troca hasn't reported one yet)."""

    if pushed is None:
        return True

    command, applied = pushed
    if not schedules_match(applied, desired, now):
        return True

    if status is None or status.status in (CommandStatus.ACCEPTED, CommandStatus.PENDING):
        return False

    failed_at = parse_troca_timestamp(status.timestamp)
    if now - failed_at < FAILED_SCHEDULE_RETRY_INTERVAL:
        logger.warning(f"Troca reported schedule command {command.command_id} as '{status.status}' - not retrying yet.")
        return False
    return True


async def poll_schedules(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Calculates the full upcoming schedule of controls and maps it to a Troca schedule for the active session.
    If Troca already has an equivalent schedule for that session - this is a no-op, otherwise the whole schedule
    is replaced with the newly calculated one.

    The full schedule (rather than just the current value) is pushed so that the station continues to follow it
    even if we lose contact with Troca - the final (open-ended) control is extended far into the future."""

    sessions = await state.client.get_sessions()
    session_statuses = await state.client.get_session_statuses()
    active_session = find_active_session(sessions, session_statuses)
    if active_session is None:
        logger.info("No active Troca session - no schedule to sync.")
        return

    evse_location = find_evse_location(active_session, await state.client.get_structure_pairs())
    if evse_location is None:
        logger.error(f"Unable to find the EVSE for session {active_session.session_id} - can't sync its schedule.")
        return

    metadata = await fetch_ocpp_metadata(session)
    if metadata is None or metadata.max_power_watts is None:
        logger.warning("The EVSE's rated power isn't known yet (see poll_metadata) - can't build a schedule.")
        return

    desired = schedule_to_troca(await calculate_schedule_values(session, now), metadata.max_power_watts, now)
    if desired is None:
        logger.info("No scheduled control values available - nothing to enforce via Troca.")
        return

    session_start = parse_troca_timestamp(active_session.arrival_date) if active_session.arrival_date else now
    pushed = find_latest_pushed_schedule(
        await state.client.get_session_commands(),
        await state.client.get_session_command_locations(),
        evse_location,
        session_start,
    )
    status = None
    if pushed is not None:
        status = latest_command_status(await state.client.get_session_command_statuses(), pushed[0].command_id)

    if not requires_push(pushed, status, desired, now):
        return

    command_id = str(uuid4())
    logger.info(
        f"Pushing {len(desired.periods)} period Troca schedule {command_id} for session {active_session.session_id}"
        f" (current setpoint {desired.periods[0].global_}) to EVSE {evse_location.location_id}."
    )
    await state.client.set_active_power_schedule(
        command_id=command_id, timestamp=to_troca_timestamp(now), schedule=desired, evse_location=evse_location
    )


async def poll_metadata(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Asks the charging station directly (via Troca's OCPP passthrough) for its rated power and updates the DB
    with the latest values."""

    connector = next((c for c in await state.client.get_connectors() if c.connector_id == state.connector_id), None)
    if connector is None or connector.connector_type != ConnectorType.Q_OCPP:
        logger.error(f"Troca connector {state.connector_id} isn't a known OCPP connector - can't fetch metadata.")
        return

    version_match = OCPP_VERSION_PATTERN.search(connector.name)
    if version_match is None:
        logger.error(f"Unable to determine the OCPP version from connector name '{connector.name}'.")
        return

    stations = await state.client.get_stations()
    if not stations:
        logger.info("No charging stations registered with Troca - nothing to record for OCPP metadata.")
        return
    if len(stations) > 1:
        logger.warning(f"{len(stations)} Troca charging stations found - only {stations[0].name} will be recorded.")

    results = await state.client.get_variables(
        connector_name=connector.name,
        ocpp_version=version_match.group(1),
        station_name=stations[0].name,
        requests=RATED_POWER_VARIABLES,
    )
    metadata = variables_to_metadata(results)
    if metadata is None:
        logger.warning(f"Station {stations[0].name} didn't report a usable rated power: {results}")
        return

    await upsert_ocpp_metadata(session, metadata)


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
