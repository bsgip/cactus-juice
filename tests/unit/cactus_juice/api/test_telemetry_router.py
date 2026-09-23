import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from fastapi.testclient import TestClient
from freezegun import freeze_time
from psycopg import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.api.deps import get_session
from cactus_juice.crud import insert_satec_readings, upsert_task_health
from cactus_juice.main import create_app
from cactus_juice.model import SatecReading
from cactus_juice.tasks import TASKS


def _make_client(db: Connection) -> Generator[TestClient]:
    os.environ["JUICE_DATABASE_URL"] = "postgresql+asyncpg://user:pass@localhost/unused"
    app = create_app()

    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        async with generate_async_session(db) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(pg_empty_config: Connection, preserved_environment) -> Generator[TestClient]:
    """A TestClient for the FastAPI app with get_session overridden to hand out sessions bound to an empty
    database - so every request in a test shares that single connection/transaction."""
    yield from _make_client(pg_empty_config)


@pytest.fixture
def seeded_client(pg_base_config: Connection, preserved_environment) -> Generator[TestClient]:
    """Same as `client` but backed by base_config.sql's seeded rows."""
    yield from _make_client(pg_base_config)


def _seed_satec_readings(db: Connection) -> list[SatecReading]:
    """Seeds SatecReadings against base_config.sql's SatecConfig ids 1/2 (labels "Meter 1 (RTU)"/"Meter 2 (TCP)")
    - one each inside the [00:01, 00:06) snapshot window used by test_get_snapshot_seeded, plus one against
    config 3 well outside it that should never show up in the response. Returns the two in-window readings."""
    in_window = [
        generate_class_instance(
            SatecReading, seed=1, satec_config_id=1, reading_start=datetime(2026, 1, 1, 0, 3, 0, tzinfo=UTC)
        ),
        generate_class_instance(
            SatecReading, seed=2, satec_config_id=2, reading_start=datetime(2026, 1, 1, 0, 4, 0, tzinfo=UTC)
        ),
    ]
    out_of_window = generate_class_instance(
        SatecReading, seed=3, satec_config_id=3, reading_start=datetime(2026, 1, 1, 0, 30, 0, tzinfo=UTC)
    )

    async def _seed() -> None:
        async with generate_async_session(db) as session:
            await insert_satec_readings(session, [*in_window, out_of_window])
            await session.commit()

    asyncio.run(_seed())
    return in_window


def test_get_snapshot_empty_db(client: TestClient):
    """With nothing in the DB, the snapshot should still resolve to a well-formed (mostly empty) response - the
    schedule always has at least the implied "no controls in effect" entry. task_health still carries an entry
    per registered task (see cactus_juice.tasks.TASKS), just with last_run_at/last_exception_at all None."""
    response = client.get("/api/telemetry/snapshot")
    assert response.status_code == 200

    body = response.json()
    assert len(body["schedule"]) == 1
    assert body["schedule"][0]["active_to"] is None
    assert body["dynamic_prices"] == []
    assert body["ocpp_readings"] == []
    assert body["ocpp_metadata"] is None
    assert body["satec_readings"] == []
    assert [h["task_name"] for h in body["task_health"]] == sorted(TASKS)
    for h in body["task_health"]:
        assert h["last_run_at"] is None
        assert h["last_exception_at"] is None
        assert h["last_exception"] is None


@freeze_time("2026-01-01T00:02:00Z")
def test_get_snapshot_seeded(seeded_client: TestClient, pg_base_config: Connection):
    """Pins "now" to fall within base_config.sql's seeded data - window becomes
    [2026-01-01T00:01:00Z, 2026-01-01T00:06:00Z)."""
    expected_satec_readings = _seed_satec_readings(pg_base_config)

    response = seeded_client.get("/api/telemetry/snapshot")
    assert response.status_code == 200

    body = response.json()
    assert body["now"] == "2026-01-01T00:02:00Z"
    assert body["window_start"] == "2026-01-01T00:01:00Z"
    assert body["window_end"] == "2026-01-01T00:06:00Z"

    # Schedule is driven by calculate_schedule_values (see test_controls.py for exhaustive coverage) - just
    # confirm the wiring produced a non-empty, well-formed schedule resolved from window_start (not "now"), so
    # the chart gets a glimpse of what was in effect over the last WINDOW_LOOKBACK_SECONDS.
    assert len(body["schedule"]) > 0
    assert datetime.fromisoformat(body["schedule"][0]["active_from"]) == datetime(2026, 1, 1, 0, 1, 0, tzinfo=UTC)

    # Every dynamic price finishes (or is cancelled) after window_start, so all 7 seeded rows are included.
    assert [p["mrid"] for p in body["dynamic_prices"]] == ["1111", "4444", "5555", "6666", "7777", "2222", "3333"]

    # Only the reading at 00:05 falls inside [00:01, 00:06).
    assert len(body["ocpp_readings"]) == 1
    reading = body["ocpp_readings"][0]
    assert reading["reading_start"] == "2026-01-01T00:05:00Z"
    assert reading["frequency_hz"] == 401

    # The most recently created OCPPMetadata row (id 2, created 00:10) regardless of the chart window.
    metadata = body["ocpp_metadata"]
    assert metadata["created_at"] == "2026-01-01T00:10:00Z"
    assert metadata["max_voltage_volts"] == 2001

    # Only the two SatecReadings inside the window are returned, each labelled by its parent SatecConfig - the
    # one against config 3 at 00:30 falls outside [00:01, 00:06) and is excluded.
    assert len(body["satec_readings"]) == 2
    expected_labels = ["Meter 1 (RTU)", "Meter 2 (TCP)"]
    for expected_reading, expected_label, actual in zip(
        expected_satec_readings, expected_labels, body["satec_readings"], strict=True
    ):
        assert actual["label"] == expected_label
        assert actual["reading_start"] == expected_reading.reading_start.isoformat().replace("+00:00", "Z")
        assert actual["total_kw"] == expected_reading.total_kw
        assert actual["total_kvar"] == expected_reading.total_kvar
        assert actual["v_avg_ln"] == expected_reading.v_avg_ln
        assert actual["frequency"] == expected_reading.frequency


def test_get_snapshot_task_health(client: TestClient, pg_empty_config: Connection):
    """One registered task (see cactus_juice.tasks.TASKS) with a healthy run, one that's currently failing -
    both are reported verbatim; there's no third registered task to exercise the "never run" (None) case here,
    that's covered by test_get_snapshot_empty_db."""
    task_names = sorted(TASKS)
    assert len(task_names) >= 2, "expected at least 2 registered tasks to exercise this test"
    healthy_task, failing_task = task_names[0], task_names[1]

    async def _seed() -> None:
        async with generate_async_session(pg_empty_config) as session:
            await upsert_task_health(session, healthy_task, datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
            await upsert_task_health(
                session, failing_task, datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), exception="connection refused"
            )
            await session.commit()

    asyncio.run(_seed())

    response = client.get("/api/telemetry/snapshot")
    assert response.status_code == 200

    body = response.json()
    by_name = {h["task_name"]: h for h in body["task_health"]}
    assert set(by_name) == set(task_names)

    assert by_name[healthy_task]["last_run_at"] == "2026-01-01T00:00:00Z"
    assert by_name[healthy_task]["last_exception_at"] is None
    assert by_name[healthy_task]["last_exception"] is None

    assert by_name[failing_task]["last_run_at"] == "2026-01-01T00:00:00Z"
    assert by_name[failing_task]["last_exception_at"] == "2026-01-01T00:00:00Z"
    assert by_name[failing_task]["last_exception"] == "connection refused"
