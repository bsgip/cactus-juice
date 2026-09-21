from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.api.deps import get_session
from cactus_juice.crud import create_satec_config, delete_satec_config, fetch_satec_configs, update_satec_config
from cactus_juice.model import SatecConfig

router = APIRouter(prefix="/api/satec-config", tags=["satec-config"])

# Mirrors satec.meter.PROFILES' keys / the --model/--parity argparse choices.
SatecModel = Literal["em133", "em235"]
SatecParity = Literal["N", "E", "O"]


class SatecConfigResponse(BaseModel):
    """A single SatecConfig as exposed to the frontend - unlike CSIPAusConfig/TrocaConfig there can be many of
    these live at once (one per meter being polled)."""

    id: int
    created_at: datetime
    label: str
    poll_rate_seconds: float
    model: SatecModel
    host: str | None
    port: str | None
    port_tcp: int
    unit: int
    baud: int
    parity: SatecParity
    timeout_seconds: float
    include_phases: bool
    include_energy: bool
    float_mode: bool

    @staticmethod
    def from_model(config: SatecConfig) -> "SatecConfigResponse":
        return SatecConfigResponse(
            id=config.satec_config_id,
            created_at=config.created_at,
            label=config.label,
            poll_rate_seconds=config.poll_rate_seconds,
            model=config.model,  # ty: ignore[invalid-argument-type]
            host=config.host,
            port=config.port,
            port_tcp=config.port_tcp,
            unit=config.unit,
            baud=config.baud,
            parity=config.parity,  # ty: ignore[invalid-argument-type]
            timeout_seconds=config.timeout_seconds,
            include_phases=config.include_phases,
            include_energy=config.include_energy,
            float_mode=config.float_mode,
        )


class SatecConfigRequest(BaseModel):
    """Body for creating/updating a SatecConfig."""

    label: str
    poll_rate_seconds: float = Field(gt=0)
    model: SatecModel = "em133"
    host: str | None = None
    port: str | None = "/dev/ttyUSB0"
    port_tcp: int = 502
    unit: int = 1
    baud: int = 19200
    parity: SatecParity = "N"
    timeout_seconds: float = 1.0
    include_phases: bool = False
    include_energy: bool = False
    float_mode: bool = False

    def to_model(self) -> SatecConfig:
        return SatecConfig(
            label=self.label,
            poll_rate_seconds=self.poll_rate_seconds,
            model=self.model,
            host=self.host,
            port=self.port,
            port_tcp=self.port_tcp,
            unit=self.unit,
            baud=self.baud,
            parity=self.parity,
            timeout_seconds=self.timeout_seconds,
            include_phases=self.include_phases,
            include_energy=self.include_energy,
            float_mode=self.float_mode,
        )


@router.get("", response_model=list[SatecConfigResponse])
async def list_satec_configs(session: AsyncSession = Depends(get_session)) -> list[SatecConfigResponse]:
    """Lists every registered SatecConfig - there's no "current" record, every row is a live meter."""
    configs = await fetch_satec_configs(session)
    return [SatecConfigResponse.from_model(c) for c in configs]


@router.post("", response_model=SatecConfigResponse, status_code=201)
async def post_satec_config(
    body: SatecConfigRequest, session: AsyncSession = Depends(get_session)
) -> SatecConfigResponse:
    """Registers a new SatecConfig - existing ones are left untouched."""
    created = await create_satec_config(session, body.to_model())
    # Server-generated columns (id, created_at) are populated on `created` by flush()'s INSERT...RETURNING - read
    # them now, since commit() below would otherwise expire the instance and force an extra query to re-read them.
    response = SatecConfigResponse.from_model(created)
    await session.commit()
    return response


@router.put("/{satec_config_id}", response_model=SatecConfigResponse)
async def put_satec_config(
    satec_config_id: int, body: SatecConfigRequest, session: AsyncSession = Depends(get_session)
) -> SatecConfigResponse:
    """Updates an existing SatecConfig in place - 404s if no row with that id exists."""
    updated = await update_satec_config(session, satec_config_id, body.to_model())
    if updated is None:
        raise HTTPException(status_code=404, detail=f"No SatecConfig with id {satec_config_id}")

    response = SatecConfigResponse.from_model(updated)
    await session.commit()
    return response


@router.delete("/{satec_config_id}", status_code=204)
async def remove_satec_config(satec_config_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    """Deletes a SatecConfig by id - 404s if no row with that id exists."""
    deleted = await delete_satec_config(session, satec_config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No SatecConfig with id {satec_config_id}")

    await session.commit()
    return Response(status_code=204)
