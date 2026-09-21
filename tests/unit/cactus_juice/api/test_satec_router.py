import os
from collections.abc import AsyncGenerator, Generator

import pytest
from assertical.fixtures.postgres import generate_async_session
from fastapi.testclient import TestClient
from psycopg import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.api.deps import get_session
from cactus_juice.main import create_app


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
    """Same as `client` but backed by base_config.sql's 3 pre-seeded SatecConfig rows (ids 1-3)."""
    yield from _make_client(pg_base_config)


SAMPLE_BODY = {
    "label": "New meter",
    "poll_rate_seconds": 2.5,
    "model": "em235",
    "host": "10.0.0.9",
    "port": None,
    "port_tcp": 502,
    "unit": 4,
    "baud": 9600,
    "parity": "E",
    "timeout_seconds": 0.5,
    "include_phases": True,
    "include_energy": True,
    "float_mode": False,
}


def test_list_satec_configs_empty(client: TestClient):
    response = client.get("/api/satec-config")
    assert response.status_code == 200
    assert response.json() == []


def test_list_satec_configs(seeded_client: TestClient):
    response = seeded_client.get("/api/satec-config")
    assert response.status_code == 200
    body = response.json()
    assert [c["id"] for c in body] == [1, 2, 3]
    assert [c["label"] for c in body] == ["Meter 1 (RTU)", "Meter 2 (TCP)", "Meter 3 (Float)"]


def test_post_satec_config_defaults(client: TestClient):
    """Only label/poll_rate_seconds are mandatory - everything else falls back to a sane default."""
    response = client.post("/api/satec-config", json={"label": "Minimal meter", "poll_rate_seconds": 1.0})
    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "Minimal meter"
    assert body["poll_rate_seconds"] == 1.0
    assert body["model"] == "em133"
    assert body["port"] == "/dev/ttyUSB0"
    assert body["host"] is None
    assert body["port_tcp"] == 502
    assert body["unit"] == 1
    assert body["baud"] == 19200
    assert body["parity"] == "N"
    assert body["timeout_seconds"] == 1.0
    assert body["include_phases"] is False
    assert body["include_energy"] is False
    assert body["float_mode"] is False
    assert body["created_at"] is not None
    assert body["id"] is not None


def test_post_then_list_satec_config_round_trips(client: TestClient):
    post_response = client.post("/api/satec-config", json=SAMPLE_BODY)
    assert post_response.status_code == 201
    created = post_response.json()
    for key, value in SAMPLE_BODY.items():
        assert created[key] == value

    list_response = client.get("/api/satec-config")
    assert list_response.json() == [created]


def test_post_satec_config_rejects_non_positive_poll_rate(client: TestClient):
    response = client.post("/api/satec-config", json={"label": "Bad meter", "poll_rate_seconds": 0})
    assert response.status_code == 422


def test_post_satec_config_rejects_invalid_model(client: TestClient):
    response = client.post(
        "/api/satec-config", json={"label": "Bad meter", "poll_rate_seconds": 1.0, "model": "not-a-real-model"}
    )
    assert response.status_code == 422


def test_post_satec_config_adds_alongside_existing(seeded_client: TestClient):
    response = seeded_client.post("/api/satec-config", json=SAMPLE_BODY)
    assert response.status_code == 201
    assert response.json()["id"] == 4

    list_response = seeded_client.get("/api/satec-config")
    assert [c["id"] for c in list_response.json()] == [1, 2, 3, 4]


def test_put_satec_config_updates_in_place(seeded_client: TestClient):
    response = seeded_client.put("/api/satec-config/2", json=SAMPLE_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 2
    for key, value in SAMPLE_BODY.items():
        assert body[key] == value

    # siblings untouched
    list_response = seeded_client.get("/api/satec-config")
    labels = {c["id"]: c["label"] for c in list_response.json()}
    assert labels[1] == "Meter 1 (RTU)"
    assert labels[3] == "Meter 3 (Float)"


def test_put_satec_config_missing_returns_404(seeded_client: TestClient):
    response = seeded_client.put("/api/satec-config/9999", json=SAMPLE_BODY)
    assert response.status_code == 404


def test_delete_satec_config(seeded_client: TestClient):
    response = seeded_client.delete("/api/satec-config/2")
    assert response.status_code == 204

    list_response = seeded_client.get("/api/satec-config")
    assert [c["id"] for c in list_response.json()] == [1, 3]


def test_delete_satec_config_missing_returns_404(seeded_client: TestClient):
    response = seeded_client.delete("/api/satec-config/9999")
    assert response.status_code == 404
