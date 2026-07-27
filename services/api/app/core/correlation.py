"""Correlation-ID propagation for HTTP requests and structured logs."""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, clear_contextvars

CORRELATION_ID_HEADER = "X-Correlation-ID"
_VALID_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Return the correlation identifier bound to the current request."""

    return _correlation_id.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Preserve a safe client ID or generate one for each request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied_id = request.headers.get(CORRELATION_ID_HEADER)
        correlation_id = (
            supplied_id
            if supplied_id and _VALID_CORRELATION_ID.fullmatch(supplied_id)
            else str(uuid4())
        )
        token: Token[str | None] = _correlation_id.set(correlation_id)
        clear_contextvars()
        bind_contextvars(correlation_id=correlation_id)

        try:
            response = await call_next(request)
            response.headers[CORRELATION_ID_HEADER] = correlation_id
            return response
        finally:
            clear_contextvars()
            _correlation_id.reset(token)
