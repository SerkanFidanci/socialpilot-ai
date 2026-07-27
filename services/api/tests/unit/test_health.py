"""Health, correlation, Problem Details, and startup tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.fakes import FakeDatabase, FakeRedis


def make_settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
    )


def make_client(
    database: FakeDatabase,
    redis: FakeRedis,
    *,
    include_test_routes: bool = False,
) -> TestClient:
    application = create_app(
        make_settings(),
        database_factory=lambda _settings: database,
        redis_factory=lambda _settings: redis,
        include_test_routes=include_test_routes,
    )
    return TestClient(application, raise_server_exceptions=False)


def test_application_can_be_created() -> None:
    assert create_app(make_settings()).title == "SocialPilot AI API"


def test_live_health_does_not_contact_dependencies() -> None:
    database = FakeDatabase(available=False)
    redis = FakeRedis(available=False)

    with make_client(database, redis) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "socialpilot-api"}
    assert database.ping_calls == 0
    assert redis.ping_calls == 0


def test_ready_health_succeeds_when_postgresql_and_redis_are_available() -> None:
    with make_client(FakeDatabase(), FakeRedis()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"postgresql": "ready", "redis": "ready"},
    }


def test_ready_health_fails_when_postgresql_is_unavailable() -> None:
    with make_client(FakeDatabase(available=False), FakeRedis()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["meta"] == {"dependencies": ["postgresql"]}


def test_ready_health_fails_when_redis_is_unavailable() -> None:
    with make_client(FakeDatabase(), FakeRedis(available=False)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["meta"] == {"dependencies": ["redis"]}


def test_correlation_id_is_generated_preserved_and_returned() -> None:
    supplied_id = "client-request-123"

    with make_client(FakeDatabase(), FakeRedis()) as client:
        generated = client.get("/health/live")
        preserved = client.get("/health/live", headers={"X-Correlation-ID": supplied_id})

    assert generated.headers["X-Correlation-ID"]
    assert preserved.headers["X-Correlation-ID"] == supplied_id


def test_invalid_correlation_id_is_replaced_with_a_safe_uuid() -> None:
    with make_client(FakeDatabase(), FakeRedis()) as client:
        response = client.get("/health/live", headers={"X-Correlation-ID": "invalid id!"})

    correlation_id = response.headers["X-Correlation-ID"]
    assert response.status_code == 200
    assert correlation_id != "invalid id!"
    assert len(correlation_id) == 36


def test_validation_error_uses_problem_details() -> None:
    with make_client(FakeDatabase(), FakeRedis(), include_test_routes=True) as client:
        response = client.get("/_test/validation?value=not-a-boolean")

    body = response.json()
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["type"] == "about:blank"
    assert body["instance"] == "/_test/validation"
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert "not-a-boolean" not in str(body["meta"])


def test_unexpected_error_uses_problem_details() -> None:
    with make_client(FakeDatabase(), FakeRedis(), include_test_routes=True) as client:
        response = client.get("/_test/unexpected")

    body = response.json()
    assert response.status_code == 500
    assert body["type"] == "about:blank"
    assert body["title"] == "Internal Server Error"
    assert body["detail"] == "Unexpected server error."
    assert body["instance"] == "/_test/unexpected"


def test_application_shutdown_closes_dependencies() -> None:
    database = FakeDatabase()
    redis = FakeRedis()

    with make_client(database, redis) as client:
        assert client.get("/health/live").status_code == 200

    assert database.disposed
    assert redis.closed
