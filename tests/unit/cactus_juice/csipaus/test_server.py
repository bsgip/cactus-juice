import unittest.mock as mock
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http import HTTPMethod, HTTPStatus
from typing import cast

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance
from envoy_schema.server.schema.sep2.device_capability import DeviceCapabilityResponse
from envoy_schema.server.schema.sep2.end_device import (
    EndDeviceListResponse,
    EndDeviceRequest,
    EndDeviceResponse,
)

from cactus_juice.csipaus.config import HttpContext
from cactus_juice.csipaus.constants import MIME_TYPE_SEP2
from cactus_juice.csipaus.server import (
    RATE_LIMIT_RETRY_DELAYS,
    fetch_list_page,
    get_resource,
    make_request,
    paginate_list_resource_items,
    resource_to_sep2_xml,
    submit_resource,
)
from cactus_juice.error import RequestError

MY_USER_AGENT = "myuseragent123"


@dataclass
class RouteBehaviour:
    status: HTTPStatus
    body: bytes
    headers: dict[str, str]

    @staticmethod
    def xml(status: HTTPStatus, file_name: str) -> "RouteBehaviour":
        with open("tests/data/csipaus/" + file_name) as fp:
            raw_xml = fp.read()
        return RouteBehaviour(status, raw_xml.encode(), {"Content-Type": MIME_TYPE_SEP2})

    @staticmethod
    def no_content_location(status: HTTPStatus, location: str) -> "RouteBehaviour":
        return RouteBehaviour(status, b"", {"Location": location})


@dataclass
class TestingAppRoute:
    __test__ = False
    method: HTTPMethod
    path: str
    behaviour: list[RouteBehaviour]


def create_test_app_for_routes(routes: list[TestingAppRoute]):
    """This is a mess of closures - apologies for that!

    Will create a test app with a route for item in routes (and those created routes will have the expected behaviour)
    """

    def add_route_to_app(app: web.Application, route: TestingAppRoute) -> None:
        async def do_behaviour(request):
            if len(route.behaviour) == 0:
                return web.Response(body=b"No more mocked behaviour", status=500)

            b = route.behaviour.pop(0)
            return web.Response(body=b.body, status=b.status, headers=b.headers)

        app.router.add_route(route.method.value, route.path, do_behaviour)

    app = web.Application()
    for r in routes:
        add_route_to_app(app, r)
    return app


@asynccontextmanager
async def create_http_context(aiohttp_client, routes: list[TestingAppRoute]) -> AsyncIterator[HttpContext]:
    client: TestClient = await aiohttp_client(create_test_app_for_routes(routes))

    yield HttpContext(ClientSession(base_url=client.server.make_url("/")), user_agent=MY_USER_AGENT)


async def test_get_resource_success(aiohttp_client):
    """Does get_resource_for_step handle parsing the XML and returning the correct data"""
    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.GET,
                "/foo/bar",
                [RouteBehaviour.xml(HTTPStatus.OK, "dcap.xml")],
            )
        ],
    ) as context:
        result = await get_resource(DeviceCapabilityResponse, context, "/foo/bar")

    # Assert - contents of response
    assert isinstance(result, DeviceCapabilityResponse)
    assert result.EndDeviceListLink
    assert result.EndDeviceListLink.all_ == 2
    assert result.EndDeviceListLink.href == "/envoy-svc-static-36/edev"


async def test_get_resource_bad_request(aiohttp_client):
    """Does get_resource_for_step properly raise exceptions if a failure status is returned"""

    # We will try and trick the code by returning a normal dcap but with a proper error
    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.GET,
                "/foo/bar",
                [RouteBehaviour.xml(HTTPStatus.BAD_REQUEST, "dcap.xml")],
            )
        ],
    ) as context:
        with pytest.raises(RequestError):
            await get_resource(DeviceCapabilityResponse, context, "/foo/bar")


async def test_get_resource_xml_failure(aiohttp_client):
    """Does get_resource_for_step properly raise exceptions if the XML can't parse into the desired type"""

    # The server is sending valid sep2 XML but the type doesn't match what we want
    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.GET,
                "/foo/bar",
                [RouteBehaviour.xml(HTTPStatus.OK, "edev-list-1.xml")],
            )
        ],
    ) as context:
        with pytest.raises(RequestError):
            await get_resource(DeviceCapabilityResponse, context, "/foo/bar")


