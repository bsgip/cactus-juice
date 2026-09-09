import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import overload

from cactus_test_definitions.csipaus import (
    CSIPAusReadingLocation,
    CSIPAusReadingType,
)
from envoy_schema.server.schema.sep2.der import (
    DefaultDERControl,
    DERCapability,
    DERControlResponse,
    DERControlType,
    DERSettings,
    DERStatus,
    DERType,
    DOESupportedMode,
    VPPControlType,
)
from envoy_schema.server.schema.sep2.der_control_types import ActivePower, VoltageRMS
from envoy_schema.server.schema.sep2.event import EventStatusType
from envoy_schema.server.schema.sep2.metering import Reading, ReadingType
from envoy_schema.server.schema.sep2.metering_mirror import (
    MirrorMeterReading,
    MirrorMeterReadingListRequest,
    MirrorUsagePoint,
    MirrorUsagePointRequest,
)
from envoy_schema.server.schema.sep2.pricing import TimeTariffIntervalResponse
from envoy_schema.server.schema.sep2.response import Response, ResponseType
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    DateTimeIntervalType,
    FlowDirectionType,
    KindType,
    RoleFlagsType,
    ServiceKind,
    UomType,
)

from cactus_juice.csipaus.dto import DefaultValues
from cactus_juice.error import BaseJuiceError
from cactus_juice.model import (
    CSIPAusControl,
    CSIPAusControlResponse,
    CSIPAusDynamicPrice,
    CSIPAusDynamicPriceResponse,
    OCPPMetadata,
    OCPPReading,
)

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


@dataclass(frozen=True, slots=True)
class MirrorUsagePointMrids:
    mup_mrid: str
    mmr_mrids: dict[CSIPAusReadingType, str]


def generate_hashed_mrid(seed: str, pen: int) -> str:
    """Generates a 32 character mrid with the last 8 characters being the pen (0 padded)"""
    hash = hashlib.md5(seed.encode(), usedforsecurity=False)
    return f"{hash.hexdigest()[:24]}{pen:08}".upper()


def generate_mmr_mrids(
    mup_mrid: str,
    reading_types: list[CSIPAusReadingType],
    pen: int,
) -> dict[CSIPAusReadingType, str]:
    """Generates mrids for all MirrorMeterReadings that lives under a MirrorUsagePoint with mup_mrid"""

    return dict((rt, generate_hashed_mrid(mup_mrid + str(rt), pen)) for rt in reading_types)


def generate_mup_mrids(
    location: CSIPAusReadingLocation, reading_types: list[CSIPAusReadingType], client_pen: int
) -> MirrorUsagePointMrids:
    """A deterministic set of calculations that always yields the same MRIDs for the same inputs but also varies
    all values for any variance (basically hash derived). MUP mrid can be set explicitly by set_mup_mrid."""
    mup_mrid = generate_hashed_mrid(str(location) + "|".join(sorted(reading_types)), client_pen)

    mmr_mrids_by_rt = generate_mmr_mrids(mup_mrid, reading_types, client_pen)

    return MirrorUsagePointMrids(mup_mrid=mup_mrid, mmr_mrids=mmr_mrids_by_rt)


