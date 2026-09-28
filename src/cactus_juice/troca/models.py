import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mashumaro.config import BaseConfig
from mashumaro.mixins.json import DataClassJSONMixin


class TrocaModel(DataClassJSONMixin):
    """Common mashumaro config shared by every model in this module."""

    class Config(BaseConfig):
        omit_none = True
        serialize_by_alias = True


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class ConnectorType(StrEnum):
    """``Connector.connectorType`` values."""

    X_EMS = "xEmsConnector"
    EMS = "EmsConnector"
    Q_OCPP = "QocppConnector"
    LINKY = "LinkyConnector"


class CommandStatus(StrEnum):
    """``CommandStatus`` schema."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REFUSED = "refused"
    ERROR = "error"
    UNKNOWN = "unknown"


class SessionCommandType(StrEnum):
    """``SessionCommandType`` schema."""

    SET_CHARGING_PROFILE = "set_charging_profile"
    CLEAR_CHARGING_PROFILE = "clear_charging_profile"
    START_NEW_TRANSACTION = "start_new_transaction"
    STOP_TRANSACTION = "stop_transaction"
    AUTHORIZE = "authorize"
    UNKNOWN = "unknown"


class SessionStatus(StrEnum):
    """``SessionStatus`` schema - reported via ``GET /sessions/status`` (not on ``SessionData`` itself)."""

    PENDING = "pending"
    CHARGING = "charging"
    COMPLETED = "completed"
    INVALID = "invalid"
    CLEARED = "cleared"
    UNKNOWN = "unknown"


class StructureLevel(StrEnum):
    """``StructureLevel`` schema - the level of the structure hierarchy a ``LocationId`` refers to."""

    POOL = "pool"
    STATION = "station"
    EVSE = "evse"
    EVSE_CONNECTOR = "evseConnector"
    ALL = "all"
    UNKNOWN = "unknown"


class ScheduleSyncMode(StrEnum):
    """How the CSIP-Aus schedule is kept in sync with the charging station (persisted on TrocaConfig)."""

    # Send OCPP charging profiles directly to the station via Troca's OCPP passthrough (see troca.ocpp_schedule)
    OCPP = "ocpp"

    # Send schedules via Troca's own session command API (see troca.session_schedule)
    TROCA_SESSION = "troca_session"


class ValueType(StrEnum):
    """``ValueType`` schema (subset) - what a ``TEmsValue``/``Bounds`` figure measures."""

    ACTIVE_POWER = "Active power"
    REACTIVE_POWER = "Reactive power"
    APPARENT_POWER = "Apparent power"
    VOLTAGE = "Voltage"
    PERCENTAGE = "Percentage"


# --------------------------------------------------------------------------
# Shared value types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TEmsValue(TrocaModel):
    """``TEmsValue`` schema - a unit tagged number. When ``unit`` is omitted, the default unit for ``value_type``
    applies (kW/kVAR/kVA for the power types)."""

    value: float
    value_type: str | None = field(default=None, metadata={"alias": "valueType"})
    unit: str | None = None


@dataclass(frozen=True)
class TEmsValuePhase(TrocaModel):
    """``TEmsValuePhase`` schema - a ``TEmsValue`` for all phases (global) and per phase."""

    global_: TEmsValue | None = field(default=None, metadata={"alias": "global"})
    l1: TEmsValue | None = None
    l2: TEmsValue | None = None
    l3: TEmsValue | None = None


@dataclass(frozen=True)
class Bounds(TrocaModel):
    """``Bounds`` schema - a min/max range or fixed value.

    NOTE: Although the schema allows min/max, a session schedule period carrying only min/max is rejected by the
    server with "The schedule is invalid: every period needs a fixed Active power value" (probed 2026-09-28), so
    ``value`` must be set for anything sent via ``set_charging_profile``."""

    value: float | None = None
    min: float | None = None
    max: float | None = None
    value_type: str | None = field(default=None, metadata={"alias": "valueType"})
    unit: str | None = None


@dataclass(frozen=True)
class LocationId(TrocaModel):
    """``LocationId`` schema - identifies one element of the pool/station/EVSE/connector hierarchy."""

    location_id: str = field(metadata={"alias": "locationId"})
    level: StructureLevel


def numeric_value(v: float | TEmsValue | None) -> float | None:
    """The spec types several fields as ``TEmsValue`` objects, but the live server returns bare numbers for them
    (eg ``meterStart: 1``, ``chargedEnergy: 0``) - models accept either, and this extracts the number."""
    if v is None:
        return None
    if isinstance(v, TEmsValue):
        return v.value
    return float(v)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Connector(TrocaModel):
    """``Connector`` schema, as returned by ``GET /config/connectors``. For OCPP connectors, ``name`` is also the
    first path segment of the OCPP passthrough (eg ``qocppConnector2.1``)."""

    name: str
    connector_id: str = field(metadata={"alias": "connectorId"})
    connector_type: ConnectorType = field(metadata={"alias": "connectorType"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    custom_data: dict[str, Any] = field(default_factory=dict, metadata={"alias": "customData"})
    enabling_modules: list[str] = field(default_factory=list, metadata={"alias": "enablingModules"})
    issuer_id: str | None = field(default=None, metadata={"alias": "issuerId"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})
    alternative_names: list[str] = field(default_factory=list, metadata={"alias": "alternativeNames"})


# --------------------------------------------------------------------------
# Structure (Pool / Station / EVSE / hierarchy)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Pool(TrocaModel):
    """``PoolData`` schema, as returned by ``GET /structure/pools``."""

    pool_id: str = field(metadata={"alias": "poolId"})
    name: str
    type: str | None = None
    issuer_id: str | None = field(default=None, metadata={"alias": "issuerId"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})


@dataclass(frozen=True)
class Station(TrocaModel):
    """``StationData`` schema, as returned by ``GET /structure/stations``. ``name`` is the OCPP charging station
    identity (eg ``FR*TRI*E123``) used by the OCPP passthrough."""

    station_id: str = field(metadata={"alias": "stationId"})
    name: str
    model: str | None = None
    vendor_id: str | None = field(default=None, metadata={"alias": "vendorId"})
    issuer_id: str | None = field(default=None, metadata={"alias": "issuerId"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})


@dataclass(frozen=True)
class EvseUid(TrocaModel):
    station_name: str | None = field(default=None, metadata={"alias": "stationName"})
    evse_nb: int | None = field(default=None, metadata={"alias": "evseNb"})


@dataclass(frozen=True)
class Evse(TrocaModel):
    """``EvseData`` schema, as returned by ``GET /structure/evses``. Carries no electrical ratings - those are
    only available by asking the station directly via the OCPP passthrough (GetVariables)."""

    evse_id: str = field(metadata={"alias": "evseId"})
    evse_uid: EvseUid | None = field(default=None, metadata={"alias": "evseUid"})
    name: str | None = None
    issuer_id: str | None = field(default=None, metadata={"alias": "issuerId"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})


@dataclass(frozen=True)
class StructurePair(TrocaModel):
    """``GET /structure/pairs`` entry - a parent/child link in the pool -> station -> evse -> evseConnector
    hierarchy. This is the only way to navigate from a session's (evseConnector) location up to its EVSE."""

    high_level_structure: LocationId = field(metadata={"alias": "highLevelStructure"})
    low_level_structure: LocationId = field(metadata={"alias": "lowLevelStructure"})