@pytest.mark.parametrize("has_location", [True, False])
async def test_submit_resource_success(aiohttp_client, has_location: bool):
    """Does submit_resource handle parsing the XML and returning the correct data"""

    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.PUT,
                "/baz",
                [
                    RouteBehaviour.no_content_location(HTTPStatus.NO_CONTENT, "/foo/bar")
                    if has_location
                    else RouteBehaviour(HTTPStatus.NO_CONTENT, b"", {})
                ],
            ),
        ],
    ) as context:
        result = await submit_resource(
            context,
            HTTPMethod.PUT,
            "/baz",
            generate_class_instance(DeviceCapabilityResponse),
            no_location_header=not has_location,
        )

    # Assert - contents of response
    assert isinstance(result, str)
    if has_location:
        assert result == "/foo/bar"
    else:
        assert result == "/baz"


async def test_submit_resource_fail_missing_location(aiohttp_client):
    """Does submit_resource error if the Location header is not returned when expected"""

    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.PUT,
                "/baz",
                [RouteBehaviour(HTTPStatus.NO_CONTENT, b"", {})],
            ),
        ],
    ) as context:
        with pytest.raises(RequestError):
            await submit_resource(
                context,
                HTTPMethod.PUT,
                "/baz",
                generate_class_instance(DeviceCapabilityResponse),
                no_location_header=False,
            )


async def test_paginate_list_resource_items(aiohttp_client):
    """Does paginate_list_resource_items work with EndDevice lists of multiple pages"""
    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.GET,
                "/foo/bar",
                [
                    RouteBehaviour.xml(HTTPStatus.OK, "edev-list-1.xml"),
                    RouteBehaviour.xml(HTTPStatus.OK, "edev-list-2.xml"),
                    RouteBehaviour.xml(HTTPStatus.OK, "edev-list-empty.xml"),
                ],
            )
        ],
    ) as context:
        result = await paginate_list_resource_items(
            EndDeviceListResponse,
            context,
            "/foo/bar",
            2,
            lambda list_response: list_response.EndDevice,
        )

    # Assert - contents of response
    assert_list_type(EndDeviceResponse, result.items, count=3)
    assert result.items[0].href == "/envoy-svc-static-36/edev/0"
    assert result.items[1].href == "/envoy-svc-static-36/edev/1"
    assert result.items[2].href == "/envoy-svc-static-36/edev/2"
    assert result.poll_rate_seconds == 60


async def test_paginate_list_resource_items_handle_failure(aiohttp_client):
    """Does paginate_list_resource_items handle failures in one of the pagination requests"""
    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.GET,
                "/foo/bar",
                [
                    RouteBehaviour.xml(HTTPStatus.OK, "edev-list-1.xml"),
                    RouteBehaviour.xml(HTTPStatus.INTERNAL_SERVER_ERROR, "edev-list-2.xml"),
                    RouteBehaviour.xml(HTTPStatus.OK, "edev-list-empty.xml"),  # Should never run
                ],
            )
        ],
    ) as context:
        with pytest.raises(RequestError):
            await paginate_list_resource_items(
                EndDeviceListResponse,
                context,
                "/foo/bar",
                2,
                lambda list_response: cast(EndDeviceListResponse, list_response).EndDevice,
            )


async def test_paginate_list_resource_items_empty_list(aiohttp_client):
    """Does paginate_list_resource_items work with an empty list"""
    behaviour = RouteBehaviour.xml(HTTPStatus.OK, "edev-list-empty.xml")
    behaviour.body = behaviour.body.decode().replace('all="3"', 'all="0"').encode()  # Make this a proper empty list
    async with create_http_context(
        aiohttp_client,
        [TestingAppRoute(HTTPMethod.GET, "/foo/bar", [behaviour])],
    ) as context:
        result = await paginate_list_resource_items(
            EndDeviceListResponse,
            context,
            "/foo/bar",
            3,
            lambda list_response: cast(EndDeviceListResponse, list_response).EndDevice,
        )

    # Assert - contents of response
    assert_list_type(EndDeviceResponse, result.items, count=0)
    assert result.poll_rate_seconds == 60


