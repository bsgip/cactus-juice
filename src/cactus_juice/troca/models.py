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
    """``CommandStatus`` schema."""

    X_EMS = "xEmsConnector"
    EMS = "EmsConnector"
    Q_OCPP = "QOcppConnector"
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


class EvseStatus(StrEnum):
    """``EvseStatus`` schema."""

    UNKNOWN = "unknown"
    AVAILABLE = "available"
    BLOCKED = "blocked"
    CHARGING = "charging"
    RESERVED = "reserved"
    INOPERATIVE = "inoperative"
    OUTOFORDER = "outoforder"
    PLANNED = "planned"


class StationStatus(StrEnum):
    """``StationData.status`` enum (distinct casing/values from EvseStatus)."""

    UNKNOWN = "UNKNOWN"
    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    CHARGING = "CHARGING"
    INOPERATIVE = "INOPERATIVE"
    OUTOFORDER = "OUTOFORDER"
    RESERVED = "RESERVED"
    OFFLINE = "OFFLINE"


class ChargerCapability(StrEnum):
    """``ChargerCapability`` schema -- feature flags, not electrical ratings."""

    CHARGING_PROFILE_CAPABILITY = "charging_profile_capability"
    CREDIT_CARD_PAYABLE = "credit_card_payable"
    REMOTE_STOP_START_CAPABLE = "remote_stop_start_capable"
    RESERVABLE = "reservable"
    RFID_READER = "rfid_reader"
    UNLOCK_CAPABLE = "unlock_capable"
    UNKNOWN = "unknown"


class ChargingRateUnit(StrEnum):
    """OCPP charging rate unit. Not enumerated by the Troca spec itself --

    ``SessionCommandData.inputParameters`` is documented only as a free-form
    object -- but a live probe accepted a standard OCPP ``ChargingSchedule``
    shape, so this follows OCPP 1.6/2.x conventions until Troca confirms
    otherwise.
    """

    WATTS = "W"
    AMPS = "A"


class ChargingProfilePurpose(StrEnum):
    """OCPP charging profile purpose (see ``ChargingRateUnit`` caveat)."""

    CHARGE_POINT_MAX_PROFILE = "ChargePointMaxProfile"
    TX_DEFAULT_PROFILE = "TxDefaultProfile"
    TX_PROFILE = "TxProfile"


class ChargingProfileKind(StrEnum):
    """OCPP charging profile kind (see ``ChargingRateUnit`` caveat)."""

    ABSOLUTE = "Absolute"
    RECURRING = "Recurring"
    RELATIVE = "Relative"


class RecurrencyKind(StrEnum):
    """OCPP recurrency kind (see ``ChargingRateUnit`` caveat)."""

    DAILY = "Daily"
    WEEKLY = "Weekly"


class SessionStatus(StrEnum):
    """``SessionStatus`` schema."""

    UNKNOWN = "unknown"
    PENDING = "pending"
    CHARGING = "charging"
    COMPLETED = "completed"
    INVALID = "invalid"


class AuthenticationMethod(StrEnum):
    """``AuthenticationMethod`` schema."""

    UNKNOWN = "unknown"
    REMOTE_START = "remote start"
    REAL_TIME_DEMAND = "real time demand"
    AUTHORIZATION_LIST = "authorization list"
    FREE_FOR_ALL = "free for all"
    LOCAL = "local"


class ValueType(StrEnum):
    """``ValueType`` schema -- the unit a ``ValuePerType`` figure is in."""

    ACTIVE_POWER = "Active power"
    REACTIVE_POWER = "Reactive power"
    APPARENT_POWER = "Apparent power"
    ENERGY = "Energy"
    PERCENTAGE = "Percentage"
    RATIO = "Ratio"
    DURATION = "Duration"


# --------------------------------------------------------------------------
# Shared value types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Coordinates(TrocaModel):
    """``GeoLocation`` schema. Observed live as string lat/lng, e.g. "0"."""

    latitude: str | None = None
    longitude: str | None = None


