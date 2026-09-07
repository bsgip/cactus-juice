import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPMethod
from typing import cast

from cactus_test_definitions.csipaus import CSIPAusReadingLocation, CSIPAusReadingType
from envoy_schema.server.schema.csip_aus.connection_point import ConnectionPointRequest
from envoy_schema.server.schema.sep2.device_capability import DeviceCapabilityResponse
from envoy_schema.server.schema.sep2.end_device import EndDeviceListResponse, EndDeviceRequest, EndDeviceResponse
from envoy_schema.server.schema.sep2.metering import ReadingType
from envoy_schema.server.schema.sep2.metering_mirror import (
    MirrorMeterReading,
    MirrorUsagePoint,
    MirrorUsagePointList,
    MirrorUsagePointRequest,
)
from envoy_schema.server.schema.sep2.time import TimeResponse
from envoy_schema.server.schema.sep2.types import DeviceCategory, FlowDirectionType, ServiceKind

from cactus_juice.csipaus.config import CSIPAusContext
from cactus_juice.csipaus.mup import (
    MirrorUsagePointMrids,
    generate_mup_mrids,
    generate_reading_type_values,
    generate_role_flags,
)
from cactus_juice.csipaus.server import get_resource, paginate_list_resource_items, submit_resource
from cactus_juice.db import DatabaseConnection

logger = logging.getLogger(__name__)

MIN_DATE = datetime(1000, 1, 1, tzinfo=UTC)  # We just want a TZ aware "minimum" date
DEFAULT_POLL_RATE = timedelta(minutes=15)

SUPPORTED_READING_TYPES = [
    CSIPAusReadingType.ActivePowerAverage,
    CSIPAusReadingType.VoltageSinglePhaseAverage,
    CSIPAusReadingType.FrequencyAverage,
]
POW10_MULTIPLIER = 0


