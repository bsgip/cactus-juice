import hashlib
import os
import ssl
import tempfile
import urllib.parse
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from ssl import SSLContext

from aiohttp import ClientSession, TCPConnector

from cactus_juice.csipaus.sep2 import convert_lfdi_to_sfdi, lfdi_from_cert_bytes
from cactus_juice.error import ConfigError
from cactus_juice.model import CSIPAusConfig

# This isn't ideal but we NEED a disk location to write certs to in order to load them with the
# python crypto library. We take as many precautions as we can but ideally you shouldn't be deploying
# this in a "unsecured" environment
SECURE_TEMP_DIR_ROOT = os.environ.get("SECURE_TEMP_DIR_ROOT", "/dev/shm")  # noqa: S108 # nosec


@dataclass(frozen=True, slots=True)
class HttpContext:
    """Used to run all CSIP Aus HTTP connections"""

    session: ClientSession  # Will have base_uri to server host
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class CSIPAusContext:
    """All data/connections for interacting with a CSIP-AUS utility server as a client"""

    http: HttpContext

    is_aggregator_client: bool  # True - aggregator client, False - device client
    nmi: str | None
    client_pen: int

    client_lfdi: str  # The LFDI of the client's certificate
    client_sfdi: int  # The SFDI of the client's certificate

    edev_lfdi: str  # The EndDevice LFDI that this client will manage. Same as client_lfdi for device client
    edev_sfdi: int  # The EndDevice SFDI that this client will manage. Same as client_sfdi for device client

    dcap_path: str  # The DeviceCapability path of the server - will be relative to base_uri in http.session


@contextmanager
def _secure_tempfile(data: bytes) -> Generator[Path]:  # noqa: S108
    """
    Write `data` to a restrictively-permissioned NamedTemporaryFile
    backed by tmpfs, yield its path, and guarantee cleanup.
    """
    fd, path = tempfile.mkstemp(dir=SECURE_TEMP_DIR_ROOT)
    try:
        # Lock down permissions before writing any sensitive bytes.
        os.chmod(path, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        yield Path(path)
    finally:
        # Best-effort overwrite before unlink to reduce residual exposure.
        try:
            length = os.path.getsize(path)
            with open(path, "ba+") as f:
                f.seek(0)
                f.write(b"\x00" * length)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass
        os.unlink(path)


def build_dcap_parts(dcap_uri: str) -> tuple[str, str]:
    """Extracts the (base_uri, dcap_path) from the server device_capability_uri"""
    dcap_host: str | None = None
    dcap_path: str | None = None
    dcap_scheme: str | None = None
    try:
        url = urllib.parse.urlparse(dcap_uri)
    except Exception as exc:
        raise ConfigError(f"device_capability_uri '{dcap_uri}' couldn't be parsed.") from exc
    dcap_host = url.netloc
    dcap_path = url.path
    dcap_scheme = url.scheme
    if not dcap_path:
        dcap_path = "/"
    if dcap_scheme not in {"https", "http"}:
        raise ConfigError(f"Unsupported scheme {dcap_scheme} for '{dcap_uri}'.")
    return (f"{dcap_scheme}://{dcap_host}/", dcap_path)


def generate_aggregator_lfdi(nmi: str | None, pen: int) -> str:
    """Generates a "unique" valid LFDI that can represent an EndDevice for an aggregator client"""

    hash = hashlib.md5(data=b"" if nmi is None else nmi.encode(), usedforsecurity=False)
    return hash.hexdigest().upper()[:32] + f"{pen:08}"


def build_csipaus_context(config: CSIPAusConfig) -> CSIPAusContext:
    """Builds a CSIPAusContext from the specified config entries. Raises ConfigError if there are issues / missing
    elements in the supplied config. Responsibility for cleaning up the allocated SSLContext falls to the caller of
    this function."""

    if config.dcap_uri is None:
        raise ConfigError("No dcap_uri is specified")

    base_uri, dcap_path = build_dcap_parts(config.dcap_uri)

    if config.certificate_pem is None or config.key_pem is None:
        raise ConfigError("Missing client key/certificate data - cannot create CSIPAusContext")

    try:
        client_pen = config.client_pen or 1
        client_lfdi = lfdi_from_cert_bytes(config.certificate_pem)
        client_sfdi = convert_lfdi_to_sfdi(client_lfdi)
        if config.is_aggregator:
            edev_lfdi = generate_aggregator_lfdi(config.nmi, client_pen)
            edev_sfdi = convert_lfdi_to_sfdi(edev_lfdi)
        else:
            edev_lfdi = client_lfdi
            edev_sfdi = client_sfdi
    except Exception as exc:
        raise ConfigError("Failure extracting LFDI/SFDI from client certificate data.") from exc

    ssl_context = SSLContext(ssl.PROTOCOL_TLSv1_2)  # TLS 1.2 required by 2030.5

    # ECDHE-ECDSA-AES128-CCM8 is mandatory per 2030.5; keep the broad set too so RSA servers still negotiate.
    # CCM8 must be listed before ALL. DEFAULT can't be used as it permanently excludes CCM8.
    # !aNULL drops the anonymous (unauthenticated) suites ALL would otherwise allow when verify-ssl is off.
    ssl_context.set_ciphers("ECDHE-ECDSA-AES128-CCM8:ALL:!aNULL")
    ssl_context.check_hostname = config.verify_hostname
    ssl_context.verify_mode = ssl.CERT_REQUIRED if config.verify_ssl else ssl.CERT_NONE
    if config.verify_ssl:
        if config.serca_pem is None:
            raise ConfigError("Missing SERCA certificate data with verify_ssl=True - cannot create CSIPAusContext")

        try:
            ssl_context.load_verify_locations(cadata=config.serca_pem.decode())
        except Exception as exc:
            raise ConfigError(
                f"Failure loading SERCA certificate from {len(config.serca_pem)} SERCA PEM bytes"
            ) from exc

    try:
        with _secure_tempfile(config.certificate_pem) as cert_file:
            with _secure_tempfile(config.key_pem) as key_file:
                ssl_context.load_cert_chain(cert_file, key_file)
    except Exception as exc:
        raise ConfigError(
            "Failure loading client certificate chain from"
            + f"{len(config.certificate_pem)} cert file bytes and {len(config.key_pem)} key file bytes."
        ) from exc

    return CSIPAusContext(
        HttpContext(
            session=ClientSession(base_url=base_uri, connector=TCPConnector(ssl=ssl_context)), user_agent="cactus-juice"
        ),
        is_aggregator_client=config.is_aggregator,
        dcap_path=dcap_path,
        nmi=config.nmi,
        client_pen=client_pen,
        client_lfdi=client_lfdi,
        client_sfdi=client_sfdi,
        edev_lfdi=edev_lfdi,
        edev_sfdi=edev_sfdi,
    )
