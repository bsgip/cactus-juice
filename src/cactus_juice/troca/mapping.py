from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cactus_juice.csipaus.dto import ActiveValues, ScheduledControlValues
from cactus_juice.model import OCPPMetadata, OCPPReading
from cactus_juice.troca.models import (
    ActivePowerSchedule,
    Bounds,
    GetVariableData,
    GetVariableResult,
    MeteringReading,
    OcppChargingProfile,
    OcppChargingProfilePurpose,
    OcppChargingSchedule,
    OcppChargingSchedulePeriod,
    OcppComponent,
    OcppEvse,
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


def values_to_charge_watts(values: ActiveValues, max_power_watts: float) -> float:
    """Maps CSIP-Aus derived control values into the fixed charge rate (W, import positive) the EV should be
    held at. Troca only accepts fixed setpoints (not limits), so:
      * connect=False or energize=False -> 0W (there's no way to genuinely open the contactor via Troca)
      * otherwise charge at max_power_watts, capped by the import and load limits (if set)

    Discharge is never requested - export_limit_watts / generation_limit_watts only ever restrict discharging,
    and storage_target_watts isn't mapped. ramp_time_seconds / ramp_percent_max_second_hundredths aren't applied
    either - every change jumps straight to the new setpoint."""

    if values.connect is False or values.energize is False:
        return 0.0

    caps = [max_power_watts] + [lim for lim in (values.import_limit_watts, values.load_limit_watts) if lim is not None]
    return float(max(0, min(caps)))


def _to_troca_kw(watts: float) -> float:
    return round(watts * TROCA_SCHEDULE_WATTS_MULTIPLIER, SETPOINT_KW_DECIMALS) + 0.0  # + 0.0 normalises -0.0


def schedule_to_troca(
    raw_schedule: Sequence[ScheduledControlValues], max_power_watts: float, now: datetime
) -> ActivePowerSchedule | None:
    """Maps the full CSIP-Aus schedule (as produced by calculate_schedule_values - contiguous, starting from now)
    into a Troca ActivePowerSchedule. Adjacent intervals resolving to the same setpoint are merged, and the final
    open-ended interval is extended out to now + SCHEDULE_HORIZON. Returns None if there's nothing to schedule."""

    horizon = now + SCHEDULE_HORIZON
    periods: list[SchedulePeriod] = []
    last_end: datetime | None = None
    for scv in raw_schedule:
        start = max(scv.active_from, now).replace(microsecond=0)
        end = min(scv.active_to or horizon, horizon).replace(microsecond=0)
        if end <= start:
            continue

        kw = _to_troca_kw(values_to_charge_watts(scv.values, max_power_watts))
        if periods and periods[-1].global_ is not None and periods[-1].global_.value == kw and last_end == start:
            periods[-1] = SchedulePeriod(periods[-1].start_time, to_troca_timestamp(end), periods[-1].global_)
        else:
            periods.append(SchedulePeriod(to_troca_timestamp(start), to_troca_timestamp(end), _bounds(kw)))
        last_end = end

    if not periods:
        return None

    periods = periods[:MAX_SCHEDULE_PERIODS]
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

    storage_target_watts isn't mapped, nor are ramp_time_seconds / ramp_percent_max_second_hundredths - OCPP
    profiles have no concept of a ramp, so every change jumps straight to the new limit."""

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
) -> OcppChargingProfile | None:
    """Maps the full CSIP-Aus schedule (as produced by calculate_schedule_values - contiguous, starting from now)
    into an absolute OCPP charging profile starting at now. Adjacent intervals resolving to the same limits are
    merged, and the final open-ended interval is extended out to now + SCHEDULE_HORIZON. Returns None if there's
    nothing to schedule."""

    start_schedule = now.replace(microsecond=0)
    horizon = start_schedule + SCHEDULE_HORIZON
    segments: list[_Segment] = []
    for scv in raw_schedule:
        start = max(scv.active_from, start_schedule).replace(microsecond=0)
        end = min(scv.active_to or horizon, horizon).replace(microsecond=0)
        _append_segment(segments, start, end, values_to_ocpp_limits(scv.values, max_power_watts))

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
