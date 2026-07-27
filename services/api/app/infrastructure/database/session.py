"""Async SQLAlchemy engine and request-scoped session infrastructure."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


class Database:
    """Own an engine and a session factory, never a mutable shared session."""

    def __init__(self, settings: Settings) -> None:
        self._timeout_seconds = settings.database_connect_timeout_seconds
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            connect_args={"timeout": settings.database_connect_timeout_seconds},
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def ping(self) -> None:
        """Confirm PostgreSQL connectivity within the configured timeout."""

        async with asyncio.timeout(self._timeout_seconds):
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        """Close the engine pool during application shutdown."""

        await self.engine.dispose()


def create_database(settings: Settings) -> Database:
    """Create a process-local database infrastructure object."""

    return Database(settings)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one controlled SQLAlchemy session for the current request."""

    database: Database = request.app.state.database
    async with database.session_factory() as session:
        yield session
