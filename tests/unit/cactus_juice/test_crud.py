from datetime import UTC, datetime

import pytest
from assertical.asserts.type import assert_list_type
from assertical.fixtures.postgres import generate_async_session

from cactus_juice.crud import fetch_controls_active_from
from cactus_juice.model import CSIPAusControl


@pytest.mark.parametrize(
    "epoch, start, limit, expected_ids",
    [
        (datetime.min, 0, 99, [1, 4, 5, 6, 7, 2, 3]),
        (datetime(2026, 1, 1, tzinfo=UTC), 0, 99, [1, 4, 5, 6, 7, 2, 3]),
        (datetime.min, 1, 2, [4, 5]),  # Paging
        (datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC), 0, 99, [4, 5, 7, 2, 3]),
        (datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC), 0, 99, [4, 5, 3]),
        (datetime(2026, 1, 1, 0, 15, 0, tzinfo=UTC), 0, 99, []),
    ],
)
async def test_fetch_controls_active_from(
    pg_base_config, epoch: datetime, start: int, limit: int, expected_ids: list[int]
):
    async with generate_async_session(pg_base_config) as session:
        actual = await fetch_controls_active_from(session, epoch=epoch, start=start, limit=limit)
        assert [e.csipaus_control_id for e in actual] == expected_ids
        assert_list_type(CSIPAusControl, actual, count=len(expected_ids))
