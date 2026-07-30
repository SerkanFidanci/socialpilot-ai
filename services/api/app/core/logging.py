"""Structured JSON logging, recursive secret redaction, and signature scrubbing.

Two guards live here and they cover different surfaces on purpose.

`redact_sensitive_values` is a structlog processor: it masks values by *key name* on events
this application writes itself. That is enough right up until a library logs for us.
`install_signature_redaction` is the second guard, and it is the one W14 exists for: a
process-wide `logging` record factory that scrubs signing material out of every record no
matter which logger produced it. During a real MinIO multipart upload, httpx logged the full
presigned URL — credential and signature query parameters included — at INFO, straight past a
key-based redactor that never saw the record.

Silencing httpx would have hidden that one line; it would not have stopped the next library
from doing the same thing, and it would have left the guard untestable on the path where the
leak actually happened. So httpx keeps its INFO level and the scrubber sits underneath every
logger instead.
"""

from __future__ import annotations

import logging
import re
import sys
import traceback
from collections.abc import MutableMapping
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
    "awsaccesskeyid",
    "access_token",
    "signature",
    "token",
    "sig",
)

_SIGNATURE_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(name) for name in _SIGNED_QUERY_PARAMS) + r")=([^&\s\"'<>\\]+)"
)


def redact_signature_material(text: str) -> str:
    """Mask the value of every signing query parameter in a piece of text.

    Only the value is replaced. The parameter name, the host, the object key and the rest of
    the query survive, because a log line that says *which* request was signed is the useful
    half and the signature is the dangerous half — a presigned URL is a bearer credential.
    """

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


def install_signature_redaction() -> None:
    """Install the process-wide record factory that scrubs signing material. Idempotent.

    A record factory rather than a handler filter: a filter only guards the handlers it was
    attached to, and the whole point is that a logger nobody registered — a new HTTP client, a
    provider SDK, a test's own handler — is covered without anyone remembering to opt in. The
    factory runs at record creation, so nothing downstream ever sees the unmasked text.

    The previous factory is chained rather than replaced, and re-entry is a no-op, so calling
    this from both the API app factory and the worker process init is safe.
    """

    global _redaction_installed
    if _redaction_installed:
        return
    inner = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = inner(*args, **kwargs)
        _scrub_record(record)
        return record

    logging.setLogRecordFactory(factory)
    _redaction_installed = True


def _scrub_record(record: logging.LogRecord) -> None:
    """Rewrite a record's message and traceback in place when they carry signing material.

    The message is resolved first because the interesting argument is rarely a string: httpx
    logs `'HTTP Request: %s %s ...'` with an `httpx.URL` object, so scrubbing `record.args`
    element by element would miss it entirely. Once rewritten, `args` is cleared so the already
    interpolated text is not formatted a second time.
    """

    _scrub_message(record)
    _scrub_traceback(record)


def _scrub_message(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
    except (TypeError, ValueError):
        # A malformed format/argument pair is the handler's error to report, not ours to hide.
        return
    if "=" not in message:
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
