import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http import HTTPMethod, HTTPStatus

from aiohttp import ClientResponse, ClientSession
from envoy_schema.server.schema.sep2.identification import List, Resource, SubscribableList

from cactus_juice.csipaus.config import HttpContext
from cactus_juice.csipaus.constants import MIME_TYPE_SEP2
from cactus_juice.error import RequestError

RATE_LIMIT_RETRY_DELAYS = (5, 15, 30)  # seconds to wait between retries on 429

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ServerResponse:
    """Represents a response from the utility server in response to a particular request"""

    request_path: str  # the path that was requested

    url: str  # The HTTP url that was resolved
    method: str  # Was this a GET/PUT/POST etc?
    status: int  # What was returned from the server?
    body: str  # The raw body response (assumed to be a string based)
    location: str | None  # The value of the Location header (if any)
    content_type: str | None  # The value of the Content-Type header (if any)

    def is_success(self) -> bool:
        return self.status >= 200 and self.status < 300

    def is_client_error(self) -> bool:
        return self.status >= 400 and self.status < 500

    @staticmethod
    async def from_response(request_path: str, response: ClientResponse) -> "ServerResponse":
        body_bytes = await response.read()
        location = response.headers.get("Location", None)
        content_type = response.headers.get("Content-Type", None)
        body_xml = body_bytes.decode(response.get_encoding())

        return ServerResponse(
            request_path=request_path,
            url=str(response.request_info.url),
            method=response.request_info.method,
            status=response.status,
            body=body_xml,
            location=location,
            content_type=content_type,
        )


def resource_to_sep2_xml(resource: Resource) -> str:
    xml = resource.to_xml(skip_empty=False, exclude_none=True, exclude_unset=True)
    if xml is None:
        return ""
    if isinstance(xml, bytes):
        return xml.decode()
    return xml


async def _single_request(
    session: ClientSession,
    path: str,
    method: HTTPMethod,
    headers: dict,
    sep2_xml_body: str | None,
) -> ServerResponse:
    """Makes a single request and returns the "raw" response. Raises if there is some form of connection issue"""

    logger.debug(f"Requesting {method} {path}")
    async with session.request(method=method, url=path, data=sep2_xml_body, headers=headers) as raw_response:
        try:
            response = await ServerResponse.from_response(path, raw_response)
        except Exception as exc:
            logger.error(f"Caught exception attempting to {method} {path}", exc_info=exc)
            raise RequestError(f"Caught exception attempting to {method} {path}: {exc}") from exc
        return response


async def make_request(
    context: HttpContext,
    path: str,
    method: HTTPMethod,
    sep2_xml_body: str | None = None,
) -> ServerResponse:
    """Makes a request to the CSIP-Aus server (for the current session) and endpoint - returns a raw parsed response.

    Raises a RequestError on connection failure.

    Retries up to 3 times on 429 responses with delays of 5, 15, 30 seconds."""

    headers = {"Accept": MIME_TYPE_SEP2}
    if sep2_xml_body is not None:
        headers["Content-Type"] = MIME_TYPE_SEP2

    user_agent = context.user_agent
    if user_agent:
        headers["User-Agent"] = user_agent

    response = await _single_request(context.session, path, method, headers, sep2_xml_body)

    for delay in RATE_LIMIT_RETRY_DELAYS:
        if response.status != HTTPStatus.TOO_MANY_REQUESTS:
            break
        logger.info(f"Rate limited (429), retrying in {delay}s")
        await asyncio.sleep(delay)
        response = await _single_request(context.session, path, method, headers, sep2_xml_body)

    return response


def parse_type_response[T: Resource](t: type[T], response: ServerResponse) -> T:
    href = response.request_path
    try:
        return t.from_xml(response.body)
    except Exception as exc:
        logger.error(
            f"Caught exception attempting to parse {len(response.body)} chars from {href}",
            exc_info=exc,
        )
        logger.error(response.body)
        raise RequestError(f"Caught exception parsing {len(response.body)} chars from {href}: {exc}") from exc


