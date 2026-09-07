from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from cactus_juice.settings import CactusJuiceSettings


@dataclass(frozen=True, slots=True)
class DatabaseConnection:
    postgres_dsn: str
    engine: AsyncEngine
    session_maker: async_sessionmaker[AsyncSession]

    @staticmethod
    def new_instance(settings: CactusJuiceSettings) -> "DatabaseConnection":
        postgres_dsn = str(settings.juice_database_url)
        engine = create_async_engine(postgres_dsn)
        session_maker = async_sessionmaker(engine, class_=AsyncSession)

        return DatabaseConnection(postgres_dsn, engine, session_maker)
