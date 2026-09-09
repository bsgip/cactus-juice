import logging
from collections.abc import Callable, Iterable
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
from envoy_schema.server.schema.sep2.identification import Link
from envoy_schema.server.schema.sep2.metering import ReadingType
from envoy_schema.server.schema.sep2.metering_mirror import (
    MirrorUsagePoint,
    MirrorUsagePointList,
)
from envoy_schema.server.schema.sep2.pricing import (
    RateComponentResponse,
    TariffProfileListResponse,
    TimeTariffIntervalListResponse,
)
from envoy_schema.server.schema.sep2.time import TimeResponse
from envoy_schema.server.schema.sep2.types import DeviceCategory
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.crud import (
    fetch_controls_with_mrids,
    fetch_dynamic_prices_with_mrids,
    fetch_ocpp_metadata,
    fetch_ocpp_readings_in_range,
    fetch_unsent_control_responses,
    fetch_unsent_dynamic_price_responses,
    update_active_default,
    upsert_control_responses,
    upsert_controls,
    upsert_dynamic_price_responses,
    upsert_dynamic_prices,
)
from cactus_juice.csipaus.config import CSIPAusContext
from cactus_juice.csipaus.server import get_resource, paginate_list_resource_items, submit_resource
from cactus_juice.db import DatabaseConnection
from cactus_juice.mapping import (
    SUPPORTED_READING_TYPES,
    MirrorUsagePointMrids,
    create_location_mup,
    csipaus_controls_to_responses,
    csipaus_prices_to_responses,
    csipaus_response_to_response,
    default_dercontrols_to_values,
    dercontrol_to_csipaus_control,
    generate_mup_mrids,
    ocpp_metadata_to_sep2,
    ocpp_readings_to_submit_mmr,
    previous_post_period,
    time_tariff_interval_to_csipaus_price,
)
from cactus_juice.model import CSIPAusControl, CSIPAusDynamicPrice

logger = logging.getLogger(__name__)

MIN_DATE = datetime(1000, 1, 1, tzinfo=UTC)  # We just want a TZ aware "minimum" date
DEFAULT_POLL_RATE = timedelta(minutes=15)
DEFAULT_POST_RATE_SECONDS = 900  # 15 minutes


@dataclass(slots=True)
class Pollable:
    last_poll: datetime
    poll_rate: timedelta

    last_post: datetime
    post_rate: timedelta


@dataclass(slots=True)
class PollableResource(Pollable):
    href: str

    @staticmethod
    def new_instance(href: str) -> "PollableResource":
        return PollableResource(MIN_DATE, DEFAULT_POLL_RATE, MIN_DATE, DEFAULT_POLL_RATE, href=href)


@dataclass(slots=True)
class PollableResources(Pollable):
    hrefs: list[str]

    @staticmethod
    def new_instance(hrefs: list[str]) -> "PollableResources":
        return PollableResources(MIN_DATE, DEFAULT_POLL_RATE, MIN_DATE, DEFAULT_POLL_RATE, hrefs=hrefs)