@dataclass(frozen=True)
class LocalizedText(TrocaModel):
    """``Text`` schema."""

    language: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class ImageInfo(TrocaModel):
    """``ImageInfo`` schema."""

    url: str | None = None
    thumbnail: str | None = None
    extension: str | None = None
    category: str | None = None
    width: int | None = None
    height: int | None = None


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Connector(TrocaModel):
    """``Connector`` schema, as returned by ``GET /config/connectors``."""

    name: str
    connector_id: str = field(metadata={"alias": "connectorId"})
    connector_type: ConnectorType = field(metadata={"alias": "connectorId"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    custom_data: dict[str, Any] = field(default_factory=dict, metadata={"alias": "customData"})
    enabling_modules: list[str] = field(default_factory=list, metadata={"alias": "enablingModules"})
    issuer_id: str | None = field(default=None, metadata={"alias": "issuerId"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})
    alternative_names: list[str] = field(default_factory=list, metadata={"alias": "alternativeNames"})


# --------------------------------------------------------------------------
# Device metadata (Pool / Station / EVSE)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Pool(TrocaModel):
    """``PoolData`` schema, as returned by ``GET /structure/pools``."""

    pool_id: str = field(metadata={"alias": "poolId"})
    name: str
    type: str | None = None
    status: str | None = None
    address: str | None = None
    city: str | None = None
    area_code: str | None = field(default=None, metadata={"alias": "areaCode"})
    postal_code: str | None = field(default=None, metadata={"alias": "postalCode"})
    coordinates: Coordinates | None = None
    time_zone: str | None = field(default=None, metadata={"alias": "timeZone"})
    alternative_names: list[str] = field(default_factory=list, metadata={"alias": "alternativeNames"})
    issuer_id: str | None = field(default=None, metadata={"alias": "issuerId"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})


@dataclass(frozen=True)
class Station(TrocaModel):
    """``StationData`` schema, as returned by ``GET /structure/stations``."""

    station_id: str = field(metadata={"alias": "stationId"})
    name: str
    evses: list[str] = field(default_factory=list)
    multiplier: float | None = None
    status: StationStatus | None = None
    math_function: str | None = field(default=None, metadata={"alias": "mathFunction"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})


@dataclass(frozen=True)
class EvseUid(TrocaModel):
    station_name: str | None = field(default=None, metadata={"alias": "stationName"})
    evse_nb: int | None = field(default=None, metadata={"alias": "evseNb"})


@dataclass(frozen=True)
class Evse(TrocaModel):
    """``EvseData`` schema, as returned by ``GET /structure/evses``.

    NOTE: this is the metadata Troca currently exposes about a connected
    device -- vendor/model/capability flags and connector IDs. It does NOT
    include electrical ratings (max power, charge/discharge rate, min/max
    voltage); those fields are not present in the published schema. See the
    module docstring.
    """

    evse_id: str = field(metadata={"alias": "evseId"})
    evse_uid: EvseUid | None = field(default=None, metadata={"alias": "evseUid"})
    name: str | None = None
    vendor_id: str | None = field(default=None, metadata={"alias": "vendorId"})
    model: str | None = None
    capability: list[ChargerCapability] = field(default_factory=list)
    status: EvseStatus | None = None
    coordinates: Coordinates | None = None
    reference: str | None = None
    directions: list[LocalizedText] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)
    connectors: list[str] = field(default_factory=list)
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})


# --------------------------------------------------------------------------
# Power usage readings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MeteringReading(TrocaModel):
    """``MeteringData`` schema, as returned by ``GET /observation-points/metering-data``."""

    timestamp: str
    instantaneous_active_power: float | None = field(default=None, metadata={"alias": "instantaneousActivePower"})
    instantaneous_reactive_power: float | None = field(default=None, metadata={"alias": "instantaneousReactivePower"})
    instantaneous_apparent_power: float | None = field(default=None, metadata={"alias": "instantaneousApparentPower"})
    power_factor: float | None = field(default=None, metadata={"alias": "powerFactor"})


