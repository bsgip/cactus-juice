import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPMethod
from typing import cast

from cactus_test_definitions.csipaus import CSIPAusReadingLocation, CSIPAusReadingType
from envoy_schema.server.schema.csip_aus.connection_point import ConnectionPointRequest
from envoy_schema.server.schema.sep2.device_capability import DeviceCapabilityResponse
from envoy_schema.server.schema.sep2.end_device import EndDeviceListResponse, EndDeviceRequest, EndDeviceResponse
from envoy_schema.server.schema.sep2.metering import Reading, ReadingType
from envoy_schema.server.schema.sep2.metering_mirror import (
    MirrorMeterReading,
    MirrorMeterReadingListRequest,
    MirrorUsagePoint,
    MirrorUsagePointList,
    MirrorUsagePointRequest,
)
from envoy_schema.server.schema.sep2.time import TimeResponse
from envoy_schema.server.schema.sep2.types import DateTimeIntervalType, DeviceCategory, FlowDirectionType, ServiceKind
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import fetch_ocpp_readings_in_range
from cactus_juice.csipaus.config import CSIPAusContext
from cactus_juice.csipaus.mup import (
    MirrorUsagePointMrids,
    generate_mup_mrids,
    generate_reading_type_values,
    generate_role_flags,
    previous_post_period,
    value_to_sep2,
)
from cactus_juice.csipaus.server import get_resource, paginate_list_resource_items, submit_resource
from cactus_juice.db import DatabaseConnection
from cactus_juice.error import RemoteServiceError
from cactus_juice.model import OCPPReading

logger = logging.getLogger(__name__)

MIN_DATE = datetime(1000, 1, 1, tzinfo=UTC)  # We just want a TZ aware "minimum" date
DEFAULT_POLL_RATE = timedelta(minutes=15)
DEFAULT_POST_RATE_SECONDS = 900  # 15 minutes