async def test_paginate_list_resource_items_too_many_requests(aiohttp_client):
    """Does paginate_list_resource_items handle failures in one of the pagination requests"""
    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.GET,
                "/foo/bar",
                [
                    RouteBehaviour.xml(HTTPStatus.OK, "edev-list-1.xml"),
                    RouteBehaviour.xml(HTTPStatus.OK, "edev-list-2.xml"),
                    RouteBehaviour.xml(HTTPStatus.OK, "edev-list-empty.xml"),  # Should never run
                ],
            )
        ],
    ) as context:
        with pytest.raises(RequestError):
            await paginate_list_resource_items(
                EndDeviceListResponse,
                context,
                "/foo/bar",
                2,
                lambda list_response: cast(EndDeviceListResponse, list_response).EndDevice,
                max_pages_requested=2,
            )


def test_resource_to_sep2_xml():
    """Mainly a sanity check on resource_to_sep2_xml to ensure it generates something that looks like XML"""
    xml1 = resource_to_sep2_xml(generate_class_instance(EndDeviceRequest, seed=1, generate_relationships=True))
    xml2 = resource_to_sep2_xml(generate_class_instance(EndDeviceRequest, seed=2, generate_relationships=True))
    xml3 = resource_to_sep2_xml(
        generate_class_instance(EndDeviceRequest, seed=2, generate_relationships=True, optional_is_none=True)
    )

    assert xml1 and isinstance(xml1, str)
    assert xml2 and isinstance(xml2, str)
    assert xml3 and isinstance(xml3, str)

    assert xml1 != xml2

    assert "</EndDevice>" in xml1


async def test_fetch_list_page(aiohttp_client):
    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.GET,
                "/foo/bar",
                [RouteBehaviour.xml(HTTPStatus.OK, "edev-list-1.xml")],
            )
        ],
    ) as context:
        start = 5
        limit = 10

        items, all_attribute, poll_rate_attribute = await fetch_list_page(
            EndDeviceListResponse,
            context,
            "/foo/bar",
            start,
            limit,
            lambda list_response: cast(EndDeviceListResponse, list_response).EndDevice,
        )

    # Assert - contents of response
    assert_list_type(EndDeviceResponse, items, count=2)
    assert items[0].href == "/envoy-svc-static-36/edev/0"
    assert items[1].href == "/envoy-svc-static-36/edev/1"
    assert all_attribute == 3
    assert poll_rate_attribute == 60


@mock.patch("cactus_juice.csipaus.server.asyncio.sleep")
async def test_request_for_step_429_retry_success(mock_sleep, aiohttp_client):
    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.GET,
                "/foo/bar",
                [
                    RouteBehaviour(HTTPStatus.TOO_MANY_REQUESTS, b"", {}),
                    RouteBehaviour.xml(HTTPStatus.OK, "dcap.xml"),
                ],
            )
        ],
    ) as context:
        response = await make_request(context, "/foo/bar", HTTPMethod.GET)

    assert response.status == HTTPStatus.OK
    mock_sleep.assert_called_once_with(RATE_LIMIT_RETRY_DELAYS[0])


@mock.patch("cactus_juice.csipaus.server.asyncio.sleep")
async def test_request_for_step_429_all_retries_exhausted(mock_sleep, aiohttp_client):
    async with create_http_context(
        aiohttp_client,
        [
            TestingAppRoute(
                HTTPMethod.GET,
                "/foo/bar",
                [
                    RouteBehaviour(HTTPStatus.TOO_MANY_REQUESTS, b"", {})
                    for _ in range(len(RATE_LIMIT_RETRY_DELAYS) + 1)
                ],
            )
        ],
    ) as context:
        response = await make_request(context, "/foo/bar", HTTPMethod.GET)

    assert response.status == HTTPStatus.TOO_MANY_REQUESTS
    assert mock_sleep.call_count == len(RATE_LIMIT_RETRY_DELAYS)
    for i, delay in enumerate(RATE_LIMIT_RETRY_DELAYS):
        assert mock_sleep.call_args_list[i] == mock.call(delay)
