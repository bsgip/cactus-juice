import ssl
from dataclasses import dataclass
from ssl import SSLContext

from aiohttp import ClientSession

from cactus_juice.model import CSIPAusConfig


@dataclass(frozen=True, slots=True)
class HttpContext:
    """Used to run all CSIP Aus HTTP connections"""

    session: ClientSession
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class CSIPAusContext:
    """All"""

    http: HttpContext

    is_aggregator_client: bool
    nmi: str
    client_pen: int

    dcap_uri: str


def build_csipaus_context(config: CSIPAusConfig) -> CSIPAusContext:
    ssl_context = SSLContext(ssl.PROTOCOL_TLSv1_2)  # TLS 1.2 required by 2030.5

    # ECDHE-ECDSA-AES128-CCM8 is mandatory per 2030.5; keep the broad set too so RSA servers still negotiate.
    # CCM8 must be listed before ALL. DEFAULT can't be used as it permanently excludes CCM8.
    # !aNULL drops the anonymous (unauthenticated) suites ALL would otherwise allow when verify-ssl is off.
    ssl_context.set_ciphers("ECDHE-ECDSA-AES128-CCM8:ALL:!aNULL")
    ssl_context.check_hostname = config.verify_hostname
    ssl_context.verify_mode = ssl.CERT_REQUIRED if config.verify_ssl else ssl.CERT_NONE
    if config.verify_ssl:
        if config.serca_pem is None:
            raise

        ssl_context.load_verify_locations(cafile=config.serca_pem)
        try:
            ssl_context.load_verify_locations(cafile=serca_pem_path)
        except Exception as exc:
            raise ConfigError(
                f"Failure loading SERCA certificate for {client_config_id} from SERCA PEM file '{serca_pem_path}'"
            ) from exc

    try:
        ssl_context.load_cert_chain(client_config.certificate_file, client_config.key_file)
    except Exception as exc:
        raise ConfigError(
            f"Failure loading client certificate chain for {client_config_id} from"
            + f"cert file {client_config.certificate_file} and key file {client_config.key_file}. {exc}"
        ) from exc
