"""Price sync by sending the CSIP-Aus dynamic prices to the charging station as an OCPP 2.1 absolutePriceSchedule
(via Troca's OCPP passthrough) - for the station to pass on to an ISO 15118-20 EV.

The prices are carried on their own TxDefaultProfile (rather than ocpp_schedule's ChargingStationMaxProfile) as
price schedules are only meaningful on the profiles governing a transaction. That profile's charging limit is set
high enough to never constrain anything - the station takes the minimum of all applicable limits, so it's
ocpp_schedule's profile (and whatever Troca applies) that actually limits the EVSE.

The Trialog simulator accepts any profile (even malformed ones) so there's no meaningful feedback from the station -
any response that isn't an API error is treated as having been sent. Like ocpp_schedule, what was last pushed is
remembered and periodically re-pushed."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import fetch_dynamic_prices_active_from
from cactus_juice.troca.client import TrocaClient
from cactus_juice.troca.mapping import PriceSegment, dynamic_prices_to_segments, prices_to_ocpp_profile
from cactus_juice.troca.models import OcppChargingProfilePurpose, OcppTarget

logger = logging.getLogger(__name__)

# Our price profile's identity on the station - distinct from ocpp_schedule.PROFILE_ID (and Troca's own ids)
PRICE_PROFILE_ID = 9002
PRICE_PROFILE_STACK_LEVEL = 0
PRICE_PROFILE_PURPOSE = OcppChargingProfilePurpose.TX_DEFAULT_PROFILE
PRICE_PROFILE_EVSE_ID = 0  # Every EVSE on the station

# The charging limit carried by the price profile - deliberately far above any real EVSE so it never constrains.
PRICE_PROFILE_LIMIT_WATTS = 1_000_000.0

# An unchanged price schedule is re-pushed this often - covers the station losing it (eg a reboot).
REFRESH_INTERVAL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class PushedPrices:
    """Records the last price segments pushed to the station (empty meaning our price profile was cleared)."""

    segments: list[PriceSegment]
    pushed_at: datetime


def requires_push(last: PushedPrices | None, desired: list[PriceSegment], now: datetime) -> bool:
    if last is None or last.segments != desired:
        return True
    return now - last.pushed_at >= REFRESH_INTERVAL


async def sync_ocpp_prices(
    client: TrocaClient, target: OcppTarget, session: AsyncSession, now: datetime, last: PushedPrices | None
) -> PushedPrices | None:
    """Pushes the current/upcoming dynamic prices to the station as our price profile (or clears that profile if
    there are no prices) - unless that's already been done recently (see requires_push).

    Returns the PushedPrices that should be passed as last on the next call."""

    desired = dynamic_prices_to_segments(await fetch_dynamic_prices_active_from(session, now), now)
    if not requires_push(last, desired, now):
        return last

    # Unique per push (the EV can use it to recognise an updated schedule) - fits OCPP's int32 until 2038
    profile = prices_to_ocpp_profile(
        desired,
        PRICE_PROFILE_ID,
        PRICE_PROFILE_STACK_LEVEL,
        PRICE_PROFILE_PURPOSE,
        PRICE_PROFILE_LIMIT_WATTS,
        price_schedule_id=int(now.timestamp()),
    )
    if profile is None:
        status = await client.clear_charging_profile(target, PRICE_PROFILE_ID)
        logger.info(
            f"No dynamic prices - cleared price profile {PRICE_PROFILE_ID} on {target.station_name} '{status}'."
        )
    else:
        status = await client.set_charging_profile(target, PRICE_PROFILE_EVSE_ID, profile)
        logger.info(
            f"Pushed {len(desired)} period price schedule (from {desired[0].start}: {desired[0].price_kwh} $/kWh) as"
            f" profile {PRICE_PROFILE_ID} to {target.station_name} - status '{status}'."
        )

    return PushedPrices(segments=desired, pushed_at=now)