@dataclass(slots=True)
class ClientState:
    """Encapsulates all the state the client needs to know about the CSIP-Aus server and its polling behaviour.

    This will assume a single EndDevice to manage / in band register with no subscriptions

    It should allow (mostly) correct polling/posting timings"""

    context: CSIPAusContext
    db: DatabaseConnection

    dcap: PollableResource

    edevl: PollableResource | None
    edev_href: str | None  # The single EndDevice that is matched to this client

    mupl: PollableResource | None
    mup_href_by_location: dict[CSIPAusReadingLocation, str]
    mup_device_mrids: MirrorUsagePointMrids
    mup_site_mrids: MirrorUsagePointMrids

    fsal: PollableResource | None  # Only have 1 to manage due to managing a single EndDevice

    derl: PollableResource | None
    derl_last_change_time: datetime | None  # The last changed time - used for identifying changes

    derpl: PollableResources | None  # We may have multiple lists via FSAs

    tpl: PollableResources | None  # We may have multiple lists via FSAs

    def next_poll_post(self) -> datetime:
        """Calculates the next moment a poll/post should occur."""

        def _candidate_poll(p: Pollable | None) -> datetime | None:
            return None if p is None else p.last_poll + p.poll_rate

        def _candidate_post(p: Pollable | None) -> datetime | None:
            return None if p is None else p.last_post + p.post_rate

        candidate_times = [
            _candidate_poll(self.dcap),
            _candidate_poll(self.edevl),
            _candidate_poll(self.mupl),
            _candidate_post(self.mupl),
            _candidate_poll(self.fsal),
            _candidate_poll(self.derl),
            _candidate_poll(self.derpl),
            _candidate_poll(self.tpl),
        ]

        # The default should never occur as dcap is mandatory - but just in case
        return min((ct for ct in candidate_times if ct is not None), default=datetime.now(UTC))

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
            dcap=PollableResource.new_instance(context.dcap_path),
            edevl=None,
            edev_href=None,
            mupl=None,
            mup_href_by_location={},
            mup_device_mrids=mup_device_mrids,
            mup_site_mrids=mup_site_mrids,
            fsal=None,
            derl=None,
            derl_last_change_time=None,
            derpl=None,
            tpl=None,
        )


def upsert_href(existing: PollableResource | None, link: Link | None) -> PollableResource | None:
    """Updates a PollableResource from a parent's link. Returns the modified instance or potentially creates
    a new instance of the href has changed"""

    # Deleting a link
    if link is None:
        return None

    # If there is nothing there or a change in resource - create a new instance
    if existing is None or existing.href != link.href:
        return PollableResource(
            MIN_DATE,
            DEFAULT_POLL_RATE,
            MIN_DATE,
            DEFAULT_POLL_RATE,
            link.href,
        )

    # Otherwise no change
    return existing


def upsert_hrefs(existing: PollableResources | None, links: Iterable[Link | None]) -> PollableResources | None:
    """Updates a PollableResources from a parent's set of links. Returns the modified instance or potentially creates
    a new instance if the hrefs have changed"""

    # Deleting a link
    hrefs = [link.href for link in links if link]
    if not hrefs:
        return None

    # If there is nothing there or a change in resource - create a new instance
    if existing is None or existing.hrefs != hrefs:
        return PollableResources(
            MIN_DATE,
            DEFAULT_POLL_RATE,
            MIN_DATE,
            DEFAULT_POLL_RATE,
            hrefs,
        )

    # Otherwise no change
    return existing


def calculate_poll_rate(poll_rates: Iterable[int | None], *parents: Pollable | None) -> timedelta:
    actual_poll_rates = [pr for pr in poll_rates if pr is not None]
    if actual_poll_rates:
        return timedelta(seconds=min(actual_poll_rates))
    else:
        for parent in parents:
            if parent is not None:
                return parent.poll_rate
    return DEFAULT_POLL_RATE


def upsert_poll_rate(
    existing: PollableResource | None, href: str, poll_rate_seconds: int | None, *parents: Pollable | None
) -> PollableResource:
    """Updates poll rate for existing in place if it exists (and returns it), otherwise creates a new instance and
    returns that instead"""
    poll_rate = calculate_poll_rate((poll_rate_seconds,), *parents)
    # If there is nothing there or a change in resource - create a new instance
    if existing is None or existing.href != href:
        return PollableResource(
            MIN_DATE,
            poll_rate,
            MIN_DATE,
            DEFAULT_POLL_RATE,
            href,
        )

    # Otherwise update in place
    existing.poll_rate = poll_rate
    return existing


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
    state.edevl = upsert_href(state.edevl, response.EndDeviceListLink)
    state.mupl = upsert_href(state.mupl, response.MirrorUsagePointListLink)

    # Mark the poll as being completed
    state.dcap = upsert_poll_rate(state.dcap, state.dcap.href, response.pollRate)
    state.dcap.last_poll = now


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
    if state.derl:
        state.derl.last_poll = MIN_DATE
    if state.mupl:
        state.mupl.last_poll = MIN_DATE
    if state.fsal:
        state.fsal.last_poll = MIN_DATE
    if state.derpl:
        state.derpl.last_poll = MIN_DATE
    if state.tpl:
        state.tpl.last_poll = MIN_DATE

    return created_edev


