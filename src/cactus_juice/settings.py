import logging

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

logger = logging.getLogger(__name__)


class CactusJuiceSettings(BaseSettings):
    # database
    juice_database_url: PostgresDsn

    # api
    # Origins allowed to call the JSON API (eg the vite dev server)
    juice_cors_origins: list[str] = ["http://localhost:5173"]

    # csipausclient
    # Maps a domain to an alternate host/IP that should be connected to instead (applies to that domain AND all of its
    # subdomains) - a wildcard-capable equivalent of a hosts file entry. The original hostname is still used for the
    # Host header and TLS SNI/verification. eg: {"cactus.example.com": "10.89.100.1"} to route all CSIP-Aus traffic
    # for run-123.cactus.example.com to an nginx instance on the container host.
    juice_csipaus_resolve_overrides: dict[str, str] = {}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,  # handles everything else
            dotenv_settings,
            file_secret_settings,
        )