# --------------------------------------------------------------------------
# Power usage readings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MeteringReading(TrocaModel):
    """``MeteringData`` schema, as returned by ``GET /observation-points/metering-data``. Sign convention (per
    Trialog) is positive while charging (importing), negative while discharging.

    NOTE: As of 2026-09-28 this endpoint returns nothing with the Trialog charging station simulator - it only
    samples Energy.Active.Import.Register and (per Trialog) won't produce active power or SoC."""

    timestamp: str
    observation_point_id: str | None = field(default=None, metadata={"alias": "observationPointId"})
    instantaneous_active_power: TEmsValuePhase | None = field(
        default=None, metadata={"alias": "instantaneousActivePower"}
    )
    instantaneous_reactive_power: TEmsValuePhase | None = field(
        default=None, metadata={"alias": "instantaneousReactivePower"}
    )
    rms_voltage: TEmsValuePhase | None = field(default=None, metadata={"alias": "rmsVoltage"})


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Token(TrocaModel):
    """``Token`` schema - the EV user's auth token."""

    value: str
    type: str | None = None
    token_issuer: str | None = field(default=None, metadata={"alias": "tokenIssuer"})
    expiry_time: str | None = field(default=None, metadata={"alias": "expiryTime"})


@dataclass(frozen=True)
class SessionData(TrocaModel):
    """``SessionData`` schema, as returned by ``GET /sessions``. Its status is NOT on this object - see
    ``SessionStatusEntry``.

    Observed quirks: ``createdAt``/``lastUpdated`` are 2 hours behind ``arrivalDate``/``endDate`` (the latter
    match the OCPP messages, so treat them as the correct times), ``meterStop`` is ``-0.001`` while a session is
    ongoing and ``endDate`` is only present once it's over."""

    session_id: str = field(metadata={"alias": "sessionId"})
    location: LocationId | None = None
    transaction_id: str | None = field(default=None, metadata={"alias": "transactionId"})
    arrival_date: str | None = field(default=None, metadata={"alias": "arrivalDate"})
    end_date: str | None = field(default=None, metadata={"alias": "endDate"})
    user_token: Token | None = field(default=None, metadata={"alias": "userToken"})
    authorization_method: str | None = field(default=None, metadata={"alias": "authorizationMethod"})
    meter_start: float | TEmsValue | None = field(default=None, metadata={"alias": "meterStart"})
    meter_stop: float | TEmsValue | None = field(default=None, metadata={"alias": "meterStop"})
    custom_data: dict[str, Any] = field(default_factory=dict, metadata={"alias": "customData"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})


