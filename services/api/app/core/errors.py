"""RFC 9457-style Problem Details responses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.correlation import get_correlation_id


class ProblemDetails(BaseModel):
    """Stable, safe public error representation."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    correlation_id: str | None
    meta: dict[str, Any] = Field(default_factory=dict)


class ProblemException(Exception):
    """Application error that maps to a documented Problem Details response."""

    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str,
        type_uri: str = "about:blank",
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.type_uri = type_uri
        self.meta = meta or {}
        super().__init__(detail)


def safe_validation_error_meta(
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Expose validation locations and messages without echoing rejected input."""

    safe_errors: list[dict[str, Any]] = []
    for error in errors:
        location = [str(part) for part in error.get("loc", ())]
        safe_errors.append(
            {
                "location": location,
                "message": str(error.get("msg", "Invalid value.")),
                "type": str(error.get("type", "validation_error")),
            }
        )
    return {"errors": safe_errors}


def problem_response(request: Request, error: ProblemException) -> JSONResponse:
    """Build a public problem response without leaking dependency details."""

    problem = ProblemDetails(
        type=error.type_uri,
        title=error.title,
        status=error.status,
        detail=error.detail,
        instance=request.url.path,
        code=error.code,
        correlation_id=get_correlation_id(),
        meta=error.meta,
    )
    response = JSONResponse(
        status_code=error.status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
    )
    correlation_id = get_correlation_id()
    if correlation_id:
        response.headers["X-Correlation-ID"] = correlation_id
    return response
