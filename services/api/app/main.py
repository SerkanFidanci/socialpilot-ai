"""FastAPI application factory for the Slice 0A platform foundation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.trace.export import SpanExporter
from starlette.responses import Response

from app.api.routes import register_routes
from app.core.config import Settings, get_settings
from app.core.correlation import CorrelationIdMiddleware
from app.core.errors import (
    ProblemDetails,
    ProblemException,
    problem_response,
    safe_validation_error_meta,
)
from app.core.logging import configure_logging
from app.core.protocols import DatabaseClient, RedisClient
from app.core.telemetry import (
    TelemetryHandle,
    instrument_database,
    setup_api_telemetry,
)
from app.infrastructure.database import create_database
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.infrastructure.media.fake_ingest import FakeContentInspector, FakeMalwareScanner
from app.infrastructure.redis import create_redis_client
from app.infrastructure.storage import create_storage
from app.modules.media.ingest import ContentInspectionPort, MalwareScanPort
from app.modules.media.storage import MultipartStoragePort

logger = structlog.get_logger(__name__)

DatabaseFactory = Callable[[Settings], DatabaseClient]
RedisFactory = Callable[[Settings], RedisClient]
StorageFactory = Callable[[Settings], MultipartStoragePort]
ContentInspectorFactory = Callable[[], ContentInspectionPort]
MalwareScannerFactory = Callable[[], MalwareScanPort]
DEFAULT_REDIS_FACTORY: RedisFactory = cast(RedisFactory, create_redis_client)


def configure_openapi(application: FastAPI) -> None:
    """Document the shared RFC 9457 error contract for every public operation."""

    def custom_openapi() -> dict[str, object]:
        if application.openapi_schema is not None:
            return cast(dict[str, object], application.openapi_schema)
        schema = get_openapi(
            title=application.title,
            version=application.version,
            routes=application.routes,
        )
        components = schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        schemas["ProblemDetails"] = ProblemDetails.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        problem_response = {
            "description": "RFC 9457 Problem Details response.",
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                }
            },
        }
        for path_item in schema["paths"].values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                responses = operation.setdefault("responses", {})
                for status in ("400", "401", "403", "404", "409", "422", "500"):
                    responses.setdefault(status, problem_response)
        application.openapi_schema = schema
        return cast(dict[str, object], schema)

    application.openapi = custom_openapi  # type: ignore[method-assign]


def create_app(
    settings: Settings | None = None,
    *,
    database_factory: DatabaseFactory = create_database,
    redis_factory: RedisFactory = DEFAULT_REDIS_FACTORY,
    storage_factory: StorageFactory = create_storage,
    content_inspector_factory: ContentInspectorFactory = FakeContentInspector,
    malware_scanner_factory: MalwareScannerFactory = FakeMalwareScanner,
    include_test_routes: bool = False,
    telemetry_span_exporter: SpanExporter | None = None,
    telemetry_metric_reader: MetricReader | None = None,
) -> FastAPI:
    """Build an app with injectable infrastructure for deterministic tests."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = database_factory(resolved_settings)
        redis_client = redis_factory(resolved_settings)
        # The SQLAlchemy engine only exists now, so bind its instrumentation here. No-op when
        # telemetry is disabled (handle is None).
        instrument_database(application.state.telemetry, database)
        application.state.database = database
        application.state.redis = redis_client
        application.state.settings = resolved_settings
        application.state.storage = storage_factory(resolved_settings)
        application.state.content_inspector = content_inspector_factory()
        application.state.malware_scanner = malware_scanner_factory()
        application.state.identity_verifier = LocalIdentityVerifier(resolved_settings)
        logger.info("application_started", environment=resolved_settings.app_env)
        try:
            yield
        finally:
            await redis_client.aclose()
            await database.dispose()
            if application.state.telemetry is not None:
                application.state.telemetry.shutdown()
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
    configure_openapi(application)
    register_routes(application)

    # Default OFF: returns None unless OTEL_EXPORTER_OTLP_ENDPOINT is set, in which case it
    # instruments FastAPI/httpx/redis. Stored so the lifespan can instrument the DB engine and
    # tear everything down. The SQLAlchemy engine is bound later, once it exists.
    telemetry_handle: TelemetryHandle | None = setup_api_telemetry(
        application,
        resolved_settings,
        span_exporter=telemetry_span_exporter,
        metric_reader=telemetry_metric_reader,
    )
    application.state.telemetry = telemetry_handle

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
