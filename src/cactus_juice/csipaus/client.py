import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPMethod
from typing import cast

from envoy_schema.server.schema.csip_aus.connection_point import ConnectionPointRequest
from envoy_schema.server.schema.sep2.device_capability import DeviceCapabilityResponse
from envoy_schema.server.schema.sep2.end_device import EndDeviceListResponse, EndDeviceRequest, EndDeviceResponse
from envoy_schema.server.schema.sep2.time import TimeResponse
from envoy_schema.server.schema.sep2.types import DeviceCategory

from cactus_juice.csipaus.config import CSIPAusContext
from cactus_juice.csipaus.server import get_resource, paginate_list_resource_items, submit_resource

logger = logging.getLogger(__name__)

MIN_DATE = datetime(1000, 1, 1, tzinfo=UTC)  # We just want a TZ aware "minimum" date
DEFAULT_POLL_RATE = timedelta(minutes=15)


@dataclass(slots=True)
class ClientState:
    """Encapsulates all the state the client needs to know about the CSIP-Aus server and its polling behaviour.

    This will assume a single EndDevice to manage / in band register with no subscriptions

    It should allow (mostly) correct polling/posting timings"""

    context: CSIPAusContext

    dcap_last_poll: datetime
    dcap_poll_rate: timedelta

    edevl_last_poll: datetime  # EndDeviceList
    edevl_poll_rate: timedelta  # EndDeviceList
    edev_href: str | None  # The single EndDevice that is matched to this client

    mupl_last_poll: datetime  # MirrorUsagePointList
    mupl_poll_rate: timedelta  # MirrorUsagePointList
    mupl_post_rate: timedelta  # MirrorUsagePointList
    mupl_href: str | None

    fsal_last_poll: datetime  # FunctionSetAssignmentsList
    fsal_poll_rate: timedelta  # FunctionSetAssignmentsList
    fsal_href: str | None  # FunctionSetAssignmentsList href - Only have 1 to manage due to managing a single EndDevice

    derl_last_poll: datetime  # DERList
    derl_poll_rate: timedelta  # DERList
    derl_href: str | None  # DERList href - We only have 1 to manage as we only manage a single EndDevice

    derpl_last_poll: datetime  # DERProgramList
    derpl_poll_rate: timedelta  # DERProgramList - We will NOT support unique pollRates per DERProgram list
    derpl_href: list[str]  # DERProgramList href - We may have multiple lists via FSAs

    tpl_last_poll: datetime  # TariffProfileList
    tpl_poll_rate: timedelta  # TariffProfileList - We will NOT support unique pollRates per TariffProfileList list
    tpl_href: list[str]  # TariffProfileList href - We may have multiple lists via FSAs

    @staticmethod
    def new_instance(context: CSIPAusContext) -> "ClientState":
        return ClientState(
            context=context,
            dcap_last_poll=MIN_DATE,
            dcap_poll_rate=DEFAULT_POLL_RATE,
            edevl_last_poll=MIN_DATE,
            edevl_poll_rate=DEFAULT_POLL_RATE,
            edev_href=None,
            mupl_last_poll=MIN_DATE,
            mupl_poll_rate=DEFAULT_POLL_RATE,
            mupl_post_rate=DEFAULT_POLL_RATE,
            mupl_href=None,
            fsal_last_poll=MIN_DATE,
            fsal_poll_rate=DEFAULT_POLL_RATE,
            fsal_href=None,
            derl_last_poll=MIN_DATE,
            derl_poll_rate=DEFAULT_POLL_RATE,
            derl_href=None,
            derpl_last_poll=MIN_DATE,
            derpl_poll_rate=DEFAULT_POLL_RATE,
            derpl_href=[],
            tpl_last_poll=MIN_DATE,
            tpl_poll_rate=DEFAULT_POLL_RATE,
            tpl_href=[],
        )