@dataclass(slots=True)
class ClientState:
    """Encapsulates all the state the client needs to know about the CSIP-Aus server and its polling behaviour.

    This will assume a single EndDevice to manage / in band register with no subscriptions

    It should allow (mostly) correct polling/posting timings"""

    context: CSIPAusContext
    db: DatabaseConnection

    dcap_last_poll: datetime
    dcap_poll_rate: timedelta

    edevl_last_poll: datetime  # EndDeviceList
    edevl_poll_rate: timedelta  # EndDeviceList
    edev_href: str | None  # The single EndDevice that is matched to this client

    mupl_last_poll: datetime  # MirrorUsagePointList
    mupl_poll_rate: timedelta  # MirrorUsagePointList
    mupl_last_post: datetime  # MirrorUsagePointList
    mupl_post_rate: timedelta  # MirrorUsagePointList
    mupl_href: str | None
    mup_href_by_location: dict[CSIPAusReadingLocation, str]
    mup_device_mrids: MirrorUsagePointMrids
    mup_site_mrids: MirrorUsagePointMrids

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
    def new_instance(context: CSIPAusContext, db: DatabaseConnection) -> "ClientState":

        # prep the expected MUP mrids
        mup_site_mrids = generate_mup_mrids(
            CSIPAusReadingLocation.Site,
            SUPPORTED_READING_TYPES,
            context.client_pen,
        )
        mup_device_mrids = generate_mup_mrids(
            CSIPAusReadingLocation.Device,
            SUPPORTED_READING_TYPES,
            context.client_pen,
        )

        return ClientState(
            context=context,
            db=db,
            dcap_last_poll=MIN_DATE,
            dcap_poll_rate=DEFAULT_POLL_RATE,
            edevl_last_poll=MIN_DATE,
            edevl_poll_rate=DEFAULT_POLL_RATE,
            edev_href=None,
            mupl_last_poll=MIN_DATE,
            mupl_poll_rate=DEFAULT_POLL_RATE,
            mupl_last_post=MIN_DATE,
            mupl_post_rate=DEFAULT_POLL_RATE,
            mupl_href=None,
            mup_href_by_location={},
            mup_device_mrids=mup_device_mrids,
            mup_site_mrids=mup_site_mrids,
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


def first_matching_caseless[T](items: list[T], value: str, key: Callable[[T], str | None]) -> T | None:
    """Does a case folded match on a string value extracted from each element in items - returning the first that
    matches or None."""
    value_folded = value.casefold()
    for item in items:
        compare_value = key(item)
        if compare_value is None or compare_value.casefold() != value_folded:
            continue

        return item

    return None


async def poll_dcap(state: ClientState, now: datetime) -> None:
    """polls DeviceCapability - updates links to EndDeviceList/MUPList"""

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
    """Polls EndDeviceList (if discovered) - ensures EndDevice existence, updates links to FSAs / DERs"""
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
    existing_edev = first_matching_caseless(
        edevl_response.items, state.context.edev_lfdi, lambda edev: cast(EndDeviceResponse, edev).lFDI
    )
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


async def create_location_mup(
    state: ClientState, mup_list_href: str, location: CSIPAusReadingLocation, mrids: MirrorUsagePointMrids
) -> str:
    """Creates a MUP for the specified location, POST's it and returns the href"""

    role_flags = generate_role_flags(location)

    mmrs: list[MirrorMeterReading] = []
    for rt, mmr_mrid in mrids.mmr_mrids.items():
        uom, kind, dq = generate_reading_type_values(rt)

        mmrs.append(
            MirrorMeterReading(
                mRID=mmr_mrid,
                readingType=ReadingType(
                    uom=uom,
                    kind=kind,
                    dataQualifier=dq,
                    flowDirection=FlowDirectionType.FORWARD,
                    powerOfTenMultiplier=POW10_MULTIPLIER,
                ),
            )
        )

    mup_href = await submit_resource(
        state.context.http,
        HTTPMethod.POST,
        mup_list_href,
        MirrorUsagePointRequest(
            roleFlags=f"{int(role_flags):04X}",
            deviceLFDI=state.context.edev_lfdi,
            mRID=mrids.mup_mrid,
            status=1,
            mirrorMeterReadings=mmrs,
            serviceCategoryKind=ServiceKind.ELECTRICITY,
        ),
    )

    logger.info(f"Created MUP {mup_href} for {location} with mRID {mrids.mup_mrid} and {len(mmrs)} MMRs")
    return mup_href


async def poll_mup_list(state: ClientState, now: datetime) -> None:
    """Polls the MirrorUsagePointList - ensures the existence of MUPs for the EndDevice"""

    if state.mupl_href is None:
        logger.info("No MirrorUsagePointList href discovered - unable to poll MUP list.")
        return

    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll MUP list.")
        return

    mups = await paginate_list_resource_items(
        MirrorUsagePointList,
        state.context.http,
        state.mupl_href,
        page_size=100,
        item_callback=lambda mupl: cast(MirrorUsagePointList, mupl).mirrorUsagePoints,
    )

    # Ensure our site/device MUP exists
    site_mup = first_matching_caseless(
        mups.items, state.mup_site_mrids.mup_mrid, lambda mup: cast(MirrorUsagePoint, mup).mRID
    )
    if site_mup is None:
        state.mup_href_by_location[CSIPAusReadingLocation.Site] = await create_location_mup(
            state, state.mupl_href, CSIPAusReadingLocation.Site, state.mup_site_mrids
        )
    device_mup = first_matching_caseless(
        mups.items, state.mup_device_mrids.mup_mrid, lambda mup: cast(MirrorUsagePoint, mup).mRID
    )
    if device_mup is None:
        state.mup_href_by_location[CSIPAusReadingLocation.Device] = await create_location_mup(
            state, state.mupl_href, CSIPAusReadingLocation.Device, state.mup_device_mrids
        )

    # Update state
    if mups.poll_rate_seconds:
        state.mupl_poll_rate = timedelta(seconds=mups.poll_rate_seconds)
    else:
        state.mupl_poll_rate = state.dcap_poll_rate  # Failover to dcap poll rate
    state.mupl_last_poll = now


async def post_mup_list(state: ClientState, now: datetime) -> None:
    """POSTs the last postRate readings in a CSIP-Aus compatible form"""
