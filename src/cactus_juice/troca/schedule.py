from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.csipaus.controls import calculate_schedule_values
from cactus_juice.csipaus.dto import ScheduledControlValues


async def calculate_ramp_schedule_values(
    session: AsyncSession, now: datetime, ramp_step: timedelta
) -> list[ScheduledControlValues]:
    """As per calculate_schedule_values - but if ramping is enabled (ramp_step > 0) and the interval active at now
    has a ramp_time_seconds, the schedule starts that much earlier. calculate_schedule_values clamps everything to
    start at now, so otherwise a ramp that's still in progress would lose the interval it's ramping from (and jump
    straight to its target) - see mapping.ramp_segments.

    Only one level of history is recovered - if the preceding interval was itself still ramping when it ended, it's
    assumed to have reached its target."""

    raw_schedule = await calculate_schedule_values(session, now)
    if ramp_step <= timedelta(0) or not raw_schedule or not raw_schedule[0].values.ramp_time_seconds:
        return raw_schedule

    return await calculate_schedule_values(session, now - timedelta(seconds=raw_schedule[0].values.ramp_time_seconds))
