from datetime import UTC, datetime, timedelta

import pytest
from assertical.asserts.generator import assert_class_instance_equality
from assertical.asserts.time import assert_nowish
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import clone_class_instance, generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from cactus_juice.crud import (
    DEFAULT_MAX_DATE,
    fetch_active_default,
    fetch_controls_active_from,
    fetch_unsent_control_responses,
    update_active_default,
    upsert_control_responses,
    upsert_controls,
)
from cactus_juice.csipaus.dto import DefaultValues
from cactus_juice.model import CSIPAusControl, CSIPAusControlResponse, CSIPAusDefault

DEFAULT_VALUE_COLUMNS = (
    "ramp_percent_max_second_hundredths",
    "connect",
    "energize",
    "import_limit_watts",
    "export_limit_watts",
    "load_limit_watts",
    "generation_limit_watts",
    "storage_target_watts",
)


def _as_utc(value: datetime) -> datetime:
    """asyncpg hands year-9999 timestamptz values back without tzinfo - normalise for instant comparison."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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


async def test_upsert_controls_empty(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        count_before = (await session.execute(select(func.count()).select_from(CSIPAusControl))).scalar_one()
        await upsert_controls(session, [])
        assert count_before == (await session.execute(select(func.count()).select_from(CSIPAusControl))).scalar_one()
        await session.commit()


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
    assert open_row.created_at == DEFAULT_CREATED_TIME, "Unchanged"
    assert open_row.cancelled_at == conflict_open.cancelled_at
    assert open_row.superseded_at == conflict_open.superseded_at
    assert open_row.import_limit_watts == 101, "Unchanged"
    assert open_row.export_limit_watts == 102, "Unchanged"

    # mrid 6666: existing cancelled_at kept, superseded_at filled
    cancelled_row = by_mrid["6666"]
    assert cancelled_row.cancelled_at == BASE_6666_CANCELLED_AT
    assert cancelled_row.superseded_at == NEW_SUPERSEDED_AT
    assert cancelled_row.created_at == DEFAULT_CREATED_TIME, "Unchanged"
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


async def test_upsert_control_responses_empty(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        count_before = (await session.execute(select(func.count()).select_from(CSIPAusControlResponse))).scalar_one()
        await upsert_control_responses(session, [])
        assert (
            count_before
            == (await session.execute(select(func.count()).select_from(CSIPAusControlResponse))).scalar_one()
        )
        await session.commit()


async def test_upsert_control_responses_no_commit(pg_base_config):
    """upsert_control_responses must never commit/rollback on its own - the caller owns the transaction."""

    async with generate_async_session(pg_base_config) as session:
        count_before = (await session.execute(select(func.count()).select_from(CSIPAusControlResponse))).scalar_one()

    # No explicit commit/rollback
    async with generate_async_session(pg_base_config) as session:
        await upsert_control_responses(
            session,
            [
                generate_class_instance(
                    CSIPAusControlResponse,
                    seed=101,
                    csipaus_control_id=2,
                    end_device_mrid="brand-new-device",
                    sent_at=None,
                )
            ],
        )

    async with generate_async_session(pg_base_config) as session:
        assert (
            count_before
            == (await session.execute(select(func.count()).select_from(CSIPAusControlResponse))).scalar_one()
        )

    # Explicit rollback
    async with generate_async_session(pg_base_config) as session:
        await upsert_control_responses(
            session,
            [
                generate_class_instance(
                    CSIPAusControlResponse,
                    seed=101,
                    csipaus_control_id=2,
                    end_device_mrid="brand-new-device",
                    sent_at=None,
                )
            ],
        )
        await session.rollback()

    async with generate_async_session(pg_base_config) as session:
        assert (
            count_before
            == (await session.execute(select(func.count()).select_from(CSIPAusControlResponse))).scalar_one()
        )

    # Will stick on commit
    async with generate_async_session(pg_base_config) as session:
        await upsert_control_responses(
            session,
            [
                generate_class_instance(
                    CSIPAusControlResponse,
                    seed=101,
                    csipaus_control_id=2,
                    end_device_mrid="brand-new-device",
                    sent_at=None,
                )
            ],
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        assert (count_before + 1) == (
            await session.execute(select(func.count()).select_from(CSIPAusControlResponse))
        ).scalar_one()


@pytest.mark.parametrize("optional_is_none", [True, False])
async def test_upsert_control_responses(pg_base_config, optional_is_none: bool):
    """Ensures upsert_control_response can insert, update (sent_at) and not update (sent_at already set)"""

    # Arrange
    async with generate_async_session(pg_base_config) as session:
        count_before = (await session.execute(select(func.count()).select_from(CSIPAusControlResponse))).scalar_one()

    new_response_1 = generate_class_instance(
        CSIPAusControlResponse,
        seed=101,
        csipaus_control_id=1,
        end_device_mrid="aaa",
        optional_is_none=not optional_is_none,
    )
    new_response_2 = generate_class_instance(
        CSIPAusControlResponse,
        seed=202,
        optional_is_none=optional_is_none,
        csipaus_control_id=1,
        end_device_mrid="aaa",
    )

    # existing row is unsent -> not_before takes the new value, everything else is left alone
    # Conflicts with #2
    conflict_2 = generate_class_instance(
        CSIPAusControlResponse,
        seed=303,
        optional_is_none=optional_is_none,
        csipaus_control_id=1,
        response_status=2,
        end_device_mrid="aaa",
    )

    # existing row has already been sent -> the whole row must be left untouched
    # Conflicts with #5
    conflict_5 = generate_class_instance(
        CSIPAusControlResponse,
        seed=404,
        optional_is_none=optional_is_none,
        csipaus_control_id=3,
        response_status=1,
        end_device_mrid="ccc",
    )

    # Act - insert clones so the originals stay detached from the session
    async with generate_async_session(pg_base_config) as session:
        await upsert_control_responses(
            session, [clone_class_instance(e) for e in [new_response_1, new_response_2, conflict_2, conflict_5]]
        )
        await session.commit()

    # Assert
    async with generate_async_session(pg_base_config) as session:
        rows = (await session.execute(select(CSIPAusControlResponse))).scalars().all()

    assert len(rows) == count_before + 2
    by_key = {(r.csipaus_control_id, r.end_device_mrid, r.response_status): r for r in rows}

    # brand new tuple inserted verbatim
    inserted_1 = by_key[
        (new_response_1.csipaus_control_id, new_response_1.end_device_mrid, new_response_1.response_status)
    ]
    assert_class_instance_equality(
        CSIPAusControlResponse,
        new_response_1,
        inserted_1,
        ignored_properties={"csipaus_control_response_id", "created_at"},
    )
    assert_nowish(inserted_1.created_at)
    inserted_2 = by_key[
        (new_response_2.csipaus_control_id, new_response_2.end_device_mrid, new_response_2.response_status)
    ]
    assert_class_instance_equality(
        CSIPAusControlResponse,
        new_response_2,
        inserted_2,
        ignored_properties={"csipaus_control_response_id", "created_at"},
    )
    assert_nowish(inserted_1.created_at)

    # unsent conflict: not_before moved, every other column untouched
    unsent_row = by_key[(conflict_2.csipaus_control_id, conflict_2.end_device_mrid, conflict_2.response_status)]
    assert unsent_row.csipaus_control_response_id == 2
    assert unsent_row.sent_at == conflict_2.sent_at, "This is updated"
    assert unsent_row.created_at == DEFAULT_CREATED_TIME, "Unchanged"
    assert unsent_row.not_before == datetime(2026, 1, 1, tzinfo=UTC), "Unchanged from base_config.sql"

    # sent conflict: nothing changed at all
    sent_row = by_key[(conflict_5.csipaus_control_id, conflict_5.end_device_mrid, conflict_5.response_status)]
    assert sent_row.csipaus_control_response_id == 5
    assert sent_row.sent_at == datetime(2025, 1, 1, tzinfo=UTC), "Unchanged from base_config.sql"
    assert sent_row.created_at == DEFAULT_CREATED_TIME, "Unchanged"
    assert sent_row.not_before == datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC), "Unchanged from base_config.sql"


@pytest.mark.parametrize(
    "now, start, limit, expected_ids",
    [
        (datetime.min, 0, 99, [2, 3, 4, 6]),
        (datetime(2026, 1, 1, tzinfo=UTC), 0, 99, [2, 3, 4, 6]),
        (datetime.min, 1, 2, [3, 4]),  # Paging
        (datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC), 0, 99, [3, 4, 6]),
        (datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC), 0, 99, [6]),
        (datetime(2026, 1, 1, 0, 15, 0, tzinfo=UTC), 0, 99, []),
    ],
)
async def test_fetch_unsent_control_responses(
    pg_base_config, now: datetime, start: int, limit: int, expected_ids: list[int]
):
    """Tests the fetched responses match expected values"""
    async with generate_async_session(pg_base_config) as session:
        actual = await fetch_unsent_control_responses(session, now=now, start=start, limit=limit)
        assert [e.csipaus_control_response_id for e in actual] == expected_ids
        assert_list_type(CSIPAusControlResponse, actual, count=len(expected_ids))


@pytest.mark.parametrize(
    "now, expected_id",
    [
        (datetime.min, None),
        (datetime(2025, 6, 1, tzinfo=UTC), None),  # before the first record
        (datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), 1),  # active_from is inclusive
        (datetime(2026, 1, 1, 0, 2, 0, tzinfo=UTC), 1),
        (datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC), 2),  # active_to is exclusive
        (datetime(2026, 1, 1, 0, 9, 59, tzinfo=UTC), 2),
        (datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC), 3),
        (datetime(2030, 1, 1, tzinfo=UTC), 3),  # the current record runs to the max date
    ],
)
async def test_fetch_active_default(pg_base_config, now: datetime, expected_id: int | None):
    async with generate_async_session(pg_base_config) as session:
        actual = await fetch_active_default(session, now)

    if expected_id is None:
        assert actual is None
    else:
        assert isinstance(actual, CSIPAusDefault)
        assert actual.csipaus_control_id == expected_id


@pytest.mark.parametrize("optional_is_none", [True, False])
async def test_update_active_default_empty(pg_empty_config, optional_is_none: bool):
    """Does update work on an empty DB"""
    now = datetime(2026, 1, 1, 0, 12, 0, tzinfo=UTC)
    new_values = generate_class_instance(DefaultValues, optional_is_none=optional_is_none)

    async with generate_async_session(pg_empty_config) as session:
        await update_active_default(session, now, new_values)
        await session.commit()

    async with generate_async_session(pg_empty_config) as session:
        rows = (
            (await session.execute(select(CSIPAusDefault).order_by(CSIPAusDefault.csipaus_control_id))).scalars().all()
        )
        assert len(rows) == 1
        entry = rows[0]
        assert entry.active_from == now
        assert entry.active_to == DEFAULT_MAX_DATE
        assert_nowish(entry.created_at)
        assert_class_instance_equality(DefaultValues, entry, new_values)


async def test_update_active_default(pg_base_config):
    """A new default is appended and the previously active record is closed off at now - the rest of the
    history is left untouched."""
    now = datetime(2026, 1, 1, 0, 12, 0, tzinfo=UTC)
    new_values = generate_class_instance(DefaultValues, seed=1001)

    async with generate_async_session(pg_base_config) as session:
        await update_active_default(session, now, new_values)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        rows = (
            (await session.execute(select(CSIPAusDefault).order_by(CSIPAusDefault.csipaus_control_id))).scalars().all()
        )

    # original 3 + 1 appended
    assert len(rows) == 4
    by_id = {r.csipaus_control_id: r for r in rows}

    # untouched history
    assert by_id[1].active_to == datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)
    assert by_id[2].active_to == datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)

    # previously active record closed off at now
    assert by_id[3].active_from == datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)
    assert by_id[3].active_to == now

    # brand new active record carrying the supplied values
    appended = by_id[4]
    assert appended.active_from == now
    assert _as_utc(appended.active_to) == DEFAULT_MAX_DATE
    for col in DEFAULT_VALUE_COLUMNS:
        assert getattr(appended, col) == getattr(new_values, col)

    # ... and it is the one now reported as active
    async with generate_async_session(pg_base_config) as session:
        active = await fetch_active_default(session, datetime(2026, 1, 1, 0, 20, 0, tzinfo=UTC))
    assert active is not None
    assert active.csipaus_control_id == 4


async def test_update_active_default_rolls_forward(pg_base_config):
    """Repeated calls keep a contiguous, non-overlapping history that always ends at the max date."""
    first = datetime(2026, 1, 1, 0, 12, 0, tzinfo=UTC)
    second = datetime(2026, 1, 1, 0, 20, 0, tzinfo=UTC)

    async with generate_async_session(pg_base_config) as session:
        await update_active_default(session, first, generate_class_instance(DefaultValues, seed=1))
        await update_active_default(session, second, generate_class_instance(DefaultValues, seed=2))
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        rows = (await session.execute(select(CSIPAusDefault).order_by(CSIPAusDefault.active_from))).scalars().all()

    assert len(rows) == 5
    for earlier, later in zip(rows, rows[1:], strict=False):
        assert earlier.active_to == later.active_from, "History must be contiguous with no gaps or overlaps"
    assert rows[-1].active_from == second
    assert _as_utc(rows[-1].active_to) == DEFAULT_MAX_DATE


async def test_update_active_default_no_commit(pg_base_config):
    """update_active_default must never commit/rollback on its own - the caller owns the transaction."""
    now = datetime(2026, 1, 1, 0, 12, 0, tzinfo=UTC)

    async with generate_async_session(pg_base_config) as session:
        count_before = (await session.execute(select(func.count()).select_from(CSIPAusDefault))).scalar_one()

    # No explicit commit -> nothing sticks
    async with generate_async_session(pg_base_config) as session:
        await update_active_default(session, now, generate_class_instance(DefaultValues, seed=1))

    async with generate_async_session(pg_base_config) as session:
        assert count_before == (await session.execute(select(func.count()).select_from(CSIPAusDefault))).scalar_one()

    # Explicit commit -> sticks
    async with generate_async_session(pg_base_config) as session:
        await update_active_default(session, now, generate_class_instance(DefaultValues, seed=1))
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        assert (count_before + 1) == (
            await session.execute(select(func.count()).select_from(CSIPAusDefault))
        ).scalar_one()


async def test_csipaus_default_rejects_overlapping_active_range(pg_base_config):
    """The gist exclusion constraint must stop two defaults being active at the same instant."""
    async with generate_async_session(pg_base_config) as session:
        session.add(
            generate_class_instance(
                CSIPAusDefault,
                active_range=None,  # generated column
                active_from=datetime(2026, 1, 1, 0, 2, 0, tzinfo=UTC),  # lands inside record #1's window
                active_to=datetime(2026, 1, 1, 0, 30, 0, tzinfo=UTC),
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
