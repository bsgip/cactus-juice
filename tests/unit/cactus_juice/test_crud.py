from datetime import UTC, datetime, timedelta

import pytest
from assertical.asserts.generator import assert_class_instance_equality
from assertical.asserts.time import assert_nowish
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import clone_class_instance, generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from sqlalchemy import func, select

from cactus_juice.crud import fetch_controls_active_from, upsert_controls
from cactus_juice.model import CSIPAusControl

DEFAULT_CREATED_TIME = datetime(2000, 1, 1, tzinfo=UTC)


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


async def test_upsert_controls_no_commit(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        count_before = (await session.execute(select(func.count()).select_from(CSIPAusControl))).scalar_one()

    # No explicit commit/rollback
    async with generate_async_session(pg_base_config) as session:
        await upsert_controls(session, [generate_class_instance(CSIPAusControl, seed=101)])

    async with generate_async_session(pg_base_config) as session:
        assert count_before == (await session.execute(select(func.count()).select_from(CSIPAusControl))).scalar_one()

    # Explicit rollback
    async with generate_async_session(pg_base_config) as session:
        await upsert_controls(session, [generate_class_instance(CSIPAusControl, seed=101)])
        await session.rollback()

    async with generate_async_session(pg_base_config) as session:
        assert count_before == (await session.execute(select(func.count()).select_from(CSIPAusControl))).scalar_one()

    # Will stick on commit
    async with generate_async_session(pg_base_config) as session:
        await upsert_controls(session, [generate_class_instance(CSIPAusControl, seed=101)])
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        assert (count_before + 1) == (
            await session.execute(select(func.count()).select_from(CSIPAusControl))
        ).scalar_one()


async def test_upsert_controls(pg_base_config):
    """Covers the three docstring behaviours in one pass:
    - a brand new mrid is inserted verbatim
    - on an mrid conflict ONLY cancelled_at/superseded_at may change, every other column is left alone
    - cancelled_at/superseded_at only move from NULL -> value, an already-set value is never overwritten
    """
    # Existing cancelled/superseded times seeded by base_config.sql for the controls we exercise below
    BASE_6666_CANCELLED_AT = datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)
    BASE_7777_SUPERSEDED_AT = datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)

    # Fresh values fed in via the upsert
    NEW_CANCELLED_AT = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    NEW_SUPERSEDED_AT = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)

    new_control = generate_class_instance(
        CSIPAusControl, seed=101, mrid="brand-new-mrid", cancelled_at=NEW_CANCELLED_AT, superseded_at=None
    )

    # mrid 1111 (id 1): both cancelled_at & superseded_at currently NULL -> both should take the new values,
    # while the (deliberately different) primacy/duration/import_limit_watts must be ignored.
    conflict_open = generate_class_instance(
        CSIPAusControl,
        seed=202,
        mrid="1111",
        cancelled_at=NEW_CANCELLED_AT,
        superseded_at=NEW_SUPERSEDED_AT,
    )

    # mrid 6666 (id 6): cancelled_at already set -> keep it; superseded_at NULL -> take the new value
    conflict_cancelled = generate_class_instance(
        CSIPAusControl,
        seed=303,
        mrid="6666",
        cancelled_at=NEW_CANCELLED_AT,
        superseded_at=NEW_SUPERSEDED_AT,
    )

    # mrid 7777 (id 7): superseded_at already set -> keep it; cancelled_at NULL -> take the new value
    conflict_superseded = generate_class_instance(
        CSIPAusControl,
        seed=404,
        mrid="7777",
        cancelled_at=NEW_CANCELLED_AT,
        superseded_at=NEW_SUPERSEDED_AT,
    )

    # mrid 2222 (id 2): both NULL in and out -> nothing to do, must not raise and must not touch other columns
    conflict_noop = generate_class_instance(
        CSIPAusControl,
        seed=505,
        mrid="2222",
        cancelled_at=None,
        superseded_at=None,
    )

    # Act
    async with generate_async_session(pg_base_config) as session:
        await upsert_controls(
            session,
            [
                clone_class_instance(e)  # Insert clones - allows us to keep the original instances out of the session
                for e in [new_control, conflict_open, conflict_cancelled, conflict_superseded, conflict_noop]
            ],
        )
        await session.commit()

    # Assert
    async with generate_async_session(pg_base_config) as session:
        rows = (await session.execute(select(CSIPAusControl))).scalars().all()
        by_mrid = {r.mrid: r for r in rows}

    # Nothing deleted, exactly one row added
    assert len(rows) == 8

    # New row inserted verbatim
    inserted = by_mrid["brand-new-mrid"]
    assert_class_instance_equality(
        CSIPAusControl, new_control, inserted, ignored_properties={"csipaus_control_id", "created_at", "finished_at"}
    )
    assert_nowish(inserted.created_at)
    assert inserted.finished_at == inserted.started_at + timedelta(seconds=inserted.duration_seconds)

    # mrid 1111: both timestamps filled from NULL, everything else untouched
    open_row = by_mrid["1111"]
    assert open_row.csipaus_control_id == 1
    assert open_row.created_at == DEFAULT_CREATED_TIME
    assert open_row.cancelled_at == conflict_open.cancelled_at
    assert open_row.superseded_at == conflict_open.superseded_at
    assert open_row.import_limit_watts == 101, "Unchanged"
    assert open_row.export_limit_watts == 102, "Unchanged"

    # mrid 6666: existing cancelled_at kept, superseded_at filled
    cancelled_row = by_mrid["6666"]
    assert cancelled_row.cancelled_at == BASE_6666_CANCELLED_AT
    assert cancelled_row.superseded_at == NEW_SUPERSEDED_AT
    assert cancelled_row.import_limit_watts == 601, "Unchanged"
    assert cancelled_row.export_limit_watts == 602, "Unchanged"

    # mrid 7777: cancelled_at filled, existing superseded_at kept
    superseded_row = by_mrid["7777"]
    assert superseded_row.cancelled_at == NEW_CANCELLED_AT
    assert superseded_row.superseded_at == BASE_7777_SUPERSEDED_AT
    assert superseded_row.import_limit_watts == 701, "Unchanged"
    assert superseded_row.export_limit_watts == 702, "Unchanged"

    # mrid 2222: untouched
    noop_row = by_mrid["2222"]
    assert noop_row.cancelled_at is None
    assert noop_row.superseded_at is None
    assert noop_row.primacy == 1
    assert noop_row.import_limit_watts == 201, "Unchanged"
    assert noop_row.export_limit_watts == 202, "Unchanged"
