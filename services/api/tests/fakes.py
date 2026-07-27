"""Typed fakes for deterministic health and startup tests."""

from __future__ import annotations


class FakeDatabase:
    """Database fake that can model readiness success or failure."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.disposed = False
        self.ping_calls = 0

    async def ping(self) -> None:
        self.ping_calls += 1
        if not self.available:
            raise ConnectionError("database unavailable")

    async def dispose(self) -> None:
        self.disposed = True


class FakeRedis:
    """Redis fake that can model readiness success or failure."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.closed = False
        self.ping_calls = 0

    async def ping(self) -> str:
        self.ping_calls += 1
        if not self.available:
            raise ConnectionError("redis unavailable")
        return "PONG"

    async def aclose(self) -> None:
        self.closed = True
