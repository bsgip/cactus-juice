from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.api.deps import get_session
from cactus_juice.crud import fetch_troca_config, update_troca_config
from cactus_juice.model import TrocaConfig
from cactus_juice.troca.client import TrocaApiError, TrocaClient
from cactus_juice.troca.models import Connector, ConnectorType, ScheduleSyncMode, ocpp_version_from_connector_name


def _blank_to_none(value: str | None) -> str | None:
    """Discoverable values left blank mean "discover it" - normalise them to None."""
    return value.strip() or None if value is not None else None


router = APIRouter(prefix="/api/troca-config", tags=["troca-config"])


class TrocaConfigResponse(BaseModel):
    """The TrocaConfig as exposed to the frontend.

    basic_password is a credential for the Troca API - its contents are never round tripped to the frontend, only
    whether one has been set (has_basic_password)."""

    created_at: datetime | None
    base_url: str | None
    basic_user: str | None
    has_basic_password: bool
    connector_id: str | None
    reading_poll_rate_seconds: int | None
    ramp_step_seconds: int | None
    schedule_poll_rate_seconds: int | None
    metadata_poll_rate_seconds: int | None
    schedule_sync_mode: ScheduleSyncMode | None
    ocpp_connector_name: str | None
    ocpp_version: str | None
    ocpp_station_name: str | None
    ocpp_evse_nb: int | None
    evse_id: str | None

    @staticmethod
    def from_model(config: TrocaConfig | None) -> "TrocaConfigResponse":
        if config is None:
            return TrocaConfigResponse(
                created_at=None,
                base_url=None,
                basic_user=None,
                has_basic_password=False,
                connector_id=None,
                reading_poll_rate_seconds=None,
                ramp_step_seconds=None,
                schedule_poll_rate_seconds=None,
                metadata_poll_rate_seconds=None,
                schedule_sync_mode=None,
                ocpp_connector_name=None,
                ocpp_version=None,
                ocpp_station_name=None,
                ocpp_evse_nb=None,
                evse_id=None,
            )

        return TrocaConfigResponse(
            created_at=config.created_at,
            base_url=config.base_url,
            basic_user=config.basic_user,
            has_basic_password=config.basic_password is not None,
            connector_id=config.connector_id,
            reading_poll_rate_seconds=config.reading_poll_rate_seconds,
            ramp_step_seconds=config.ramp_step_seconds,
            schedule_poll_rate_seconds=config.schedule_poll_rate_seconds,
            metadata_poll_rate_seconds=config.metadata_poll_rate_seconds,
            schedule_sync_mode=ScheduleSyncMode(config.schedule_sync_mode),
            ocpp_connector_name=config.ocpp_connector_name,
            ocpp_version=config.ocpp_version,
            ocpp_station_name=config.ocpp_station_name,
            ocpp_evse_nb=config.ocpp_evse_nb,
            evse_id=config.evse_id,
        )


class TrocaConfigRequest(BaseModel):
    """Body for updating the TrocaConfig.

    basic_password is optional - omit it (or send null) to keep whatever password is currently on record.

    ocpp_connector_name / ocpp_version / ocpp_station_name / ocpp_evse_nb / evse_id can all be discovered via the
    Troca API (see /discovery) - leave them null to have them discovered on every poll instead."""

    base_url: str
    basic_user: str
    basic_password: str | None = None
    connector_id: str | None = None
    reading_poll_rate_seconds: int = Field(default=20, gt=0)
    ramp_step_seconds: int = Field(default=3, gt=0)
    schedule_poll_rate_seconds: int = Field(default=10, gt=0)
    metadata_poll_rate_seconds: int = Field(default=30, gt=0)
    schedule_sync_mode: ScheduleSyncMode = ScheduleSyncMode.OCPP
    ocpp_connector_name: str | None = None
    ocpp_version: str | None = None
    ocpp_station_name: str | None = None
    ocpp_evse_nb: int | None = Field(default=None, ge=0)
    evse_id: str | None = None


class TrocaConnectorResponse(BaseModel):
    name: str
    connector_id: str
    connector_type: ConnectorType
    ocpp_version: str | None  # Derived from name - only meaningful for OCPP connectors
    created_at: str | None
    issuer_id: str | None
    last_updated: str | None
    alternative_names: list[str]

    @staticmethod
    def from_model(connector: Connector) -> "TrocaConnectorResponse":
        return TrocaConnectorResponse(
            name=connector.name,
            connector_id=connector.connector_id,
            connector_type=connector.connector_type,
            ocpp_version=(
                ocpp_version_from_connector_name(connector.name)
                if connector.connector_type == ConnectorType.Q_OCPP
                else None
            ),
            created_at=connector.created_at,
            issuer_id=connector.issuer_id,
            last_updated=connector.last_updated,
            alternative_names=connector.alternative_names,
        )


class TrocaStationResponse(BaseModel):
    station_id: str
    name: str  # The OCPP charging station identity - ie a candidate TrocaConfig.ocpp_station_name
    model: str | None
    vendor_id: str | None


