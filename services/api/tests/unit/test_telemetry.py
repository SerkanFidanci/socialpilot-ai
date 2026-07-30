"""OpenTelemetry wiring tests: default-OFF no-op, redaction, correlation<->trace, metrics.

These pin the guarantees the W05 work order calls out — most importantly that a presigned
object-storage URL or a token can never leave the process on a span attribute or metric label,
and that telemetry costs nothing when no exporter endpoint is configured.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import telemetry as tel
from app.core.config import Settings
from app.core.logging import add_trace_context
from app.main import create_app
from tests.fakes import FakeDatabase, FakeRedis

SENTINEL_SIG = "SENTINELSIGNATUREdeadbeef"
SENTINEL_TOKEN = "SENTINELTOKENya29abc"
PRESIGNED_URL = (
    f"https://minio:9000/socialpilot-media/uploads/asset?"
    f"X-Amz-Credential=AKIA%2Fx&X-Amz-Signature={SENTINEL_SIG}&partNumber=1"
)


def _settings(*, endpoint: str = "") -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql+asyncpg://t:t@localhost:5432/t",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
        otel_exporter_otlp_endpoint=endpoint,
    )


# --------------------------------------------------------------------------- default OFF


def test_disabled_by_default() -> None:
    assert _settings().telemetry_enabled is False


def test_setup_is_noop_without_endpoint() -> None:
    # No endpoint -> no handle, so no provider, no exporter thread, no queue.
    assert tel.setup_api_telemetry(create_app(_settings()), _settings()) is None
    assert tel.setup_worker_telemetry(_settings()) is None


def test_app_runs_with_telemetry_disabled() -> None:
    app = create_app(
        _settings(),
        database_factory=lambda _s: FakeDatabase(),
        redis_factory=lambda _s: FakeRedis(),
    )
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
    assert app.state.telemetry is None


def test_endpoint_validation_rejects_non_http() -> None:
    with pytest.raises(ValueError):
        _settings(endpoint="ftp://collector:4318")


# ---------------------------------------------------------------------------- redaction


def test_redact_url_strips_query_and_userinfo() -> None:
    assert tel.redact_url(PRESIGNED_URL) == "https://minio:9000/socialpilot-media/uploads/asset"
    assert tel.redact_url("https://user:pass@host/p?a=1") == "https://host/p"
    # A non-URL value is untouched (it was never a leak vector).
    assert tel.redact_url("SELECT 1") == "SELECT 1"


def test_redact_attribute_drops_secrets_and_bare_query() -> None:
    assert tel.redact_attribute("authorization", f"Bearer {SENTINEL_TOKEN}") == "[REDACTED]"
    assert tel.redact_attribute("http.request.header.x-amz-date", "v") == "[REDACTED]"
    assert tel.redact_attribute("url.query", f"X-Amz-Signature={SENTINEL_SIG}") == "[REDACTED]"
    assert SENTINEL_SIG not in tel.redact_attribute("url.full", PRESIGNED_URL)
    # Safe, bounded values pass through unchanged.
    assert tel.redact_attribute("http.request.method", "GET") == "GET"
    assert tel.redact_attribute("business_id", "biz-1") == "biz-1"


def test_redacting_exporter_scrubs_presigned_url_and_token() -> None:
    """The guaranteed net: a sentinel signature/token never reaches the wrapped exporter."""

    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "t"}))
    provider.add_span_processor(SimpleSpanProcessor(tel._RedactingSpanExporter(memory)))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("GET") as span:
        span.set_attribute("url.full", PRESIGNED_URL)
        span.set_attribute("http.request.header.authorization", f"Bearer {SENTINEL_TOKEN}")
        span.set_attribute("db.statement", "SELECT 1")
    provider.force_flush()

    (exported,) = memory.get_finished_spans()
    attributes = dict(exported.attributes or {})
    blob = repr(attributes)
    assert SENTINEL_SIG not in blob
    assert SENTINEL_TOKEN not in blob
    # Non-sensitive attributes survive so traces stay useful.
    assert attributes["db.statement"] == "SELECT 1"


def test_httpx_hook_redacts_recording_span() -> None:
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    span = provider.get_tracer("t").start_span("GET")

    class _Request:
        url = PRESIGNED_URL

    tel._httpx_request_hook(span, _Request())
    span.end()
    provider.force_flush()

    attributes = dict(memory.get_finished_spans()[0].attributes or {})
    assert SENTINEL_SIG not in str(attributes.get("url.full"))
    assert SENTINEL_SIG not in str(attributes.get("http.url"))


# --------------------------------------------------------------- correlation <-> trace


def test_add_trace_context_stamps_ids_only_inside_a_span() -> None:
    provider = TracerProvider()
    tracer = provider.get_tracer("t")

    outside = add_trace_context(None, "info", {"event": "x"})
    assert "trace_id" not in outside

    with tracer.start_as_current_span("unit"):
        inside = add_trace_context(None, "info", {"event": "x"})
    assert len(inside["trace_id"]) == 32
    assert len(inside["span_id"]) == 16


def test_enabled_request_produces_server_span_bound_to_correlation_id() -> None:
    memory = InMemorySpanExporter()
    app = create_app(
        _settings(endpoint="http://collector:4318"),
        database_factory=lambda _s: FakeDatabase(),
        redis_factory=lambda _s: FakeRedis(),
        telemetry_span_exporter=memory,
        telemetry_metric_reader=InMemoryMetricReader(),
    )
    with TestClient(app) as client:
        response = client.get("/health/ready", headers={"X-Correlation-ID": "cid-xyz"})
        assert response.status_code == 200
        assert response.headers["X-Correlation-ID"] == "cid-xyz"
    app.state.telemetry.tracer_provider.force_flush()

    server_spans = [s for s in memory.get_finished_spans() if s.name == "GET /health/ready"]
    assert server_spans, "a server span must be produced for the request"
    assert dict(server_spans[0].attributes or {}).get("correlation_id") == "cid-xyz"


# ------------------------------------------------------------------------------ metrics


def _collect_metrics(reader: InMemoryMetricReader) -> dict[str, list[dict[str, object]]]:
    data = reader.get_metrics_data()
    collected: dict[str, list[dict[str, object]]] = {}
    for resource_metric in data.resource_metrics if data else []:
        for scope in resource_metric.scope_metrics:
            for metric in scope.metrics:
                points = list(getattr(metric.data, "data_points", []))
                collected[metric.name] = [dict(point.attributes) for point in points]
    return collected


def test_api_latency_and_error_metrics_emitted() -> None:
    reader = InMemoryMetricReader()
    app = create_app(
        _settings(endpoint="http://collector:4318"),
        database_factory=lambda _s: FakeDatabase(),
        redis_factory=lambda _s: FakeRedis(),
        telemetry_span_exporter=InMemorySpanExporter(),
        telemetry_metric_reader=reader,
    )
    with TestClient(app) as client:
        client.get("/health/live")
        metrics = _collect_metrics(reader)

    # http.server.duration is the API-latency histogram; its status-code attribute is the
    # error-rate source. No high-cardinality label (path is templated, no ids).
    assert "http.server.duration" in metrics


def test_job_duration_and_queue_depth_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery.signals import task_postrun, task_prerun  # type: ignore[import-untyped]

    class _FakeRedis:
        def llen(self, _name: str) -> int:
            return 7

        def close(self) -> None:
            return None

    import redis

    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *a, **k: _FakeRedis()))

    reader = InMemoryMetricReader()
    handle = tel.setup_worker_telemetry(
        _settings(endpoint="http://collector:4318"),
        span_exporter=InMemorySpanExporter(),
        metric_reader=reader,
    )
    assert handle is not None
    try:

        class _Task:
            name = "media.ingest.drain"

        task_prerun.send(sender=None, task_id="job-1", task=_Task())
        task_postrun.send(sender=None, task_id="job-1", task=_Task(), state="SUCCESS")
        metrics = _collect_metrics(reader)
    finally:
        handle.shutdown()

    assert "job.duration" in metrics
    assert metrics["job.duration"] == [{"task": "media.ingest.drain", "status": "success"}]
    assert "queue.depth" in metrics
    assert metrics["queue.depth"] == [{"queue": "default"}]


def test_metric_labels_are_low_cardinality() -> None:
    reader = InMemoryMetricReader()
    app = create_app(
        _settings(endpoint="http://collector:4318"),
        database_factory=lambda _s: FakeDatabase(),
        redis_factory=lambda _s: FakeRedis(),
        telemetry_span_exporter=InMemorySpanExporter(),
        telemetry_metric_reader=reader,
    )
    with TestClient(app) as client:
        client.get("/health/live")
        metrics = _collect_metrics(reader)

    forbidden = {"asset_id", "job_id", "user_id", "correlation_id", "upload_id", "url.full"}
    for label_sets in metrics.values():
        for labels in label_sets:
            assert forbidden.isdisjoint(labels), labels


# ----------------------------------------------------------------- domain stays clean


def test_no_domain_module_imports_telemetry() -> None:
    """Instrumentation lives in core/infrastructure; domain code must not call telemetry."""

    modules_root = Path(__file__).resolve().parents[2] / "app" / "modules"
    offenders = [
        path.relative_to(modules_root).as_posix()
        for path in modules_root.rglob("*.py")
        if "telemetry" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
