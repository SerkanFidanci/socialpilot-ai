"""PostgreSQL integration coverage for direct media upload control plane."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
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
from app.modules.media.storage import (
    CompletedPart,
    CreatedUpload,
    StoredObjectMetadata,
    UploadPartInstruction,
)

pytestmark = pytest.mark.integration
KEY, CHECKSUM = "test-local-identity-signing-key-123", "a" * 64
SIGNED_URL_SENTINEL = "X-Amz-Signature=do-not-log-this-signature"


def config(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": os.environ["DATABASE_URL"],
        "redis_url": os.environ["REDIS_URL"],
        "celery_broker_url": os.environ["CELERY_BROKER_URL"],
        "celery_result_backend": os.environ["CELERY_RESULT_BACKEND"],
        "local_identity_signing_key": SecretStr(KEY),
        # The development container exports S3_* variables; the control-plane suite must
        # keep the byte-free adapter regardless of the surrounding environment.
        "storage_adapter": "fake",
    }
    values.update(overrides)
    return Settings.model_validate(values)


class SignedUrlStorage(FakeMultipartStorage):
    """Fake adapter whose part URLs carry a sentinel no log or audit row may contain."""

    async def create_part_urls(
        self,
        *,
        object_key: str,
        storage_upload_id: str,
        expires_at: datetime,
        part_numbers: tuple[int, ...],
    ) -> tuple[UploadPartInstruction, ...]:
        await super().create_part_urls(
            object_key=object_key,
            storage_upload_id=storage_upload_id,
            expires_at=expires_at,
            part_numbers=part_numbers,
        )
        return tuple(
            UploadPartInstruction(
                part,
                f"https://storage.example.invalid/part/{part}?{SIGNED_URL_SENTINEL}",
            )
            for part in part_numbers
        )


class RefusingStorage:
    """Adapter that fails the test if authorization ever lets a call reach storage."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _refuse(self, operation: str) -> None:
        self.calls.append(operation)
        raise AssertionError(f"storage was called before authorization: {operation}")

    async def create_upload(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_at: datetime,
        part_numbers: tuple[int, ...],
    ) -> CreatedUpload:
        self._refuse("create_upload")
        raise AssertionError("unreachable")

    async def create_part_urls(
        self,
        *,
        object_key: str,
        storage_upload_id: str,
        expires_at: datetime,
        part_numbers: tuple[int, ...],
    ) -> tuple[UploadPartInstruction, ...]:
        self._refuse("create_part_urls")
        raise AssertionError("unreachable")

    async def complete_upload(
        self, *, object_key: str, storage_upload_id: str, parts: tuple[CompletedPart, ...]
    ) -> StoredObjectMetadata:
        self._refuse("complete_upload")
        raise AssertionError("unreachable")

    async def get_object_metadata(self, *, object_key: str) -> StoredObjectMetadata:
        self._refuse("get_object_metadata")
        raise AssertionError("unreachable")

    async def persist_file(
        self, *, object_key: str, source_path: Path, content_type: str
    ) -> StoredObjectMetadata:
        self._refuse("persist_file")
        raise AssertionError("unreachable")

    async def cancel_upload(self, *, object_key: str, storage_upload_id: str) -> None:
        self._refuse("cancel_upload")


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
                    "TRUNCATE media_upload_sessions, media_assets, business_members, businesses, external_identities, users, jobs, outbox_events, idempotency_keys, audit_logs CASCADE"
                )
            )
    finally:
        await engine.dispose()