def generate_reading_type_values(  # noqa: C901
    rt: CSIPAusReadingType,
) -> tuple[UomType, KindType, DataQualifierType]:
    """Generates a CSIP-Aus compliant set of reading type values based on the associated test definition enum"""
    match rt:
        case CSIPAusReadingType.ActivePowerAverage:
            return (UomType.REAL_POWER_WATT, KindType.POWER, DataQualifierType.AVERAGE)
        case CSIPAusReadingType.ActivePowerInstantaneous:
            return (UomType.REAL_POWER_WATT, KindType.POWER, DataQualifierType.STANDARD)
        case CSIPAusReadingType.ActivePowerMaximum:
            return (UomType.REAL_POWER_WATT, KindType.POWER, DataQualifierType.MAXIMUM)
        case CSIPAusReadingType.ActivePowerMinimum:
            return (UomType.REAL_POWER_WATT, KindType.POWER, DataQualifierType.MINIMUM)

        case CSIPAusReadingType.ReactivePowerAverage:
            return (
                UomType.REACTIVE_POWER_VAR,
                KindType.POWER,
                DataQualifierType.AVERAGE,
            )
        case CSIPAusReadingType.ReactivePowerInstantaneous:
            return (
                UomType.REACTIVE_POWER_VAR,
                KindType.POWER,
                DataQualifierType.STANDARD,
            )
        case CSIPAusReadingType.ReactivePowerMaximum:
            return (
                UomType.REACTIVE_POWER_VAR,
                KindType.POWER,
                DataQualifierType.MAXIMUM,
            )
        case CSIPAusReadingType.ReactivePowerMinimum:
            return (
                UomType.REACTIVE_POWER_VAR,
                KindType.POWER,
                DataQualifierType.MINIMUM,
            )

        case CSIPAusReadingType.FrequencyAverage:
            return (UomType.FREQUENCY_HZ, KindType.POWER, DataQualifierType.AVERAGE)
        case CSIPAusReadingType.FrequencyInstantaneous:
            return (UomType.FREQUENCY_HZ, KindType.POWER, DataQualifierType.STANDARD)
        case CSIPAusReadingType.FrequencyMaximum:
            return (UomType.FREQUENCY_HZ, KindType.POWER, DataQualifierType.MAXIMUM)
        case CSIPAusReadingType.FrequencyMinimum:
            return (UomType.FREQUENCY_HZ, KindType.POWER, DataQualifierType.MINIMUM)

        case CSIPAusReadingType.VoltageSinglePhaseAverage:
            return (UomType.VOLTAGE, KindType.POWER, DataQualifierType.AVERAGE)
        case CSIPAusReadingType.VoltageSinglePhaseInstantaneous:
            return (UomType.VOLTAGE, KindType.POWER, DataQualifierType.STANDARD)
        case CSIPAusReadingType.VoltageSinglePhaseMaximum:
            return (UomType.VOLTAGE, KindType.POWER, DataQualifierType.MAXIMUM)
        case CSIPAusReadingType.VoltageSinglePhaseMinimum:
            return (UomType.VOLTAGE, KindType.POWER, DataQualifierType.MINIMUM)

        case _:
            raise BaseJuiceError(f"No ReadingType mapping configured for {rt}.")


def generate_role_flags(location: CSIPAusReadingLocation) -> RoleFlagsType:
    match location:
        case CSIPAusReadingLocation.Device:
            return RoleFlagsType.IS_MIRROR | RoleFlagsType.IS_DER | RoleFlagsType.IS_SUBMETER
        case CSIPAusReadingLocation.Site:
            return RoleFlagsType.IS_MIRROR | RoleFlagsType.IS_PREMISES_AGGREGATION_POINT

        case _:
            raise BaseJuiceError(f"No CSIPAusReadingLocation mapping configured for {location}.")


