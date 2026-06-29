from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from ..core.database import get_db


async def get_database() -> AsyncSession:
    """Dependency for database session."""
    async for session in get_db():
        yield session