async def poll_end_device_list(state: ClientState, now: datetime) -> None:
    """Polls EndDeviceList (if discovered) - ensures EndDevice existence, updates links to FSAs / DERs"""
    if state.edevl is None:
        logger.info("No EndDeviceList discovered - unable to poll.")
        return
    logger.info(f"Polling EndDeviceList {state.edevl.href}")

    # poll the list
    edevl_response = await paginate_list_resource_items(
        EndDeviceListResponse,
        state.context.http,
        state.edevl.href,
        page_size=100,
        item_callback=lambda edevl: cast(EndDeviceListResponse, edevl).EndDevice,
    )

    # Look for our EndDevice - registering if required
    existing_edev = first_matching_caseless(
        edevl_response.items, state.context.edev_lfdi, lambda edev: cast(EndDeviceResponse, edev).lFDI
    )
    if existing_edev is None:
        existing_edev = await in_band_register(state, state.edevl.href)

    # Update the state
    state.edevl = upsert_poll_rate(state.edevl, state.edevl.href, edevl_response.poll_rate_seconds, state.dcap)
    state.edev_href = existing_edev.href
    state.fsal = upsert_href(state.fsal, existing_edev.FunctionSetAssignmentsListLink)
    state.derl = upsert_href(state.derl, existing_edev.DERListLink)
    state.edevl.last_poll = now


async def poll_mup_list(state: ClientState, now: datetime) -> None:
    """Polls the MirrorUsagePointList - ensures the existence of MUPs for the EndDevice"""

    if state.mupl is None:
        logger.info("No MirrorUsagePointList href discovered - unable to poll MUP list.")
        return

    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll MUP list.")
        state.mupl.last_poll = now  # We count this as a poll
        return

    mups = await paginate_list_resource_items(
        MirrorUsagePointList,
        state.context.http,
        state.mupl.href,
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
            state.mupl.href,
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
            state.mupl.href,
            create_location_mup(CSIPAusReadingLocation.Device, state.mup_device_mrids, state.context.edev_lfdi),
        )
        logger.info(f"Created Device MUP {mup_href} with mRID {state.mup_device_mrids.mup_mrid}")
        device_mup = await get_resource(MirrorUsagePoint, state.context.http, mup_href)
        state.mup_href_by_location[CSIPAusReadingLocation.Device] = mup_href

    # Update state
    state.mupl = upsert_poll_rate(state.mupl, state.mupl.href, mups.poll_rate_seconds, state.dcap)

    # We simplify MUP post rates into a single value
    state.mupl.post_rate = timedelta(
        seconds=min(device_mup.postRate or DEFAULT_POST_RATE_SECONDS, site_mup.postRate or DEFAULT_POST_RATE_SECONDS)
    )
    state.mupl.last_poll = now


