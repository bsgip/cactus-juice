from datetime import datetime

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.api.deps import get_session
from cactus_juice.crud import fetch_troca_config, update_troca_config
from cactus_juice.model import TrocaConfig
from cactus_juice.troca.client import TrocaApiError, TrocaClient
from cactus_juice.troca.models import Connector, ConnectorType

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

    @staticmethod
    def from_model(config: TrocaConfig | None) -> "TrocaConfigResponse":
        if config is None:
            return TrocaConfigResponse(
                created_at=None,
                base_url=None,
                basic_user=None,
                has_basic_password=False,
                connector_id=None,
            )

        return TrocaConfigResponse(
            created_at=config.created_at,
            base_url=config.base_url,
            basic_user=config.basic_user,
            has_basic_password=config.basic_password is not None,
            connector_id=config.connector_id,
        )


class TrocaConfigRequest(BaseModel):
    """Body for updating the TrocaConfig.

    basic_password is optional - omit it (or send null) to keep whatever password is currently on record."""

    base_url: str
    basic_user: str
    basic_password: str | None = None
    connector_id: str | None = None


class TrocaConnectorResponse(BaseModel):
    name: str
    connector_id: str
    connector_type: ConnectorType
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
            created_at=connector.created_at,
            issuer_id=connector.issuer_id,
            last_updated=connector.last_updated,
            alternative_names=connector.alternative_names,
        )


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
    )

    await update_troca_config(session, values)
    await session.commit()

    updated = await fetch_troca_config(session)
    return TrocaConfigResponse.from_model(updated)


@router.get("/connectors", response_model=list[TrocaConnectorResponse])
async def get_troca_connectors(session: AsyncSession = Depends(get_session)) -> list[TrocaConnectorResponse]:
    """Enumerates the connectors available on the configured Troca API.

    Requires a TrocaConfig to already be on record (its base_url/credentials are used to make the call) - errors
    with a 400 if no TrocaConfig has been registered yet."""
    config = await fetch_troca_config(session)
    if config is None:
        raise HTTPException(status_code=400, detail="No TrocaConfig is currently registered")

    async with TrocaClient(config.base_url, config.basic_user, config.basic_password) as client:
        try:
            connectors = await client.get_connectors()
        except TrocaApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except aiohttp.ClientError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach the Troca API: {exc}") from exc

    return [TrocaConnectorResponse.from_model(connector) for connector in connectors]
