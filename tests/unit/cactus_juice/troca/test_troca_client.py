import logging

import pytest
from aiohttp import web

from cactus_juice.troca.client import TrocaApiError, TrocaClient

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
