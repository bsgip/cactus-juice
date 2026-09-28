"""Schedule sync by sending OCPP charging profiles directly to the charging station (via Troca's OCPP passthrough).

The CSIP-Aus schedule is pushed as a ChargingStationMaxProfile - a station wide cap that the station combines
(taking the minimum) with whatever TxProfile/TxDefaultProfile Troca's own scheduler applies to a session. So it
enforces our limits without competing with Troca, and doesn't depend on there being an active session/transaction.

The station's applied profiles can't be read back - GetChargingProfiles results are sent to Troca (not us) and the
Trialog simulator's GetCompositeSchedule always reports 0W - so instead we remember what we last pushed and
periodically re-push it (SetChargingProfile with the same id is idempotent, replacing the existing profile)."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import fetch_ocpp_metadata
from cactus_juice.csipaus.controls import calculate_schedule_values
from cactus_juice.troca.client import TrocaClient
from cactus_juice.troca.mapping import ocpp_profiles_match, schedule_to_ocpp_profile
from cactus_juice.troca.models import OcppChargingProfile, OcppChargingProfilePurpose, OcppTarget

logger = logging.getLogger(__name__)

# Our profile's identity on the station - chosen to stay well clear of the ids Troca uses for its own profiles
# (observed: 1). Resubmitting a profile with this id replaces our previous one.
PROFILE_ID = 9001
PROFILE_STACK_LEVEL = 0  # The station supports stack levels 0-3 (SmartChargingCtrlr.ProfileStackLevel)
PROFILE_PURPOSE = OcppChargingProfilePurpose.CHARGING_STATION_MAX_PROFILE
PROFILE_EVSE_ID = 0  # ChargingStationMaxProfile must target evseId 0 (the whole station)

# An accepted (and unchanged) profile is re-pushed this often - covers the station losing it (eg a reboot) without
# us otherwise having any way of knowing.
REFRESH_INTERVAL = timedelta(minutes=15)

# A rejected profile won't be retried (unless it changes) until this long after the rejection - retrying on every
# schedule poll would just spam the station with the same bad request.
FAILED_RETRY_INTERVAL = timedelta(minutes=5)

ACCEPTED = "Accepted"

# ClearChargingProfile responds Unknown when there was nothing to clear - which is still the outcome we want.
CLEAR_OK_STATUSES = (ACCEPTED, "Unknown")


@dataclass(frozen=True, slots=True)
class PushedProfile:
    """Records the last profile pushed to the station (None meaning our profile was cleared)."""

    profile: OcppChargingProfile | None
    pushed_at: datetime
    accepted: bool


def _profiles_equivalent(a: OcppChargingProfile | None, b: OcppChargingProfile | None, now: datetime) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return ocpp_profiles_match(a, b, now)


def requires_push(last: PushedProfile | None, desired: OcppChargingProfile | None, now: datetime) -> bool:
    """Decides whether desired needs to be pushed (or cleared if None), given what was last pushed (None meaning
    nothing has been pushed since startup)."""

    if last is None or not _profiles_equivalent(last.profile, desired, now):
        return True

    age = now - last.pushed_at
    if not last.accepted:
        return age >= FAILED_RETRY_INTERVAL
    return age >= REFRESH_INTERVAL


async def sync_ocpp_schedule(
    client: TrocaClient, target: OcppTarget, session: AsyncSession, now: datetime, last: PushedProfile | None
) -> PushedProfile | None:
    """Calculates the full upcoming schedule of controls and pushes it to the station as our charging profile (or
    clears our profile if there's no schedule) - unless that's already been done recently (see requires_push).

    Returns the PushedProfile that should be passed as last on the next call."""

    metadata = await fetch_ocpp_metadata(session)
    if metadata is None or metadata.max_power_watts is None:
        logger.warning("The EVSE's rated power isn't known yet (see poll_metadata) - can't build a profile.")
        return last

    desired = schedule_to_ocpp_profile(
        await calculate_schedule_values(session, now),
        metadata.max_power_watts,
        now,
        PROFILE_ID,
        PROFILE_STACK_LEVEL,
        PROFILE_PURPOSE,
    )
    if not requires_push(last, desired, now):
        return last

    if desired is None:
        status = await client.clear_charging_profile(target, PROFILE_ID)
        accepted = status in CLEAR_OK_STATUSES
        logger.info(f"Cleared charging profile {PROFILE_ID} on {target.station_name} - status '{status}'.")
    else:
        status = await client.set_charging_profile(target, PROFILE_EVSE_ID, desired)
        accepted = status == ACCEPTED
        periods = desired.charging_schedule[0].charging_schedule_period
        logger.info(
            f"Pushed {len(periods)} period charging profile {PROFILE_ID} to {target.station_name} (current limits"
            f" {periods[0].limit}W / {periods[0].discharge_limit}W) - status '{status}'."
        )

    if not accepted:
        logger.error(f"Station {target.station_name} rejected charging profile {PROFILE_ID} with status '{status}'.")

    return PushedProfile(profile=desired, pushed_at=now, accepted=accepted)
