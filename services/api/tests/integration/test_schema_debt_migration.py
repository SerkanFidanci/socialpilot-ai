"""Migration 0011 data-safety coverage (W10, item 2 / acceptance criterion 1).

Two things must hold: the widened ``storage_upload_id`` column actually holds a real (long)
provider ``UploadId``, and reverting that widening on downgrade preserves the row rather than
dropping or corrupting it. The downgrade path is exercised with the real Alembic CLI so the
recreation of the enum types is covered at the same time.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

_API_DIR = Path(__file__).resolve().parents[2]


async def _insert_upload_graph(storage_upload_id: str) -> str:
    """Insert the minimal user -> business -> asset -> session graph; return the session id."""

    user_id, business_id, asset_id, session_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    suffix = uuid4().hex
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO users (id, email, status) VALUES (:id, :email, 'active')"),
                {"id": user_id, "email": f"migration-{suffix}@example.com"},
            )
            await connection.execute(
                text(
                    "INSERT INTO businesses (id, name, slug, status, timezone, created_by_user_id) "
                    "VALUES (:id, 'Migration', :slug, 'active', 'Europe/Istanbul', :uid)"
                ),
                {"id": business_id, "slug": f"migration-{suffix}", "uid": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO media_assets (id, business_id, created_by_user_id, "
                    "storage_object_key, content_type, byte_size, sha256_checksum, status, "
                    "ingest_status) VALUES (:id, :bid, :uid, :okey, 'video/mp4', 128, :chk, "
                    "'uploaded', 'pending')"
                ),
                {
                    "id": asset_id,
                    "bid": business_id,
                    "uid": user_id,
                    "okey": f"tenant/{business_id}/media/{asset_id}/original/{suffix}",
                    "chk": "a" * 64,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO media_upload_sessions (id, business_id, asset_id, "
                    "storage_upload_id, expected_part_count, status, expires_at) VALUES "
                    "(:id, :bid, :aid, :suid, 2, 'created', now() + interval '1 hour')"
                ),
                {"id": session_id, "bid": business_id, "aid": asset_id, "suid": storage_upload_id},
            )
    finally:
        await engine.dispose()
    return str(session_id)


async def _read_storage_upload_id(session_id: str) -> str | None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text("SELECT storage_upload_id FROM media_upload_sessions WHERE id = :id"),
                {"id": session_id},
            )
            return cast("str | None", value)
    finally:
        await engine.dispose()


async def _clear() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE media_upload_sessions, media_assets, businesses, users CASCADE")
            )
    finally:
        await engine.dispose()


def _run_alembic(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *command],
        cwd=_API_DIR,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


def _alembic(*command: str) -> None:
    completed = _run_alembic(*command)
    assert completed.returncode == 0, completed.stderr


async def _current_column_length() -> int:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            return int(
                cast(
                    int,
                    await connection.scalar(
                        text(
                            "SELECT character_maximum_length FROM information_schema.columns "
                            "WHERE table_name = 'media_upload_sessions' "
                            "AND column_name = 'storage_upload_id'"
                        )
                    ),
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(_clear())
    yield
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        _alembic("upgrade", "head")
        asyncio.run(_clear())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_widened_column_holds_a_real_provider_upload_id() -> None:
    # A representative long S3-style UploadId that the old String(128) could never hold.
    long_upload_id = "aBcDeF0123456789" * 18  # 288 chars
    assert len(long_upload_id) > 128

    session_id = asyncio.run(_insert_upload_graph(long_upload_id))

    assert asyncio.run(_read_storage_upload_id(session_id)) == long_upload_id


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_downgrade_preserves_storage_upload_id_data() -> None:
    # Fits both widths, so reverting the widening is a lossless in-place ALTER, not a rebuild.
    upload_id = "provider-upload-" + "x" * 100
    assert len(upload_id) <= 128

    session_id = asyncio.run(_insert_upload_graph(upload_id))

    _alembic("downgrade", "0010_brand_catalog")
    try:
        # The row and its value survived the column-type reversal.
        assert asyncio.run(_read_storage_upload_id(session_id)) == upload_id
    finally:
        _alembic("upgrade", "head")

    # And it is still there after re-applying the widening.
    assert asyncio.run(_read_storage_upload_id(session_id)) == upload_id


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_downgrade_refuses_in_the_open_when_an_upload_id_cannot_fit() -> None:
    """W10 verification finding 1: a real 288-character UploadId broke the reversal.

    Nothing was lost — the driver refused the truncation — but the operator got
    `StringDataRightTruncationError`, which names neither the table nor the fix, and W10's
    "up -> down -> up" acceptance criterion was not true of real data. It cannot be made true:
    a value of 288 characters has nowhere to go in `varchar(128)`. So the requirement is to
    stop *understandably* and to stop *before* touching anything.
    """

    long_upload_id = "aBcDeF0123456789" * 18  # 288 chars, the length AWS actually returns
    session_id = asyncio.run(_insert_upload_graph(long_upload_id))
    # Read the head rather than naming it: what this test asserts is that the refusal moved
    # nothing, and pinning a revision id here made every later slice edit an unrelated test.
    before = _run_alembic("current").stdout

    refused = _run_alembic("downgrade", "0010_brand_catalog")

    assert refused.returncode != 0
    output = refused.stdout + refused.stderr
    assert "MIGRATION_0011_DOWNGRADE_BLOCKED" in output
    assert "1 row(s)" in output and "288 characters" in output and session_id in output
    # The driver's error never reaches the operator, because the shrink is never attempted.
    assert "StringDataRightTruncationError" not in output
    # And nothing moved: same head, same column width, same value.
    assert asyncio.run(_current_column_length()) == 512
    assert asyncio.run(_read_storage_upload_id(session_id)) == long_upload_id
    assert _run_alembic("current").stdout == before

    # Once the offending session is gone, the same reversal runs to completion.
    asyncio.run(_clear())
    _alembic("downgrade", "0010_brand_catalog")
    assert asyncio.run(_current_column_length()) == 128
    _alembic("upgrade", "head")
    assert asyncio.run(_current_column_length()) == 512


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_downgrade_precondition_does_not_fire_on_an_empty_table() -> None:
    """The guard must refuse impossible reversals, not ordinary ones."""

    assert asyncio.run(_read_storage_upload_id(str(uuid4()))) is None

    _alembic("downgrade", "0010_brand_catalog")
    try:
        assert asyncio.run(_current_column_length()) == 128
    finally:
        _alembic("upgrade", "head")
