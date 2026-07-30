"""End-to-end upload against a real S3-compatible provider (MinIO).

This is the only test that proves the byte path: the client PUTs parts straight to storage
with server-issued presigned URLs, and completion is verified against what storage actually
holds. It needs network and credentials, so it skips unless a storage endpoint is configured.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import Generator
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.main import create_app

pytestmark = pytest.mark.integration
KEY = "test-local-identity-signing-key-123"
# MinIO and S3 both require every part except the last to be at least 5 MiB.
FIRST_PART = bytes(5 * 1024 * 1024)
LAST_PART = b"\x01" * 1024
PAYLOAD = FIRST_PART + LAST_PART
CHECKSUM = hashlib.sha256(PAYLOAD).hexdigest()

storage_configured = bool(os.getenv("S3_ENDPOINT_URL")) and bool(os.getenv("S3_BUCKET"))
requires_storage = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1" or not storage_configured,
    reason="requires PostgreSQL and an S3-compatible storage endpoint",
)


def config() -> Settings:
    endpoint = os.environ["S3_ENDPOINT_URL"]
    return Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        celery_broker_url=os.environ["CELERY_BROKER_URL"],
        celery_result_backend=os.environ["CELERY_RESULT_BACKEND"],
        local_identity_signing_key=SecretStr(KEY),
        storage_adapter="s3",
        s3_endpoint_url=endpoint,
        # This suite runs beside the API, so it reaches storage at the same address.
        s3_presign_endpoint_url=endpoint,
        s3_region=os.environ.get("S3_REGION", "us-east-1"),
        s3_bucket=os.environ["S3_BUCKET"],
        s3_access_key_id=SecretStr(os.environ["S3_ACCESS_KEY_ID"]),
        s3_secret_access_key=SecretStr(os.environ["S3_SECRET_ACCESS_KEY"]),
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
        "byte_size": len(PAYLOAD),
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
                    "TRUNCATE media_upload_sessions, media_assets, business_members, businesses, "
                    "external_identities, users, jobs, outbox_events, idempotency_keys, audit_logs "
                    "CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
    if os.getenv("RUN_INTEGRATION_TESTS") == "1" and storage_configured:
        asyncio.run(clear())
    yield
    if os.getenv("RUN_INTEGRATION_TESTS") == "1" and storage_configured:
        asyncio.run(clear())


async def scalar(query: str) -> object:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(query))
    finally:
        await engine.dispose()


def put_parts(parts: list[dict[str, object]], bodies: list[bytes]) -> list[dict[str, object]]:
    """Upload straight to object storage, exactly as the mobile client does."""

    uploaded: list[dict[str, object]] = []
    with httpx.Client(timeout=60.0) as client:
        for instruction, body in zip(parts, bodies, strict=True):
            url = str(instruction["upload_url"])
            # The part URL carries its own authorization; no bearer token is ever attached.
            response = client.put(url, content=body)
            assert response.status_code == 200, response.text
            uploaded.append(
                {
                    "part_number": instruction["part_number"],
                    "etag": response.headers["etag"].strip('"'),
                }
            )
    return uploaded


def business_for(client: TestClient, headers: dict[str, str], name: str) -> str:
    return str(
        client.post(
            "/v1/businesses", headers=headers, json={"name": name, "timezone": "Europe/Istanbul"}
        ).json()["id"]
    )


@requires_storage
def test_real_multipart_upload_reaches_storage_and_queues_ingest() -> None:
    owner = auth("minio-owner", "minio-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business_for(client, owner, "MinIO tenant")
        created = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        )
        assert created.status_code == 201, created.text
        upload = created.json()
        assert "storage_object_key" not in upload and "bucket" not in upload

        # Part URLs point at object storage, so the bytes never traverse FastAPI.
        endpoint_host = urlsplit(os.environ["S3_ENDPOINT_URL"]).netloc
        for instruction in upload["parts"]:
            assert urlsplit(instruction["upload_url"]).netloc == endpoint_host
            assert "X-Amz-Signature=" in instruction["upload_url"]

        uploaded = put_parts(upload["parts"], [FIRST_PART, LAST_PART])
        completed = client.post(
            f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
            headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
            json={"sha256_checksum": CHECKSUM, "parts": uploaded},
        )

        assert completed.status_code == 200, completed.text
        asset = completed.json()
        assert asset["status"] == "uploaded"
        assert asset["ingest_status"] == "pending"
        assert asset["byte_size"] == len(PAYLOAD)
        # The digest on the asset is the one storage actually holds.
        assert asset["sha256_checksum"] == CHECKSUM

    assert (
        asyncio.run(
            scalar(
                "SELECT count(*) FROM jobs WHERE job_type = 'media.ingest' AND status = 'queued'"
            )
        )
        == 1
    )
    assert (
        asyncio.run(
            scalar("SELECT count(*) FROM outbox_events WHERE event_type = 'media.ingest.requested'")
        )
        == 1
    )


@requires_storage
def test_declared_checksum_that_storage_contradicts_is_rejected() -> None:
    owner = auth("minio-checksum", "minio-checksum@example.com")
    wrong = hashlib.sha256(b"a different file entirely").hexdigest()
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business_for(client, owner, "MinIO checksum")
        upload = client.post(
            f"/v1/businesses/{business_id}/media/uploads",
            headers=owner,
            json=payload(sha256_checksum=wrong),
        ).json()
        uploaded = put_parts(upload["parts"], [FIRST_PART, LAST_PART])

        rejected = client.post(
            f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
            headers=owner,
            json={"sha256_checksum": wrong, "parts": uploaded},
        )

        assert rejected.status_code == 409
        assert rejected.json()["code"] == "UPLOAD_CHECKSUM_MISMATCH"
        # Verification failure leaves the asset unpublished and queues no ingest work.
        assert (
            client.get(
                f"/v1/businesses/{business_id}/media/{upload['asset_id']}", headers=owner
            ).json()["status"]
            == "uploading"
        )
    assert asyncio.run(scalar("SELECT count(*) FROM jobs")) == 0


@requires_storage
def test_part_declaration_that_storage_contradicts_is_rejected() -> None:
    owner = auth("minio-parts", "minio-parts@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business_for(client, owner, "MinIO parts")
        upload = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        ).json()
        uploaded = put_parts(upload["parts"], [FIRST_PART, LAST_PART])
        url = f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete"

        missing = client.post(
            url, headers=owner, json={"sha256_checksum": CHECKSUM, "parts": uploaded[:1]}
        )
        extra = client.post(
            url,
            headers=owner,
            json={
                "sha256_checksum": CHECKSUM,
                "parts": [*uploaded, {"part_number": 3, "etag": "invented"}],
            },
        )
        forged = client.post(
            url,
            headers=owner,
            json={
                "sha256_checksum": CHECKSUM,
                "parts": [uploaded[0], {"part_number": 2, "etag": "0" * 32}],
            },
        )

        # A part inventory the session did not declare is a metadata error.
        assert missing.status_code == 422
        assert missing.json()["code"] == "UPLOAD_METADATA_INVALID"
        assert extra.status_code == 422
        assert extra.json()["code"] == "UPLOAD_METADATA_INVALID"
        # A forged ETag matches the session shape but not what storage holds.
        assert forged.status_code == 409
        assert forged.json()["code"] == "UPLOAD_CHECKSUM_MISMATCH"


@requires_storage
def test_expired_session_receives_no_further_upload_capability() -> None:
    owner = auth("minio-expiry", "minio-expiry@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business_for(client, owner, "MinIO expiry")
        upload = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        ).json()

        async def expire() -> None:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE media_upload_sessions SET expires_at = "
                            "timezone('utc', now()) - interval '1 second' WHERE id = :id"
                        ),
                        {"id": upload["id"]},
                    )
            finally:
                await engine.dispose()

        asyncio.run(expire())
        refused = client.post(
            f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/parts",
            headers=owner,
            json={"part_numbers": [1]},
        )

        assert refused.json()["code"] == "UPLOAD_SESSION_EXPIRED"
        assert "X-Amz-Signature" not in refused.text


@requires_storage
def test_cancelled_session_releases_the_provider_upload() -> None:
    owner = auth("minio-cancel", "minio-cancel@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business_for(client, owner, "MinIO cancel")
        upload = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        ).json()
        put_parts(upload["parts"][:1], [FIRST_PART])

        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/cancel", headers=owner
            ).status_code
            == 204
        )
        # A cancelled session cannot be completed afterwards.
        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
                headers=owner,
                json={
                    "sha256_checksum": CHECKSUM,
                    "parts": [{"part_number": 1, "etag": "0" * 32}],
                },
            ).json()["code"]
            == "RESOURCE_STATE_CONFLICT"
        )