# --------------------------------------------------------------------------
# Sessions -- this is where EV charge/discharge-rate metadata actually lives
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ValuePerType(TrocaModel):
    """``ValuePerType`` schema. NOTE: the spec names the numeric field

    ``values`` (plural) despite it holding a single number; kept as an alias
    so the Python attribute can be sensibly named ``value``.
    """

    type: ValueType | None = None
    value: float | None = field(default=None, metadata={"alias": "values"})


@dataclass(frozen=True)
class SessionEvseConnectorUid(TrocaModel):
    evse_uid: EvseUid | None = field(default=None, metadata={"alias": "evseUid"})
    connector_nb: int | None = field(default=None, metadata={"alias": "connectorNb"})


@dataclass(frozen=True)
class SessionUserConstraints(TrocaModel):
    departure_time: str | None = field(default=None, metadata={"alias": "departureTime"})
    required_energy: float | None = field(default=None, metadata={"alias": "requiredEnergy"})


@dataclass(frozen=True)
class SessionConstraints(TrocaModel):
    """The EV's own charge/discharge-rate metadata for this session -- this

    is the answer to "what's the max power / charge rate of the connected
    EV", not a static EVSE property (see module docstring / client docs).
    """

    max_charge_level: ValuePerType | None = field(default=None, metadata={"alias": "maxChargeLevel"})
    min_charge_level: ValuePerType | None = field(default=None, metadata={"alias": "minChargeLevel"})
    max_discharge_level: ValuePerType | None = field(default=None, metadata={"alias": "maxDischargeLevel"})
    min_discharge_level: ValuePerType | None = field(default=None, metadata={"alias": "minDischargeLevel"})
    charge_threshold_level: ValuePerType | None = field(default=None, metadata={"alias": "chargeThresholdLevel"})
    discharge_threshold_level: ValuePerType | None = field(default=None, metadata={"alias": "dischargeThresholdLevel"})
    battery_capacity: float | None = field(default=None, metadata={"alias": "batteryCapacity"})
    v2g_compatible: bool | None = field(default=None, metadata={"alias": "v2gCompatible"})


@dataclass(frozen=True)
class SessionData(TrocaModel):
    """``SessionData`` schema, as returned by ``GET /sessions``.

    Only exists once a vehicle is actually plugged in and a session has
    been created -- there is no way to query an EVSE's rated capability
    ahead of a session via this object.
    """

    session_id: str = field(metadata={"alias": "sessionId"})
    evse_id: str | None = field(default=None, metadata={"alias": "evseId"})
    evse_connector_uid: SessionEvseConnectorUid | None = field(default=None, metadata={"alias": "evseConnectorUid"})
    emsp_id: str | None = field(default=None, metadata={"alias": "emspId"})
    transaction_id: str | None = field(default=None, metadata={"alias": "transactionId"})
    user_constraints: SessionUserConstraints | None = field(default=None, metadata={"alias": "userConstraints"})
    constraints: SessionConstraints | None = None
    status: SessionStatus | None = None
    authorization_method: AuthenticationMethod | None = field(default=None, metadata={"alias": "authorizationMethod"})
    user_token: str | None = field(default=None, metadata={"alias": "userToken"})
    arrival_date: str | None = field(default=None, metadata={"alias": "arrivalDate"})
    end_date: str | None = field(default=None, metadata={"alias": "endDate"})
    meter_start: float | None = field(default=None, metadata={"alias": "meterStart"})
    meter_stop: float | None = field(default=None, metadata={"alias": "meterStop"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})


# --------------------------------------------------------------------------
# Charging schedule (charging profile) read/write
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ChargingSchedulePeriod(TrocaModel):
    start_period: int = field(metadata={"alias": "startPeriod"})
    limit: float
    number_phases: int | None = field(default=None, metadata={"alias": "numberPhases"})


