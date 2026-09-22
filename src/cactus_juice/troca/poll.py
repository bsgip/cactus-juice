import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import add_ocpp_readings, upsert_ocpp_metadata
from cactus_juice.csipaus.controls import calculate_schedule_values
from cactus_juice.db import DatabaseConnection
from cactus_juice.troca.client import TrocaClient
from cactus_juice.troca.mapping import (
    current_values_to_charging_profile,
    reading_to_db,
    session_constraints_to_metadata,
)
from cactus_juice.troca.models import ChargingProfile, CommandStatus, SessionCommand, SessionCommandType, SessionStatus

logger = logging.getLogger(__name__)

# A fixed identity for the profile we push - resubmitting a ChargingProfile with the same id/stack_level is
# expected (per OCPP convention) to replace whatever was previously in effect rather than stacking alongside it.
CHARGING_PROFILE_ID = 1
CHARGING_PROFILE_STACK_LEVEL = 0

# Session statuses that mean "not currently drawing/able to draw power" - excluded when looking for the session
# whose constraints should back OCPPMetadata.
INACTIVE_SESSION_STATUSES = (SessionStatus.COMPLETED, SessionStatus.INVALID)


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
            readings_poll=Pollable(datetime.min, timedelta(seconds=readings_poll_rate_seconds)),
            schedule_poll=Pollable(datetime.min, timedelta(seconds=schedule_poll_rate_seconds)),
            metadata_poll=Pollable(datetime.min, timedelta(seconds=metadata_poll_rate_seconds)),
            ramp_step=timedelta(seconds=ramp_step_seconds),
        )


async def poll_readings(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Takes a snapshot of OCPP readings via troca and pushes them into the DB"""
    readings = await state.client.get_metering_data()

    await add_ocpp_readings(session, [reading_to_db(r) for r in readings])
    state.readings_poll.last_poll = now


def _latest_charging_profile_command(commands: Iterable[SessionCommand]) -> SessionCommand | None:
    """Finds the most recently received set/clear_charging_profile command out of a (potentially unordered,
    per SessionCommand's docstring) batch. Used to check what Troca last actually applied before deciding
    whether a new push is needed."""

    profile_command_types = (SessionCommandType.SET_CHARGING_PROFILE, SessionCommandType.CLEAR_CHARGING_PROFILE)
    relevant = [c for c in commands if c.type in profile_command_types]
    if not relevant:
        return None
    return max(relevant, key=lambda c: c.received_at or c.created_at or "")


def _profile_already_applied(latest: SessionCommand | None, desired: ChargingProfile | None) -> bool:
    """Checks whether the last accepted set/clear_charging_profile command already matches what we'd otherwise
    push now - used to avoid needlessly resubmitting an identical profile every poll."""

    if latest is None or latest.status != CommandStatus.ACCEPTED:
        return False

    if desired is None:
        return latest.type == SessionCommandType.CLEAR_CHARGING_PROFILE

    if latest.type != SessionCommandType.SET_CHARGING_PROFILE or latest.input_parameters is None:
        return False

    try:
        applied = ChargingProfile.from_dict(latest.input_parameters)
    except Exception:
        # SessionCommand's response shape is still under discovery (see its docstring) - if we can't parse it
        # back into a ChargingProfile, just assume it doesn't match and let the caller re-push ours.
        logger.warning("Could not parse the last SessionCommand's input_parameters as a ChargingProfile.")
        return False

    return applied.charging_schedule.charging_schedule_period == desired.charging_schedule.charging_schedule_period


async def poll_schedules(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Calculates the upcoming schedule of controls and maps it to a set of charging profiles. If those charging
    profiles match the current troca profiles - this is a no-op, otherwise the existing set will be replaced/updated
    with the newly calculated schedule"""
    raw_schedule = await calculate_schedule_values(session, now)
    if not raw_schedule:
        logger.info("No scheduled control values available - nothing to enforce via Troca.")
        state.schedule_poll.last_poll = now
        return

    # Only the immediately-current interval is mapped - see current_values_to_charging_profile's docstring for
    # why the full future schedule isn't pre-loaded as a multi-period profile.
    desired_profile = current_values_to_charging_profile(
        raw_schedule[0].values, CHARGING_PROFILE_ID, CHARGING_PROFILE_STACK_LEVEL, now
    )

    commands = await state.client.get_session_commands()
    latest_command = _latest_charging_profile_command(commands)

    if _profile_already_applied(latest_command, desired_profile):
        state.schedule_poll.last_poll = now
        return

    command_id = str(uuid4())

    # NOTE: pool_id/station_id/evse_id are deliberately left unset below. ClientState/TrocaConfig only track a
    # single connector_id (from GET /config/connectors) and there's no discovered mapping from that id to the
    # pool/station/evse triplet set_charging_profile/clear_charging_profile actually take. This assumes a
    # single-connector deployment where the server infers the sole target - revisit once that mapping (or
    # multi-connector support) is clarified.
    if desired_profile is None:
        logger.info("Clearing Troca charging profile - no CSIP-Aus import limit currently active.")
        await state.client.clear_charging_profile(command_id=command_id, charging_profile_id=CHARGING_PROFILE_ID)
    else:
        limit_watts = desired_profile.charging_schedule.charging_schedule_period[0].limit
        logger.info(f"Pushing Troca charging profile with a {limit_watts}W import limit.")
        await state.client.set_charging_profile(command_id=command_id, profile=desired_profile)

    state.schedule_poll.last_poll = now


async def poll_metadata(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Fetches metadata from the current charging session and updates the DB with the latest values"""
    sessions = await state.client.get_sessions()
    active_sessions = [s for s in sessions if s.status not in INACTIVE_SESSION_STATUSES]

    if not active_sessions:
        logger.info("No active Troca session - nothing to record for OCPP metadata.")
        state.metadata_poll.last_poll = now
        return

    if len(active_sessions) > 1:
        # Troca's get_sessions() has no way to filter by connector, and SessionData doesn't expose an id we can
        # match against ClientState.connector_id (see poll_schedules' pool/station/evse note for the same gap).
        # Best effort: pick whichever active session was updated most recently.
        logger.warning(f"{len(active_sessions)} active Troca sessions found - using the most recently updated one.")

    target_session = max(active_sessions, key=lambda s: s.last_updated or s.created_at or "")

    metadata = session_constraints_to_metadata(target_session)
    if metadata is None:
        logger.info(f"Session {target_session.session_id} has no usable charge/discharge constraints to record.")
        state.metadata_poll.last_poll = now
        return

    await upsert_ocpp_metadata(session, metadata)
    state.metadata_poll.last_poll = now


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

    if poll_required(state.schedule_poll, now):
        try:
            async with state.db.session_maker() as session:
                await poll_schedules(state, session, now)
                await session.commit()
        except Exception:
            logger.exception("Failed polling/pushing the Troca charging schedule.")
        finally:
            state.schedule_poll.last_poll = now

    if poll_required(state.metadata_poll, now):
        try:
            async with state.db.session_maker() as session:
                await poll_metadata(state, session, now)
                await session.commit()
        except Exception:
            logger.exception("Failed polling Troca session metadata.")
        finally:
            state.metadata_poll.last_poll = now

    # Figure out our next call to this function
    return max(state.next_poll(), datetime.now(UTC) + min_wait)
