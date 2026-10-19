from datetime import UTC, datetime, timedelta

import pytest
from assertical.fake.generator import generate_class_instance

from cactus_juice.csipaus.dto import ActiveValues, ScheduledControlValues
from cactus_juice.troca import schedule
from cactus_juice.troca.schedule import calculate_ramp_schedule_values

NOW = datetime(2026, 9, 28, 4, 0, 0, tzinfo=UTC)


def raw_schedule(ramp_time_seconds: int | None) -> list[ScheduledControlValues]:
    v = generate_class_instance(ActiveValues, optional_is_none=True, ramp_time_seconds=ramp_time_seconds)
    return [ScheduledControlValues(active_from=NOW, active_to=None, values=v)]


@pytest.mark.parametrize(
    "first, ramp_step, expected_calls",
    [
        (raw_schedule(None), timedelta(seconds=3), [NOW]),
        (raw_schedule(0), timedelta(seconds=3), [NOW]),
        (raw_schedule(100), timedelta(0), [NOW]),  # Ramping disabled
        (raw_schedule(100), timedelta(seconds=3), [NOW, NOW - timedelta(seconds=100)]),
        ([], timedelta(seconds=3), [NOW]),
    ],
)
async def test_calculate_ramp_schedule_values(
    monkeypatch: pytest.MonkeyPatch,
    first: list[ScheduledControlValues],
    ramp_step: timedelta,
    expected_calls: list[datetime],
):
    calls: list[datetime] = []
    lookback = raw_schedule(None)

    async def calculate_schedule_values(session, now):
        calls.append(now)
        return first if len(calls) == 1 else lookback

    monkeypatch.setattr(schedule, "calculate_schedule_values", calculate_schedule_values)

    result = await calculate_ramp_schedule_values(None, NOW, ramp_step)  # ty: ignore[invalid-argument-type]

    assert calls == expected_calls
    assert result is (first if len(expected_calls) == 1 else lookback)
