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

from cactus_juice.error import ConfigError
from cactus_juice.model import CSIPAusConfig


@dataclass(frozen=True, slots=True)
class HttpContext:
    """Used to run all CSIP Aus HTTP connections"""

    session: ClientSession  # Will have base_uri to server host
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class CSIPAusContext:
    """All data/connections for interacting with a CSIP-AUS utility server as a client"""

    http: HttpContext

    is_aggregator_client: bool
    nmi: str | None
    client_pen: int

    dcap_path: str  # The DeviceCapability path of the server - will be relative to base_uri in http.session


@contextmanager
def _secure_tempfile(data: bytes, dir_: str = "/dev/shm") -> Generator[Path]:  # noqa: S108
    """
    Write `data` to a restrictively-permissioned NamedTemporaryFile
    backed by tmpfs, yield its path, and guarantee cleanup.
    """
    fd, path = tempfile.mkstemp(dir=dir_)
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


def build_csipaus_context(config: CSIPAusConfig) -> CSIPAusContext:
    """Builds a CSIPAusContext from the specified config entries. Raises ConfigError if there are issues / missing
    elements in the supplied config. Responsibility for cleaning up the allocated SSLContext falls to the caller of
    this function."""

    if config.dcap_uri is None:
        raise ConfigError("No dcap_uri is specified")

    base_uri, dcap_path = build_dcap_parts(config.dcap_uri)

    if config.certificate_pem is None or config.key_pem is None:
        raise ConfigError("Missing client key/certificate data - cannot create CSIPAusContext")

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
        nmi=config.nmi,
        client_pen=config.client_pen or 1,
        dcap_path=dcap_path,
    )
