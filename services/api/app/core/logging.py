"""Structured JSON logging with recursive secret redaction."""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any, cast

import structlog

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


def configure_logging(log_level: str) -> None:
    """Configure standard-library and structlog JSON output once per process."""

    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=log_level.upper(), force=True
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
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