SUPPORTED_READING_TYPES = [
    CSIPAusReadingType.ActivePowerAverage,
    CSIPAusReadingType.ReactivePowerAverage,
    CSIPAusReadingType.VoltageSinglePhaseAverage,
    CSIPAusReadingType.FrequencyAverage,
]
POW10_BY_READING_TYPE = {
    CSIPAusReadingType.ActivePowerAverage: 0,
    CSIPAusReadingType.ReactivePowerAverage: 0,
    CSIPAusReadingType.VoltageSinglePhaseAverage: -1,
    CSIPAusReadingType.FrequencyAverage: -3,
}


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
    mupl_post_rate: timedelta  # MirrorUsagePointList (condensed to a single value)
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
) -> MirrorUsagePoint:
    """Creates a MUP for the specified location, POST's it and returns the href"""

    role_flags = generate_role_flags(location)

    mmrs: list[MirrorMeterReading] = []
    for rt, mmr_mrid in mrids.mmr_mrids.items():
        uom, kind, dq = generate_reading_type_values(rt)

        # The device sign differs from the site sign
        flow_dir = FlowDirectionType.FORWARD if location == CSIPAusReadingLocation.Site else FlowDirectionType.REVERSE

        pow10 = POW10_BY_READING_TYPE[rt]

        mmrs.append(
            MirrorMeterReading(
                mRID=mmr_mrid,
                readingType=ReadingType(
                    uom=uom,
                    kind=kind,
                    dataQualifier=dq,
                    flowDirection=flow_dir,
                    powerOfTenMultiplier=pow10,
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
    return await get_resource(MirrorUsagePoint, state.context.http, mup_href)


async def poll_mup_list(state: ClientState, now: datetime) -> None:
    """Polls the MirrorUsagePointList - ensures the existence of MUPs for the EndDevice"""

    if state.mupl_href is None:
        logger.info("No MirrorUsagePointList href discovered - unable to poll MUP list.")
        return

    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll MUP list.")
        state.mupl_last_poll = now  # We count this as a poll
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
        site_mup = await create_location_mup(state, state.mupl_href, CSIPAusReadingLocation.Site, state.mup_site_mrids)
        if site_mup.href is None:
            raise RemoteServiceError("Received a MirrorUsagePoint with no href")
        state.mup_href_by_location[CSIPAusReadingLocation.Site] = site_mup.href
    device_mup = first_matching_caseless(
        mups.items, state.mup_device_mrids.mup_mrid, lambda mup: cast(MirrorUsagePoint, mup).mRID
    )
    if device_mup is None:
        device_mup = await create_location_mup(
            state, state.mupl_href, CSIPAusReadingLocation.Device, state.mup_device_mrids
        )
        if device_mup.href is None:
            raise RemoteServiceError("Received a MirrorUsagePoint with no href")
        state.mup_href_by_location[CSIPAusReadingLocation.Device] = device_mup.href

    # Update state
    if mups.poll_rate_seconds:
        state.mupl_poll_rate = timedelta(seconds=mups.poll_rate_seconds)
    else:
        state.mupl_poll_rate = state.dcap_poll_rate  # Failover to dcap poll rate

    # We simplify MUP post rates into a single value
    state.mupl_post_rate = timedelta(
        seconds=min(device_mup.postRate or DEFAULT_POST_RATE_SECONDS, site_mup.postRate or DEFAULT_POST_RATE_SECONDS)
    )
    state.mupl_last_poll = now


def average_readings(readings: Iterable[OCPPReading], key: Callable[[OCPPReading], float | None]) -> float | None:
    total = 0.0
    count = 0
    for r in readings:
        val = key(r)
        if val is None:
            continue

        total += val
        count += 1

    if count == 0:
        return None
    else:
        return total / count


def _append_mmr_value(
    mmrs: list[MirrorMeterReading],
    mrids: MirrorUsagePointMrids,
    rt: CSIPAusReadingType,
    value: float | None,
    start: datetime,
    duration: timedelta,
) -> None:
    """Adds an entry to mmrs if there is a MMR value to transmit"""
    mrid = mrids.mmr_mrids.get(rt)
    if mrid is not None and value is not None:
        mmrs.append(
            MirrorMeterReading(
                mRID=mrid,
                reading=Reading(
                    value=value_to_sep2(value, POW10_BY_READING_TYPE[rt]),
                    timePeriod=DateTimeIntervalType(
                        duration=int(duration.total_seconds()), start=int(start.timestamp())
                    ),
                ),
            )
        )


async def post_site_readings(state: ClientState, session: AsyncSession, now: datetime, site_mup_href: str) -> None:
    """Goes to the database and looks for all ocpp readings within the previous postRate window, averages them
    and then sends them to the site level MUP"""
    readings_from = previous_post_period(now, state.mupl_post_rate)
    readings_to = readings_from + state.mupl_post_rate
    all_readings = await fetch_ocpp_readings_in_range(session, readings_from, readings_to)

    avg_import_watts = average_readings(all_readings, lambda r: r.import_active_power_watts)
    avg_export_watts = average_readings(all_readings, lambda r: r.export_active_power_watts)
    avg_watts = (avg_import_watts or 0.0) - (avg_export_watts or 0.0)

    avg_import_var = average_readings(all_readings, lambda r: r.import_reactive_power_var)
    avg_export_var = average_readings(all_readings, lambda r: r.export_reactive_power_var)
    avg_var = (avg_import_var or 0.0) - (avg_export_var or 0.0)

    avg_volts = average_readings(all_readings, lambda r: r.voltage_volts)
    avg_hz = average_readings(all_readings, lambda r: r.frequency_hz)

    # Build our post packet for site readings (we will do everything at the site level)
    mmrs: list[MirrorMeterReading] = []
    postrate = state.mupl_post_rate
    site_mrids = state.mup_site_mrids
    _append_mmr_value(mmrs, site_mrids, CSIPAusReadingType.ActivePowerAverage, avg_watts, readings_from, postrate)
    _append_mmr_value(mmrs, site_mrids, CSIPAusReadingType.ReactivePowerAverage, avg_var, readings_from, postrate)
    _append_mmr_value(
        mmrs, site_mrids, CSIPAusReadingType.VoltageSinglePhaseAverage, avg_volts, readings_from, postrate
    )
    _append_mmr_value(mmrs, site_mrids, CSIPAusReadingType.VoltageSinglePhaseAverage, avg_hz, readings_from, postrate)

    # Send the readings
    if len(mmrs) == 0:
        logger.info(f"No readings from {readings_from} to {readings_to} to submit to {site_mup_href}")
    else:
        logger.info(
            f"Submitting {len(mmrs)} MMRs for readings from {readings_from} to {readings_to} to {site_mup_href}"
        )
        await submit_resource(
            state.context.http,
            HTTPMethod.POST,
            site_mup_href,
            MirrorMeterReadingListRequest(mirrorMeterReadings=mmrs),
            no_location_header=True,
        )


async def post_mup_list(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """POSTs the last postRate readings in a CSIP-Aus compatible form"""

    if state.mupl_href is None:
        logger.info("No MirrorUsagePointList href discovered - unable to poll MUP list.")
        state.mupl_last_post = now  # We count this as a post
        return

    # Collect and send the readings
    device_mup_href = state.mup_href_by_location.get(CSIPAusReadingLocation.Device)
    site_mup_href = state.mup_href_by_location.get(CSIPAusReadingLocation.Site)
    if device_mup_href is None or site_mup_href is None:
        logger.info(f"No MirrorUsagePoint for Device/Site location(s). Skipping readings {state.mup_href_by_location}")
        state.mupl_last_post = now  # We count this as a post
        return
    await post_site_readings(state, session, now, site_mup_href)

    # update state
    state.mupl_last_post = now


async def post_der_metadata(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Updates the DER Metadata (DERSettings, DERStatus, DERCapability) based on what in the DB"""

    raise NotImplementedError()
