import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cactus_juice.api.routers import config as config_router
from cactus_juice.db import DatabaseConnection
from cactus_juice.settings import CactusJuiceSettings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.db = DatabaseConnection.new_instance(app.state.settings)
    try:
        yield
    finally:
        await app.state.db.engine.dispose()


def create_app() -> FastAPI:
    settings = CactusJuiceSettings()  # ty: ignore[missing-argument]  # values are sourced from the environment

    app = FastAPI(title="cactus-juice", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.juice_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(config_router.router)

    return app


# No module-level `app = create_app()` here - constructing CactusJuiceSettings() reads required
# config from the environment, and doing that as an import side effect breaks anything that merely
# imports this module (eg test collection) without JUICE_DATABASE_URL set. Run this app via uvicorn's
# factory support instead: `uvicorn cactus_juice.main:create_app --factory`
