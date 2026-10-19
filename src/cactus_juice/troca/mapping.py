from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from cactus_juice.csipaus.dto import ActiveValues, ScheduledControlValues
from cactus_juice.model import CSIPAusDynamicPrice, OCPPMetadata, OCPPReading
from cactus_juice.troca.models import (
    ActivePowerSchedule,
    Bounds,
    Evse,
    GetVariableData,
    GetVariableResult,
    MeteringReading,
    OcppAbsolutePriceSchedule,
    OcppChargingProfile,
    OcppChargingProfilePurpose,
    OcppChargingSchedule,
    OcppChargingSchedulePeriod,
    OcppComponent,
    OcppEvse,
    OcppPriceRule,
    OcppPriceRuleStack,
    OcppRationalNumber,
    OcppTarget,
    OcppVariable,
    SchedulePeriod,
    TEmsValue,
    TEmsValuePhase,
)

# Troca's session schedules express power in kW with the sign INVERTED relative to our import-positive convention
# (see ActivePowerSchedule) - so charging at 7kW is sent as -7.
TROCA_SCHEDULE_UNIT = "kW"
TROCA_SCHEDULE_WATTS_MULTIPLIER = -1 / 1000

# How far out the final, open-ended, period of a schedule is extended - this is what keeps the station following
# the last known control if we stop talking to Troca. Matches what Trialog used in their own example.
SCHEDULE_HORIZON = timedelta(days=365)

# The station reports SmartChargingCtrlr.PeriodsPerSchedule = 100 - anything past this is truncated (and will be
# pushed on a later sync, once the earlier periods have elapsed).
MAX_SCHEDULE_PERIODS = 100

# Setpoints are rounded to whole watts - avoids float noise causing spurious "schedule changed" re-pushes.
SETPOINT_KW_DECIMALS = 3

# Multipliers to convert a TEmsValue into W / VAR / V. A missing unit means the valueType's default (kW etc).
UNIT_MULTIPLIERS: dict[str, float] = {
    "kW": 1000,
    "kVAR": 1000,
    "kVA": 1000,
    "W": 1,
    "V": 1,
}
DEFAULT_UNIT_MULTIPLIER = 1000

# The OCPP EVSE number assumed when none is configured - the Trialog simulator has a single EVSE, numbered 1.
DEFAULT_OCPP_EVSE_NB = 1


def rated_power_variables(evse_nb: int = DEFAULT_OCPP_EVSE_NB) -> list[GetVariableData]:
    """The OCPP 2.x variables that might carry an EVSE's rated power, in order of preference. The Trialog simulator
    only answers ElectricalFeed.Power (EVSE.Power is reported as UnknownVariable)."""
    return [
        GetVariableData(
            component=OcppComponent(name="EVSE", evse=OcppEvse(id=evse_nb)),
            variable=OcppVariable(name="Power"),
            attribute_type="MaxSet",
        ),
        GetVariableData(component=OcppComponent(name="ElectricalFeed"), variable=OcppVariable(name="Power")),
    ]


# OCPP specifies Power variables in W, but the Trialog simulator reports ElectricalFeed.Power as "40" (which can
# only sensibly be kW). Any value below this threshold is assumed to be kW rather than W.
RATED_POWER_KW_THRESHOLD = 1000


