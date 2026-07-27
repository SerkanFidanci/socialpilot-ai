"""Liveness and dependency readiness endpoints."""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.errors import ProblemException
from app.core.protocols import DatabaseClient, RedisClient

router = APIRouter(tags=["health"])


class LiveHealthResponse(BaseModel):
    """Response for process-only liveness."""

    status: Literal["ok"]
    service: Literal["socialpilot-api"]


class ReadyHealthResponse(BaseModel):
    """Response for safe dependency readiness information."""

    status: Literal["ready"]
    dependencies: dict[str, Literal["ready"]]


@router.get("/health/live", response_model=LiveHealthResponse)
async def live() -> LiveHealthResponse:
    """Return process liveness without contacting dependencies."""

    return LiveHealthResponse(status="ok", service="socialpilot-api")


@router.get("/health/ready", response_model=ReadyHealthResponse)
async def ready(request: Request) -> ReadyHealthResponse:
    """Check PostgreSQL and Redis independently without exposing connection data."""

    database = cast(DatabaseClient, request.app.state.database)
    redis_client = cast(RedisClient, request.app.state.redis)
    failed_dependencies: list[str] = []

    try:
        await database.ping()
    except Exception:
        failed_dependencies.append("postgresql")

    try:
        await redis_client.ping()
    except Exception:
        failed_dependencies.append("redis")

    if failed_dependencies:
        raise ProblemException(
            status=503,
            code="DEPENDENCY_UNAVAILABLE",
            title="Service Unavailable",
            detail="One or more required dependencies are unavailable.",
            meta={"dependencies": failed_dependencies},
        )

    return ReadyHealthResponse(
        status="ready",
        dependencies={"postgresql": "ready", "redis": "ready"},
    )
