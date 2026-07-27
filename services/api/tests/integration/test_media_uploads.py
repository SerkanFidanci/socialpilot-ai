"""PostgreSQL integration coverage for direct media upload control plane."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.main import create_app
from app.modules.media.storage import StoredObjectMetadata

pytestmark = pytest.mark.integration
KEY, CHECKSUM = "test-local-identity-signing-key-123", "a" * 64


def config() -> Settings:
    return Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        celery_broker_url=os.environ["CELERY_BROKER_URL"],
        celery_result_backend=os.environ["CELERY_RESULT_BACKEND"],
        local_identity_signing_key=SecretStr(KEY),
    )


def auth(subject: str, email: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + LocalIdentityVerifier.sign_for_testing(signing_key=KEY, subject=subject, email=email)
    }


def payload(**values: object) -> dict[str, object]:
    result: dict[str, object] = {
        "filename": "clip.mp4",
        "content_type": "video/mp4",
        "byte_size": 128,
        "sha256_checksum": CHECKSUM,
        "part_count": 2,
    }
    result.update(values)
    return result


async def clear() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE media_upload_sessions, media_assets, business_members, businesses, external_identities, users CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean() -> Generator[None, None, None]:
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(clear())
    yield
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(clear())


async def storage_id(session_id: str) -> str:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            return str(
                await connection.scalar(
                    text("SELECT storage_upload_id FROM media_upload_sessions WHERE id = :id"),
                    {"id": session_id},
                )
            )
    finally:
        await engine.dispose()


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_upload_authorization_metadata_and_completion() -> None:
    owner, viewer, outsider = (
        auth("owner-media", "owner-media@example.com"),
        auth("viewer-media", "viewer-media@example.com"),
        auth("outsider-media", "outsider-media@example.com"),
    )
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses",
            headers=owner,
            json={"name": "Media tenant", "timezone": "Europe/Istanbul"},
        ).json()["id"]
        assert client.get("/v1/me", headers=viewer).status_code == 200
        assert (
            client.post(
                f"/v1/businesses/{business_id}/members",
                headers=owner,
                json={"email": "viewer-media@example.com", "role": "viewer"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads", headers=viewer, json=payload()
            ).status_code
            == 403
        )
        for invalid in (
            payload(content_type="application/pdf", filename="x.pdf"),
            payload(byte_size=999_999_999),
            payload(sha256_checksum="bad"),
            payload(object_key="forbidden"),
        ):
            assert client.post(
                f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=invalid
            ).status_code in {400, 422}
        created = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        )
        assert created.status_code == 201
        upload = created.json()
        assert "storage_object_key" not in upload and "bucket" not in upload
        fake = cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage)
        fake.mark_uploaded_for_testing(
            storage_upload_id=asyncio.run(storage_id(upload["id"])),
            parts={1: "one", 2: "two"},
            metadata=StoredObjectMetadata(128, "video/mp4", CHECKSUM),
        )
        completed = client.post(
            f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
            headers=owner,
            json={
                "sha256_checksum": CHECKSUM,
                "parts": [{"part_number": 1, "etag": "one"}, {"part_number": 2, "etag": "two"}],
            },
        )
        assert completed.status_code == 200
        asset = completed.json()
        assert asset["status"] == "uploaded" and asset["ingest_status"] == "pending"
        assert (
            client.get(
                f"/v1/businesses/{business_id}/media/{asset['id']}", headers=viewer
            ).status_code
            == 200
        )
        other_business = client.post(
            "/v1/businesses",
            headers=outsider,
            json={"name": "Other tenant", "timezone": "Europe/Istanbul"},
        ).json()["id"]
        assert (
            client.get(
                f"/v1/businesses/{other_business}/media/{asset['id']}", headers=outsider
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/v1/businesses/{other_business}/media/uploads/{upload['id']}/parts",
                headers=outsider,
                json={"part_numbers": [1]},
            ).status_code
            == 404
        )


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_cancel_expiry_invalid_parts_and_storage_error() -> None:
    owner = auth("media-states", "media-states@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses",
            headers=owner,
            json={"name": "Media states", "timezone": "Europe/Istanbul"},
        ).json()["id"]
        cancelled = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        ).json()
        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads/{cancelled['id']}/cancel",
                headers=owner,
            ).status_code
            == 204
        )
        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads/{cancelled['id']}/complete",
                headers=owner,
                json={"sha256_checksum": CHECKSUM, "parts": [{"part_number": 1, "etag": "one"}]},
            ).json()["code"]
            == "RESOURCE_STATE_CONFLICT"
        )
        incomplete = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        ).json()
        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads/{incomplete['id']}/complete",
                headers=owner,
                json={"sha256_checksum": CHECKSUM, "parts": [{"part_number": 1, "etag": "one"}]},
            ).status_code
            == 422
        )
        expired = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        ).json()

        async def expire() -> None:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE media_upload_sessions SET expires_at = timezone('utc', now()) - interval '1 second' WHERE id = :id"
                        ),
                        {"id": expired["id"]},
                    )
            finally:
                await engine.dispose()

        asyncio.run(expire())
        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads/{expired['id']}/parts",
                headers=owner,
                json={"part_numbers": [1]},
            ).json()["code"]
            == "UPLOAD_SESSION_EXPIRED"
        )
        unavailable = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        ).json()
        cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage).fail_for_testing(
            asyncio.run(storage_id(unavailable["id"]))
        )
        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads/{unavailable['id']}/parts",
                headers=owner,
                json={"part_numbers": [1]},
            ).json()["code"]
            == "STORAGE_UNAVAILABLE"
        )


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_concurrent_completion_is_serialized() -> None:
    owner = auth("media-concurrent", "media-concurrent@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses",
            headers=owner,
            json={"name": "Concurrent media", "timezone": "Europe/Istanbul"},
        ).json()["id"]
        upload = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        ).json()
        cast(
            FakeMultipartStorage, cast(FastAPI, client.app).state.storage
        ).mark_uploaded_for_testing(
            storage_upload_id=asyncio.run(storage_id(upload["id"])),
            parts={1: "one", 2: "two"},
            metadata=StoredObjectMetadata(128, "video/mp4", CHECKSUM),
        )

        def complete() -> int:
            return client.post(
                f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
                headers=owner,
                json={
                    "sha256_checksum": CHECKSUM,
                    "parts": [{"part_number": 1, "etag": "one"}, {"part_number": 2, "etag": "two"}],
                },
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: complete(), range(2)))
        assert sorted(statuses) == [200, 409]