class TrocaEvseResponse(BaseModel):
    evse_id: str  # Troca's UUID - ie a candidate TrocaConfig.evse_id
    name: str | None
    station_name: str | None
    evse_nb: int | None  # The OCPP EVSE number - ie a candidate TrocaConfig.ocpp_evse_nb


class TrocaDiscoveryResponse(BaseModel):
    """Everything on the configured Troca API that can be used to fill out a TrocaConfig."""

    connectors: list[TrocaConnectorResponse]
    stations: list[TrocaStationResponse]
    evses: list[TrocaEvseResponse]


@router.get("", response_model=TrocaConfigResponse)
async def get_troca_config(session: AsyncSession = Depends(get_session)) -> TrocaConfigResponse:
    """Fetches the current TrocaConfig (or a set of defaults if none has been configured yet)"""
    current = await fetch_troca_config(session)
    return TrocaConfigResponse.from_model(current)


@router.put("", response_model=TrocaConfigResponse)
async def put_troca_config(
    body: TrocaConfigRequest, session: AsyncSession = Depends(get_session)
) -> TrocaConfigResponse:
    """Updates the TrocaConfig, inserting a new historical record (see update_troca_config).

    basic_password is optional on any given call - if omitted, whatever password is currently stored is preserved.
    It's mandatory the very first time a TrocaConfig is created."""
    current = await fetch_troca_config(session)

    new_basic_password = body.basic_password
    if new_basic_password is None:
        new_basic_password = current.basic_password if current is not None else None
    if new_basic_password is None:
        raise HTTPException(status_code=400, detail="basic_password is required when creating a new TrocaConfig")

    values = TrocaConfig(
        base_url=body.base_url,
        basic_user=body.basic_user,
        basic_password=new_basic_password,
        connector_id=body.connector_id,
        reading_poll_rate_seconds=body.reading_poll_rate_seconds,
        ramp_step_seconds=body.ramp_step_seconds,
        schedule_poll_rate_seconds=body.schedule_poll_rate_seconds,
        metadata_poll_rate_seconds=body.metadata_poll_rate_seconds,
        schedule_sync_mode=body.schedule_sync_mode.value,
        ocpp_connector_name=_blank_to_none(body.ocpp_connector_name),
        ocpp_version=_blank_to_none(body.ocpp_version),
        ocpp_station_name=_blank_to_none(body.ocpp_station_name),
        ocpp_evse_nb=body.ocpp_evse_nb,
        evse_id=_blank_to_none(body.evse_id),
    )

    await update_troca_config(session, values)
    await session.commit()

    updated = await fetch_troca_config(session)
    return TrocaConfigResponse.from_model(updated)


async def _configured_client(session: AsyncSession) -> TrocaClient:
    """A TrocaClient for the current TrocaConfig - errors with a 400 if no TrocaConfig has been registered yet."""
    config = await fetch_troca_config(session)
    if config is None:
        raise HTTPException(status_code=400, detail="No TrocaConfig is currently registered")
    return TrocaClient(config.base_url, config.basic_user, config.basic_password)


@asynccontextmanager
async def _troca_errors_as_502() -> AsyncIterator[None]:
    """Translates failures talking to the Troca API into a clean 502."""
    try:
        yield
    except TrocaApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach the Troca API: {exc}") from exc


@router.get("/connectors", response_model=list[TrocaConnectorResponse])
async def get_troca_connectors(session: AsyncSession = Depends(get_session)) -> list[TrocaConnectorResponse]:
    """Enumerates the connectors available on the configured Troca API.

    Requires a TrocaConfig to already be on record (its base_url/credentials are used to make the call) - errors
    with a 400 if no TrocaConfig has been registered yet."""
    async with await _configured_client(session) as client, _troca_errors_as_502():
        connectors = await client.get_connectors()

    return [TrocaConnectorResponse.from_model(connector) for connector in connectors]


@router.get("/discovery", response_model=TrocaDiscoveryResponse)
async def get_troca_discovery(session: AsyncSession = Depends(get_session)) -> TrocaDiscoveryResponse:
    """Enumerates the connectors, charging stations and EVSEs on the configured Troca API - ie the candidate values
    for every discoverable TrocaConfig field.

    Requires a TrocaConfig to already be on record - errors with a 400 if no TrocaConfig has been registered yet."""
    async with await _configured_client(session) as client, _troca_errors_as_502():
        connectors = await client.get_connectors()
        stations = await client.get_stations()
        evses = await client.get_evses()

    return TrocaDiscoveryResponse(
        connectors=[TrocaConnectorResponse.from_model(c) for c in connectors],
        stations=[
            TrocaStationResponse(station_id=s.station_id, name=s.name, model=s.model, vendor_id=s.vendor_id)
            for s in stations
        ],
        evses=[
            TrocaEvseResponse(
                evse_id=e.evse_id,
                name=e.name,
                station_name=e.evse_uid.station_name if e.evse_uid else None,
                evse_nb=e.evse_uid.evse_nb if e.evse_uid else None,
            )
            for e in evses
        ],
    )
