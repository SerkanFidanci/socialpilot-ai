"""FastAPI application factory for the Slice 0A platform foundation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response

from app.api.routes.businesses import router as businesses_router
from app.api.routes.health import router as health_router
from app.api.routes.identity import router as identity_router
from app.api.routes.media import router as media_router
from app.core.config import Settings, get_settings
from app.core.correlation import CorrelationIdMiddleware
from app.core.errors import ProblemException, problem_response, safe_validation_error_meta
from app.core.logging import configure_logging
from app.core.protocols import DatabaseClient, RedisClient
from app.infrastructure.database import create_database
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.infrastructure.redis import create_redis_client
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.modules.media.storage import MultipartStoragePort

logger = structlog.get_logger(__name__)

DatabaseFactory = Callable[[Settings], DatabaseClient]
RedisFactory = Callable[[Settings], RedisClient]
StorageFactory = Callable[[], MultipartStoragePort]
DEFAULT_REDIS_FACTORY: RedisFactory = cast(RedisFactory, create_redis_client)


def create_app(
    settings: Settings | None = None,
    *,
    database_factory: DatabaseFactory = create_database,
    redis_factory: RedisFactory = DEFAULT_REDIS_FACTORY,
    storage_factory: StorageFactory = FakeMultipartStorage,
    include_test_routes: bool = False,
) -> FastAPI:
    """Build an app with injectable infrastructure for deterministic tests."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = database_factory(resolved_settings)
        redis_client = redis_factory(resolved_settings)
        application.state.database = database
        application.state.redis = redis_client
        application.state.settings = resolved_settings
        application.state.storage = storage_factory()
        application.state.identity_verifier = LocalIdentityVerifier(resolved_settings)
        logger.info("application_started", environment=resolved_settings.app_env)
        try:
            yield
        finally:
            await redis_client.aclose()
            await database.dispose()
            logger.info("application_stopped", environment=resolved_settings.app_env)

    application = FastAPI(
        title="SocialPilot AI API",
        version="0.1.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(CorrelationIdMiddleware)
    application.include_router(health_router)
    application.include_router(identity_router)
    application.include_router(businesses_router)
    application.include_router(media_router)

    if include_test_routes:

        @application.get("/_test/validation", include_in_schema=False)
        async def validation_test(value: int) -> dict[str, int]:
            return {"value": value}

        @application.get("/_test/unexpected", include_in_schema=False)
        async def unexpected_test_error() -> None:
            raise RuntimeError("test-only unexpected error")

    @application.exception_handler(ProblemException)
    async def handle_problem(request: Request, error: ProblemException) -> Response:
        return problem_response(request, error)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError) -> Response:
        problem = ProblemException(
            status=400,
            code="REQUEST_VALIDATION_FAILED",
            title="Invalid Request",
            detail="The request could not be validated.",
            meta=safe_validation_error_meta(error.errors()),
        )
        return problem_response(request, problem)

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> Response:
        logger.error("unhandled_exception", error_type=type(error).__name__)
        problem = ProblemException(
            status=500,
            code="INTERNAL_ERROR",
            title="Internal Server Error",
            detail="Unexpected server error.",
        )
        return problem_response(request, problem)

    return application


app = create_app()
