import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPMethod
from typing import cast

from cactus_test_definitions.csipaus import CSIPAusReadingLocation
from envoy_schema.server.schema.csip_aus.connection_point import ConnectionPointRequest
from envoy_schema.server.schema.sep2.der import (
    DefaultDERControl,
    DERControlListResponse,
    DERListResponse,
    DERProgramListResponse,
)
from envoy_schema.server.schema.sep2.device_capability import DeviceCapabilityResponse
from envoy_schema.server.schema.sep2.end_device import EndDeviceListResponse, EndDeviceRequest, EndDeviceResponse
from envoy_schema.server.schema.sep2.function_set_assignments import FunctionSetAssignmentsListResponse
from envoy_schema.server.schema.sep2.metering_mirror import (
    MirrorUsagePoint,
    MirrorUsagePointList,
)
from envoy_schema.server.schema.sep2.time import TimeResponse
from envoy_schema.server.schema.sep2.types import DeviceCategory
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import (
    fetch_controls_with_mrids,
    fetch_ocpp_metadata,
    fetch_ocpp_readings_in_range,
    fetch_unsent_control_responses,
    update_active_default,
    upsert_control_responses,
    upsert_controls,
)
from cactus_juice.csipaus.config import CSIPAusContext
from cactus_juice.csipaus.server import get_resource, paginate_list_resource_items, submit_resource
from cactus_juice.db import DatabaseConnection
from cactus_juice.mapping import (
    SUPPORTED_READING_TYPES,
    MirrorUsagePointMrids,
    create_location_mup,
    csipaus_controls_to_responses,
    csipaus_response_to_response,
    default_dercontrols_to_values,
    dercontrol_to_csipaus_control,
    generate_mup_mrids,
    ocpp_metadata_to_sep2,
    ocpp_readings_to_submit_mmr,
    previous_post_period,
)
from cactus_juice.model import CSIPAusControl

logger = logging.getLogger(__name__)

MIN_DATE = datetime(1000, 1, 1, tzinfo=UTC)  # We just want a TZ aware "minimum" date
DEFAULT_POLL_RATE = timedelta(minutes=15)
DEFAULT_POST_RATE_SECONDS = 900  # 15 minutes


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
    derl_last_change_time: datetime | None  # The last changed time - used for identifying changes

    derpl_last_poll: datetime  # DERProgramList
    derpl_poll_rate: timedelta  # DERProgramList - We will NOT support unique pollRates per DERProgram list
    derpl_hrefs: list[str]  # DERProgramList href - We may have multiple lists via FSAs

    tpl_last_poll: datetime  # TariffProfileList
    tpl_poll_rate: timedelta  # TariffProfileList - We will NOT support unique pollRates per TariffProfileList list
    tpl_hrefs: list[str]  # TariffProfileList href - We may have multiple lists via FSAs

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
            derl_last_change_time=None,
            derpl_last_poll=MIN_DATE,
            derpl_poll_rate=DEFAULT_POLL_RATE,
            derpl_hrefs=[],
            tpl_last_poll=MIN_DATE,
            tpl_poll_rate=DEFAULT_POLL_RATE,
            tpl_hrefs=[],
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

    # Mark certain resources as requiring an update after we register an EndDevice
    state.derl_last_poll = MIN_DATE
    state.mupl_last_poll = MIN_DATE
    state.mupl_last_post = MIN_DATE
    state.fsal_last_poll = MIN_DATE
    state.derpl_last_poll = MIN_DATE
    state.tpl_last_poll = MIN_DATE

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
        mup_href = await submit_resource(
            state.context.http,
            HTTPMethod.POST,
            state.mupl_href,
            create_location_mup(CSIPAusReadingLocation.Site, state.mup_site_mrids, state.context.edev_lfdi),
        )
        logger.info(f"Created Site MUP {mup_href} with mRID {state.mup_site_mrids.mup_mrid}")
        site_mup = await get_resource(MirrorUsagePoint, state.context.http, mup_href)
        state.mup_href_by_location[CSIPAusReadingLocation.Site] = mup_href

    device_mup = first_matching_caseless(
        mups.items, state.mup_device_mrids.mup_mrid, lambda mup: cast(MirrorUsagePoint, mup).mRID
    )
    if device_mup is None:
        mup_href = await submit_resource(
            state.context.http,
            HTTPMethod.POST,
            state.mupl_href,
            create_location_mup(CSIPAusReadingLocation.Device, state.mup_device_mrids, state.context.edev_lfdi),
        )
        logger.info(f"Created Device MUP {mup_href} with mRID {state.mup_device_mrids.mup_mrid}")
        device_mup = await get_resource(MirrorUsagePoint, state.context.http, mup_href)
        state.mup_href_by_location[CSIPAusReadingLocation.Device] = mup_href

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

    readings_from = previous_post_period(now, state.mupl_post_rate)
    readings_to = readings_from + state.mupl_post_rate
    all_readings = await fetch_ocpp_readings_in_range(session, readings_from, readings_to)

    site_readings, device_readings = ocpp_readings_to_submit_mmr(
        readings_from, readings_to, all_readings, state.mup_site_mrids, state.mup_device_mrids
    )

    # Send the readings
    if site_readings is None:
        logger.info(f"No site readings from {readings_from} to {readings_to} to submit to {site_mup_href}")
    else:
        logger.info(f"Submitting site readings from {readings_from} to {readings_to} to {site_mup_href}")
        await submit_resource(
            state.context.http, HTTPMethod.POST, site_mup_href, site_readings, no_location_header=True
        )

    if device_readings is None:
        logger.info(f"No device readings from {readings_from} to {readings_to} to submit to {site_mup_href}")
    else:
        logger.info(f"Submitting device readings from {readings_from} to {readings_to} to {site_mup_href}")
        await submit_resource(
            state.context.http, HTTPMethod.POST, device_mup_href, device_readings, no_location_header=True
        )

    # update state
    state.mupl_last_post = now


