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
