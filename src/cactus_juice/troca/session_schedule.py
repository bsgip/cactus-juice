"""Schedule sync via Troca's own session command API (POST /sessions/commands) - Troca converts the schedule into
an OCPP TxProfile for the active session's transaction.

This has been superseded by ocpp_schedule (see ScheduleSyncMode in poll.py) but is retained in case we need to
switch back. Its main limitation is that Troca only accepts fixed setpoints (not limits) - see
mapping.values_to_charge_watts for how CSIP-Aus limits are approximated."""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import fetch_ocpp_metadata
from cactus_juice.csipaus.controls import calculate_schedule_values
from cactus_juice.troca.client import TrocaClient
from cactus_juice.troca.mapping import parse_troca_timestamp, schedule_to_troca, schedules_match, to_troca_timestamp
from cactus_juice.troca.models import (
    ActivePowerSchedule,
    CommandStatus,
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


async def sync_session_schedule(
    client: TrocaClient, session: AsyncSession, now: datetime, evse_location: LocationId | None = None
) -> None:
    """Calculates the full upcoming schedule of controls and maps it to a Troca schedule for the active session.
    If Troca already has an equivalent schedule for that session - this is a no-op, otherwise the whole schedule
    is replaced with the newly calculated one.

    The full schedule (rather than just the current value) is pushed so that the station continues to follow it
    even if we lose contact with Troca - the final (open-ended) control is extended far into the future.

    evse_location is the EVSE the schedule is sent to - if None, it's discovered from the active session."""

    sessions = await client.get_sessions()
    session_statuses = await client.get_session_statuses()
    active_session = find_active_session(sessions, session_statuses)
    if active_session is None:
        logger.info("No active Troca session - no schedule to sync.")
        return

    if evse_location is None:
        evse_location = find_evse_location(active_session, await client.get_structure_pairs())
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
        await client.get_session_commands(),
        await client.get_session_command_locations(),
        evse_location,
        session_start,
    )
    status = None
    if pushed is not None:
        status = latest_command_status(await client.get_session_command_statuses(), pushed[0].command_id)

    if not requires_push(pushed, status, desired, now):
        return

    command_id = str(uuid4())
    logger.info(
        f"Pushing {len(desired.periods)} period Troca schedule {command_id} for session {active_session.session_id}"
        f" (current setpoint {desired.periods[0].global_}) to EVSE {evse_location.location_id}."
    )
    await client.set_active_power_schedule(
        command_id=command_id, timestamp=to_troca_timestamp(now), schedule=desired, evse_location=evse_location
    )
