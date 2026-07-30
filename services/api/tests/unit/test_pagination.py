"""Cursor pagination primitive: opacity, strict rejection, stable page boundaries."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.core.errors import ProblemException
from app.core.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_SIZE,
    build_page,
    decode_cursor,
    encode_cursor,
    fetch_size,
    resolve_limit,
)

MOMENT = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Row:
    created_at: datetime
    id: UUID


def row(offset_seconds: int) -> Row:
    return Row(created_at=MOMENT - timedelta(seconds=offset_seconds), id=uuid4())


def key(value: Row) -> tuple[datetime, UUID]:
    return value.created_at, value.id


def test_cursor_round_trip_preserves_the_ordering_key() -> None:
    identifier = uuid4()
    encoded = encode_cursor(created_at=MOMENT, identifier=identifier)
    assert "=" not in encoded and "/" not in encoded and "+" not in encoded
    decoded = decode_cursor(encoded)
    assert decoded is not None
    assert decoded.created_at == MOMENT
    assert decoded.identifier == identifier


def test_cursor_is_normalized_to_utc() -> None:
    aware = datetime(2026, 7, 30, 15, 0, tzinfo=UTC).astimezone(datetime.now().astimezone().tzinfo)
    decoded = decode_cursor(encode_cursor(created_at=aware, identifier=uuid4()))
    assert decoded is not None
    assert decoded.created_at.tzinfo is UTC
    assert decoded.created_at == aware


def test_absent_cursor_is_the_first_page_not_an_error() -> None:
    assert decode_cursor(None) is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        "!!!not-base64!!!",
        base64.urlsafe_b64encode(b"not json").decode().rstrip("="),
        base64.urlsafe_b64encode(b'["array"]').decode().rstrip("="),
        base64.urlsafe_b64encode(b'{"c":"2026-07-30T12:00:00+00:00"}').decode().rstrip("="),
        base64.urlsafe_b64encode(b'{"c":"2026-07-30T12:00:00+00:00","i":"not-a-uuid"}')
        .decode()
        .rstrip("="),
        base64.urlsafe_b64encode(
            json.dumps({"c": "2026-07-30T12:00:00", "i": str(uuid4())}).encode()
        )
        .decode()
        .rstrip("="),
        base64.urlsafe_b64encode(
            json.dumps({"c": "2026-07-30T12:00:00+00:00", "i": str(uuid4()), "x": 1}).encode()
        )
        .decode()
        .rstrip("="),
        "A" * (MAX_CURSOR_LENGTH + 1),
    ],
)
def test_malformed_cursor_is_rejected_and_never_silently_reset(value: str) -> None:
    """A bad cursor must not quietly restart the list: the client would re-read page one."""

    with pytest.raises(ProblemException) as error:
        decode_cursor(value)
    assert error.value.status == 400
    assert error.value.code == "PAGINATION_CURSOR_INVALID"
    assert not value or value not in error.value.detail


def test_limit_is_clamped_between_one_and_the_ceiling() -> None:
    assert resolve_limit(None) == DEFAULT_PAGE_SIZE
    assert resolve_limit(0) == 1
    assert resolve_limit(-5) == 1
    assert resolve_limit(7) == 7
    assert resolve_limit(MAX_PAGE_SIZE + 5_000) == MAX_PAGE_SIZE
    assert fetch_size(10) == 11


def test_full_page_reports_the_last_returned_row_as_the_next_cursor() -> None:
    rows = [row(index) for index in range(4)]
    page = build_page(rows, limit=3, key=key)
    assert [item.id for item in page.items] == [item.id for item in rows[:3]]
    assert page.has_more
    decoded = decode_cursor(page.next_cursor)
    assert decoded is not None
    assert decoded.identifier == rows[2].id


def test_partial_and_empty_pages_end_the_walk() -> None:
    assert build_page([row(0), row(1)], limit=3, key=key).next_cursor is None
    assert build_page([], limit=3, key=key).items == ()
    assert build_page([], limit=3, key=key).has_more is False


def test_exactly_full_page_without_look_ahead_row_ends_the_walk() -> None:
    """`limit` rows and no look-ahead row means the list ended exactly on the boundary."""

    page = build_page([row(0), row(1), row(2)], limit=3, key=key)
    assert len(page.items) == 3
    assert page.next_cursor is None


def test_walking_pages_covers_every_row_exactly_once() -> None:
    """The primitive's contract in miniature: no skipped row, no repeated row."""

    rows = sorted((row(index) for index in range(10)), key=key, reverse=True)
    seen: list[UUID] = []
    position = 0
    while position < len(rows):
        page = build_page(rows[position : position + 4], limit=3, key=key)
        seen.extend(item.id for item in page.items)
        if not page.has_more:
            break
        position += len(page.items)
    assert seen == [item.id for item in rows]
    assert len(set(seen)) == len(rows)
