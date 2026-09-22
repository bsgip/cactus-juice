import os
from collections.abc import AsyncGenerator, Generator
from unittest import mock

import aiohttp
import pytest
from assertical.fixtures.postgres import generate_async_session
from fastapi.testclient import TestClient
from psycopg import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.api.deps import get_session
from cactus_juice.main import create_app
from cactus_juice.troca.client import TrocaApiError
from cactus_juice.troca.models import Connector, ConnectorType


@pytest.fixture
def client(pg_empty_config: Connection, preserved_environment) -> Generator[TestClient]:
    """A TestClient for the FastAPI app with get_session overridden to hand out sessions bound to
    pg_empty_config - so every request in a test shares that single connection/transaction.

    juice_database_url is a required setting but is never actually used by these tests (the
    DatabaseConnection built from it sits unused behind the get_session override) - a syntactically
    valid dummy value keeps create_app() happy without needing a second real database."""
    os.environ["JUICE_DATABASE_URL"] = "postgresql+asyncpg://user:pass@localhost/unused"
    app = create_app()

    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        async with generate_async_session(pg_empty_config) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client


def test_get_troca_config_defaults_when_unset(client: TestClient):
    """With no TrocaConfig ever inserted, GET should return sane defaults rather than an error"""
    response = client.get("/api/troca-config")
    assert response.status_code == 200

    body = response.json()
    assert body["created_at"] is None
    assert body["base_url"] is None
    assert body["basic_user"] is None
    assert body["has_basic_password"] is False
    assert body["connector_id"] is None
    assert body["reading_poll_rate_seconds"] is None
    assert body["ramp_step_seconds"] is None
    assert body["schedule_poll_rate_seconds"] is None
    assert body["metadata_poll_rate_seconds"] is None


def test_put_then_get_troca_config_round_trips(client: TestClient):
    """PUT should insert a new record, and GET should reflect it - basic_password itself should never be returned,
    only that it's set."""
    response = client.put(
        "/api/troca-config",
        json={
            "base_url": "https://troca.example.com",
            "basic_user": "user1",
            "basic_password": "secret",
            "connector_id": "conn-1",
            "reading_poll_rate_seconds": 30,
            "ramp_step_seconds": 5,
            "schedule_poll_rate_seconds": 15,
            "metadata_poll_rate_seconds": 60,
        },
    )
    assert response.status_code == 200
    put_body = response.json()
    assert put_body["base_url"] == "https://troca.example.com"
    assert put_body["basic_user"] == "user1"
    assert put_body["has_basic_password"] is True
    assert put_body["connector_id"] == "conn-1"
    assert put_body["created_at"] is not None
    assert put_body["reading_poll_rate_seconds"] == 30
    assert put_body["ramp_step_seconds"] == 5
    assert put_body["schedule_poll_rate_seconds"] == 15
    assert put_body["metadata_poll_rate_seconds"] == 60

    get_body = client.get("/api/troca-config").json()
    assert get_body == put_body


def test_put_troca_config_defaults_polling_rates_when_omitted(client: TestClient):
    """The polling/ramp rate fields are optional on the request - omitting them should fall back to the
    documented defaults."""
    response = client.put(
        "/api/troca-config",
        json={"base_url": "https://troca.example.com", "basic_user": "user1", "basic_password": "secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reading_poll_rate_seconds"] == 20
    assert body["ramp_step_seconds"] == 3
    assert body["schedule_poll_rate_seconds"] == 10
    assert body["metadata_poll_rate_seconds"] == 30


def test_put_troca_config_without_password_preserves_existing_password(client: TestClient):
    """A subsequent PUT that omits basic_password should retain whatever password is already stored."""
    client.put(
        "/api/troca-config",
        json={"base_url": "https://troca.example.com", "basic_user": "user1", "basic_password": "secret"},
    )

    response = client.put(
        "/api/troca-config",
        json={"base_url": "https://troca.example.com", "basic_user": "user2"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["basic_user"] == "user2"
    assert body["has_basic_password"] is True  # preserved even though this PUT didn't send it


def test_put_troca_config_requires_password_on_first_create(client: TestClient):
    """Without any existing TrocaConfig, omitting basic_password should error rather than silently creating a
    config with no way to authenticate."""
    response = client.put(
        "/api/troca-config",
        json={"base_url": "https://troca.example.com", "basic_user": "user1"},
    )
    assert response.status_code == 400


def test_get_connectors_errors_without_config(client: TestClient):
    """Enumerating connectors requires a TrocaConfig to already be on record - it can't call the Troca API
    without a base_url/credentials to use."""
    response = client.get("/api/troca-config/connectors")
    assert response.status_code == 400


def test_get_connectors_returns_configured_client_results(client: TestClient):
    client.put(
        "/api/troca-config",
        json={"base_url": "https://troca.example.com", "basic_user": "user1", "basic_password": "secret"},
    )

    connector = Connector(name="Connector One", connector_id="conn-1", connector_type=ConnectorType.EMS)
    with mock.patch("cactus_juice.api.routers.troca.TrocaClient") as mock_client_cls:
        mock_client = mock.AsyncMock()
        mock_client.get_connectors.return_value = [connector]
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        response = client.get("/api/troca-config/connectors")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Connector One"
    assert body[0]["connector_id"] == "conn-1"
    assert body[0]["connector_type"] == ConnectorType.EMS.value

    mock_client_cls.assert_called_once_with("https://troca.example.com", "user1", "secret")


def test_get_connectors_propagates_connection_errors(client: TestClient):
    """A Troca API that's simply unreachable raises aiohttp.ClientError (not TrocaApiError) - this must also
    surface as a clean 502 rather than an unhandled 500."""
    client.put(
        "/api/troca-config",
        json={"base_url": "https://troca.example.com", "basic_user": "user1", "basic_password": "secret"},
    )

    with mock.patch("cactus_juice.api.routers.troca.TrocaClient") as mock_client_cls:
        mock_client = mock.AsyncMock()
        mock_client.get_connectors.side_effect = aiohttp.ClientConnectorError(
            mock.Mock(ssl=None), OSError("Connect call failed")
        )
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        response = client.get("/api/troca-config/connectors")

    assert response.status_code == 502


def test_get_connectors_propagates_api_errors(client: TestClient):
    client.put(
        "/api/troca-config",
        json={"base_url": "https://troca.example.com", "basic_user": "user1", "basic_password": "secret"},
    )

    with mock.patch("cactus_juice.api.routers.troca.TrocaClient") as mock_client_cls:
        mock_client = mock.AsyncMock()
        mock_client.get_connectors.side_effect = TrocaApiError(500, "GET", "/config/connectors", "boom")
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        response = client.get("/api/troca-config/connectors")

    assert response.status_code == 502
