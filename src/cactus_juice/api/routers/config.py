from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.api.deps import get_session
from cactus_juice.crud import fetch_csipaus_config, update_csipaus_config
from cactus_juice.model import CSIPAusConfig

router = APIRouter(prefix="/api/config", tags=["config"])


class CSIPAusConfigResponse(BaseModel):
    """The CSIPAusConfig as exposed to the frontend.

    key_pem is a client private key - its contents are never round tripped to the frontend, only whether one has
    been uploaded (has_key_pem)."""

    created_at: datetime | None
    is_aggregator: bool
    nmi: str | None
    client_pen: int | None
    dcap_uri: str | None
    verify_hostname: bool
    verify_ssl: bool
    certificate_pem: str | None
    serca_pem: str | None
    has_key_pem: bool

    @staticmethod
    def from_model(config: CSIPAusConfig | None) -> "CSIPAusConfigResponse":
        if config is None:
            return CSIPAusConfigResponse(
                created_at=None,
                is_aggregator=True,
                nmi=None,
                client_pen=None,
                dcap_uri=None,
                verify_hostname=True,
                verify_ssl=True,
                certificate_pem=None,
                serca_pem=None,
                has_key_pem=False,
            )

        return CSIPAusConfigResponse(
            created_at=config.created_at,
            is_aggregator=config.is_aggregator,
            nmi=config.nmi,
            client_pen=config.client_pen,
            dcap_uri=config.dcap_uri,
            verify_hostname=config.verify_hostname,
            verify_ssl=config.verify_ssl,
            certificate_pem=config.certificate_pem.decode() if config.certificate_pem is not None else None,
            serca_pem=config.serca_pem.decode() if config.serca_pem is not None else None,
            has_key_pem=config.key_pem is not None,
        )


async def _read_upload(upload: UploadFile | None) -> bytes | None:
    if upload is None or not upload.filename:
        return None
    return await upload.read()


@router.get("", response_model=CSIPAusConfigResponse)
async def get_config(session: AsyncSession = Depends(get_session)) -> CSIPAusConfigResponse:
    """Fetches the current CSIPAusConfig (or a set of defaults if none has been configured yet)"""
    current = await fetch_csipaus_config(session)
    return CSIPAusConfigResponse.from_model(current)


@router.put("", response_model=CSIPAusConfigResponse)
async def put_config(  # noqa: ANN201
    session: AsyncSession = Depends(get_session),
    is_aggregator: bool = Form(...),
    nmi: str | None = Form(None),
    client_pen: int | None = Form(None),
    dcap_uri: str | None = Form(None),
    verify_hostname: bool = Form(True),
    verify_ssl: bool = Form(True),
    certificate_pem: UploadFile | None = File(None),
    key_pem: UploadFile | None = File(None),
    serca_pem: UploadFile | None = File(None),
    clear_certificate_pem: bool = Form(False),
    clear_key_pem: bool = Form(False),
    clear_serca_pem: bool = Form(False),
) -> CSIPAusConfigResponse:
    """Updates the CSIPAusConfig, inserting a new historical record (see update_csipaus_config).

    Each of the three PEM files is optional on any given call:
        * upload a file to replace it
        * set the matching clear_*_pem flag to remove it
        * do neither to leave the existing stored value untouched
    """
    current = await fetch_csipaus_config(session)

    new_certificate_pem = await _read_upload(certificate_pem)
    if new_certificate_pem is None and not clear_certificate_pem:
        new_certificate_pem = current.certificate_pem if current is not None else None

    new_key_pem = await _read_upload(key_pem)
    if new_key_pem is None and not clear_key_pem:
        new_key_pem = current.key_pem if current is not None else None

    new_serca_pem = await _read_upload(serca_pem)
    if new_serca_pem is None and not clear_serca_pem:
        new_serca_pem = current.serca_pem if current is not None else None

    values = CSIPAusConfig(
        is_aggregator=is_aggregator,
        certificate_pem=new_certificate_pem,
        key_pem=new_key_pem,
        nmi=nmi,
        client_pen=client_pen,
        dcap_uri=dcap_uri,
        serca_pem=new_serca_pem,
        verify_hostname=verify_hostname,
        verify_ssl=verify_ssl,
    )

    await update_csipaus_config(session, values)
    await session.commit()

    updated = await fetch_csipaus_config(session)
    return CSIPAusConfigResponse.from_model(updated)
