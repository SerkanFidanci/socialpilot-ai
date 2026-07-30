"""Opaque cursor pagination primitives shared by every tenant list endpoint.

Technical only: no domain type appears here. A caller supplies the two ordering columns — a
creation timestamp and the tie-breaking UUID primary key — and receives a keyset predicate.
Keyset paging is used rather than `OFFSET` because tenant lists grow while a client is paging
through them: an insert before the current position shifts every later offset, which silently
skips or repeats rows. A `(created_at, id)` boundary cannot, because the boundary is the last
row the client actually saw.

The cursor is opaque and self-validating, not signed: it carries no authorization and is always
re-filtered by the caller's tenant scope, so a forged cursor can only move a client inside its
own list. A cursor that does not decode is rejected rather than ignored.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, SQLColumnExpression

from app.core.errors import ProblemException

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_CURSOR_LENGTH = 256

CURSOR_INVALID_CODE = "PAGINATION_CURSOR_INVALID"


@dataclass(frozen=True, slots=True)
class Cursor:
    """The last row a client received, expressed in the stable ordering key."""

    created_at: datetime
    identifier: UUID


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of rows plus the cursor that continues it."""

    items: tuple[T, ...]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


def cursor_invalid() -> ProblemException:
    """One rejection for every malformed cursor; the reason is never echoed back."""

    return ProblemException(
        status=400,
        code=CURSOR_INVALID_CODE,
        title="Invalid pagination cursor",
        detail="The pagination cursor is not valid. Restart the list without a cursor.",
    )


def encode_cursor(*, created_at: datetime, identifier: UUID) -> str:
    """Encode the ordering key as unpadded base64url so it stays URL-safe and opaque."""

    payload = json.dumps(
        {"c": created_at.astimezone(UTC).isoformat(), "i": str(identifier)},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(value: str | None) -> Cursor | None:
    """Decode a client cursor strictly; anything unexpected is a `400`, never a silent reset."""

    if value is None:
        return None
    if not value or len(value) > MAX_CURSOR_LENGTH:
        raise cursor_invalid()
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding)
        document = json.loads(decoded.decode())
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise cursor_invalid() from None
    if not isinstance(document, dict) or set(document) != {"c", "i"}:
        raise cursor_invalid()
    raw_created_at, raw_identifier = document["c"], document["i"]
    if not isinstance(raw_created_at, str) or not isinstance(raw_identifier, str):
        raise cursor_invalid()
    try:
        created_at = datetime.fromisoformat(raw_created_at)
        identifier = UUID(raw_identifier)
    except ValueError:
        raise cursor_invalid() from None
    if created_at.tzinfo is None:
        raise cursor_invalid()
    return Cursor(created_at=created_at.astimezone(UTC), identifier=identifier)


def resolve_limit(requested: int | None) -> int:
    """Clamp a page size for internal callers; the HTTP layer rejects out-of-range input."""

    if requested is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(requested, MAX_PAGE_SIZE))


def fetch_size(limit: int) -> int:
    """Read one row beyond the page to learn whether a next page exists, without counting."""

    return limit + 1


def apply_cursor(
    statement: Select[Any],
    *,
    created_at: SQLColumnExpression[datetime],
    identifier: SQLColumnExpression[UUID],
    cursor: Cursor | None,
) -> Select[Any]:
    """Order newest-first with a total tie-break and continue strictly after the cursor row.

    The predicate is written as an explicit disjunction rather than a row-value comparison so
    the emitted SQL and its parameter types stay obvious in a query log.
    """

    ordered = statement.order_by(created_at.desc(), identifier.desc())
    if cursor is None:
        return ordered
    return ordered.where(
        (created_at < cursor.created_at)
        | ((created_at == cursor.created_at) & (identifier < cursor.identifier))
    )


def build_page[T](
    rows: Sequence[T], *, limit: int, key: Callable[[T], tuple[datetime, UUID]]
) -> Page[T]:
    """Trim the look-ahead row and derive the next cursor from the last returned row."""

    items = tuple(rows[:limit])
    if len(rows) <= limit or not items:
        return Page(items=items, next_cursor=None)
    created_at, identifier = key(items[-1])
    return Page(
        items=items, next_cursor=encode_cursor(created_at=created_at, identifier=identifier)
    )
