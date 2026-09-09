import os
from collections.abc import AsyncGenerator, Generator

import pytest
from assertical.fixtures.postgres import generate_async_session
from fastapi.testclient import TestClient
from psycopg import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.api.deps import get_session
from cactus_juice.main import create_app


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


def test_get_config_defaults_when_unset(client: TestClient):
    """With no CSIPAusConfig ever inserted, GET should return sane defaults rather than an error"""
    response = client.get("/api/config")
    assert response.status_code == 200

    body = response.json()
    assert body["created_at"] is None
    assert body["is_aggregator"] is True
    assert body["verify_hostname"] is True
    assert body["verify_ssl"] is True
    assert body["certificate_pem"] is None
    assert body["serca_pem"] is None
    assert body["has_key_pem"] is False


def test_put_then_get_config_round_trips(client: TestClient):
    """PUT with all three PEM files uploaded, then GET should reflect it - key_pem content itself should never be
    returned, only that it's set."""
    cert_bytes = b"-----BEGIN CERTIFICATE-----\ncert-data\n-----END CERTIFICATE-----\n"
    key_bytes = b"-----BEGIN PRIVATE KEY-----\nkey-data\n-----END PRIVATE KEY-----\n"
    serca_bytes = b"-----BEGIN CERTIFICATE-----\nserca-data\n-----END CERTIFICATE-----\n"

    response = client.put(
        "/api/config",
        data={
            "is_aggregator": "false",
            "nmi": "1234567890",
            "client_pen": "12345",
            "dcap_uri": "https://example.com/dcap",
            "verify_hostname": "true",
            "verify_ssl": "true",
        },
        files={
            "certificate_pem": ("cert.pem", cert_bytes, "application/x-pem-file"),
            "key_pem": ("key.pem", key_bytes, "application/x-pem-file"),
            "serca_pem": ("serca.pem", serca_bytes, "application/x-pem-file"),
        },
    )
    assert response.status_code == 200
    put_body = response.json()
    assert put_body["is_aggregator"] is False
    assert put_body["nmi"] == "1234567890"
    assert put_body["client_pen"] == 12345
    assert put_body["dcap_uri"] == "https://example.com/dcap"
    assert put_body["certificate_pem"] == cert_bytes.decode()
    assert put_body["serca_pem"] == serca_bytes.decode()
    assert put_body["has_key_pem"] is True
    assert put_body["created_at"] is not None

    get_body = client.get("/api/config").json()
    assert get_body == put_body


def test_put_config_without_files_preserves_existing_pems(client: TestClient):
    """A subsequent PUT that omits the file uploads (and doesn't set the clear_* flags) should retain whatever
    PEM data is already stored."""
    cert_bytes = b"-----BEGIN CERTIFICATE-----\ncert-data\n-----END CERTIFICATE-----\n"

    client.put(
        "/api/config",
        data={"is_aggregator": "true", "verify_hostname": "true", "verify_ssl": "true"},
        files={"certificate_pem": ("cert.pem", cert_bytes, "application/x-pem-file")},
    )

    response = client.put(
        "/api/config",
        data={"is_aggregator": "true", "nmi": "updated-nmi", "verify_hostname": "false", "verify_ssl": "false"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["nmi"] == "updated-nmi"
    assert body["verify_hostname"] is False
    assert body["certificate_pem"] == cert_bytes.decode()  # preserved even though this PUT didn't upload it


def test_put_config_clear_flag_removes_pem(client: TestClient):
    """Setting a clear_*_pem flag should remove that PEM even if others are preserved"""
    cert_bytes = b"-----BEGIN CERTIFICATE-----\ncert-data\n-----END CERTIFICATE-----\n"

    client.put(
        "/api/config",
        data={"is_aggregator": "true", "verify_hostname": "true", "verify_ssl": "true"},
        files={"certificate_pem": ("cert.pem", cert_bytes, "application/x-pem-file")},
    )

    response = client.put(
        "/api/config",
        data={
            "is_aggregator": "true",
            "verify_hostname": "true",
            "verify_ssl": "true",
            "clear_certificate_pem": "true",
        },
    )
    assert response.status_code == 200
    assert response.json()["certificate_pem"] is None
