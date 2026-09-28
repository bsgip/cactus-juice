import socket
from itertools import product

import pytest
from aiohttp import TCPConnector
from aiohttp.abc import AbstractResolver, ResolveResult
from assertical.fake.generator import generate_class_instance

from cactus_juice.csipaus.config import OverrideResolver, build_csipaus_context
from cactus_juice.csipaus.sep2 import convert_lfdi_to_sfdi
from cactus_juice.error import ConfigError
from cactus_juice.model import CSIPAusConfig


@pytest.mark.parametrize("seed, optional_is_none", product([101, 202], [True, False]))
async def test_build_csipaus_context_all_set(
    seed: int, optional_is_none: bool, serca_cert_bytes: bytes, client_key_bytes: bytes, client_cert_bytes: bytes
):
    # Arrange
    config = generate_class_instance(
        CSIPAusConfig,
        seed=seed,
        optional_is_none=optional_is_none,
        key_pem=client_key_bytes,
        certificate_pem=client_cert_bytes,
        serca_pem=serca_cert_bytes,
        verify_hostname=True,
        verify_ssl=True,
        dcap_uri="https://foo.bar:12345/example/dcap",
    )

    # Act
    ctx = build_csipaus_context(config)

    # Assert
    try:
        assert ctx.is_aggregator_client is config.is_aggregator
        assert ctx.nmi == config.nmi
        assert ctx.client_pen == (config.client_pen or 1)

        assert ctx.dcap_path == "/example/dcap"
        assert str(ctx.http.session._base_url) == "https://foo.bar:12345/"

        if config.is_aggregator:
            # Aggregators have their own unique lfdi/sfdi for edevs
            assert ctx.edev_lfdi != ctx.client_lfdi
            assert ctx.edev_sfdi != ctx.client_sfdi
        else:
            # Devices have matching edev/client lfdi/sfdi
            assert ctx.edev_lfdi == ctx.client_lfdi
            assert ctx.edev_sfdi == ctx.client_sfdi

        # This is invariant - always having matching SFDI to LFDI
        assert len(ctx.edev_lfdi) == 40 and (ctx.edev_lfdi.upper() == ctx.edev_lfdi)
        assert len(ctx.client_lfdi) == 40 and (ctx.client_lfdi.upper() == ctx.client_lfdi)
        assert convert_lfdi_to_sfdi(ctx.edev_lfdi) == ctx.edev_sfdi
        assert convert_lfdi_to_sfdi(ctx.client_lfdi) == ctx.client_sfdi
    finally:
        await ctx.http.session.close()


async def test_build_csipaus_context_no_verify(client_key_bytes: bytes, client_cert_bytes: bytes):
    # Arrange
    config = generate_class_instance(
        CSIPAusConfig,
        key_pem=client_key_bytes,
        certificate_pem=client_cert_bytes,
        serca_pem=None,
        verify_hostname=False,
        verify_ssl=False,
        dcap_uri="http://foo.bar:12345/example/dcap",
    )

    # Act
    ctx = build_csipaus_context(config)

    # Assert
    try:
        assert ctx.is_aggregator_client is config.is_aggregator
        assert ctx.nmi == config.nmi
        assert ctx.client_pen == config.client_pen

        assert ctx.dcap_path == "/example/dcap"
        assert str(ctx.http.session._base_url) == "http://foo.bar:12345/"
    finally:
        await ctx.http.session.close()