@dataclass(frozen=True)
class SessionStatusEntry(TrocaModel):
    """``GET /sessions/status`` entry - a timestamped history, so a session's current status is its entry with
    the latest ``timestamp``."""

    session_id: str = field(metadata={"alias": "sessionId"})
    status: SessionStatus
    timestamp: str


@dataclass(frozen=True)
class SessionOperationalData(TrocaModel):
    """``SessionOperationalData`` schema, as returned by ``GET /sessions/operational-data``.

    NOTE: Trialog say SoC is reported here, but neither the schema nor the live server (2026-09-28) has any such
    field - and with the simulator, ``chargedEnergy`` isn't reliably updated from the meter values."""

    session_id: str = field(metadata={"alias": "sessionId"})
    timestamp: str
    charged_energy: float | TEmsValue | None = field(default=None, metadata={"alias": "chargedEnergy"})
    discharged_energy: float | TEmsValue | None = field(default=None, metadata={"alias": "dischargedEnergy"})


# --------------------------------------------------------------------------
# Session commands (charging schedule read/write)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SchedulePeriod(TrocaModel):
    """``SchedulePeriod`` schema - one period of an ``ActivePowerSchedule``."""

    start_time: str = field(metadata={"alias": "startTime"})
    end_time: str = field(metadata={"alias": "endTime"})
    global_: Bounds | None = field(default=None, metadata={"alias": "global"})


@dataclass(frozen=True)
class ActivePowerSchedule(TrocaModel):
    """The (undocumented) schedule body of a ``set_charging_profile`` command. Discovered from a request Trialog
    sent on our behalf - see ``set_charging_profile_parameters`` for how it's wrapped.

    Troca converts this into an OCPP 2.1 ``SetChargingProfile`` (TxProfile, CentralSetpoint operation mode) for
    the session's current transaction, with one OCPP period per ``SchedulePeriod``. Each period's
    ``global_.value`` (kW) becomes a fixed OCPP *setpoint* (not just a limit) with its sign INVERTED - a value
    of -7 was sent to the station as setpoint +7000W (ie charge at 7kW) and +5 as -5000W (discharge at 5kW)."""

    start_time: str = field(metadata={"alias": "startTime"})
    end_time: str = field(metadata={"alias": "endTime"})
    periods: list[SchedulePeriod]
    value_type: str = field(default=ValueType.ACTIVE_POWER, metadata={"alias": "valueType"})


def set_charging_profile_parameters(schedule: ActivePowerSchedule) -> dict[str, Any]:
    """Wraps an ActivePowerSchedule into the ``CommandParameter.parameters`` shape Troca expects for a
    ``set_charging_profile`` command."""
    return {"schedule": {ValueType.ACTIVE_POWER.value: {"recurrency": False, "schedule": schedule.to_dict()}}}


def parse_set_charging_profile_parameters(parameters: dict[str, Any]) -> ActivePowerSchedule | None:
    """Inverse of set_charging_profile_parameters - returns None if parameters isn't of that shape."""
    try:
        raw = parameters["schedule"][ValueType.ACTIVE_POWER.value]["schedule"]
        return ActivePowerSchedule.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CommandParameter(TrocaModel):
    """``CommandParameter`` schema - timestamped command input/response parameters."""

    timestamp: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionCommand(TrocaModel):
    """``SessionCommandData`` schema - both the request body of ``POST /sessions/commands`` and each entry of
    ``GET /sessions/commands``.

    A POSTed command has no location - it isn't validated or dispatched to a station until its location is
    set via ``POST /sessions/commands/locations``. Troca then records a second, internal command of its own
    (with ``customData.clientCommandId`` pointing back at ours) - use ``is_client_command`` to tell them apart."""

    command_id: str = field(metadata={"alias": "commandId"})
    type: SessionCommandType
    input_parameters: CommandParameter | None = field(default=None, metadata={"alias": "inputParameters"})
    custom_data: dict[str, Any] = field(default_factory=dict, metadata={"alias": "customData"})
    issuer_id: str | None = field(default=None, metadata={"alias": "issuerId"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})

    @property
    def is_client_command(self) -> bool:
        return "clientCommandId" not in self.custom_data