def match_end_device_on_lfdi_caseless(end_devices: list[EndDeviceResponse], lfdi: str) -> EndDeviceResponse | None:
    """Does a very lightweight match on EndDevice.lfdi - returning the first EndDevice that matches or None."""
    lfdi_folded = lfdi.casefold()
    for edev in end_devices:
        if edev.lFDI is None or edev.lFDI.casefold() != lfdi_folded:
            continue

        return edev

    return None


async def poll_dcap(state: ClientState, now: datetime) -> None:

    logger.info(f"Polling DeviceCapability {state.context.dcap_path}")
    response = await get_resource(DeviceCapabilityResponse, state.context.http, state.context.dcap_path)

    # Follow the TimeLink - mainly for compliance purposes
    if response.TimeLink is not None:
        await get_resource(TimeResponse, state.context.http, response.TimeLink.href)

    # Extract link data
    if response.EndDeviceListLink is None:
        state.edev_href = None
    else:
        state.edev_href = response.EndDeviceListLink.href

    if response.MirrorUsagePointListLink is None:
        state.mupl_href = None
    else:
        state.mupl_href = response.MirrorUsagePointListLink.href

    # Mark the poll as being completed
    state.dcap_poll_rate = DEFAULT_POLL_RATE if response.pollRate is None else timedelta(seconds=response.pollRate)
    state.dcap_last_poll = now


async def in_band_register(state: ClientState, end_device_list_href: str) -> EndDeviceResponse:

    logger.info(f"In-Band registering lfdi={state.context.edev_lfdi} sfdi={state.context.edev_sfdi}")
    device_category = DeviceCategory.ELECTRIC_VEHICLE | DeviceCategory.ELECTRIC_VEHICLE_SUPPLY_EQUIPMENT

    # Submit and then refetch
    body = EndDeviceRequest(
        changedTime=int(datetime.now(UTC).timestamp()),
        enabled=True,
        deviceCategory=f"{int(device_category):x}",
        lFDI=state.context.edev_lfdi,
        sFDI=state.context.edev_sfdi,
    )
    edev_href = await submit_resource(state.context.http, HTTPMethod.POST, end_device_list_href, body)
    created_edev = await get_resource(EndDeviceResponse, state.context.http, edev_href)

    # Set the NMI
    if state.context.nmi:
        if created_edev.ConnectionPointLink is None:
            logger.warning(f"Unable to write NMI for {edev_href} - no ConnectionPointLink")
        else:
            await submit_resource(
                state.context.http, HTTPMethod.POST, end_device_list_href, ConnectionPointRequest(id=state.context.nmi)
            )

    return created_edev


async def poll_end_device_list(state: ClientState, now: datetime) -> None:

    if state.edev_href is None:
        logger.info("No EndDeviceList discovered - unable to poll.")
        return
    logger.info(f"Polling EndDeviceList {state.edev_href}")

    # poll the list
    edevl_response = await paginate_list_resource_items(
        EndDeviceListResponse,
        state.context.http,
        state.edev_href,
        page_size=100,
        item_callback=lambda edevl: cast(EndDeviceListResponse, edevl).EndDevice,
    )

    # Look for our EndDevice - registering if required
    existing_edev = match_end_device_on_lfdi_caseless(edevl_response.items, state.context.edev_lfdi)
    if existing_edev is None:
        existing_edev = await in_band_register(state, state.edev_href)

    # Update the state
    if edevl_response.poll_rate_seconds:
        state.edevl_poll_rate = timedelta(seconds=edevl_response.poll_rate_seconds)
    else:
        state.edevl_poll_rate = state.dcap_poll_rate  # failover to dcap
    state.edev_href = existing_edev.href
    if existing_edev.FunctionSetAssignmentsListLink is not None:
        state.fsal_href = existing_edev.FunctionSetAssignmentsListLink.href
    if existing_edev.DERListLink is not None:
        state.derl_href = existing_edev.DERListLink.href

    state.edevl_last_poll = now