@dataclass(frozen=True)
class ChargingSchedule(TrocaModel):
    charging_rate_unit: ChargingRateUnit = field(metadata={"alias": "chargingRateUnit"})
    charging_schedule_period: list[ChargingSchedulePeriod] = field(metadata={"alias": "chargingSchedulePeriod"})
    duration: int | None = None
    start_schedule: str | None = field(default=None, metadata={"alias": "startSchedule"})
    min_charging_rate: float | None = field(default=None, metadata={"alias": "minChargingRate"})


@dataclass(frozen=True)
class ChargingProfile(TrocaModel):
    """Shape of ``SessionCommandData.inputParameters`` for a
    ``set_charging_profile`` command. This is the object used to express a
    time-bounded power limit.
    """

    charging_profile_id: int = field(metadata={"alias": "chargingProfileId"})
    stack_level: int = field(metadata={"alias": "stackLevel"})
    charging_profile_purpose: ChargingProfilePurpose = field(metadata={"alias": "chargingProfilePurpose"})
    charging_profile_kind: ChargingProfileKind = field(metadata={"alias": "chargingProfileKind"})
    charging_schedule: ChargingSchedule = field(metadata={"alias": "chargingSchedule"})
    recurrency_kind: RecurrencyKind | None = field(default=None, metadata={"alias": "recurrencyKind"})
    valid_from: str | None = field(default=None, metadata={"alias": "validFrom"})
    valid_to: str | None = field(default=None, metadata={"alias": "validTo"})
    transaction_id: int | None = field(default=None, metadata={"alias": "transactionId"})


@dataclass(frozen=True)
class SessionCommandRequest(TrocaModel):
    """Request body for ``POST /sessions/commands``, per ``SessionCommandData``.

    ``input_parameters`` is a raw dict (rather than typed as
    ``ChargingProfile``) because the same envelope is reused for
    ``start_new_transaction`` / ``stop_transaction`` / ``authorize``, each
    with a different payload shape. Build it with ``ChargingProfile(...).to_dict()``
    for ``set_charging_profile`` / ``clear_charging_profile`` commands.
    """

    id: str
    type: SessionCommandType
    pool_id: str | None = field(default=None, metadata={"alias": "poolId"})
    station_id: str | None = field(default=None, metadata={"alias": "stationId"})
    evse_id: int | None = field(default=None, metadata={"alias": "evseId"})
    input_parameters: dict[str, Any] | None = field(default=None, metadata={"alias": "inputParameters"})


@dataclass(frozen=True)
class SessionCommand(TrocaModel):
    """Response shape of ``GET /sessions/commands``.

    NOTE: this does NOT match the documented ``SessionCommandData`` schema.
    Live probing of the dev tenant on 2026-09-21 showed the server returns
    ``commandId`` (not the client-supplied ``id``), plus ``issuerId`` and
    ``customData`` that aren't in the spec at all, while ``receivedAt`` from
    the spec was absent. Fields below reflect the observed response;
    ``id`` is kept for round-tripping a request you just sent, in case a
    future server revision echoes it back.
    """

    command_id: str | None = field(default=None, metadata={"alias": "commandId"})
    id: str | None = None
    type: SessionCommandType | None = None
    status: CommandStatus | None = None
    pool_id: str | None = field(default=None, metadata={"alias": "poolId"})
    station_id: str | None = field(default=None, metadata={"alias": "stationId"})
    evse_id: int | None = field(default=None, metadata={"alias": "evseId"})
    input_parameters: dict[str, Any] | None = field(default=None, metadata={"alias": "inputParameters"})
    results: dict[str, Any] | None = None
    issuer_id: str | None = field(default=None, metadata={"alias": "issuerId"})
    custom_data: dict[str, Any] = field(default_factory=dict, metadata={"alias": "customData"})
    received_at: str | None = field(default=None, metadata={"alias": "receivedAt"})
    created_at: str | None = field(default=None, metadata={"alias": "createdAt"})
    last_updated: str | None = field(default=None, metadata={"alias": "lastUpdated"})
