import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from cactus_test_definitions.csipaus import (
    CSIPAusReadingLocation,
    CSIPAusReadingType,
)
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    KindType,
    RoleFlagsType,
    UomType,
)

from cactus_juice.error import BaseJuiceError


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