async def post_der_metadata(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Updates the DER Metadata (DERSettings, DERStatus, DERCapability) based on what in the DB"""

    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll DERList.")
        return

    if state.derl_href is None:
        logger.info("No DERList href discovered - unable to poll.")
        return

    # Get DER
    der_items = await paginate_list_resource_items(
        DERListResponse, state.context.http, state.derl_href, 100, lambda derl: cast(DERListResponse, derl).DER_
    )

    if not der_items.items:
        logger.info("No DER entry in DERList - unable to update DERList.")
        state.derl_last_poll = now
        return

    # Get metadata
    metadata = await fetch_ocpp_metadata(session)
    if metadata is None:
        logger.info("No device metadata available - unable to update DERList.")
        state.derl_last_poll = now
        return

    if metadata.created_at == state.derl_last_change_time:
        logger.info("Device metadata the same as previous poll. Skipping updates.")
        state.derl_last_poll = now
        return

    # Update DERCapability / DERSettings / DERStatus
    der = der_items.items[0]
    capability, settings, status = ocpp_metadata_to_sep2(metadata)

    if capability is not None:
        if der.DERCapabilityLink is None:
            logger.info(f"No DERCapabilityLink for der {der.href} in {state.derl_href}. Skipping update")
        else:
            logger.info(f"Updating DERCapability for der {der.href} in {state.derl_href}.")
            await submit_resource(
                state.context.http, HTTPMethod.PUT, der.DERCapabilityLink.href, capability, no_location_header=True
            )

    if settings is not None:
        if der.DERSettingsLink is None:
            logger.info(f"No DERSettingsLink for der {der.href} in {state.derl_href}. Skipping update")
        else:
            logger.info(f"Updating DERSettings for der {der.href} in {state.derl_href}.")
            await submit_resource(
                state.context.http, HTTPMethod.PUT, der.DERSettingsLink.href, settings, no_location_header=True
            )

    if status is not None:
        if der.DERStatusLink is None:
            logger.info(f"No DERStatusLink for der {der.href} in {state.derl_href}. Skipping update")
        else:
            logger.info(f"Updating DERStatus for der {der.href} in {state.derl_href}.")
            await submit_resource(
                state.context.http, HTTPMethod.PUT, der.DERStatusLink.href, status, no_location_header=True
            )

    # Update state
    state.derl_last_poll = now
    state.derl_last_change_time = metadata.created_at


async def poll_fsa_list(state: ClientState, now: datetime) -> None:
    """Polls an EndDevice FunctionSetAssignmentList - updating the DERProgramList / TariffProfileList hrefs"""
    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll FunctionSetAssignmentList.")
        return

    if state.fsal_href is None:
        logger.info("No FunctionSetAssignmentList href discovered - unable to poll.")
        return

    fsas = await paginate_list_resource_items(
        FunctionSetAssignmentsListResponse,
        state.context.http,
        state.fsal_href,
        100,
        lambda fsal: cast(FunctionSetAssignmentsListResponse, fsal).FunctionSetAssignments,
    )

    # Update state
    state.fsal_poll_rate = (
        state.dcap_poll_rate if fsas.poll_rate_seconds is None else timedelta(seconds=fsas.poll_rate_seconds)
    )
    state.fsal_last_poll = now

    derpl_hrefs = [fsa.DERProgramListLink.href for fsa in fsas.items if fsa.DERProgramListLink is not None]
    if derpl_hrefs != state.derpl_hrefs:
        logger.info(f"Updating DERProgramList hrefs from {state.derpl_hrefs} to {derpl_hrefs}")
        state.derpl_hrefs = derpl_hrefs
        state.derpl_last_poll = MIN_DATE  # Trigger an immediate poll on change

    tp_hrefs = [fsa.TariffProfileListLink.href for fsa in fsas.items if fsa.TariffProfileListLink is not None]
    if derpl_hrefs != state.derpl_hrefs:
        logger.info(f"Updating TariffProfileList hrefs from {state.tpl_hrefs} to {tp_hrefs}")
        state.tpl_hrefs = tp_hrefs
        state.tpl_last_poll = MIN_DATE  # Trigger an immediate poll on change


async def poll_derprogram_list(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Polls the current DERProgram lists - writing/updating any controls into the DB"""

    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll DERProgramList.")
        return

    if not state.derpl_hrefs:
        logger.info("No DERProgramList href(s) discovered - unable to poll.")
        return

    # Walk all the DERPrograms - looking for defaults and DERControls
    all_poll_rates: list[int] = []
    all_default_primacies: list[tuple[int, DefaultDERControl]] = []
    all_controls: list[CSIPAusControl] = []
    for derpl_href in state.derpl_hrefs:
        derps = await paginate_list_resource_items(
            DERProgramListResponse,
            state.context.http,
            derpl_href,
            100,
            lambda derpl: cast(DERProgramListResponse, derpl).DERProgram,
        )
        if derps.poll_rate_seconds is not None:
            all_poll_rates.append(derps.poll_rate_seconds)
        for derp in derps.items:
            primacy = derp.primacy
            # Fetch the DefaultDERControl
            if derp.DefaultDERControlLink is not None:
                logger.info(f"Fetching DefaultDERControl {derp.DefaultDERControlLink.href} for DERProgram {derp.href}")
                dderc = await get_resource(DefaultDERControl, state.context.http, derp.DefaultDERControlLink.href)
                all_default_primacies.append((primacy, dderc))

            # Fetch the DERControls
            if derp.DERControlListLink is not None:
                logger.info(f"Fetching DERControls {derp.DERControlListLink.href} for DERProgram {derp.href}")
                dercs = await paginate_list_resource_items(
                    DERControlListResponse,
                    state.context.http,
                    derp.DERControlListLink.href,
                    100,
                    lambda dercl: cast(DERControlListResponse, dercl).DERControl,
                )

                if dercs.items:
                    all_controls.extend(dercontrol_to_csipaus_control(derc, primacy) for derc in dercs.items)

    # Persist all the data we just scraped - we have to carefully update things - these crud functions will ensure
    # we correctly insert/update the records and don't thrash the DB if we keep polling the same data.
    logger.info(f"Updating with {len(all_controls)} DERControls and {len(all_default_primacies)} DefaultDERControls")
    await upsert_controls(session, all_controls)
    await update_active_default(session, now, default_dercontrols_to_values(all_default_primacies))

    # next we want to queue up some responses - these require the PK of the CSIPAusControl as well as the ACTUAL
    # values for superseded/cancelled so we need to go via the DB. The upsert will NOT duplicate responses so
    # we're free to send "everything" down
    db_controls = await fetch_controls_with_mrids(session, (c.mrid for c in all_controls))
    responses = csipaus_controls_to_responses(db_controls, state.context.edev_lfdi)
    await upsert_control_responses(session, responses)

    # Update the state with the info that we polled
    state.derpl_poll_rate = timedelta(seconds=min(all_poll_rates)) if all_poll_rates else state.fsal_poll_rate
    state.derpl_last_poll = now


async def post_unsent_responses(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Selects all unsent Responses that are due to send - sends them and then marks the records as sent"""
    responses = await fetch_unsent_control_responses(session, now, include_control=True)

    logger.info(f"Found {len(responses)} unsent DERControl Responses to send")
    for response in responses:
        if response.control.reply_to:
            body = csipaus_response_to_response(response, subject_mrid=response.control.mrid)
            await submit_resource(
                state.context.http, HTTPMethod.POST, response.control.reply_to, body, no_location_header=True
            )
            response.sent_at = now

    await session.flush()