async def get_resource[T: Resource](t: type[T], context: HttpContext, href: str) -> T:
    """Makes a GET request for a particular href and parses the resulting XML into an expected type (t). Raises a
    RequestError if the connection fails, returns an error or fails to parse to t"""
    # Make the raw request
    response = await make_request(context, href, HTTPMethod.GET)

    if not response.is_success():
        raise RequestError(f"Received status {response.status} requesting {response.method} {href}.")

    return parse_type_response(t, response)


async def submit_resource[T: Resource](
    context: HttpContext,
    method: HTTPMethod,
    href: str,
    submitted_resource: T,
    no_location_header: bool = False,
) -> str:
    """Makes a method request to a particular href, submitting submitted_resource and expecting a success response.

    Raises RequestError if the response is NOT a success.

    Returns location header value if no_location_header is False, otherwise returns href"""

    # Make the submit request
    response = await make_request(
        context,
        href,
        method,
        sep2_xml_body=resource_to_sep2_xml(submitted_resource),
    )
    if not response.is_success():
        raise RequestError(f"Received status {response.status} requesting {response.method} {href}.")

    if no_location_header:
        return href
    else:
        if not response.location:
            raise RequestError(
                f"{response.status} response from {response.method} {href} did not return an expected 'Location' header"
            )
        return response.location


def build_paging_params(
    start: int | None = None,
    limit: int | None = None,
    changed_after: datetime | None = None,
) -> str:
    """Builds up a sep2 paging query string in the form of ?s={start}&l={limit}&a={changed_after}.
    None params will not be included in the query string"""

    parts: list[str] = []
    if start is not None:
        parts.append(f"s={start}")
    if limit is not None:
        parts.append(f"l={limit}")
    if changed_after is not None:
        parts.append(f"a={int(changed_after.timestamp())}")

    if parts:
        return "?" + "&".join(parts)
    else:
        return ""


async def paginate_list_resource_items[ListT: List | SubscribableList, ChildT: Resource](
    list_type: type[ListT],
    context: HttpContext,
    list_href: str,
    page_size: int,
    item_callback: Callable[[ListT], list[ChildT] | None],
    max_pages_requested: int = 20,
) -> list[ChildT]:
    """Helper function for paginating through an entire list object (eg EndDeviceList) over multiple requests and
    returning the resulting child items (eg EndDevice) as a single list.

    list_type: The type to parse the responses as (eg EndDeviceList)
    context: The HttpContext to make the request with
    list_href: The href to the list (no query params included). Eg /sep2/edev
    page_size: How many items to request on each page
    item_callback: Will be called on each page object to extract the items
    max_pages_requested: A safety valve to prevent infinite pagination
    """
    pages_requested = 0
    start = 0
    all_items: list[ChildT] = []

    while True:
        latest_items, received_all = await fetch_list_page(
            list_type, context, list_href, start, page_size, item_callback
        )
        all_items.extend(latest_items)

        # Prepare next page
        # This is deliberately over paginating in order to catch any odd server behaviour
        start += page_size
        if len(latest_items) == 0:  # When we receive an empty page - we know we are done
            break

        # Safety valve in case a server misbehaves and keeps sending us more data
        pages_requested += 1
        if pages_requested >= max_pages_requested:
            raise RequestError(
                f"Paginating {list_href} exceeded max pages {max_pages_requested} at page size {page_size}."
            )

    return all_items


async def fetch_list_page[ListT: List | SubscribableList, ChildT: Resource](
    list_type: type[ListT],
    context: HttpContext,
    list_href: str,
    start: int,
    limit: int,
    item_callback: Callable[[ListT], list[ChildT] | None],
) -> tuple[list[ChildT], int | None]:
    """
    Fetch a single page of a list resource and extract items with validation.

    Returns:
        tuple of (items, all_attribute)
    """
    page_href = list_href + build_paging_params(start=start, limit=limit)
    latest_list = await get_resource(list_type, context, page_href)
    latest_items = item_callback(latest_list)
    if latest_items is None:
        latest_items = []  # pydantic-xml can parse a missing/empty list as None

    # Extract and validate metadata
    received_all: int | None = getattr(latest_list, "all_", None)

    return latest_items, received_all
