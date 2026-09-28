import math
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

# The OCPP 2.x variables that might carry an EVSE's rated power, in order of preference. The Trialog simulator
# only answers ElectricalFeed.Power (EVSE.Power is reported as UnknownVariable).
RATED_POWER_VARIABLES = [
    GetVariableData(
        component=OcppComponent(name="EVSE", evse=OcppEvse(id=1)),
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
    """Extracts the EVSE's rated power (in W) from a GetVariables response to RATED_POWER_VARIABLES, using the
    first variable (in RATED_POWER_VARIABLES order) the station accepted with a usable value."""

    for requested in RATED_POWER_VARIABLES:
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
    """Maps a GetVariables response to RATED_POWER_VARIABLES into an OCPPMetadata snapshot (or None if there's
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
    kw: float


def _segments_from(schedule: ActivePowerSchedule, now: datetime) -> list[_Segment]:
    """The schedule as a list of (merged) segments clipped to start no earlier than now."""
    segments: list[_Segment] = []
    for p in schedule.periods:
        start = max(parse_troca_timestamp(p.start_time), now)
        end = parse_troca_timestamp(p.end_time)
        if end <= start or p.global_ is None or p.global_.value is None:
            continue

        kw = p.global_.value
        if segments and segments[-1].end == start and math.isclose(segments[-1].kw, kw, abs_tol=1e-3):
            segments[-1] = _Segment(segments[-1].start, end, kw)
        else:
            segments.append(_Segment(start, end, kw))
    return segments


def schedules_match(applied: ActivePowerSchedule, desired: ActivePowerSchedule, now: datetime) -> bool:
    """True if applied (a previously pushed schedule) will produce the same setpoints as desired from now onwards.

    The end of the final period is only loosely compared - a freshly calculated schedule always extends its last
    period out to now + SCHEDULE_HORIZON, so it's considered matching as long as the applied schedule still has at
    least half of that horizon remaining."""

    a = _segments_from(applied, now)
    d = _segments_from(desired, now)
    if len(a) != len(d) or not d:
        return False

    for i, (sa, sd) in enumerate(zip(a, d, strict=True)):
        if sa.start != sd.start or not math.isclose(sa.kw, sd.kw, abs_tol=1e-3):
            return False

        is_last = i == len(d) - 1
        if not is_last and sa.end != sd.end:
            return False
        if is_last and sa.end != sd.end and sa.end < now + SCHEDULE_HORIZON / 2:
            return False

    return True
