"""OpenTelemetry wiring tests: default-OFF no-op, redaction, correlation<->trace, metrics.

These pin the guarantees the W05 work order calls out — most importantly that a presigned
object-storage URL or a token can never leave the process on a span attribute or metric label,
and that telemetry costs nothing when no exporter endpoint is configured.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry import context as otel_context
from opentelemetry import trace
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


# -------------------------------------------------------- durable envelope trace carrier


def _memory_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "t"}))
    provider.add_span_processor(SimpleSpanProcessor(memory))
    return provider, memory


def test_carrier_is_empty_when_no_span_is_recording() -> None:
    """Telemetry off means the envelope is written exactly as it was before this feature."""

    assert tel.current_trace_carrier() == {}


def test_envelope_carries_the_current_trace_and_the_worker_rejoins_it() -> None:
    """The whole point: an event written under a request is drained inside that same trace."""

    provider, memory = _memory_provider()
    tracer = provider.get_tracer("t")

    with tracer.start_as_current_span("POST /v1/.../complete") as request_span:
        carrier = tel.current_trace_carrier()
        request_trace_id = request_span.get_span_context().trace_id

    assert tel.TRACEPARENT_FIELD in carrier
    assert f"{request_trace_id:032x}" in carrier[tel.TRACEPARENT_FIELD]

    # A worker process, later, with nothing but the durable envelope.
    envelope: dict[str, object] = {"job_id": "j", "asset_id": "a", **carrier}
    with tel.continue_trace(envelope):
        with tracer.start_as_current_span("operations.outbox.dispatch") as worker_span:
            assert worker_span.get_span_context().trace_id == request_trace_id
    provider.force_flush()

    drained = [s for s in memory.get_finished_spans() if s.name == "operations.outbox.dispatch"]
    assert drained and drained[0].context.trace_id == request_trace_id


def test_envelope_carries_trace_context_and_nothing_else() -> None:
    """`traceparent` is an identifier; no attribute, prompt, URL or baggage may ride along."""

    from opentelemetry import baggage

    provider, _ = _memory_provider()
    token = otel_context.attach(baggage.set_baggage("presigned_url", "https://minio/x?sig=SECRET"))
    try:
        with provider.get_tracer("t").start_as_current_span("GET"):
            carrier = tel.current_trace_carrier()
    finally:
        otel_context.detach(token)

    assert set(carrier) <= {tel.TRACEPARENT_FIELD, tel.TRACESTATE_FIELD}
    assert "SECRET" not in repr(carrier)


@pytest.mark.parametrize(
    "traceparent",
    [
        "not-a-traceparent",
        "00-00000000000000000000000000000000-0123456789abcdef-01",  # all-zero trace id
        "00-0123456789abcdef0123456789abcdef-0000000000000000-01",  # all-zero span id
        "ff-0123456789abcdef0123456789abcdef-0123456789abcdef-01",  # forbidden version
        "00-0123456789ABCDEF0123456789ABCDEF-0123456789abcdef-01",  # uppercase hex
        "00-0123456789abcdef0123456789abcdef-0123456789abcdef",  # truncated
        "",
        12345,
    ],
)
def test_a_hostile_or_broken_traceparent_never_reaches_the_tracer(traceparent: object) -> None:
    """Envelopes are durable data: a corrupt or planted value must start a new trace, not join."""

    provider, _ = _memory_provider()
    assert tel.trace_carrier_from_envelope({tel.TRACEPARENT_FIELD: traceparent}) is None
    with tel.continue_trace({tel.TRACEPARENT_FIELD: traceparent}):
        # Nothing was attached, so the drain starts its own trace instead of joining one.
        assert not trace.get_current_span().get_span_context().is_valid
        with provider.get_tracer("t").start_as_current_span("drain") as span:
            assert span.get_span_context().trace_id != 0x0123456789ABCDEF0123456789ABCDEF


def test_tracestate_rides_along_only_when_bounded() -> None:
    valid = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    carrier = tel.trace_carrier_from_envelope(
        {tel.TRACEPARENT_FIELD: valid, tel.TRACESTATE_FIELD: "vendor=1"}
    )
    assert carrier == {tel.TRACEPARENT_FIELD: valid, tel.TRACESTATE_FIELD: "vendor=1"}
    oversized = tel.trace_carrier_from_envelope(
        {tel.TRACEPARENT_FIELD: valid, tel.TRACESTATE_FIELD: "x" * 4096}
    )
    assert oversized == {tel.TRACEPARENT_FIELD: valid}


def test_continue_trace_is_inert_for_an_envelope_without_trace_context() -> None:
    """The pre-telemetry payload shape still flows through unchanged."""

    before = otel_context.get_current()
    with tel.continue_trace({"job_id": "j", "asset_id": "a"}):
        assert otel_context.get_current() is before


# ----------------------------------------------------------------- domain stays clean

# Domain code may read the opaque envelope carrier — `traceparent` is request context, stored
# next to `correlation_id` (§26.4). It may not instrument: no OpenTelemetry import, no tracer,
# no meter, no span, no metric. This is the single permitted line.
PERMITTED_TELEMETRY_LINE = "from app.core.telemetry import current_trace_carrier"
INSTRUMENTATION_MARKERS = (
    "opentelemetry",
    "get_tracer",
    "get_meter",
    "start_span",
    "start_as_current_span",
    "create_histogram",
    "create_counter",
    "create_observable",
    "set_attribute",
)


def test_domain_modules_read_the_carrier_but_never_instrument() -> None:
    modules_root = Path(__file__).resolve().parents[2] / "app" / "modules"
    instrumenting: list[str] = []
    unexpected_telemetry: list[str] = []
    for path in modules_root.rglob("*.py"):
        name = path.relative_to(modules_root).as_posix()
        source = path.read_text(encoding="utf-8")
        if any(marker in source for marker in INSTRUMENTATION_MARKERS):
            instrumenting.append(name)
        for line in source.splitlines():
            if "app.core.telemetry" in line and line.strip() != PERMITTED_TELEMETRY_LINE:
                unexpected_telemetry.append(f"{name}: {line.strip()}")
    assert instrumenting == []
    assert unexpected_telemetry == []
