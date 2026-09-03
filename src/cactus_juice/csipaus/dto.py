from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic.dataclasses import dataclass


@runtime_checkable
class HasDefaultValues(Protocol):
    # Common bits
    connect: bool | None
    energize: bool | None
    import_limit_watts: int | None
    export_limit_watts: int | None
    load_limit_watts: int | None
    generation_limit_watts: int | None
    storage_target_watts: int | None

    # Unique to default
    ramp_percent_max_second_hundredths: int | None


@dataclass(frozen=True, slots=True)
class DefaultValues:
    """The various default control values that might be active at a point in time"""

    # Common bits
    connect: bool | None
    energize: bool | None
    import_limit_watts: int | None
    export_limit_watts: int | None
    load_limit_watts: int | None
    generation_limit_watts: int | None
    storage_target_watts: int | None

    # Unique to default
    ramp_percent_max_second_hundredths: int | None


@runtime_checkable
class HasControlValues(Protocol):
    # Common bits
    connect: bool | None
    energize: bool | None
    import_limit_watts: int | None
    export_limit_watts: int | None
    load_limit_watts: int | None
    generation_limit_watts: int | None
    storage_target_watts: int | None

    # Unique to DERControl
    ramp_time_seconds: int | None


@dataclass(frozen=True, slots=True)
class ControlValues:
    """The various DER control values that might be active at a point in time"""

    # Common bits
    connect: bool | None
    energize: bool | None
    import_limit_watts: int | None
    export_limit_watts: int | None
    load_limit_watts: int | None
    generation_limit_watts: int | None
    storage_target_watts: int | None

    # Unique to DERControl
    ramp_time_seconds: int | None


@dataclass(frozen=True, slots=True)
class ActiveValues:
    """A combination of active DERControls + Defaults - everything that is applicable at a moment in time"""

    # Common bits
    connect: bool | None
    energize: bool | None
    import_limit_watts: int | None
    export_limit_watts: int | None
    load_limit_watts: int | None
    generation_limit_watts: int | None
    storage_target_watts: int | None

    # Unique to default
    ramp_percent_max_second_hundredths: int | None

    # Unique to DERControl
    ramp_time_seconds: int | None


@dataclass(frozen=True, slots=True)
class ScheduledControlValues:
    active_from: datetime  # The inclusive start time
    active_to: datetime | None  # The exclusive end time (None means ongoing)

    values: ActiveValues  # That values