async def persisted_text() -> str:
    """Every row this flow can write, rendered as text for a leak search."""

    engine = create_async_engine(os.environ["DATABASE_URL"])
    tables = (
        "audit_logs",
        "idempotency_keys",
        "outbox_events",
        "jobs",
        "job_attempts",
        "media_assets",
        "media_upload_sessions",
    )
    try:
        async with engine.connect() as connection:
            rows = [
                str(row)
                for table in tables
                for row in (await connection.execute(text(f"SELECT {table}::text FROM {table}")))
            ]
    finally:
        await engine.dispose()
    return "\n".join(rows)


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
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
            return int(
                client.post(
                    f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
                    headers=owner,
                    json={
                        "sha256_checksum": CHECKSUM,
                        "parts": [
                            {"part_number": 1, "etag": "one"},
                            {"part_number": 2, "etag": "two"},
                        ],
                    },
                ).status_code
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: complete(), range(2)))
        assert sorted(statuses) == [200, 409]


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_unauthorized_and_foreign_tenant_requests_never_reach_storage() -> None:
    owner, viewer, outsider = (
        auth("owner-guard", "owner-guard@example.com"),
        auth("viewer-guard", "viewer-guard@example.com"),
        auth("outsider-guard", "outsider-guard@example.com"),
    )
    refusing = RefusingStorage()
    application = create_app(config(), storage_factory=lambda _: refusing)
    with TestClient(application, raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Guard", "timezone": "Europe/Istanbul"}
        ).json()["id"]
        assert client.get("/v1/me", headers=viewer).status_code == 200
        assert client.get("/v1/me", headers=outsider).status_code == 200
        assert (
            client.post(
                f"/v1/businesses/{business_id}/members",
                headers=owner,
                json={"email": "viewer-guard@example.com", "role": "viewer"},
            ).status_code
            == 201
        )
        session_id = "11111111-1111-1111-1111-111111111111"
        completion = {
            "sha256_checksum": CHECKSUM,
            "parts": [{"part_number": 1, "etag": "one"}],
        }

        # A member without the upload permission is refused before any adapter call.
        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads", headers=viewer, json=payload()
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/v1/businesses/{business_id}/media/uploads/{session_id}/parts",
                headers=viewer,
                json={"part_numbers": [1]},
            ).status_code
            == 403
        )
        # A non-member is not even told the tenant exists.
        for status, response in (
            (
                404,
                client.post(
                    f"/v1/businesses/{business_id}/media/uploads", headers=outsider, json=payload()
                ),
            ),
            (
                404,
                client.post(
                    f"/v1/businesses/{business_id}/media/uploads/{session_id}/complete",
                    headers=outsider,
                    json=completion,
                ),
            ),
            (
                404,
                client.post(
                    f"/v1/businesses/{business_id}/media/uploads/{session_id}/cancel",
                    headers=outsider,
                ),
            ),
        ):
            assert response.status_code == status

    assert refusing.calls == []


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_signed_part_urls_stay_out_of_logs_audit_rows_and_error_bodies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    owner = auth("owner-redact", "owner-redact@example.com")
    application = create_app(config(), storage_factory=lambda _: SignedUrlStorage())
    with TestClient(application, raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses",
            headers=owner,
            json={"name": "Redaction", "timezone": "Europe/Istanbul"},
        ).json()["id"]
        created = client.post(
            f"/v1/businesses/{business_id}/media/uploads", headers=owner, json=payload()
        )
        upload = created.json()
        # The responding client is the only place a signed URL may appear.
        assert SIGNED_URL_SENTINEL in created.text
        more = client.post(
            f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/parts",
            headers=owner,
            json={"part_numbers": [1, 2]},
        )
        assert SIGNED_URL_SENTINEL in more.text
        storage = cast(SignedUrlStorage, cast(FastAPI, client.app).state.storage)
        storage.mark_uploaded_for_testing(
            storage_upload_id=asyncio.run(storage_id(upload["id"])),
            parts={1: "one", 2: "two"},
            metadata=StoredObjectMetadata(128, "video/mp4", CHECKSUM),
        )
        completed = client.post(
            f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
            headers=owner | {"Idempotency-Key": "redaction-1"},
            json={
                "sha256_checksum": CHECKSUM,
                "parts": [{"part_number": 1, "etag": "one"}, {"part_number": 2, "etag": "two"}],
            },
        )
        assert completed.status_code == 200
        failure = client.post(
            f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/parts",
            headers=owner,
            json={"part_numbers": [1]},
        )
        assert failure.status_code >= 400 and SIGNED_URL_SENTINEL not in failure.text

    emitted = capsys.readouterr().out
    # Guard the guard: if log capture ever stops working, fail instead of passing vacuously.
    assert "application_started" in emitted
    for leak in (SIGNED_URL_SENTINEL, "storage.example.invalid", "X-Amz-Signature"):
        assert leak not in emitted
    persisted = asyncio.run(persisted_text())
    for leak in (SIGNED_URL_SENTINEL, "storage.example.invalid", "X-Amz-Signature"):
        assert leak not in persisted


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_completion_is_idempotent_per_key_and_rejects_a_changed_payload() -> None:
    owner = auth("owner-idem", "owner-idem@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses",
            headers=owner,
            json={"name": "Idempotent", "timezone": "Europe/Istanbul"},
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
        body = {
            "sha256_checksum": CHECKSUM,
            "parts": [{"part_number": 1, "etag": "one"}, {"part_number": 2, "etag": "two"}],
        }
        url = f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete"

        first = client.post(url, headers=owner | {"Idempotency-Key": "retry-1"}, json=body)
        replay = client.post(url, headers=owner | {"Idempotency-Key": "retry-1"}, json=body)
        conflict = client.post(
            url,
            headers=owner | {"Idempotency-Key": "retry-1"},
            json=body | {"sha256_checksum": "b" * 64},
        )

        assert first.status_code == 200
        # A retried delivery replays the first result instead of completing twice.
        assert replay.status_code == 200
        assert replay.json()["id"] == first.json()["id"]
        assert replay.json()["uploaded_at"] == first.json()["uploaded_at"]
        assert conflict.status_code == 409


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_ios_default_media_types_are_accepted_and_rejections_stay_opaque() -> None:
    owner = auth("owner-mime", "owner-mime@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "MIME", "timezone": "Europe/Istanbul"}
        ).json()["id"]
        url = f"/v1/businesses/{business_id}/media/uploads"

        for filename, content_type in (
            ("IMG_0001.HEIC", "image/heic"),
            ("IMG_0002.heif", "image/heif"),
            ("IMG_0003.heic", "image/heif"),
            ("IMG_0004.MOV", "video/quicktime"),
        ):
            created = client.post(
                url, headers=owner, json=payload(filename=filename, content_type=content_type)
            )
            assert created.status_code == 201, (filename, content_type, created.text)
            assert created.json()["asset_id"]

        for filename, content_type in (
            ("payload.pdf", "application/pdf"),
            ("IMG_0005.heic", "video/quicktime"),
            ("clip.mov", "video/mp4"),
        ):
            rejected = client.post(
                url, headers=owner, json=payload(filename=filename, content_type=content_type)
            )
            assert rejected.status_code == 422
            body = rejected.json()
            assert body["code"] == "UPLOAD_METADATA_INVALID"
            # The rejection must not hand back the allowlist.
            for leak in ("image/", "video/", "audio/", "heic", "quicktime", "mp4"):
                assert leak not in rejected.text
