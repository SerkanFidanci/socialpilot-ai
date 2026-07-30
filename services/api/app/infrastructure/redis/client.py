"""Redis client creation with bounded connection timeouts."""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    """Create a Redis client; connection is verified separately by readiness."""

    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_connect_timeout_seconds,
        health_check_interval=30,
    )
