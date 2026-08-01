"""Structured JSON logging, recursive secret redaction, and signature scrubbing.

Two guards live here and they cover different surfaces on purpose.

`redact_sensitive_values` is a structlog processor: it masks values by *key name* on events
this application writes itself. That is enough right up until a library logs for us.
`install_signature_redaction` is the second guard, and it is the one W14 exists for: it scrubs
signing material out of every `logging` record no matter which logger produced it. During a real
MinIO multipart upload, httpx logged the full presigned URL — credential and signature query
parameters included — at INFO, straight past a key-based redactor that never saw the record.

Silencing httpx would have hidden that one line; it would not have stopped the next library
from doing the same thing, and it would have left the guard untestable on the path where the
leak actually happened. So httpx keeps its INFO level and the scrubber sits underneath every
logger instead.

W14 installed that guard as a record factory alone, and W16 exists because a record factory
cannot see the whole record. `Logger.makeRecord` copies `extra={...}` onto the record *after*
the factory has returned, so `logger.info("…", extra={"url": httpx.URL(signed)})` reached a
handler formatting `%(url)s` with the signature intact — in the API and in the worker. The
scrub therefore also runs from `Logger.callHandlers`, which is the last point a record passes
through before any handler sees it, and the first point at which `extra` exists. A handler
filter would not do: the leak is precisely about handlers nobody registered with us, and the
reproduction attached its own.
"""

from __future__ import annotations

import logging
import re
import sys
import traceback
from collections.abc import Mapping, MutableMapping
from typing import Any, cast

import structlog
from opentelemetry import trace

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "database_url",
    "password",
    "redis_url",
    "secret",
    "signed_url",
    "token",
)

REDACTED = "[REDACTED]"

# Query parameters that carry signing material in a presigned object-storage URL. The list is
# provider-shaped rather than S3-shaped: an adapter for R2, GCS or Azure signs with different
# parameter names, and the guard has to hold the day one of those is configured. Names are
# matched case-insensitively, longest first, so `X-Amz-Signature` cannot be reduced to the
# shorter `sig` alternative and lose its prefix.
_SIGNED_QUERY_PARAMS = (
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "x-goog-signature",
    "x-goog-credential",
    "googleaccessid",
    "awsaccesskeyid",
    "access_token",
    "signature",
    "token",
    "sig",
)

_SIGNATURE_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(name) for name in _SIGNED_QUERY_PARAMS) + r")=([^&\s\"'<>\\]+)"
)

# Cheap pre-filter. The scrub now runs over every attribute of every record, so the common case —
# an ordinary log line with nothing signed in it — must not pay for the alternation above. Every
# name in `_SIGNED_QUERY_PARAMS` contains one of these fragments, and a test asserts that, so the
# fast path cannot turn into a silent false negative when a parameter is added.
_CANDIDATE_MARKERS = re.compile(r"(?i)sig|cred|token|keyid|accessid")


def redact_signature_material(text: str) -> str:
    """Mask the value of every signing query parameter in a piece of text.

    Only the value is replaced. The parameter name, the host, the object key and the rest of
    the query survive, because a log line that says *which* request was signed is the useful
    half and the signature is the dangerous half — a presigned URL is a bearer credential.
    """

    if "=" not in text or _CANDIDATE_MARKERS.search(text) is None:
        return text
    return _SIGNATURE_PATTERN.sub(lambda match: f"{match.group(1)}={REDACTED}", text)


def _redact(value: Any) -> Any:
    if isinstance(value, MutableMapping):
        return {
            key: REDACTED
            if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, str):
        # A key-based rule cannot catch a URL that arrived under an innocent key name, or the
        # event message itself. Scrub the text too.
        return redact_signature_material(value)
    return value


