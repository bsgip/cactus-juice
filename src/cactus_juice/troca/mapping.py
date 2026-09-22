from datetime import UTC, datetime

from cactus_juice.csipaus.dto import ActiveValues
from cactus_juice.model import OCPPMetadata, OCPPReading
from cactus_juice.troca.models import (
    ChargingProfile,
    ChargingProfileKind,
    ChargingProfilePurpose,
    ChargingRateUnit,
    ChargingSchedule,
    ChargingSchedulePeriod,
    MeteringReading,
    SessionData,
    ValuePerType,
    ValueType,
)


def reading_to_db(mr: MeteringReading) -> OCPPReading:
    """Maps a reading from troca into an equivalent DB model"""

    reading_start = datetime.fromisoformat(mr.timestamp)
    if reading_start.tzinfo is None:
        reading_start = reading_start.replace(tzinfo=UTC)

    import_watts: float = 0
    export_watts: float = 0
    if mr.instantaneous_active_power is not None:
        if mr.instantaneous_active_power >= 0:
            import_watts = mr.instantaneous_active_power
        else:
            export_watts = -mr.instantaneous_active_power

    import_var: float | None = None
    export_var: float | None = None
    if mr.instantaneous_reactive_power is not None:
        if mr.instantaneous_reactive_power >= 0:
            import_var = mr.instantaneous_reactive_power
            export_var = 0.0
        else:
            import_var = 0.0
            export_var = -mr.instantaneous_reactive_power

    return OCPPReading(
        reading_start=reading_start,
        import_active_power_watts=import_watts,
        export_active_power_watts=export_watts,
        import_reactive_power_var=import_var,
        export_reactive_power_var=export_var,
    )


def _watts_from_value_per_type(vpt: ValuePerType | None) -> float | None:
    """Extracts a Watts figure from a ValuePerType. Only trusted when its type is explicitly ACTIVE_POWER -
    Troca doesn't document what unit the other ValueType members are actually expressed in (eg a PERCENTAGE
    figure would need a battery_capacity conversion we can't confidently perform), so those are left unmapped."""
    if vpt is None or vpt.value is None or vpt.type != ValueType.ACTIVE_POWER:
        return None
    return vpt.value


def session_constraints_to_metadata(session_data: SessionData) -> OCPPMetadata | None:
    """Maps a Troca SessionData's constraints (the connected EV's own charge/discharge capability, as reported
    for this session) into an OCPPMetadata snapshot. Returns None if there's nothing usable to record.

    Troca has no discovered endpoint for static EVSE nameplate ratings (voltage bounds, an absolute max power, or
    a charge/discharge power gradient) - get_evses() explicitly documents that it omits electrical ratings, and
    there's no equivalent pre-session capability endpoint (see TrocaClient docstrings). So
    max_voltage_volts/min_voltage_volts/max_power_watts/set_grad_w are always left as None here - stubbed until
    Troca exposes (or discovery finds) a source for them."""

    if session_data.constraints is None:
        return None

    max_charge_rate_watts = _watts_from_value_per_type(session_data.constraints.max_charge_level)
    max_discharge_rate_watts = _watts_from_value_per_type(session_data.constraints.max_discharge_level)
    if max_charge_rate_watts is None and max_discharge_rate_watts is None:
        return None

    return OCPPMetadata(
        max_voltage_volts=None,
        min_voltage_volts=None,
        max_power_watts=None,
        max_charge_rate_watts=max_charge_rate_watts,
        max_discharge_rate_watts=max_discharge_rate_watts,
        set_grad_w=None,
    )


def current_values_to_charging_profile(
    values: ActiveValues, charging_profile_id: int, stack_level: int, valid_from: datetime
) -> ChargingProfile | None:
    """Maps the currently-active CSIP-Aus derived control values into a Troca ChargingProfile expressing an
    import charging-rate limit from ``valid_from`` onwards, or None if the connector should be left unconstrained
    (any previously-pushed profile should be cleared instead of replaced).

    Troca's ChargingProfile/ChargingSchedule (per OCPP convention - see the ``ChargingRateUnit`` caveat in
    troca.models) can only express a single import power limit over time. There is no discovered TrocaClient
    method for actually toggling connect/energize (ie opening/closing the contactor), nor for limiting
    export/discharge, generation or storage targets, so those are approximated or dropped here:
      * connect=False or energize=False is approximated as a 0W import limit - the closest available lever, since
        genuinely disconnecting/de-energizing isn't exposed by TrocaClient at all. Stubbed until such a method is
        discovered.
      * export_limit_watts / generation_limit_watts / storage_target_watts have no Troca analog and are ignored.
      * ramp_time_seconds / ramp_percent_max_second_hundredths (and ClientState.ramp_step) aren't applied - every
        change jumps straight to the new limit rather than a staged ramp. Revisit once Troca's profile semantics
        around staged/ramped limits are clarified.

    Only ever maps a single, immediately-active interval (not the full future schedule) - the caller is expected
    to re-run this on a short poll cadence so the next interval's value gets pushed once it actually becomes
    current, rather than guessing how Troca wants a multi-period "unconstrained" gap represented."""

    if values.connect is False or values.energize is False:
        limit_watts = 0.0
    elif values.import_limit_watts is not None:
        limit_watts = float(max(0, values.import_limit_watts))
    else:
        return None

    valid_from_str = valid_from.isoformat()
    return ChargingProfile(
        charging_profile_id=charging_profile_id,
        stack_level=stack_level,
        charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
        charging_profile_kind=ChargingProfileKind.ABSOLUTE,
        charging_schedule=ChargingSchedule(
            charging_rate_unit=ChargingRateUnit.WATTS,
            charging_schedule_period=[ChargingSchedulePeriod(start_period=0, limit=limit_watts)],
            start_schedule=valid_from_str,
        ),
        valid_from=valid_from_str,
    )
