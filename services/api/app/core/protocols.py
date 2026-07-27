"""Small infrastructure contracts used by the application factory and routes."""

from __future__ import annotations

from typing import Protocol


class DatabaseClient(Protocol):
    """Database lifecycle and health contract."""

    async def ping(self) -> None: ...

    async def dispose(self) -> None: ...


class RedisClient(Protocol):
    """Redis lifecycle and health contract."""

    async def ping(self) -> object: ...

    async def aclose(self) -> None: ...
