"""Structured JSON logging with recursive secret redaction."""

from __future__ import annotations

import logging
import sys
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


def _redact(value: Any) -> Any:
    if isinstance(value, MutableMapping):
        return {
            key: "[REDACTED]"
            if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def redact_sensitive_values(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Remove secrets and signed URLs before a log event leaves the process."""

    return cast(MutableMapping[str, Any], _redact(event_dict))


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

    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=log_level.upper(), force=True
    )
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
