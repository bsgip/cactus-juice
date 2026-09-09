from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_juice.db import DatabaseConnection


async def get_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding an AsyncSession sourced from the DatabaseConnection stashed on app.state by the
    lifespan handler in main.py"""

    db: DatabaseConnection = request.app.state.db
    async with db.session_maker() as session:
        yield session