def redact_sensitive_values(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Remove secrets and signed URLs before a log event leaves the process."""

    return cast(MutableMapping[str, Any], _redact(event_dict))


class RedactingFormatter(logging.Formatter):
    """Scrub the rendered line, which is the only place traceback text can be reached.

    The record factory below rewrites `msg`/`args`, but a formatter renders `exc_info` into
    text afterwards, and an httpx exception repr carries the request URL. This closes that gap
    for the handler this module installs.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_signature_material(super().format(record))


_redaction_installed = False

# Marks a callable this module installed, so a second install cannot wrap it twice even when the
# `_redaction_installed` flag has been reset (a test does exactly that to re-run worker init).
_INSTALLED_MARK = "_socialpilot_signature_redaction"

# Marks a record that has already been scrubbed. A record normally passes two barriers on its way
# out — the logger's dispatch and each handler's own `handle` — and without this it would be
# walked once per handler.
_SCRUBBED_MARK = "_socialpilot_signature_scrubbed"

# Everything the standard library itself puts on a record. Whatever is left is `extra`, which is
# the surface W16 exists for. Derived from a real record rather than hand-listed so a new
# attribute in a future Python is not mistaken for a user-supplied field; `message` and `asctime`
# are added by `Formatter.format` afterwards, from values this module has already scrubbed.
_RESERVED_RECORD_ATTRIBUTES = frozenset(
    logging.LogRecord("", logging.NOTSET, "", 0, "", None, None).__dict__
) | {"message", "asctime", _SCRUBBED_MARK}

# A nested `extra` value is walked, not stringified, so `%(payload)s` still renders a dict. The
# ceiling stops a self-referential or pathological structure from turning one log call into an
# unbounded walk; past it the value is rendered once and scrubbed as text.
_MAX_VALUE_DEPTH = 4


def install_signature_redaction() -> None:
    """Install the process-wide scrubbing of signing material out of log records. Idempotent.

    Three hooks, because no single one covers every path a record can take:

    * a **record factory**, which catches `msg`/`exc_info` at creation, so a record that is
      handed straight to a `Formatter` is covered too;
    * a wrapper around **`Logger.callHandlers`**, which is where `extra` finally exists and the
      last point before any handler runs. `callHandlers` rather than `handle`, because since
      Python 3.12 a filter may *return a different record*, and `handle` would scrub too early to
      see it. This also covers a record rebuilt by `logging.makeLogRecord` — a queue or socket
      listener never goes near the factory;
    * a wrapper around **`Handler.handle`**, the backstop for a record that reaches a handler
      without passing through a logger at all. Records that came the normal way carry a mark and
      skip the second walk, so a line with five handlers is still scrubbed once.

    All three are chained rather than replaced, and re-entry is a no-op, so calling this from the
    API app factory and from the worker process init is safe.
    """

    global _redaction_installed
    if _redaction_installed:
        return
    _install_record_factory()
    _install_dispatch_barriers()
    _redaction_installed = True


def _install_record_factory() -> None:
    inner = logging.getLogRecordFactory()
    if getattr(inner, _INSTALLED_MARK, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = inner(*args, **kwargs)
        _scrub_message(record)
        _scrub_traceback(record)
        return record

    setattr(factory, _INSTALLED_MARK, True)
    logging.setLogRecordFactory(factory)


def _install_dispatch_barriers() -> None:
    dispatch = logging.Logger.callHandlers
    if not getattr(dispatch, _INSTALLED_MARK, False):

        def call_handlers(self: logging.Logger, record: logging.LogRecord) -> None:
            _scrub_record(record)
            dispatch(self, record)

        setattr(call_handlers, _INSTALLED_MARK, True)
        logging.Logger.callHandlers = call_handlers  # type: ignore[method-assign]

    emit = logging.Handler.handle
    if not getattr(emit, _INSTALLED_MARK, False):

        def handle(self: logging.Handler, record: logging.LogRecord) -> bool:
            _scrub_record(record)
            return emit(self, record)

        setattr(handle, _INSTALLED_MARK, True)
        logging.Handler.handle = handle  # type: ignore[method-assign]


def _scrub_record(record: logging.LogRecord) -> None:
    """Rewrite a record's message, traceback and `extra` attributes when they carry a signature.

    The message is resolved first because the interesting argument is rarely a string: httpx
    logs `'HTTP Request: %s %s ...'` with an `httpx.URL` object, so scrubbing `record.args`
    element by element would miss it entirely. Once rewritten, `args` is cleared so the already
    interpolated text is not formatted a second time.

    The record is marked afterwards. That is not a cache for correctness — a second walk would
    find nothing — but for cost: a record passes this function once per handler on top of the
    logger's own dispatch, and resolving the message each time is the expensive part.
    """

    if getattr(record, _SCRUBBED_MARK, False):
        return
    _scrub_message(record)
    _scrub_traceback(record)
    _scrub_extras(record)
    record.__dict__[_SCRUBBED_MARK] = True


def _scrub_extras(record: logging.LogRecord) -> None:
    """Replace every `extra` attribute whose text carries signing material.

    The values themselves are never mutated — a log record does not own the objects handed to
    it, and a caller's dict changing shape because it was logged would be a worse bug than the
    one being fixed. The *reference on the record* is swapped for the redacted form, which is
    what a `%(url)s` handler formats.
    """

    extras = record.__dict__.keys() - _RESERVED_RECORD_ATTRIBUTES
    for key in extras:
        value = record.__dict__[key]
        scrubbed = _scrub_value(value, _MAX_VALUE_DEPTH)
        if scrubbed is not value:
            record.__dict__[key] = scrubbed


def _scrub_value(value: Any, depth: int) -> Any:
    """Return the redacted form of `value`, or `value` itself when nothing had to change."""

    if isinstance(value, str):
        scrubbed = redact_signature_material(value)
        return scrubbed if scrubbed != value else value
    if value is None or isinstance(value, int | float | complex):
        return value
    if depth <= 0:
        return _scrub_rendered(value)
    if isinstance(value, Mapping):
        rebuilt = {key: _scrub_value(item, depth - 1) for key, item in value.items()}
        if all(rebuilt[key] is item for key, item in value.items()):
            return value
        return rebuilt
    if isinstance(value, list | tuple):
        items = [_scrub_value(item, depth - 1) for item in value]
        if all(new is old for new, old in zip(items, value, strict=True)):
            return value
        return tuple(items) if isinstance(value, tuple) else items
    return _scrub_rendered(value)


def _scrub_rendered(value: Any) -> Any:
    """Scrub an opaque object through its `str()`, which is what a handler would format.

    `httpx.URL` is the case that matters: it is not a string, it is not a container, and its
    text is the presigned URL. A `__str__` that raises is left alone — the same call would fail
    in the formatter, which reports it rather than emitting the value.
    """

    try:
        rendered = str(value)
    except Exception:
        return value
    scrubbed = redact_signature_material(rendered)
    return scrubbed if scrubbed != rendered else value


def _scrub_message(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
    except (TypeError, ValueError):
        # A malformed format/argument pair is the handler's error to report, not ours to hide.
        return
    scrubbed = redact_signature_material(message)
    if scrubbed != message:
        record.msg = scrubbed
        record.args = None


def _scrub_traceback(record: logging.LogRecord) -> None:
    """Pre-render a scrubbed traceback so no handler formats the raw one.

    `logging.Formatter` renders `exc_info` itself unless `exc_text` is already populated, in
    which case it appends that text verbatim. Filling it here is therefore the only way to keep
    a signed URL out of a traceback that reaches a handler this module never configured — an
    httpx error repr carries the request URL.

    The cache is written only when scrubbing actually removed something, so a formatter with its
    own `formatException` keeps its behaviour on every ordinary exception.
    """

    if record.exc_info is None or record.exc_text is not None:
        return
    rendered = "".join(traceback.format_exception(*record.exc_info))
    scrubbed = redact_signature_material(rendered)
    if scrubbed != rendered:
        record.exc_text = scrubbed


def add_trace_context(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Stamp the active OpenTelemetry trace/span id onto every log event.

    This is the correlation<->trace bridge (W05): with these ids on the log line you can jump
    from a log to its trace and back, while the existing ``correlation_id`` field is untouched.
    When telemetry is disabled the current span is the no-op ``INVALID_SPAN`` and nothing is
    added, so the only cost is one context read per log call.
    """

    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event_dict.setdefault("trace_id", trace.format_trace_id(context.trace_id))
        event_dict.setdefault("span_id", trace.format_span_id(context.span_id))
    return event_dict


def configure_logging(log_level: str) -> None:
    """Configure standard-library and structlog JSON output once per process."""

    install_signature_redaction()
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=log_level.upper(), force=True
    )
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter("%(message)s"))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_trace_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive_values,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
