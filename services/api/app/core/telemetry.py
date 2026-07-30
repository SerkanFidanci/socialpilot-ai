"""OpenTelemetry setup, teardown, and span/metric redaction — default OFF.

Nothing here runs unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` is configured. With no endpoint the
functions return ``None`` immediately: no exporter is built, no background export thread or
queue starts, no global tracer/meter provider is installed, and the OpenTelemetry API stays on
its built-in no-op providers. That is a requirement, not a convenience — on the single server
(ADR-013) the idle cost must be zero, and CI must stay green without a collector or credentials.

Redaction (see ``docs/architecture/observability.md``) is the most important part of this
module. Span attributes and metric labels are collected automatically, so they leak more
easily than a hand-written log line. Two layers guard them:

* the httpx request/response hooks strip the query + userinfo from the request URL while the
  span is still recording, so a *presigned* object-storage URL (whose signature lives in the
  query) never sits on a span even briefly;
* ``_RedactingSpanExporter`` is the guaranteed net on the export path: it drops secret-named
  attributes and strips every URL-valued attribute for *all* instrumentations, right before a
  span leaves the process.

The second job of this module is the **durable trace carrier**. Work crosses the API/worker
boundary through the transactional outbox, not a direct enqueue, so in-process propagation
cannot reach the worker. ``current_trace_carrier`` renders the current W3C trace context as
plain envelope fields the domain stores next to ``correlation_id``, and ``continue_trace``
validates and re-attaches it on the worker side.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import structlog
from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

if TYPE_CHECKING:
    from app.core.config import Settings

logger = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- redaction

# Attribute keys whose *value* is always dropped, matched as a case-insensitive substring.
# A superset of the logging redaction set plus auto-instrumentation keys that carry secrets.
_SENSITIVE_ATTR_PARTS: tuple[str, ...] = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "x-amz-",
)
# Attribute keys that hold a bare query string (no scheme/host to key off), so they are
# dropped wholesale rather than parsed as a URL.
_DROP_ATTR_KEYS: frozenset[str] = frozenset({"url.query", "http.target"})
_URL_MARKER = "://"
_REDACTED = "[REDACTED]"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _DROP_ATTR_KEYS or any(part in lowered for part in _SENSITIVE_ATTR_PARTS)


def redact_url(value: str) -> str:
    """Keep scheme/host/path; drop userinfo, query, and fragment.

    Presigned object-storage URLs carry the credential in the query string and can embed a
    userinfo section, so both must go. A value that does not parse as a URL is returned
    unchanged — it was never a leak vector.
    """

    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return value
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def redact_attribute(key: str, value: Any) -> Any:
    """Return a span/metric-safe version of one attribute value."""

    if _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        return redact_url(value) if _URL_MARKER in value else value
    if isinstance(value, (list, tuple)):
        return type(value)(redact_attribute(key, item) for item in value)
    return value


def _redact_span_in_place(span: ReadableSpan) -> None:
    """Overwrite a finished span's sensitive attributes on the pinned SDK's storage.

    A finished span's ``BoundedAttributes`` is immutable through the public mapping, so the
    redacted values are written to the underlying dict. The exact internal is pinned in
    ``uv.lock`` and guarded by a test; if a future SDK removes it, we raise rather than let a
    span through unredacted (the caller drops the span instead of exporting it).
    """

    attributes = span.attributes
    if not attributes:
        return
    replacements: dict[str, Any] = {}
    for key, value in attributes.items():
        redacted = redact_attribute(key, value)
        if redacted != value:
            replacements[key] = redacted
    if not replacements:
        return
    bounded = getattr(span, "_attributes", None)
    store = getattr(bounded, "_dict", None)
    if store is None:
        raise RuntimeError("TELEMETRY_SPAN_REDACTION_UNAVAILABLE")
    store.update(replacements)


class _RedactingSpanExporter(SpanExporter):
    """Scrub every span before delegating to the real exporter; never leak on failure."""

    def __init__(self, wrapped: SpanExporter) -> None:
        self._wrapped = wrapped

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        safe: list[ReadableSpan] = []
        for span in spans:
            try:
                _redact_span_in_place(span)
            except Exception:
                # Redaction is a hard invariant: a span we cannot scrub is dropped, never sent.
                logger.error("telemetry_span_redaction_failed", span_name=span.name)
                continue
            safe.append(span)
        if not safe:
            return SpanExportResult.SUCCESS
        return self._wrapped.export(safe)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._wrapped.force_flush(timeout_millis)

    def shutdown(self) -> None:
        self._wrapped.shutdown()


# ------------------------------------------------------------------------- span hooks


def _redact_span_url(span: Span | None, url: object) -> None:
    if span is None or not span.is_recording():
        return
    safe = redact_url(str(url))
    # Cover both the current and legacy HTTP semconv keys; a redacted extra key is harmless.
    span.set_attribute("url.full", safe)
    span.set_attribute("http.url", safe)


def _httpx_request_hook(span: Span, request: Any) -> None:
    _redact_span_url(span, request.url)


def _httpx_response_hook(span: Span, request: Any, response: Any) -> None:
    _redact_span_url(span, request.url)


def _server_request_hook(span: Span, scope: Any) -> None:
    """Bind the request correlation id onto the server span (best effort, header-based)."""

    if span is None or not span.is_recording():
        return
    from app.core.correlation import CORRELATION_ID_HEADER, get_correlation_id

    correlation_id = get_correlation_id()
    if not correlation_id and isinstance(scope, dict):
        wanted = CORRELATION_ID_HEADER.lower().encode("latin-1")
        for name, value in scope.get("headers", []):
            if name == wanted:
                correlation_id = value.decode("latin-1")
                break
    if correlation_id:
        span.set_attribute("correlation_id", correlation_id)


# ------------------------------------------------------------- durable envelope carrier

TRACEPARENT_FIELD = "traceparent"
TRACESTATE_FIELD = "tracestate"
_TRACE_FIELDS: frozenset[str] = frozenset({TRACEPARENT_FIELD, TRACESTATE_FIELD})

# W3C trace-context §3.2.2: `version-traceid-spanid-flags`, lowercase hex. Version `ff` is
# invalid, and an all-zero trace id or span id means "no parent" rather than a usable one.
_TRACEPARENT = re.compile(
    r"^(?!ff)[0-9a-f]{2}-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$"
)
_MAX_TRACESTATE_LENGTH = 512

# The W3C propagator is used directly rather than the configured global one: the global
# propagator also carries baggage, and the event envelope must contain trace context and
# nothing else — no attributes, no prompts, no URLs.
_TRACE_PROPAGATOR = TraceContextTextMapPropagator()


def current_trace_carrier() -> dict[str, str]:
    """Render the current trace context as envelope fields; empty when telemetry is off.

    An outbox event outlives the request that produced it, so the only way its worker-side
    continuation can join the originating trace is to persist the W3C ``traceparent`` with the
    event (PRD §26.4). With telemetry disabled there is no recording span, the propagator
    writes nothing, and the caller stores exactly the payload it stored before.
    """

    carrier: dict[str, str] = {}
    _TRACE_PROPAGATOR.inject(carrier)
    return {
        key: value
        for key, value in carrier.items()
        if key in _TRACE_FIELDS and isinstance(value, str)
    }


def trace_carrier_from_envelope(envelope: Mapping[str, object]) -> dict[str, str] | None:
    """Return a *validated* carrier, or ``None`` when the envelope has none or it is malformed.

    The envelope is durable data: a corrupt row, a hand-edited payload, or a hostile value must
    never reach the tracer. Anything that is not a well-formed traceparent is dropped so the
    consumer starts a fresh trace instead of joining or poisoning someone else's.
    """

    raw = envelope.get(TRACEPARENT_FIELD)
    if not isinstance(raw, str) or not _TRACEPARENT.match(raw):
        return None
    carrier = {TRACEPARENT_FIELD: raw}
    state = envelope.get(TRACESTATE_FIELD)
    if isinstance(state, str) and 0 < len(state) <= _MAX_TRACESTATE_LENGTH:
        carrier[TRACESTATE_FIELD] = state
    return carrier


@contextlib.contextmanager
def continue_trace(envelope: Mapping[str, object]) -> Iterator[None]:
    """Run the block under the trace context the envelope carries.

    Spans created inside — including the ones Celery's instrumentation creates when the outbox
    publisher enqueues a drain task — become children of the request that wrote the event, so
    API and worker stop being two islands. No span is started here; this only re-attaches a
    parent context, and it is inert when the envelope carries nothing valid.
    """

    carrier = trace_carrier_from_envelope(envelope)
    if carrier is None:
        yield
        return
    parent = _TRACE_PROPAGATOR.extract(carrier)
    if not trace.get_current_span(parent).get_span_context().is_valid:
        yield
        return
    token = otel_context.attach(parent)
    try:
        yield
    finally:
        otel_context.detach(token)


# --------------------------------------------------------------------------- providers


def _parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        candidate = pair.strip()
        if not candidate or "=" not in candidate:
            continue
        key, _, value = candidate.partition("=")
        if key.strip():
            headers[key.strip()] = value.strip()
    return headers


def _signal_endpoint(base: str, signal: str) -> str:
    return f"{base.rstrip('/')}/v1/{signal}"


def _build_providers(
    settings: Settings,
    *,
    span_exporter: SpanExporter | None,
    metric_reader: MetricReader | None,
) -> tuple[TracerProvider, SdkMeterProvider]:
    resource = Resource.create(
        {
            "service.name": settings.otel_resource_service_name,
            "deployment.environment": settings.app_env,
        }
    )
    headers = _parse_headers(settings.otel_exporter_otlp_headers.get_secret_value())

    # A caller-supplied exporter/reader is the test seam; it still goes through redaction.
    injected = span_exporter is not None
    raw_exporter = span_exporter or OTLPSpanExporter(
        endpoint=_signal_endpoint(settings.otel_exporter_otlp_endpoint, "traces"),
        headers=headers or None,
    )
    tracer_provider = TracerProvider(resource=resource)
    redacting = _RedactingSpanExporter(raw_exporter)
    tracer_provider.add_span_processor(
        SimpleSpanProcessor(redacting) if injected else BatchSpanProcessor(redacting)
    )

    reader = metric_reader or PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=_signal_endpoint(settings.otel_exporter_otlp_endpoint, "metrics"),
            headers=headers or None,
        ),
        export_interval_millis=settings.otel_metric_export_interval_millis,
    )
    meter_provider = SdkMeterProvider(resource=resource, metric_readers=[reader])
    return tracer_provider, meter_provider


# ----------------------------------------------------------------------------- handle


@dataclass
class TelemetryHandle:
    """Owns the providers and instrumentations so a process can tear telemetry down cleanly."""

    tracer_provider: TracerProvider
    meter_provider: SdkMeterProvider
    app: Any = None
    sqlalchemy_instrumented: bool = False
    celery_instrumented: bool = False
    job_signals: list[tuple[Any, Callable[..., None]]] = field(default_factory=list)

    def shutdown(self) -> None:
        for signal, receiver in self.job_signals:
            with contextlib.suppress(Exception):
                signal.disconnect(receiver)
        if self.app is not None:
            with contextlib.suppress(Exception):
                FastAPIInstrumentor.uninstrument_app(self.app)
        for instrumentor in (HTTPXClientInstrumentor(), RedisInstrumentor()):
            with contextlib.suppress(Exception):
                instrumentor.uninstrument()
        if self.sqlalchemy_instrumented:
            with contextlib.suppress(Exception):
                SQLAlchemyInstrumentor().uninstrument()
        if self.celery_instrumented:
            with contextlib.suppress(Exception):
                CeleryInstrumentor().uninstrument()  # type: ignore[no-untyped-call]
        with contextlib.suppress(Exception):
            self.tracer_provider.shutdown()
        with contextlib.suppress(Exception):
            self.meter_provider.shutdown()


def _instrument_clients(tracer_provider: TracerProvider, meter_provider: SdkMeterProvider) -> None:
    HTTPXClientInstrumentor().instrument(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        request_hook=_httpx_request_hook,
        response_hook=_httpx_response_hook,
        async_request_hook=_httpx_request_hook,
        async_response_hook=_httpx_response_hook,
    )
    RedisInstrumentor().instrument(tracer_provider=tracer_provider)


def _install_global_providers(
    tracer_provider: TracerProvider, meter_provider: SdkMeterProvider
) -> None:
    """Install process-global providers, first writer wins.

    OpenTelemetry refuses to override an already-set global provider (it only warns). Every
    instrumentor here also receives the provider explicitly, so span/metric routing is correct
    regardless; guarding the global set keeps the warning out of a test run that enables
    telemetry more than once in one process.
    """

    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(tracer_provider)
    if not isinstance(metrics.get_meter_provider(), SdkMeterProvider):
        metrics.set_meter_provider(meter_provider)


# --------------------------------------------------------------------------- job metrics

_JOB_START: dict[str, float] = {}


def _install_job_metrics(
    meter_provider: SdkMeterProvider, settings: Settings
) -> list[tuple[Any, Callable[..., None]]]:
    """Worker-side metrics that auto-instrumentation does not provide: job duration + queue depth.

    Job duration is timed from Celery's ``task_prerun``/``task_postrun`` signals (bounded
    ``task``/``status`` labels — the six drain task names, never a job or asset id). Queue depth
    is an observable gauge that reads the broker list length best-effort. Both are wired here,
    outside domain code, so no ``app/modules/**`` file learns about telemetry.
    """

    from celery.signals import (  # type: ignore[import-untyped]
        task_postrun,
        task_prerun,
    )

    meter = meter_provider.get_meter("app.worker")
    job_duration = meter.create_histogram(
        "job.duration", unit="s", description="Durable job drain wall-clock duration"
    )

    def _on_prerun(task_id: str | None = None, **_: Any) -> None:
        if task_id:
            _JOB_START[task_id] = time.monotonic()

    def _on_postrun(
        task_id: str | None = None, task: Any = None, state: Any = None, **_: Any
    ) -> None:
        if not task_id:
            return
        started = _JOB_START.pop(task_id, None)
        if started is None:
            return
        job_duration.record(
            time.monotonic() - started,
            {"task": getattr(task, "name", "unknown"), "status": str(state or "unknown").lower()},
        )

    task_prerun.connect(_on_prerun, weak=False)
    task_postrun.connect(_on_postrun, weak=False)

    broker_url = settings.celery_broker_url

    def _observe_queue_depth(_options: CallbackOptions) -> Iterable[Observation]:
        # Best effort: a broker hiccup must never crash the metric-collection thread. The
        # default queue is the only one routed today (§38.2 per-queue split not yet landed).
        try:
            import redis

            client = redis.Redis.from_url(broker_url, socket_connect_timeout=1, socket_timeout=1)
            try:
                return [Observation(int(client.llen("default")), {"queue": "default"})]
            finally:
                client.close()
        except Exception:
            return []

    meter.create_observable_gauge(
        "queue.depth",
        callbacks=[_observe_queue_depth],
        unit="{message}",
        description="Broker queue depth (pending messages)",
    )
    return [(task_prerun, _on_prerun), (task_postrun, _on_postrun)]


# ------------------------------------------------------------------------------- setup


def setup_api_telemetry(
    app: Any,
    settings: Settings,
    *,
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
) -> TelemetryHandle | None:
    """Instrument the FastAPI app, httpx, and redis. No-op unless telemetry is enabled."""

    if not settings.telemetry_enabled:
        return None
    tracer_provider, meter_provider = _build_providers(
        settings, span_exporter=span_exporter, metric_reader=metric_reader
    )
    _install_global_providers(tracer_provider, meter_provider)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        server_request_hook=_server_request_hook,
    )
    _instrument_clients(tracer_provider, meter_provider)
    logger.info("telemetry_enabled", role="api", service=settings.otel_resource_service_name)
    return TelemetryHandle(tracer_provider=tracer_provider, meter_provider=meter_provider, app=app)


def instrument_database(handle: TelemetryHandle | None, database: Any) -> None:
    """Instrument the SQLAlchemy engine once it exists (created in the app/worker lifespan)."""

    if handle is None:
        return
    engine = getattr(database, "engine", None)
    if engine is None:
        return
    sync_engine = getattr(engine, "sync_engine", engine)
    SQLAlchemyInstrumentor().instrument(engine=sync_engine, tracer_provider=handle.tracer_provider)
    handle.sqlalchemy_instrumented = True


def setup_worker_telemetry(
    settings: Settings,
    *,
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
) -> TelemetryHandle | None:
    """Instrument Celery, httpx, and redis in a worker process. No-op unless enabled."""

    if not settings.telemetry_enabled:
        return None
    tracer_provider, meter_provider = _build_providers(
        settings, span_exporter=span_exporter, metric_reader=metric_reader
    )
    _install_global_providers(tracer_provider, meter_provider)
    _instrument_clients(tracer_provider, meter_provider)
    CeleryInstrumentor().instrument(tracer_provider=tracer_provider)  # type: ignore[no-untyped-call]
    signals = _install_job_metrics(meter_provider, settings)
    logger.info("telemetry_enabled", role="worker", service=settings.otel_resource_service_name)
    return TelemetryHandle(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        celery_instrumented=True,
        job_signals=signals,
    )


__all__ = [
    "TRACEPARENT_FIELD",
    "TRACESTATE_FIELD",
    "TelemetryHandle",
    "continue_trace",
    "current_trace_carrier",
    "instrument_database",
    "redact_attribute",
    "redact_url",
    "setup_api_telemetry",
    "setup_worker_telemetry",
    "trace_carrier_from_envelope",
]