def to_troca_timestamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_troca_timestamp(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _to_base_units(v: TEmsValue | None) -> float | None:
    """Converts a TEmsValue into its base unit (W / VAR / V)."""
    if v is None:
        return None
    return v.value * UNIT_MULTIPLIERS.get(v.unit or "", DEFAULT_UNIT_MULTIPLIER)


def _global_value(vp: TEmsValuePhase | None) -> float | None:
    return None if vp is None else _to_base_units(vp.global_)


def reading_to_db(mr: MeteringReading) -> OCPPReading:
    """Maps a reading from troca into an equivalent DB model. Troca's metering data is positive while charging
    (importing) and negative while discharging (exporting)."""

    import_watts: float | None = None
    export_watts: float | None = None
    active_watts = _global_value(mr.instantaneous_active_power)
    if active_watts is not None:
        import_watts = max(active_watts, 0.0)
        export_watts = max(-active_watts, 0.0)

    import_var: float | None = None
    export_var: float | None = None
    reactive_var = _global_value(mr.instantaneous_reactive_power)
    if reactive_var is not None:
        import_var = max(reactive_var, 0.0)
        export_var = max(-reactive_var, 0.0)

    return OCPPReading(
        reading_start=parse_troca_timestamp(mr.timestamp),
        import_active_power_watts=import_watts,
        export_active_power_watts=export_watts,
        import_reactive_power_var=import_var,
        export_reactive_power_var=export_var,
        voltage_volts=_global_value(mr.rms_voltage),
    )


def rated_power_watts(results: Sequence[GetVariableResult]) -> float | None:
    """Extracts the EVSE's rated power (in W) from a GetVariables response to rated_power_variables(), using the
    first variable (in rated_power_variables() order) the station accepted with a usable value."""

    for requested in rated_power_variables():
        for r in results:
            if not r.accepted or r.component.name != requested.component.name:
                continue
            if r.variable.name != requested.variable.name:
                continue
            try:
                value = float(r.attribute_value or "")
            except ValueError:
                continue
            if value <= 0:
                continue
            return value * 1000 if value < RATED_POWER_KW_THRESHOLD else value
    return None


def variables_to_metadata(results: Sequence[GetVariableResult]) -> OCPPMetadata | None:
    """Maps a GetVariables response to rated_power_variables() into an OCPPMetadata snapshot (or None if there's
    nothing usable). Only max_power_watts is available - the station exposes nothing for voltage bounds,
    separate charge/discharge rates or a power gradient."""

    max_power_watts = rated_power_watts(results)
    if max_power_watts is None:
        return None

    return OCPPMetadata(
        max_voltage_volts=None,
        min_voltage_volts=None,
        max_power_watts=max_power_watts,
        max_charge_rate_watts=None,
        max_discharge_rate_watts=None,
        set_grad_w=None,
    )


def find_evse(evses: Iterable[Evse], target: OcppTarget, evse_nb: int, evse_id: str | None = None) -> Evse | None:
    """Finds the Troca EVSE being controlled - by evse_id (Troca's UUID) if set, otherwise by the target station's
    name and evse_nb."""
    for e in evses:
        if evse_id is not None:
            if e.evse_id == evse_id:
                return e
        elif e.evse_uid is not None and (e.evse_uid.station_name, e.evse_uid.evse_nb) == (target.station_name, evse_nb):
            return e
    return None


def evse_to_metadata(evse: Evse) -> OCPPMetadata | None:
    """Maps the (kW) ratings Troca reports on an EVSE into an OCPPMetadata snapshot (or None if it has none). The
    EVSE's overall rated power is taken as the larger of its charge/discharge ratings."""

    charge_watts = None if evse.max_charge_power is None else evse.max_charge_power * 1000
    discharge_watts = None if evse.max_discharge_power is None else evse.max_discharge_power * 1000
    ratings = [w for w in (charge_watts, discharge_watts) if w is not None and w > 0]
    if not ratings:
        return None

    return OCPPMetadata(
        max_voltage_volts=None,
        min_voltage_volts=None,
        max_power_watts=max(ratings),
        max_charge_rate_watts=charge_watts,
        max_discharge_rate_watts=discharge_watts,
        set_grad_w=None,
    )


def values_to_charge_watts(values: ActiveValues, max_power_watts: float) -> float:
    """Maps CSIP-Aus derived control values into the fixed charge rate (W, import positive - so negative means
    discharge) the EV should be held at. Troca only accepts fixed setpoints (not limits), so:
      * connect=False or energize=False -> 0W (there's no way to genuinely open the contactor via Troca)
      * storage_target_watts set -> that setpoint takes precedence (it's export positive - so positive values
        discharge, negative values charge). Its magnitude is capped by max_power_watts and either the export and
        generation limits (discharging) or the import and load limits (charging)
      * otherwise charge at max_power_watts, capped by the import and load limits (if set)

    Discharge is only ever requested via storage_target_watts. ramp_time_seconds is applied separately (see
    ramp_segments) and ramp_percent_max_second_hundredths isn't applied at all."""

    if values.connect is False or values.energize is False:
        return 0.0

    charge_limits = [lim for lim in (values.import_limit_watts, values.load_limit_watts) if lim is not None]
    if values.storage_target_watts is not None and values.storage_target_watts > 0:
        discharge_limits = [
            lim for lim in (values.export_limit_watts, values.generation_limit_watts) if lim is not None
        ]
        return -float(max(0, min([values.storage_target_watts, max_power_watts] + discharge_limits))) + 0.0
    if values.storage_target_watts is not None:
        return float(max(0, min([-values.storage_target_watts, max_power_watts] + charge_limits)))

    return float(max(0, min([max_power_watts] + charge_limits)))


def _to_troca_kw(watts: float) -> float:
    return round(watts * TROCA_SCHEDULE_WATTS_MULTIPLIER, SETPOINT_KW_DECIMALS) + 0.0  # + 0.0 normalises -0.0


def schedule_to_troca(
    raw_schedule: Sequence[ScheduledControlValues], max_power_watts: float, now: datetime, ramp_step: timedelta
) -> ActivePowerSchedule | None:
    """Maps the full CSIP-Aus schedule (as produced by calculate_schedule_values - contiguous, starting no later than
    now) into a Troca ActivePowerSchedule. Adjacent intervals resolving to the same setpoint are merged, and the final
    open-ended interval is extended out to now + SCHEDULE_HORIZON. Transitions into an interval with a
    ramp_time_seconds are stepped every ramp_step (see ramp_segments). Returns None if there's nothing to schedule."""

    segments = ramp_segments(
        raw_schedule,
        lambda v: (values_to_charge_watts(v, max_power_watts),),
        now,
        now + SCHEDULE_HORIZON,
        ramp_step,
    )
    periods: list[SchedulePeriod] = []
    for seg in segments[:MAX_SCHEDULE_PERIODS]:
        watts = seg.value[0]
        kw = _to_troca_kw(0.0 if watts is None else watts)
        periods.append(SchedulePeriod(to_troca_timestamp(seg.start), to_troca_timestamp(seg.end), _bounds(kw)))

    if not periods:
        return None

    return ActivePowerSchedule(start_time=periods[0].start_time, end_time=periods[-1].end_time, periods=periods)


def _bounds(kw: float) -> Bounds:
    return Bounds(value=kw, unit=TROCA_SCHEDULE_UNIT)


@dataclass(frozen=True, slots=True)
class _Segment:
    start: datetime
    end: datetime
    value: tuple[float | None, ...]


def _append_segment(segments: list[_Segment], start: datetime, end: datetime, value: tuple[float | None, ...]) -> None:
    """Appends a segment (clipped to start no earlier than now), merging it into the previous segment if it's a
    contiguous continuation of the same value."""
    if end <= start:
        return
    if segments and segments[-1].end == start and segments[-1].value == value:
        segments[-1] = _Segment(segments[-1].start, end, value)
    else:
        segments.append(_Segment(start, end, value))


def _interpolate(
    previous: tuple[float | None, ...], target: tuple[float | None, ...], fraction: float
) -> tuple[float | None, ...]:
    """Linearly interpolates (rounded to whole watts) each component of a value fraction of the way from previous to
    target. A component that's None at either end can't be interpolated - it jumps straight to target."""
    return tuple(
        t if p is None or t is None else float(round(p + (t - p) * fraction))
        for p, t in zip(previous, target, strict=True)
    )


def ramp_segments(
    raw_schedule: Sequence[ScheduledControlValues],
    to_value: Callable[[ActiveValues], tuple[float | None, ...]],
    now: datetime,
    horizon: datetime,
    ramp_step: timedelta,
) -> list[_Segment]:
    """Flattens the full CSIP-Aus schedule (contiguous, in order) into non overlapping segments (clipped to start no
    earlier than now, and end no later than horizon) of to_value(values), merging contiguous segments of the same
    value.

    An interval's ramp_time_seconds is the time to ramp TO that interval's value from wherever the previous interval
    left off. This is approximated by stepping every ramp_step - each step holding the value of a linear ramp at the
    start of that step, so that the target is reached exactly ramp_time_seconds after the interval starts. eg a ramp
    from 0W to 1000W over 100s with a 25s ramp_step yields 0W, 250W, 500W, 750W (each for 25s) and then 1000W.

    If an interval ends before its ramp completes, the next interval ramps from the value it was left at. The first
    interval has nothing to ramp from, so it's never ramped - raw_schedule should start early enough to include
    whatever preceded any ramp still in progress at now (see troca.schedule). A ramp_step of 0 disables ramping."""

    now = now.replace(microsecond=0)
    horizon = horizon.replace(microsecond=0)
    segments: list[_Segment] = []
    previous: tuple[float | None, ...] | None = None  # The value the previous interval finished at
    for scv in raw_schedule:
        start = scv.active_from.replace(microsecond=0)
        end = min(scv.active_to or horizon, horizon).replace(microsecond=0)
        target = to_value(scv.values)

        reached = target
        ramp = timedelta(seconds=scv.values.ramp_time_seconds or 0)
        step_start = start
        if previous is not None and previous != target and ramp_step > timedelta(0) and ramp > timedelta(0):
            ramp_end = min(start + ramp, end)
            while step_start < ramp_end:
                reached = _interpolate(previous, target, (step_start - start) / ramp)
                step_end = min(step_start + ramp_step, ramp_end)
                _append_segment(segments, max(step_start, now), step_end, reached)
                step_start = step_end
            if step_start < end:
                reached = target

        _append_segment(segments, max(step_start, now), end, target)
        previous = reached

    return segments


def _segments_match(applied: list[_Segment], desired: list[_Segment], now: datetime) -> bool:
    """True if the applied segments will produce the same values as desired.

    The end of the final segment is only loosely compared - a freshly calculated schedule always extends its last
    period out to now + SCHEDULE_HORIZON, so it's considered matching as long as the applied schedule still has at
    least half of that horizon remaining."""

    if len(applied) != len(desired) or not desired:
        return False

    for i, (sa, sd) in enumerate(zip(applied, desired, strict=True)):
        if sa.start != sd.start or sa.value != sd.value:
            return False

        is_last = i == len(desired) - 1
        if not is_last and sa.end != sd.end:
            return False
        if is_last and sa.end != sd.end and sa.end < now + SCHEDULE_HORIZON / 2:
            return False

    return True


def _troca_segments(schedule: ActivePowerSchedule, now: datetime) -> list[_Segment]:
    segments: list[_Segment] = []
    for p in schedule.periods:
        if p.global_ is None or p.global_.value is None:
            continue
        start = max(parse_troca_timestamp(p.start_time), now)
        value = (round(p.global_.value, SETPOINT_KW_DECIMALS) + 0.0,)
        _append_segment(segments, start, parse_troca_timestamp(p.end_time), value)
    return segments


def schedules_match(applied: ActivePowerSchedule, desired: ActivePowerSchedule, now: datetime) -> bool:
    """True if applied (a previously pushed schedule) will produce the same setpoints as desired from now onwards
    (see _segments_match)."""
    return _segments_match(_troca_segments(applied, now), _troca_segments(desired, now), now)


# --------------------------------------------------------------------------
# Direct OCPP charging profiles
# --------------------------------------------------------------------------

OCPP_CHARGING_RATE_UNIT = "W"
OCPP_PROFILE_KIND_ABSOLUTE = "Absolute"


def values_to_ocpp_limits(values: ActiveValues, max_power_watts: float) -> tuple[float, float | None]:
    """Maps CSIP-Aus derived control values into OCPP (charge limit, discharge limit) watts. Unlike Troca's own
    session schedules, OCPP profiles can express genuine limits so these map far more directly:
      * connect=False or energize=False -> no charging or discharging (there's no way to genuinely open the
        contactor via OCPP charging profiles)
      * charge limit: the import and load limits (if set) - OCPP requires a limit, so max_power_watts is used
        when neither is set
      * discharge limit: the export and generation limits (if set) as a negative number (per OCPP 2.1), or None
        for no discharge limit

    storage_target_watts isn't mapped (it's a setpoint, not a limit - see ocpp_schedule.sync_storage_target),
    nor is ramp_percent_max_second_hundredths. ramp_time_seconds is applied separately (see ramp_segments) as
    OCPP profiles have no concept of a ramp."""

    if values.connect is False or values.energize is False:
        return 0.0, 0.0

    charge_caps = [lim for lim in (values.import_limit_watts, values.load_limit_watts) if lim is not None]
    charge_limit = float(max(0, min(charge_caps, default=max_power_watts)))

    discharge_caps = [lim for lim in (values.export_limit_watts, values.generation_limit_watts) if lim is not None]
    discharge_limit = -float(max(0, min(discharge_caps))) + 0.0 if discharge_caps else None

    return charge_limit, discharge_limit


def schedule_to_ocpp_profile(
    raw_schedule: Sequence[ScheduledControlValues],
    max_power_watts: float,
    now: datetime,
    profile_id: int,
    stack_level: int,
    purpose: OcppChargingProfilePurpose,
    ramp_step: timedelta,
) -> OcppChargingProfile | None:
    """Maps the full CSIP-Aus schedule (as produced by calculate_schedule_values - contiguous, starting no later than
    now) into an absolute OCPP charging profile starting at now. Adjacent intervals resolving to the same limits are
    merged, and the final open-ended interval is extended out to now + SCHEDULE_HORIZON. Transitions into an
    interval with a ramp_time_seconds are stepped every ramp_step (see ramp_segments). Returns None if there's
    nothing to schedule."""

    start_schedule = now.replace(microsecond=0)
    segments = ramp_segments(
        raw_schedule,
        lambda v: values_to_ocpp_limits(v, max_power_watts),
        start_schedule,
        start_schedule + SCHEDULE_HORIZON,
        ramp_step,
    )

    segments = segments[:MAX_SCHEDULE_PERIODS]
    if not segments:
        return None

    periods = [
        OcppChargingSchedulePeriod(
            start_period=int((seg.start - start_schedule).total_seconds()),
            limit=seg.value[0],
            discharge_limit=seg.value[1],
        )
        for seg in segments
    ]
    return OcppChargingProfile(
        id=profile_id,
        stack_level=stack_level,
        charging_profile_purpose=purpose,
        charging_profile_kind=OCPP_PROFILE_KIND_ABSOLUTE,
        charging_schedule=[
            OcppChargingSchedule(
                id=1,
                charging_rate_unit=OCPP_CHARGING_RATE_UNIT,
                charging_schedule_period=periods,
                start_schedule=to_troca_timestamp(start_schedule),
                duration=int((segments[-1].end - start_schedule).total_seconds()),
            )
        ],
    )


def _ocpp_segments(profile: OcppChargingProfile, now: datetime) -> list[_Segment]:
    segments: list[_Segment] = []
    for schedule in profile.charging_schedule:
        if schedule.start_schedule is None or schedule.duration is None:
            continue
        start_schedule = parse_troca_timestamp(schedule.start_schedule)
        schedule_end = start_schedule + timedelta(seconds=schedule.duration)
        periods = schedule.charging_schedule_period
        for i, p in enumerate(periods):
            start = start_schedule + timedelta(seconds=p.start_period)
            end = (
                start_schedule + timedelta(seconds=periods[i + 1].start_period)
                if i + 1 < len(periods)
                else schedule_end
            )
            _append_segment(segments, max(start, now), end, (p.limit, p.discharge_limit))
    return segments


def ocpp_profiles_match(applied: OcppChargingProfile, desired: OcppChargingProfile, now: datetime) -> bool:
    """True if applied (a previously pushed profile) will produce the same limits as desired from now onwards
    (see _segments_match). Only absolute profiles (with a startSchedule/duration) are supported."""
    return _segments_match(_ocpp_segments(applied, now), _ocpp_segments(desired, now), now)


# -- Dynamic prices -> OCPP 2.1 absolutePriceSchedule ---------------------------------------------------------------

# CSIP-Aus prices are in dollars / kWh
PRICE_CURRENCY = "AUD"
PRICE_LANGUAGE = "en"
PRICE_ALGORITHM = "urn:iso:std:iso:15118:-20:PriceAlgorithm:1-Power"

# Conservative cap on the number of priceRuleStacks sent - anything past this is truncated (and will be pushed on a
# later sync, once the earlier prices have elapsed).
MAX_PRICE_RULE_STACKS = 100


@dataclass(frozen=True, slots=True)
class PriceSegment:
    start: datetime
    end: datetime
    price_kwh: Decimal


def dynamic_prices_to_segments(prices: Sequence[CSIPAusDynamicPrice], now: datetime) -> list[PriceSegment]:
    """Flattens (potentially overlapping) dynamic prices into the contiguous run of non overlapping price segments
    that's either active at now, or the next to start. Where prices overlap, only those at the lowest primacy
    apply - and if there are several at that primacy, their prices are summed. Prices without a price_kwh, that are
    cancelled or that have finished are ignored. Adjacent segments with the same price are merged.

    Segments keep the start time of their price (even if that's before now) so that the result is stable across
    polls. Only the first contiguous run is returned (a gap ends it) - capped at MAX_PRICE_RULE_STACKS segments."""

    # (primacy, start, end, price_kwh) of every price that's still relevant
    candidates: list[tuple[int, datetime, datetime, Decimal]] = []
    for p in prices:
        end = min(p.finished_at, p.cancelled_at) if p.cancelled_at is not None else p.finished_at
        if p.price_kwh is not None and end > now and end > p.started_at:
            candidates.append((p.primacy, p.started_at, end, p.price_kwh))

    boundaries = sorted({t for _, start, end, _ in candidates for t in (start, end)})
    segments: list[PriceSegment] = []
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        active = [
            (primacy, price) for primacy, p_start, p_end, price in candidates if p_start <= start and p_end >= end
        ]
        if not active:
            if segments:
                break  # Gap - end of the first contiguous run
            continue

        top_primacy = min(primacy for primacy, _ in active)
        price = sum((price for primacy, price in active if primacy == top_primacy), Decimal(0))
        if segments and segments[-1].price_kwh == price:
            segments[-1] = PriceSegment(segments[-1].start, end, price)
        else:
            segments.append(PriceSegment(start, end, price))

    return segments[:MAX_PRICE_RULE_STACKS]


def price_to_rational(price: Decimal) -> OcppRationalNumber:
    """Encodes a decimal price exactly as an OCPP RationalNumber (value * 10^exponent), eg 0.3050 -> 305e-3."""
    sign, digits, exponent = price.normalize().as_tuple()
    value = int("".join(str(d) for d in digits)) * (-1 if sign else 1)
    if not isinstance(exponent, int):
        raise ValueError(f"Price {price} isn't a finite number.")
    if exponent > 0:  # eg 1E+1 - keep the exponent non positive so the value carries every significant digit
        value, exponent = value * 10**exponent, 0
    return OcppRationalNumber(exponent=exponent, value=value)


def prices_to_ocpp_profile(
    segments: Sequence[PriceSegment],
    profile_id: int,
    stack_level: int,
    purpose: OcppChargingProfilePurpose,
    limit_watts: float,
    price_schedule_id: int,
) -> OcppChargingProfile | None:
    """Maps price segments (see dynamic_prices_to_segments) into an absolute OCPP 2.1 charging profile carrying an
    absolutePriceSchedule - one priceRuleStack per segment. The profile's only charging period is a flat
    limit_watts for the duration of the prices. Returns None if there are no segments."""

    if not segments:
        return None

    anchor = segments[0].start.replace(microsecond=0)
    timestamp = to_troca_timestamp(anchor)
    stacks = [
        OcppPriceRuleStack(
            duration=int((seg.end - seg.start).total_seconds()),
            price_rule=[
                OcppPriceRule(
                    energy_fee=price_to_rational(seg.price_kwh),
                    power_range_start=OcppRationalNumber(exponent=0, value=0),
                )
            ],
        )
        for seg in segments
    ]
    return OcppChargingProfile(
        id=profile_id,
        stack_level=stack_level,
        charging_profile_purpose=purpose,
        charging_profile_kind=OCPP_PROFILE_KIND_ABSOLUTE,
        charging_schedule=[
            OcppChargingSchedule(
                id=1,
                charging_rate_unit=OCPP_CHARGING_RATE_UNIT,
                charging_schedule_period=[OcppChargingSchedulePeriod(start_period=0, limit=limit_watts)],
                start_schedule=timestamp,
                duration=int((segments[-1].end - anchor).total_seconds()),
                absolute_price_schedule=OcppAbsolutePriceSchedule(
                    time_anchor=timestamp,
                    price_schedule_id=price_schedule_id,
                    currency=PRICE_CURRENCY,
                    language=PRICE_LANGUAGE,
                    price_algorithm=PRICE_ALGORITHM,
                    price_rule_stacks=stacks,
                ),
            )
        ],
    )