def previous_post_period(dt: datetime, post_rate: timedelta) -> datetime:
    """
    Return the most recent datetime that aligns to a "post rate period"
    boundary, given a tz-aware datetime and a timedelta that evenly
    divides 24 hours (e.g. 1 minute, 5 minutes, 15 minutes, 1 hour).

    Boundaries are aligned to local midnight (00:00:00) of `dt`'s date,
    in `dt`'s own timezone.

    Example:
        previous_post_period(dt=13:22:33, post_rate=5min) -> 13:20:00
        previous_post_period(dt=13:22:33, post_rate=15min) -> 13:15:00
    """
    if dt.tzinfo is None:
        raise ValueError("dt must be timezone-aware")

    seconds_per_day = 24 * 60 * 60
    rate_seconds = post_rate.total_seconds()

    if rate_seconds <= 0:
        raise ValueError("post_rate must be a positive timedelta")
    if seconds_per_day % rate_seconds != 0:
        raise ValueError("post_rate must evenly divide a 24 hour period")

    # Midnight for the same calendar date, same tzinfo (avoids DST-shift
    # issues that arise from subtracting a timedelta across a DST boundary).
    midnight = datetime.combine(dt.date(), time.min, tzinfo=dt.tzinfo)

    elapsed = (dt - midnight).total_seconds()
    periods_elapsed = int(elapsed // rate_seconds)

    return midnight + timedelta(seconds=periods_elapsed * rate_seconds)


def value_to_sep2(value: float, pow10: int) -> int:
    decimal_power = pow(10, -pow10)
    return int(value * decimal_power)


@overload
def sep2_to_value(ap: None) -> None: ...
@overload
def sep2_to_value(ap: ActivePower) -> float: ...
def sep2_to_value(ap: ActivePower | None) -> float | None:
    if ap is None:
        return None

    return ap.value * pow(10, ap.multiplier)


@overload
def value_to_active_power(value: None) -> None: ...
@overload
def value_to_active_power(value: float) -> ActivePower: ...
def value_to_active_power(value: float | None) -> ActivePower | None:
    if value is None:
        return None
    else:
        pow10 = POW10_BY_READING_TYPE[CSIPAusReadingType.ActivePowerAverage]
        return ActivePower(value=value_to_sep2(value, pow10=pow10), multiplier=pow10)


def value_to_voltage(value: float | None) -> VoltageRMS | None:
    if value is None:
        return None
    else:
        pow10 = POW10_BY_READING_TYPE[CSIPAusReadingType.VoltageSinglePhaseAverage]
        return VoltageRMS(value=value_to_sep2(value, pow10=pow10), multiplier=pow10)


def ocpp_metadata_to_sep2(metadata: OCPPMetadata) -> tuple[DERCapability | None, DERSettings | None, DERStatus | None]:
    """Maps a OCPPMetadata into the equivalent sep2 DER metadata"""
    changed_time = int(metadata.created_at.timestamp())

    status = DERStatus(alarmStatus="00", readingTime=changed_time)
    capability: DERCapability | None = None
    settings: DERSettings | None = None
    if metadata.max_power_watts is None:
        return (capability, settings, status)

    supported_doe_modes = (
        DOESupportedMode.OP_MOD_EXPORT_LIMIT_W
        | DOESupportedMode.OP_MOD_GENERATION_LIMIT_W
        | DOESupportedMode.OP_MOD_IMPORT_LIMIT_W
        | DOESupportedMode.OP_MOD_LOAD_LIMIT_W
    )
    supported_vpp_modes = VPPControlType(0)
    supported_der_controls = (
        DERControlType.OP_MOD_CONNECT
        | DERControlType.OP_MOD_ENERGIZE
        | DERControlType.CHARGE_MODE
        | DERControlType.DISCHARGE_MODE
        | DERControlType.OP_MOD_FIXED_W
        | DERControlType.OP_MOD_MAX_LIM_W
        | DERControlType.OP_MOD_TARGET_W
    )

    capability = DERCapability(
        # Mandatory
        type_=DERType.EVSE,
        modesSupported=f"{int(supported_der_controls):X}",
        rtgMaxW=value_to_active_power(metadata.max_power_watts),
        doeModesSupported=f"{int(supported_doe_modes):X}",
        vppModesSupported=f"{int(supported_vpp_modes):X}",
        # Optionals
        rtgMaxV=value_to_voltage(metadata.max_voltage_volts),
        rtgMinV=value_to_voltage(metadata.min_voltage_volts),
        rtgMaxChargeRateW=value_to_active_power(metadata.max_charge_rate_watts),
        rtgMaxDischargeRateW=value_to_active_power(metadata.max_discharge_rate_watts),
    )

    settings = DERSettings(
        # Mandatory
        modesEnabled=f"{int(supported_der_controls):X}",
        setMaxW=value_to_active_power(metadata.max_power_watts),
        doeModesEnabled=f"{int(supported_doe_modes):X}",
        vppModesEnabled=f"{int(supported_vpp_modes):X}",
        updatedTime=changed_time,
        setGradW=27 if metadata.set_grad_w is None else int(metadata.set_grad_w),
        # Optionals
        setMaxV=value_to_voltage(metadata.max_voltage_volts),
        setMinV=value_to_voltage(metadata.min_voltage_volts),
        setMaxChargeRateW=value_to_active_power(metadata.max_charge_rate_watts),
        setMaxDischargeRateW=value_to_active_power(metadata.max_discharge_rate_watts),
    )

    return (capability, settings, status)


def _average_readings(readings: Iterable[OCPPReading], key: Callable[[OCPPReading], float | None]) -> float | None:
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


def create_location_mup(
    location: CSIPAusReadingLocation, mrids: MirrorUsagePointMrids, device_lfdi: str
) -> MirrorUsagePoint:
    """Creates a MUP for creating the specified location when submitted to a CSIP-Aus server."""

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

    return MirrorUsagePointRequest(
        roleFlags=f"{int(role_flags):04X}",
        deviceLFDI=device_lfdi,
        mRID=mrids.mup_mrid,
        status=1,
        mirrorMeterReadings=mmrs,
        serviceCategoryKind=ServiceKind.ELECTRICITY,
    )


def ocpp_readings_to_submit_mmr(
    readings_from: datetime,
    readings_to: datetime,
    all_readings: Sequence[OCPPReading],
    site_mrids: MirrorUsagePointMrids,
    device_mrids: MirrorUsagePointMrids,
) -> tuple[MirrorMeterReadingListRequest | None, MirrorMeterReadingListRequest | None]:
    """Given a set of database readings - convert them to a site / device level MUP submission

    returns [site_mmr_list, device_mmr_list]"""

    if not all_readings:
        return (None, None)

    avg_import_watts = _average_readings(all_readings, lambda r: r.import_active_power_watts)
    avg_export_watts = _average_readings(all_readings, lambda r: r.export_active_power_watts)
    avg_watts = (avg_import_watts or 0.0) - (avg_export_watts or 0.0)

    avg_import_var = _average_readings(all_readings, lambda r: r.import_reactive_power_var)
    avg_export_var = _average_readings(all_readings, lambda r: r.export_reactive_power_var)
    avg_var = (avg_import_var or 0.0) - (avg_export_var or 0.0)

    avg_volts = _average_readings(all_readings, lambda r: r.voltage_volts)
    avg_hz = _average_readings(all_readings, lambda r: r.frequency_hz)

    # Build our post packet for site readings (we will do everything at the site level)
    site_mmrs: list[MirrorMeterReading] = []
    postrate = readings_to - readings_from
    _append_mmr_value(site_mmrs, site_mrids, CSIPAusReadingType.ActivePowerAverage, avg_watts, readings_from, postrate)
    _append_mmr_value(site_mmrs, site_mrids, CSIPAusReadingType.ReactivePowerAverage, avg_var, readings_from, postrate)
    _append_mmr_value(
        site_mmrs, site_mrids, CSIPAusReadingType.VoltageSinglePhaseAverage, avg_volts, readings_from, postrate
    )
    _append_mmr_value(site_mmrs, site_mrids, CSIPAusReadingType.FrequencyAverage, avg_hz, readings_from, postrate)

    # Send the readings
    if len(site_mmrs) == 0:
        return (None, None)
    else:
        # We dont map device level readings
        return (MirrorMeterReadingListRequest(mirrorMeterReadings=site_mmrs), None)


def default_dercontrols_to_values(dderc_primacy_vals: Iterable[tuple[int, DefaultDERControl]]) -> DefaultValues:
    """Given a set of DefaultDERControls tupled with their primacy - combine them into a single set of DefaultValues"""

    connect: bool | None = None
    energize: bool | None = None
    import_limit_watts: int | None = None
    export_limit_watts: int | None = None
    load_limit_watts: int | None = None
    generation_limit_watts: int | None = None
    storage_target_watts: int | None = None
    ramp_percent_max_second_hundredths: int | None = None

    # Enumerate from low priority (high primacy) - updating only the set values
    for _, dderc in sorted(dderc_primacy_vals, key=lambda e: e[0], reverse=True):
        if dderc.DERControlBase_.opModConnect is not None:
            connect = dderc.DERControlBase_.opModConnect
        if dderc.DERControlBase_.opModEnergize is not None:
            energize = dderc.DERControlBase_.opModEnergize
        if dderc.DERControlBase_.opModImpLimW is not None:
            import_limit_watts = int(sep2_to_value(dderc.DERControlBase_.opModImpLimW))
        if dderc.DERControlBase_.opModExpLimW is not None:
            export_limit_watts = int(sep2_to_value(dderc.DERControlBase_.opModExpLimW))
        if dderc.DERControlBase_.opModLoadLimW is not None:
            load_limit_watts = int(sep2_to_value(dderc.DERControlBase_.opModLoadLimW))
        if dderc.DERControlBase_.opModGenLimW is not None:
            generation_limit_watts = int(sep2_to_value(dderc.DERControlBase_.opModGenLimW))
        if dderc.DERControlBase_.opModStorageTargetW is not None:
            storage_target_watts = int(sep2_to_value(dderc.DERControlBase_.opModStorageTargetW))
        if dderc.setGradW is not None:
            ramp_percent_max_second_hundredths = dderc.setGradW

    return DefaultValues(
        connect=connect,
        energize=energize,
        import_limit_watts=import_limit_watts,
        export_limit_watts=export_limit_watts,
        load_limit_watts=load_limit_watts,
        generation_limit_watts=generation_limit_watts,
        storage_target_watts=storage_target_watts,
        ramp_percent_max_second_hundredths=ramp_percent_max_second_hundredths,
    )


def _value_to_int(v: float | int | None) -> int | None:
    if v is None:
        return None
    return int(v)


def dercontrol_to_csipaus_control(derc: DERControlResponse, primacy: int) -> CSIPAusControl:
    """Maps a raw DERControl to the internal DB representation. The cancelled/superseded times will be set to
    now (if appropriate), NOT the actual time it was superseded as we only care about when WE discovered it"""

    # We mark something as cancelled/superseded based on when we saw it
    # The crud layer will ensure that the value will be write once
    cancelled_at = None
    if (
        derc.EventStatus_.currentStatus == EventStatusType.Cancelled
        or derc.EventStatus_.currentStatus == EventStatusType.CancelledWithRandomization
    ):
        cancelled_at = datetime.now(UTC)

    superseded_at = None
    if derc.EventStatus_.currentStatus == EventStatusType.Superseded:
        superseded_at = datetime.now(UTC)

    # We *should* be a little more granular here but we're just going to assume that ANY response required will
    # generate all responses.
    reply_to = None
    if derc.responseRequired and int(derc.responseRequired, 16):
        reply_to = derc.replyTo

    base = derc.DERControlBase_
    return CSIPAusControl(
        primacy=primacy,
        mrid=derc.mRID,
        started_at=datetime.fromtimestamp(derc.interval.start, tz=UTC),
        duration_seconds=derc.interval.duration,
        cancelled_at=cancelled_at,
        superseded_at=superseded_at,
        reply_to=reply_to,
        ramp_time_seconds=base.rampTms,
        connect=base.opModConnect,
        energize=base.opModEnergize,
        import_limit_watts=_value_to_int(sep2_to_value(base.opModImpLimW)),
        export_limit_watts=_value_to_int(sep2_to_value(base.opModExpLimW)),
        load_limit_watts=_value_to_int(sep2_to_value(base.opModLoadLimW)),
        generation_limit_watts=_value_to_int(sep2_to_value(base.opModGenLimW)),
        storage_target_watts=_value_to_int(sep2_to_value(base.opModStorageTargetW)),
    )


def csipaus_controls_to_responses(controls: Iterable[CSIPAusControl], edev_lfdi: str) -> list[CSIPAusControlResponse]:
    """Converts each control into a set of CSIPAusControlResponses that will be required to be sent. Does NOT
    factor in the current clock time, all required responses will be generated and returned.

    controls should be pulled from the DB directly - they will require PK / other DB generated fields to be set"""

    responses: list[CSIPAusControlResponse] = []
    for control in controls:
        if control.reply_to is None:
            continue

        # Every response needs to be received/started
        responses.append(
            CSIPAusControlResponse(
                control=control,
                response_status=ResponseType.EVENT_RECEIVED,
                end_device_lfdi=edev_lfdi,
                not_before=control.created_at,
                sent_at=None,
            )
        )
        responses.append(
            CSIPAusControlResponse(
                control=control,
                response_status=ResponseType.EVENT_STARTED,
                end_device_lfdi=edev_lfdi,
                not_before=control.started_at,
                sent_at=None,
            )
        )

        if control.cancelled_at:
            responses.append(
                CSIPAusControlResponse(
                    control=control,
                    response_status=ResponseType.EVENT_CANCELLED,
                    end_device_lfdi=edev_lfdi,
                    not_before=control.cancelled_at,
                    sent_at=None,
                )
            )
        if control.superseded_at:
            responses.append(
                CSIPAusControlResponse(
                    control=control,
                    response_status=ResponseType.EVENT_SUPERSEDED,
                    end_device_lfdi=edev_lfdi,
                    not_before=control.superseded_at,
                    sent_at=None,
                )
            )
        if control.superseded_at is None and control.cancelled_at is None:
            responses.append(
                CSIPAusControlResponse(
                    control=control,
                    response_status=ResponseType.EVENT_COMPLETED,
                    end_device_lfdi=edev_lfdi,
                    not_before=control.finished_at,
                    sent_at=None,
                )
            )
    return responses


def csipaus_response_to_response(
    response: CSIPAusControlResponse | CSIPAusDynamicPriceResponse, subject_mrid: str
) -> Response:
    """Maps a db response representation to the sep2 representation"""
    return Response(
        status=ResponseType(response.response_status),
        createdDateTime=int(response.not_before.timestamp()),
        endDeviceLFDI=response.end_device_lfdi,
        subject=subject_mrid,
    )


def calculate_dollars_kwh(encoded_price: int, wh_pow10: int, currency_pow10: int) -> Decimal:
    """Convert an integer-encoded price into a Decimal $/kWh price.

    Args:
        encoded_price: The raw integer price value.
        wh_pow10: Power-of-ten exponent of the energy unit relative to a
            watt-hour (e.g. 3 == kWh, 6 == MWh, 0 == Wh).
        currency_pow10: Power-of-ten exponent of the currency's base unit
            relative to one dollar (e.g. -2 == cents, 0 == dollars).

    Returns:
        A decimal.Decimal representing the price in dollars per kWh.
    """
    exponent = currency_pow10 - wh_pow10 + 3
    return Decimal(encoded_price).scaleb(exponent)


def time_tariff_interval_to_csipaus_price(
    primacy: int, currency_pow10: int, rc_rt: ReadingType, tti: TimeTariffIntervalResponse
) -> CSIPAusDynamicPrice | None:
    """Converts a TimeTariffInterval into a dynamic price. Can return None if the TTI is missing critical info or
    represents a periodic price (which cannot map to a CSIPAusDynamicPrice)"""

    # We only price real energy
    if rc_rt.uom != UomType.REAL_ENERGY_WATT_HOURS:
        return None

    # Find the basic price
    if (
        not tti.ConsumptionTariffIntervalListSummary
        or not tti.ConsumptionTariffIntervalListSummary.ConsumptionTariffInterval
    ):
        return None
    raw_price: int | None = None
    for cti in tti.ConsumptionTariffIntervalListSummary.ConsumptionTariffInterval:
        if cti.price is None:
            continue
        raw_price = cti.price
        if cti.startValue == 0:
            break
    if raw_price is None:
        return None

    price_kwh = calculate_dollars_kwh(
        encoded_price=raw_price, wh_pow10=rc_rt.powerOfTenMultiplier or 0, currency_pow10=currency_pow10
    )

    # We mark something as cancelled based on when we saw it
    # The crud layer will ensure that the value will be write once
    cancelled_at = None
    if (
        tti.EventStatus_.currentStatus == EventStatusType.Cancelled
        or tti.EventStatus_.currentStatus == EventStatusType.CancelledWithRandomization
    ):
        cancelled_at = datetime.now(UTC)

    # We *should* be a little more granular here but we're just going to assume that ANY response required will
    # generate all responses.
    reply_to = None
    if tti.responseRequired and int(tti.responseRequired, 16):
        reply_to = tti.replyTo

    return CSIPAusDynamicPrice(
        primacy=primacy,
        mrid=tti.mRID,
        duration_seconds=tti.interval.duration,
        started_at=datetime.fromtimestamp(tti.interval.start, tz=UTC),
        cancelled_at=cancelled_at,
        reply_to=reply_to,
        price_kwh=price_kwh,
    )


def csipaus_prices_to_responses(
    prices: Iterable[CSIPAusDynamicPrice], edev_lfdi: str
) -> list[CSIPAusDynamicPriceResponse]:
    """Converts each price into a set of CSIPAusDynamicPriceResponse that will be required to be sent. Does NOT
    factor in the current clock time, all required responses will be generated and returned.

    prices should be pulled from the DB directly - they will require PK / other DB generated fields to be set"""

    responses: list[CSIPAusDynamicPriceResponse] = []
    for price in prices:
        if price.reply_to is None:
            continue

        # Prices only sent responses for received/cancelled
        responses.append(
            CSIPAusDynamicPriceResponse(
                dynamic_price=price,
                response_status=ResponseType.EVENT_RECEIVED,
                end_device_lfdi=edev_lfdi,
                not_before=price.created_at,
                sent_at=None,
            )
        )
        if price.cancelled_at:
            responses.append(
                CSIPAusDynamicPriceResponse(
                    dynamic_price=price,
                    response_status=ResponseType.EVENT_CANCELLED,
                    end_device_lfdi=edev_lfdi,
                    not_before=price.cancelled_at,
                    sent_at=None,
                )
            )
    return responses
