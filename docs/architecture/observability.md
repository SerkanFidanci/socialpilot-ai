# Observability — OpenTelemetry Trace + Metric

This is the implementation reality behind PRD §37 ([95-observability.md](../product/requirements/95-observability.md)).
The requirement lists log fields, metrics, the trace chain, and alerts; this document records
what the code actually collects, what it deliberately does **not** collect and why, how to turn
it on, and the redaction guarantees. Alerting and the collector/dashboards are out of scope
(see [Not in scope](#not-in-scope)).

Source: [`app/core/telemetry.py`](../../services/api/app/core/telemetry.py) ·
ADR: [ADR-014](../adr/ADR-014-opentelemetry-observability-foundation.md).

## Default OFF

Telemetry is inert unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set. With no endpoint, the setup
functions return `None`: no OTLP exporter is constructed, no `BatchSpanProcessor` thread and no
`PeriodicExportingMetricReader` thread start, no global tracer/meter provider is installed, and
the OpenTelemetry API stays on its built-in no-op providers. The `add_trace_context` log
processor still runs but sees the no-op `INVALID_SPAN` and adds nothing.

This is a requirement, not a convenience:

- **Single server (ADR-013).** The idle CPU/RAM budget is tight; telemetry must cost zero when
  no one is collecting it.
- **CI stays green with no collector or credentials.** `make verify` never needs an endpoint.

When the endpoint *is* set, the exporter uses the **OTLP http/protobuf** transport to a single
collector endpoint. gRPC is deliberately avoided so `grpcio` never enters the image.

### Settings

| Setting | Default | Meaning |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `""` (OFF) | Collector base URL, e.g. `http://otel-collector:4318`. Signal paths (`/v1/traces`, `/v1/metrics`) are appended. |
| `OTEL_EXPORTER_OTLP_HEADERS` | `""` | Optional OTLP auth, `key=value,key2=value2`. Held as a secret; passed to the exporter, never placed on a span. |
| `OTEL_SERVICE_NAME` | `service_name` | `service.name` resource value. |
| `OTEL_METRIC_EXPORT_INTERVAL_MILLIS` | `60000` | Periodic metric export interval. |

## Trace

When enabled, these are auto-instrumented, giving the PRD §37.3 chain
`mobile request → API → DB → queue → worker → provider → storage`:

| Layer | Instrumentation | Where wired |
|---|---|---|
| FastAPI (server spans) | `FastAPIInstrumentor` | API — `main.create_app` |
| SQLAlchemy (DB spans) | `SQLAlchemyInstrumentor` on the async engine's `sync_engine` | API lifespan + worker composition |
| httpx (provider/storage client spans) | `HTTPXClientInstrumentor` | API + worker |
| redis | `RedisInstrumentor` | API + worker |
| Celery (task spans) | `CeleryInstrumentor` | worker composition (`start_worker_process`, after fork) |

### Correlation ID ↔ trace

The existing `X-Correlation-ID` mechanism ([`correlation.py`](../../services/api/app/core/correlation.py))
is untouched. The bridge is two-directional:

- **trace → log:** the `add_trace_context` structlog processor stamps `trace_id` and `span_id`
  (hex) onto every log event that occurs inside a span, next to the existing `correlation_id`.
  From any log line you can pivot to its trace and back.
- **correlation → trace:** the FastAPI server-request hook copies the request correlation id
  onto the server span as the `correlation_id` attribute (read from the request header, so it is
  present for the client-supplied case).

### Worker jobs and trace continuity — the durable carrier

Work does not cross to the worker through a direct enqueue. Domain writes go to a transactional
outbox and durable `jobs` rows; Celery **Beat** fires drain tasks on a timer, and each drain
**self-selects** an eligible job from PostgreSQL rather than trusting a message payload id
(worker invariant, [ADR-005](../adr/ADR-005-transactional-outbox.md)). In-process propagation
therefore cannot reach the worker: there is no edge to propagate across.

The link is carried by the **event envelope** instead. The envelope already transports
`correlation_id` (PRD §26.4); it now also transports the W3C
[`traceparent`](https://www.w3.org/TR/trace-context/) — and `tracestate` when one exists — of
whatever caused the event. This needed **no schema change**: the envelope is `payload_json`.

| Step | What happens | Where |
|---|---|---|
| API request writes an event | `current_trace_carrier()` renders the active span as `traceparent`; `event_envelope()` stores it beside `job_id`/`asset_id` | `core/telemetry.py`, `modules/operations/service.py` |
| Beat tick drains the outbox | `continue_trace(envelope)` validates the value and re-attaches it as the parent context around the publish | `worker/tasks.py`, `modules/operations/service.py` |
| Publisher enqueues the drain task | Celery instrumentation injects the *attached* context into the message, so the drain task span is a child of the originating request | `infrastructure/celery_publisher.py` |
| The drain writes the next event | It runs inside that trace, so `event_envelope()` stamps the same trace onto the successor event and the whole ingest → analysis → understanding pipeline stays in one trace | `modules/media/*` |

Guarantees this has to keep, each pinned by a test:

- **Off means off.** With no endpoint there is no recording span, the carrier is empty, and the
  envelope is byte-identical to the pre-telemetry payload.
- **The envelope is not a telemetry sink.** Only `traceparent`/`tracestate` are written. The W3C
  propagator is used directly rather than the configured global one, so baggage cannot ride
  along, and no attribute, prompt, or URL is ever placed in an envelope.
- **A durable value is untrusted input.** A corrupt, truncated, or planted `traceparent` — wrong
  version, all-zero trace or span id, uppercase hex, non-string — is dropped, and the consumer
  starts a fresh trace instead of joining or poisoning someone else's.

#### Where the chain still stops

- **The wake-up is the causal link, not the record.** A drain woken by event A processes
  *whatever* eligible job it finds, which under concurrency may be job B (that is the invariant
  that makes duplicate messages harmless). The trace therefore answers "this request's publish
  woke this drain", not "this request's job ran here". `correlation_id` remains the exact
  per-record link, and it is on every span and log line.
- **Recovery starts a new trace.** `operations.recovery.drain` reclaims timed-out `jobs` rows,
  which carry no envelope; a recovered job's retry begins at the beat tick.
- **An idle tick has no parent.** A drain that finds nothing has no originating request, so its
  trace starts at the tick. That is correct — nothing caused it.

## Metric

Produced when enabled. The instrument is created once; adding a metric in a later module is a
one-line `meter.create_*` call plus a `record`.

| Metric | Kind | Source / measurement point | Labels (all low-cardinality) |
|---|---|---|---|
| `http.server.duration` | histogram | FastAPI auto-instrumentation — **API latency**, and its `http.status_code` attribute is the **API error-rate** source | method, templated route, status code |
| `http.client.request.duration` | histogram | httpx auto-instrumentation — provider/storage client latency | method, status code |
| `job.duration` | histogram | worker: Celery `task_prerun`/`task_postrun` signals timed in `telemetry._install_job_metrics` | `task` (the ~6 drain task names), `status` |
| `queue.depth` | observable gauge | worker: broker `LLEN` of the routed queue, read best-effort in the gauge callback | `queue` (`default` today) |

Note on queue depth: only the `default` queue is routed today; the per-queue split (§38.2) has
not landed, so the gauge reads that one list. When routing lands, the callback extends to the
queue set — still a bounded label.

### Cardinality rule

Metric labels must stay low-cardinality. **Asset id, job id, upload id, correlation id, and
user id are never metric labels** (they may be span attributes). `job.duration` is labelled by
task *name*, not task id. A test (`test_metric_labels_are_low_cardinality`) asserts these keys
never appear on any emitted metric.

## Redaction — what is NOT collected, and why

Span attributes and metric labels are collected automatically, so they leak more easily than a
hand-written log line. The following never leave the process:

- **Tokens, credentials, secrets** — any attribute whose key contains `authorization`,
  `token`, `secret`, `credential`, `password`, `cookie`, `api_key`, or `x-amz-` is replaced with
  `[REDACTED]`.
- **Signed object-storage URLs** — httpx records the full request URL, which for storage is a
  *presigned* URL whose query string is a valid credential. Every URL-valued attribute is
  reduced to `scheme://host/path`: the query, fragment, and userinfo are dropped. Bare
  query-string attributes (`url.query`, `http.target`) are dropped whole.
- **Raw prompts, raw provider responses, media-extracted text** — these live only in domain
  code, which is never instrumented (see below), so they are never placed on a span.
- **High-cardinality ids as metric labels** — see the cardinality rule above.
- **`user_id`** — masked if ever needed; **`business_id`** is allowed.

Two layers enforce URL/secret redaction:

1. the httpx request/response hooks strip the URL **while the span is still recording**, so the
   signature never sits on a span even briefly;
2. `_RedactingSpanExporter` is the guaranteed net on the export path — it scrubs **every** span
   from **every** instrumentation right before it is handed to the OTLP exporter, and a span it
   cannot scrub is dropped rather than exported.

Tests `test_redacting_exporter_scrubs_presigned_url_and_token` and `test_httpx_hook_redacts_recording_span`
pin this with a sentinel signature and token (the W01 sentinel pattern).

### The same rule on the log-record surface

Spans are not the only automatic surface. A `logging` record is produced by whichever library
happens to be in the call path — httpx wrote a full presigned URL at `INFO` during a real MinIO
multipart upload — so log redaction is enforced by the same shape of guarantee, in
`app/core/logging.py` and installed by `install_signature_redaction()`.

The signing parameters masked by value are S3's `X-Amz-Signature` / `X-Amz-Credential` /
`X-Amz-Security-Token`, GCS's `Signature` / `GoogleAccessId` / `X-Goog-*`, Azure's `sig`, and a
bare `access_token`. The parameter name, host and object key survive: which request was signed is
the useful half of a log line.

Three hooks cover the three ways a record can reach a handler, because no single one covers all
of them:

1. the **record factory** scrubs `msg` and the rendered traceback at creation;
2. **`Logger.callHandlers`** scrubs the whole record — including every `extra={...}` attribute,
   which the factory *cannot* see, because `Logger.makeRecord` copies `extra` on after the
   factory returns (W14 shipped the factory alone and W16 closed this: a handler formatting
   `%(url)s` printed the raw signature). `callHandlers` rather than `Logger.handle`, so a filter
   that returns a *different* record (Python 3.12+) is covered too;
3. **`Handler.handle`** is the backstop for a record built by hand and handed to a handler
   without a logger in between.

A record is walked once and marked, so five handlers do not cost five walks, and a cheap
substring pre-filter means an ordinary line never runs the full pattern. Non-string values are
covered: an `httpx.URL` object, a nested dict, a list. The value on the record is replaced;
the caller's object is never mutated.

The parameter *name* is matched through percent encoding at any depth. `X-Amz-%53ignature` is
still `X-Amz-Signature` to `urllib.parse.parse_qsl` and to a server, and `%2553` decodes to `%53`
decodes to `S`, so the escape has no fixed depth to decode away; the name pattern accepts
`%(?:25)*XX` in place of every character instead. Masking is applied to the raw text, so the log
line still reads as the request that was actually made. The pre-filter is safe by a two-branch
argument, and both branches are pinned by tests: a name written literally contains one of the
marker fragments, and a name written any other way needs a `%` to do it.

**Known residual:** a record that is both hand-built *and* passed to a `Handler` subclass which
overrides `handle()` without calling `super()` escapes all three. That is application code
bypassing the logging framework, not a library leaking; `RedactingFormatter` covers the handlers
this application installs.

## Instrumentation boundary

Instrumentation is wired **only in `core` and the composition roots** (`app/core/telemetry.py`,
`app/main.py`, `app/worker/composition.py`). No `app/modules/**` domain file imports telemetry
or emits a span/metric; a test (`test_no_domain_module_imports_telemetry`) enforces this. Domain
services stay portable and testable; telemetry is a cross-cutting concern applied from the edges.

## Not in scope

- The **collector, Prometheus/Grafana/Loki, dashboards, and alerts** (PRD §37.4). The exporter
  talks to one endpoint; standing that endpoint up is separate operations work.
- **Sentry** — a separate adapter/slice.
- Adding OTLP variables to `compose.yaml`/`.env.example` — those files belong to other work
  orders (W06 / W01); the variables are documented here in the meantime.
- Carrying trace context on durable **`jobs`** rows. Only the event envelope carries it today,
  which is what restores the API→worker chain without a migration; a job recovered after a
  timeout still starts a new trace (see above).
