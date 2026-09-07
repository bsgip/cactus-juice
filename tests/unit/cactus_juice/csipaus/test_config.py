import pytest
from assertical.fake.generator import generate_class_instance

from cactus_juice.csipaus.config import build_csipaus_context
from cactus_juice.error import ConfigError
from cactus_juice.model import CSIPAusConfig


@pytest.mark.parametrize("seed", [101, 202])
async def test_build_csipaus_context_all_set(
    seed: int, serca_cert_bytes: bytes, client_key_bytes: bytes, client_cert_bytes: bytes
):
    # Arrange
    config = generate_class_instance(
        CSIPAusConfig,
        seed=seed,
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
        assert ctx.client_pen == config.client_pen

        assert ctx.dcap_path == "/example/dcap"
        assert str(ctx.http.session._base_url) == "https://foo.bar:12345/"
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
