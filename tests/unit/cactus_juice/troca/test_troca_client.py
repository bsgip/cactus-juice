import logging
from datetime import UTC, datetime

import pytest
from aiohttp import web

from cactus_juice.troca.client import TROCA_PAGE_SIZE, TrocaApiError, TrocaClient

RESPONSE_BODY = '[{"connectorId": "abc"}]'


async def _handle_get(request: web.Request) -> web.Response:
    return web.Response(text=RESPONSE_BODY, content_type="application/json")


async def _handle_post(request: web.Request) -> web.Response:
    return web.Response(text="Success")


async def _handle_error(request: web.Request) -> web.Response:
    return web.Response(status=400, text="Bad things")


@pytest.fixture
async def troca_url(aiohttp_server) -> str:
    app = web.Application()
    app.router.add_get("/base/config/connectors", _handle_get)
    app.router.add_post("/base/sessions/commands", _handle_post)
    app.router.add_get("/base/sessions", _handle_error)
    server = await aiohttp_server(app)
    return str(server.make_url("/base"))


async def test_request_logging_info(troca_url: str, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="cactus_juice.troca.client")
    async with TrocaClient(troca_url, "user", "pass") as client:
        await client._get("/config/connectors", {"limit": 5})
        assert await client._post("/sessions/commands", {"a": 1}) is None  # "Success" isn't JSON

    assert [r.levelno for r in caplog.records] == [logging.INFO, logging.INFO]
    assert caplog.records[0].getMessage() == (
        "Troca GET /config/connectors params={'limit': 5} -> 200 "
        f"(request 0 bytes, response {len(RESPONSE_BODY)} bytes)"
    )
    assert caplog.records[1].getMessage() == (
        "Troca POST /sessions/commands params={} -> 200 (request 8 bytes, response 7 bytes)"
    )


async def test_request_logging_debug(troca_url: str, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG, logger="cactus_juice.troca.client")
    async with TrocaClient(troca_url, "user", "pass") as client:
        await client._post("/sessions/commands", {"a": 1})

    debug_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_messages == [
        'Troca POST /sessions/commands request body: {"a": 1}',
        "Troca POST /sessions/commands response body: Success",
    ]


async def test_request_logging_error(troca_url: str, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="cactus_juice.troca.client")
    async with TrocaClient(troca_url, "user", "pass") as client:
        with pytest.raises(TrocaApiError) as exc_info:
            await client._get("/sessions")

    assert exc_info.value.status == 400
    assert exc_info.value.body == "Bad things"
    assert caplog.records[0].getMessage() == (
        "Troca GET /sessions params={} -> 400 (request 0 bytes, response 10 bytes)"
    )


def test_params():
    assert TrocaClient._params() == {}
    assert TrocaClient._params(
        10, 20, "-timestamp", {"sessionId": "abc", "timestamp[gt]": datetime(2026, 10, 1, 1, 2, 3, 456, tzinfo=UTC)}
    ) == {
        "sessionId": "abc",
        "timestamp[gt]": "2026-10-01T01:02:03Z",
        "orderby": "-timestamp",
        "limit": 10,
        "offset": 20,
    }
    assert TrocaClient._params(filters={"sessionId": None}) == {}  # Unset filters are dropped


TOTAL_ITEMS = TROCA_PAGE_SIZE * 2 + 1


@pytest.fixture
async def paged_troca(aiohttp_server) -> tuple[str, list[dict[str, str]]]:
    """Serves TOTAL_ITEMS metering readings, paged the same way as Troca. Returns (url, every request's query)."""
    queries: list[dict[str, str]] = []

    async def _handle(request: web.Request) -> web.Response:
        queries.append(dict(request.query))
        offset = int(request.query.get("offset", 0))
        limit = int(request.query.get("limit", TROCA_PAGE_SIZE))
        items = [{"timestamp": f"2026-10-01T00:00:{i % 60:02d}Z"} for i in range(TOTAL_ITEMS)][offset : offset + limit]
        return web.json_response(items)

    app = web.Application()
    app.router.add_get("/observation-points/metering-data", _handle)
    server = await aiohttp_server(app)
    return str(server.make_url("")), queries


async def test_get_list_pages_until_short_page(paged_troca: tuple[str, list[dict[str, str]]]):
    url, queries = paged_troca
    after = datetime(2026, 10, 1, tzinfo=UTC)
    async with TrocaClient(url, "user", "pass") as client:
        readings = await client.get_metering_data(after=after, orderby="timestamp")

    assert len(readings) == TOTAL_ITEMS
    assert [(q["offset"], q["limit"]) for q in queries] == [
        ("0", str(TROCA_PAGE_SIZE)),
        (str(TROCA_PAGE_SIZE), str(TROCA_PAGE_SIZE)),
        (str(TROCA_PAGE_SIZE * 2), str(TROCA_PAGE_SIZE)),
    ]
    assert all(q["timestamp[gt]"] == "2026-10-01T00:00:00Z" and q["orderby"] == "timestamp" for q in queries)


async def test_get_list_with_limit_is_one_page(paged_troca: tuple[str, list[dict[str, str]]]):
    url, queries = paged_troca
    async with TrocaClient(url, "user", "pass") as client:
        readings = await client.get_metering_data(limit=3, offset=5)

    assert len(readings) == 3
    assert queries == [{"limit": "3", "offset": "5"}]
