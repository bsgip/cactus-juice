from datetime import datetime

from assertical.asserts.type import assert_list_type
from assertical.fixtures.postgres import generate_async_session

from cactus_juice.crud import fetch_controls_active_from
from cactus_juice.model import CSIPAusControl


async def test_fetch_controls_active_from(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        actual = await fetch_controls_active_from(session, datetime.min)
        assert_list_type(CSIPAusControl, actual, count=1)