async def post_mup_list(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """POSTs the last postRate readings in a CSIP-Aus compatible form"""

    if state.mupl is None:
        logger.info("No MirrorUsagePointList href discovered - unable to poll MUP list.")
        return

    # Collect and send the readings
    device_mup_href = state.mup_href_by_location.get(CSIPAusReadingLocation.Device)
    site_mup_href = state.mup_href_by_location.get(CSIPAusReadingLocation.Site)
    if device_mup_href is None or site_mup_href is None:
        logger.info(f"No MirrorUsagePoint for Device/Site location(s). Skipping readings {state.mup_href_by_location}")
        state.mupl.last_post = now  # We count this as a post
        return

    readings_from = previous_post_period(now, state.mupl.post_rate)
    readings_to = readings_from + state.mupl.post_rate
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
        logger.info(f"No device readings from {readings_from} to {readings_to} to submit to {device_mup_href}")
    else:
        logger.info(f"Submitting device readings from {readings_from} to {readings_to} to {device_mup_href}")
        await submit_resource(
            state.context.http, HTTPMethod.POST, device_mup_href, device_readings, no_location_header=True
        )

    # update state
    state.mupl.last_post = now


async def post_der_metadata(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Updates the DER Metadata (DERSettings, DERStatus, DERCapability) based on what in the DB"""

    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll DERList.")
        return

    if state.derl is None:
        logger.info("No DERList href discovered - unable to poll.")
        return

    # Get DER
    der_items = await paginate_list_resource_items(
        DERListResponse, state.context.http, state.derl.href, 100, lambda derl: cast(DERListResponse, derl).DER_
    )

    if not der_items.items:
        logger.info("No DER entry in DERList - unable to update DERList.")
        state.derl.last_poll = now
        return

    # Get metadata
    metadata = await fetch_ocpp_metadata(session)
    if metadata is None:
        logger.info("No device metadata available - unable to update DERList.")
        state.derl.last_poll = now
        return

    if metadata.created_at == state.derl_last_change_time:
        logger.info("Device metadata the same as previous poll. Skipping updates.")
        state.derl.last_poll = now
        return

    # Update DERCapability / DERSettings / DERStatus
    der = der_items.items[0]
    capability, settings, status = ocpp_metadata_to_sep2(metadata)

    if capability is not None:
        if der.DERCapabilityLink is None:
            logger.info(f"No DERCapabilityLink for der {der.href} in {state.derl.href}. Skipping update")
        else:
            logger.info(f"Updating DERCapability for der {der.href} in {state.derl.href}.")
            await submit_resource(
                state.context.http, HTTPMethod.PUT, der.DERCapabilityLink.href, capability, no_location_header=True
            )

    if settings is not None:
        if der.DERSettingsLink is None:
            logger.info(f"No DERSettingsLink for der {der.href} in {state.derl.href}. Skipping update")
        else:
            logger.info(f"Updating DERSettings for der {der.href} in {state.derl.href}.")
            await submit_resource(
                state.context.http, HTTPMethod.PUT, der.DERSettingsLink.href, settings, no_location_header=True
            )

    if status is not None:
        if der.DERStatusLink is None:
            logger.info(f"No DERStatusLink for der {der.href} in {state.derl.href}. Skipping update")
        else:
            logger.info(f"Updating DERStatus for der {der.href} in {state.derl.href}.")
            await submit_resource(
                state.context.http, HTTPMethod.PUT, der.DERStatusLink.href, status, no_location_header=True
            )

    # Update state
    state.derl = upsert_poll_rate(state.derl, state.derl.href, der_items.poll_rate_seconds, state.edevl, state.dcap)
    state.derl.last_poll = now
    state.derl_last_change_time = metadata.created_at


async def poll_fsa_list(state: ClientState, now: datetime) -> None:
    """Polls an EndDevice FunctionSetAssignmentList - updating the DERProgramList / TariffProfileList hrefs"""
    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll FunctionSetAssignmentList.")
        return

    if state.fsal is None:
        logger.info("No FunctionSetAssignmentList href discovered - unable to poll.")
        return

    fsas = await paginate_list_resource_items(
        FunctionSetAssignmentsListResponse,
        state.context.http,
        state.fsal.href,
        100,
        lambda fsal: cast(FunctionSetAssignmentsListResponse, fsal).FunctionSetAssignments,
    )

    # Update state
    state.fsal = upsert_poll_rate(state.fsal, state.fsal.href, fsas.poll_rate_seconds, state.edevl, state.dcap)
    state.derpl = upsert_hrefs(state.derpl, (fsa.DERProgramListLink for fsa in fsas.items))
    state.tpl = upsert_hrefs(state.tpl, (fsa.TariffProfileListLink for fsa in fsas.items))

    state.fsal.last_poll = now


async def poll_derprogram_list(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Polls the current DERProgram lists - writing/updating any controls into the DB"""

    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll DERProgramList.")
        return

    if not state.derpl:
        logger.info("No DERProgramList href(s) discovered - unable to poll.")
        return

    # Walk all the DERPrograms - looking for defaults and DERControls
    all_poll_rates: list[int] = []
    all_default_primacies: list[tuple[int, DefaultDERControl]] = []
    all_controls: list[CSIPAusControl] = []
    for derpl_href in state.derpl.hrefs:
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
    state.derpl.poll_rate = calculate_poll_rate(all_poll_rates, state.fsal, state.edevl, state.dcap)
    state.derpl.last_poll = now


async def poll_tariff_list(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Polls the discovered TariffProfile lists and their associated TimeTariffInterval/RateComponents. Will
    create CSIPAusDynamicPrice records (and their responses) in the DB."""
    if state.edev_href is None:
        logger.info("No EndDevice registered - unable to poll TariffProfileList.")
        return

    if not state.tpl:
        logger.info("No TariffProfileList href(s) discovered - unable to poll.")
        return

    # Walk all the TariffProfiles via combined list - looking for TimeTariffIntervals
    all_poll_rates: list[int] = []
    rate_component_by_href: dict[str, RateComponentResponse] = {}
    reading_type_by_href: dict[str, ReadingType] = {}
    all_dynamic_prices: list[CSIPAusDynamicPrice] = []
    for tpl_href in state.tpl.hrefs:
        tps = await paginate_list_resource_items(
            TariffProfileListResponse,
            state.context.http,
            tpl_href,
            100,
            lambda derpl: cast(TariffProfileListResponse, derpl).TariffProfile,
        )
        if tps.poll_rate_seconds is not None:
            all_poll_rates.append(tps.poll_rate_seconds)
        for tp in tps.items:
            primacy = tp.primacyType
            currency_pow10 = tp.pricePowerOfTenMultiplier or 0

            if not tp.CombinedTimeTariffIntervalListLink:
                logger.info(f"TariffProfile {tp.href} has no CombinedTimeTariffIntervalListLink")
                continue

            ttis = await paginate_list_resource_items(
                TimeTariffIntervalListResponse,
                state.context.http,
                tp.CombinedTimeTariffIntervalListLink.href,
                100,
                lambda derpl: cast(TimeTariffIntervalListResponse, derpl).TimeTariffInterval,
            )

            # Strictly speaking - we should be splitting TTI discovery from TP discovery on seperate polls
            # but we are simplifying things here for this demonstration
            if ttis.poll_rate_seconds is not None:
                all_poll_rates.append(ttis.poll_rate_seconds)

            for tti in ttis.items:
                if not tti.RateComponentLink:
                    logger.info(f"TariffProfile {tp.href} has TimeTariffInterval {tti.href} with no RateComponentLink")
                    continue

                # Cache the RateComponent lookups
                rc = rate_component_by_href.get(tti.RateComponentLink.href)
                if rc is None:
                    rc = await get_resource(RateComponentResponse, state.context.http, tti.RateComponentLink.href)
                    rate_component_by_href[tti.RateComponentLink.href] = rc
                rc_rt = reading_type_by_href.get(rc.ReadingTypeLink.href)
                if rc_rt is None:
                    rc_rt = await get_resource(ReadingType, state.context.http, rc.ReadingTypeLink.href)
                    reading_type_by_href[rc.ReadingTypeLink.href] = rc_rt

                # See if this is a dynamic energy price or something else
                dynamic_price = time_tariff_interval_to_csipaus_price(primacy, currency_pow10, rc_rt, tti)
                if dynamic_price is not None:
                    all_dynamic_prices.append(dynamic_price)

    # Persist all the data we just scraped - we have to carefully update things - these crud functions will ensure
    # we correctly insert/update the records and don't thrash the DB if we keep polling the same data.
    logger.info(f"Updating with {len(all_dynamic_prices)} TimeTariffIntervals")
    await upsert_dynamic_prices(session, all_dynamic_prices)

    # next we want to queue up some responses - these require the PK of the CSIPAusDynamicPirce as well as the ACTUAL
    # values for cancelled so we need to go via the DB. The upsert will NOT duplicate responses so
    # we're free to send "everything" down
    db_prices = await fetch_dynamic_prices_with_mrids(session, (c.mrid for c in all_dynamic_prices))
    responses = csipaus_prices_to_responses(db_prices, state.context.edev_lfdi)
    await upsert_dynamic_price_responses(session, responses)

    # Update the state with the info that we polled
    state.tpl.poll_rate = calculate_poll_rate(all_poll_rates, state.fsal, state.edevl, state.dcap)
    state.tpl.last_poll = now


async def post_unsent_responses(state: ClientState, session: AsyncSession, now: datetime) -> None:
    """Selects all unsent Responses that are due to send - sends them and then marks the records as sent. This isn't
    required to be done on a regular schedule. Will do nothing if there are no unsent responses."""

    # Control responses
    control_responses = await fetch_unsent_control_responses(session, now, include_control=True)
    logger.info(f"Found {len(control_responses)} unsent DERControl Responses to send")
    for ctrl_response in control_responses:
        if ctrl_response.control.reply_to:
            body = csipaus_response_to_response(ctrl_response, subject_mrid=ctrl_response.control.mrid)
            await submit_resource(
                state.context.http, HTTPMethod.POST, ctrl_response.control.reply_to, body, no_location_header=True
            )
            ctrl_response.sent_at = now

    # Price responses
    price_responses = await fetch_unsent_dynamic_price_responses(session, now, include_dynamic_price=True)
    logger.info(f"Found {len(price_responses)} unsent TimeTariffInterval Responses to send")
    for price_response in price_responses:
        if price_response.dynamic_price.reply_to:
            body = csipaus_response_to_response(price_response, subject_mrid=price_response.dynamic_price.mrid)
            await submit_resource(
                state.context.http,
                HTTPMethod.POST,
                price_response.dynamic_price.reply_to,
                body,
                no_location_header=True,
            )
            price_response.sent_at = now

    await session.flush()


def poll_required(pollable: Pollable | None, now: datetime) -> bool:
    if pollable is None:
        return False
    return now >= (pollable.last_poll + pollable.poll_rate)


def post_required(pollable: Pollable | None, now: datetime) -> bool:
    if pollable is None:
        return False
    return now >= (pollable.last_post + pollable.post_rate)


async def run_responses(state: ClientState) -> None:
    """Runs sending every unsent Response to the server"""
    now = datetime.now(UTC)
    async with state.db.session_maker() as session:
        await post_unsent_responses(state, session, now)
        await session.commit()


async def run_polls(state: ClientState, min_wait: timedelta = timedelta(seconds=10)) -> datetime:
    """Runs every required poll of server, updating state as required. Will manage the creation of db sessions

    Returns the next "wakeup" time for the next set of polls

    Will not poll/post resources that have been polled recently

    Will NOT send responses"""

    now = datetime.now(UTC)

    if poll_required(state.dcap, now):
        await poll_dcap(state, now)

    if poll_required(state.edevl, now):
        await poll_end_device_list(state, now)

    if poll_required(state.derl, now):
        async with state.db.session_maker() as session:
            await post_der_metadata(state, session, now)
            await session.commit()

    if poll_required(state.mupl, now):
        await poll_mup_list(state, now)

    if post_required(state.mupl, now):
        async with state.db.session_maker() as session:
            await post_mup_list(state, session, now)
            await session.commit()

    if poll_required(state.fsal, now):
        await poll_fsa_list(state, now)

    if poll_required(state.derpl, now):
        async with state.db.session_maker() as session:
            await poll_derprogram_list(state, session, now)
            await session.commit()

    if poll_required(state.tpl, now):
        async with state.db.session_maker() as session:
            await poll_tariff_list(state, session, now)
            await session.commit()

    # Figure out our next call to this function
    return max(state.next_poll_post(), datetime.now(UTC) + min_wait)