@dataclass(frozen=True)
class SessionCommandStatusEntry(TrocaModel):
    """``SessionCommandStatus`` schema, as returned by ``GET /sessions/commands/status``."""

    command_id: str = field(metadata={"alias": "commandId"})
    status: CommandStatus
    timestamp: str


@dataclass(frozen=True)
class SessionCommandLocation(TrocaModel):
    """``SessionCommandLocations`` schema - request body of ``POST /sessions/commands/locations`` and each entry
    of the equivalent GET. For ``set_charging_profile``, the location must be at the ``evse`` level."""

    command_id: str = field(metadata={"alias": "commandId"})
    location_id: LocationId = field(metadata={"alias": "locationId"})


# --------------------------------------------------------------------------
# OCPP passthrough (OCPP 2.x payloads, not Troca's own schemas)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OcppEvse(TrocaModel):
    id: int
    connector_id: int | None = field(default=None, metadata={"alias": "connectorId"})


@dataclass(frozen=True)
class OcppComponent(TrocaModel):
    name: str
    instance: str | None = None
    evse: OcppEvse | None = None


@dataclass(frozen=True)
class OcppVariable(TrocaModel):
    name: str
    instance: str | None = None


@dataclass(frozen=True)
class GetVariableData(TrocaModel):
    """OCPP 2.x ``GetVariableDataType``."""

    component: OcppComponent
    variable: OcppVariable
    attribute_type: str | None = field(default=None, metadata={"alias": "attributeType"})


@dataclass(frozen=True)
class GetVariableResult(TrocaModel):
    """OCPP 2.x ``GetVariableResultType``. ``attribute_value`` is only set when ``attribute_status`` is
    ``Accepted``."""

    attribute_status: str = field(metadata={"alias": "attributeStatus"})
    component: OcppComponent
    variable: OcppVariable
    attribute_type: str | None = field(default=None, metadata={"alias": "attributeType"})
    attribute_value: str | None = field(default=None, metadata={"alias": "attributeValue"})

    @property
    def accepted(self) -> bool:
        return self.attribute_status == "Accepted"


# Pulls the OCPP version off the end of an OCPP connector's name, eg "qocppConnector2.1" -> "2.1"
OCPP_VERSION_PATTERN = re.compile(r"(\d+(?:\.\d+)+)$")


def ocpp_version_from_connector_name(name: str) -> str | None:
    """Troca's OCPP connectors are named for the OCPP version they speak, eg "qocppConnector2.1" -> "2.1"."""
    match = OCPP_VERSION_PATTERN.search(name)
    return None if match is None else match.group(1)


@dataclass(frozen=True)
class OcppTarget:
    """Identifies where OCPP passthrough messages are sent - POST /{connector_name}/ocpp/{ocpp_version}/command/
    {messageType}/{station_name}"""

    connector_name: str  # The Troca connector's name, eg "qocppConnector2.1"
    ocpp_version: str  # eg "2.1"
    station_name: str  # The OCPP charging station identity, eg "FR*TRI*E123"


class OcppChargingProfilePurpose(StrEnum):
    CHARGING_STATION_MAX_PROFILE = "ChargingStationMaxProfile"
    TX_DEFAULT_PROFILE = "TxDefaultProfile"
    TX_PROFILE = "TxProfile"


@dataclass(frozen=True)
class OcppChargingSchedulePeriod(TrocaModel):
    """OCPP 2.1 ``ChargingSchedulePeriodType`` (subset). ``discharge_limit`` is <= 0 (OCPP 2.1 only)."""

    start_period: int = field(metadata={"alias": "startPeriod"})
    limit: float | None = None
    discharge_limit: float | None = field(default=None, metadata={"alias": "dischargeLimit"})


@dataclass(frozen=True)
class OcppChargingSchedule(TrocaModel):
    """OCPP 2.x ``ChargingScheduleType`` (subset)."""

    id: int
    charging_rate_unit: str = field(metadata={"alias": "chargingRateUnit"})
    charging_schedule_period: list[OcppChargingSchedulePeriod] = field(metadata={"alias": "chargingSchedulePeriod"})
    start_schedule: str | None = field(default=None, metadata={"alias": "startSchedule"})
    duration: int | None = None


@dataclass(frozen=True)
class OcppChargingProfile(TrocaModel):
    """OCPP 2.x ``ChargingProfileType`` (subset)."""

    id: int
    stack_level: int = field(metadata={"alias": "stackLevel"})
    charging_profile_purpose: OcppChargingProfilePurpose = field(metadata={"alias": "chargingProfilePurpose"})
    charging_profile_kind: str = field(metadata={"alias": "chargingProfileKind"})
    charging_schedule: list[OcppChargingSchedule] = field(metadata={"alias": "chargingSchedule"})