async def test_build_csipaus_context_config_errors(
    serca_cert_bytes: bytes, client_key_bytes: bytes, client_cert_bytes: bytes
):

    # Malformed dcap uri
    with pytest.raises(ConfigError):
        build_csipaus_context(
            generate_class_instance(
                CSIPAusConfig,
                key_pem=client_key_bytes,
                certificate_pem=client_cert_bytes,
                serca_pem=serca_cert_bytes,
                verify_hostname=True,
                verify_ssl=True,
                dcap_uri="not a uri",
            )
        )

    # No dcap uri
    with pytest.raises(ConfigError):
        build_csipaus_context(
            generate_class_instance(
                CSIPAusConfig,
                key_pem=client_key_bytes,
                certificate_pem=client_cert_bytes,
                serca_pem=serca_cert_bytes,
                verify_hostname=True,
                verify_ssl=True,
                dcap_uri=None,
            )
        )

    # No serca with verify on
    with pytest.raises(ConfigError):
        build_csipaus_context(
            generate_class_instance(
                CSIPAusConfig,
                key_pem=client_key_bytes,
                certificate_pem=client_cert_bytes,
                serca_pem=None,
                verify_hostname=True,
                verify_ssl=True,
                dcap_uri="https://foo.bar:12345/example/dcap",
            )
        )

    # malformed serca with verify on
    with pytest.raises(ConfigError):
        build_csipaus_context(
            generate_class_instance(
                CSIPAusConfig,
                key_pem=client_key_bytes,
                certificate_pem=client_cert_bytes,
                serca_pem=b"---- not a cert",
                verify_hostname=True,
                verify_ssl=True,
                dcap_uri="https://foo.bar:12345/example/dcap",
            )
        )

    # No client cert
    with pytest.raises(ConfigError):
        build_csipaus_context(
            generate_class_instance(
                CSIPAusConfig,
                key_pem=client_key_bytes,
                certificate_pem=None,
                serca_pem=serca_cert_bytes,
                verify_hostname=True,
                verify_ssl=True,
                dcap_uri="https://foo.bar:12345/example/dcap",
            )
        )

    # Malformed client cert
    with pytest.raises(ConfigError):
        build_csipaus_context(
            generate_class_instance(
                CSIPAusConfig,
                key_pem=client_key_bytes,
                certificate_pem=b"---- not a cert",
                serca_pem=serca_cert_bytes,
                verify_hostname=True,
                verify_ssl=True,
                dcap_uri="https://foo.bar:12345/example/dcap",
            )
        )

    # No client key
    with pytest.raises(ConfigError):
        build_csipaus_context(
            generate_class_instance(
                CSIPAusConfig,
                key_pem=None,
                certificate_pem=client_cert_bytes,
                serca_pem=serca_cert_bytes,
                verify_hostname=True,
                verify_ssl=True,
                dcap_uri="https://foo.bar:12345/example/dcap",
            )
        )

    # Malformed client key
    with pytest.raises(ConfigError):
        build_csipaus_context(
            generate_class_instance(
                CSIPAusConfig,
                key_pem=b"---not a key",
                certificate_pem=client_cert_bytes,
                serca_pem=serca_cert_bytes,
                verify_hostname=True,
                verify_ssl=True,
                dcap_uri="https://foo.bar:12345/example/dcap",
            )
        )


class RecordingResolver(AbstractResolver):
    """Resolves every host to a fixed IP - recording the hosts it was asked to resolve"""

    def __init__(self) -> None:
        self.resolved_hosts: list[str] = []
        self.closed = False

    async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET):
        self.resolved_hosts.append(host)
        return [ResolveResult(hostname=host, host="192.0.2.1", port=port, family=family, proto=0, flags=0)]

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "host, expected_target",
    [
        ("cactus.example.com", "10.0.0.1"),
        ("run-123.cactus.example.com", "10.0.0.1"),
        ("RUN-123.Cactus.Example.com.", "10.0.0.1"),
        ("a.b.cactus.example.com", "10.0.0.1"),
        ("special.cactus.example.com", "10.0.0.2"),  # most specific override wins
        ("run-1.special.cactus.example.com", "10.0.0.2"),
        ("notcactus.example.com", None),  # suffix match must be on a domain boundary
        ("example.com", None),
        ("other.host", None),
    ],
)
def test_override_resolver_target_for(host: str, expected_target: str | None):
    resolver = OverrideResolver(
        {"cactus.example.com": "10.0.0.1", "special.cactus.example.com.": "10.0.0.2"}, inner=RecordingResolver()
    )
    assert resolver.target_for(host) == expected_target


async def test_override_resolver_resolve():
    inner = RecordingResolver()
    resolver = OverrideResolver({"cactus.example.com": "host.target"}, inner=inner)

    # Overridden host - resolves the target, but reports the original hostname (used for SNI / verification)
    results = await resolver.resolve("run-1.cactus.example.com", 443)
    assert inner.resolved_hosts == ["host.target"]
    assert [(r["hostname"], r["host"], r["port"]) for r in results] == [("run-1.cactus.example.com", "192.0.2.1", 443)]

    # Non overridden host - passes straight through
    results = await resolver.resolve("other.host", 8443)
    assert inner.resolved_hosts == ["host.target", "other.host"]
    assert [(r["hostname"], r["host"], r["port"]) for r in results] == [("other.host", "192.0.2.1", 8443)]

    await resolver.close()
    assert inner.closed


@pytest.mark.parametrize("resolve_overrides, expect_override", [(None, False), ({}, False), ({"a.b": "c"}, True)])
async def test_build_csipaus_context_resolve_overrides(
    resolve_overrides: dict[str, str] | None,
    expect_override: bool,
    client_key_bytes: bytes,
    client_cert_bytes: bytes,
):
    config = generate_class_instance(
        CSIPAusConfig,
        key_pem=client_key_bytes,
        certificate_pem=client_cert_bytes,
        serca_pem=None,
        verify_hostname=False,
        verify_ssl=False,
        dcap_uri="https://foo.bar:12345/example/dcap",
    )

    ctx = build_csipaus_context(config, resolve_overrides)
    try:
        connector = ctx.http.session.connector
        assert isinstance(connector, TCPConnector)
        assert isinstance(connector._resolver, OverrideResolver) is expect_override
    finally:
        await ctx.http.session.close()
