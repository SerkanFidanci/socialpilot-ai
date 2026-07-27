"""PostgreSQL coverage for the tenant-safe Phase 1A ingest gate."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.infrastructure.media.fake_ingest import FakeMalwareScanner
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.main import create_app
from app.modules.media.ingest import MediaIngestService
from app.modules.media.models import IngestStatus, MalwareScanStatus, MediaAsset, MediaAssetStatus
from app.modules.media.storage import StoredObjectMetadata
from app.modules.operations.models import BackgroundJob, JobStatus

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


async def clear() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE media_malware_scans, media_ingest_inspections, audit_logs, "
                    "idempotency_keys, job_attempts, jobs, outbox_events, media_upload_sessions, "
                    "media_assets, business_members, businesses, external_identities, users CASCADE"
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


def upload_payload() -> dict[str, object]:
    return {
        "filename": "clip.mp4",
        "content_type": "video/mp4",
        "byte_size": 128,
        "sha256_checksum": CHECKSUM,
        "part_count": 2,
    }


async def upload_details(session_id: str) -> tuple[str, str]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT s.storage_upload_id, a.storage_object_key "
                        "FROM media_upload_sessions s JOIN media_assets a ON a.id = s.asset_id "
                        "WHERE s.id = :id"
                    ),
                    {"id": session_id},
                )
            ).one()
            return str(row.storage_upload_id), str(row.storage_object_key)
    finally:
        await engine.dispose()


def complete_upload(
    client: TestClient, business_id: str, headers: dict[str, str]
) -> dict[str, object]:
    created = client.post(
        f"/v1/businesses/{business_id}/media/uploads", headers=headers, json=upload_payload()
    )
    assert created.status_code == 201
    upload = cast(dict[str, object], created.json())
    fake = cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage)
    storage_upload_id, _ = asyncio.run(upload_details(str(upload["id"])))
    fake.mark_uploaded_for_testing(
        storage_upload_id=storage_upload_id,
        parts={1: "one", 2: "two"},
        metadata=StoredObjectMetadata(128, "video/mp4", CHECKSUM, "etag-1"),
    )
    completed = client.post(
        f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
        headers={**headers, "Idempotency-Key": f"complete-{upload['id']}"},
        json={
            "sha256_checksum": CHECKSUM,
            "parts": [{"part_number": 1, "etag": "one"}, {"part_number": 2, "etag": "two"}],
        },
    )
    assert completed.status_code == 200
    return cast(dict[str, object], completed.json())


async def process_next(application: FastAPI) -> BackgroundJob | None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            service = MediaIngestService(
                session,
                config(),
                cast(FakeMultipartStorage, application.state.storage),
                application.state.content_inspector,
                application.state.malware_scanner,
            )
            return await service.process_next()
    finally:
        await engine.dispose()


async def stored_asset(asset_id: str) -> tuple[MediaAsset, BackgroundJob]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            asset = await session.scalar(select(MediaAsset).where(MediaAsset.id == asset_id))
            job = await session.scalar(
                select(BackgroundJob).where(BackgroundJob.resource_id == asset_id)
            )
            assert asset is not None and job is not None
            return asset, job
    finally:
        await engine.dispose()


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_ingest_records_verified_metadata_and_clean_scan() -> None:
    owner = auth("ingest-owner", "ingest-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Ingest", "timezone": "UTC"}
        ).json()["id"]
        asset = complete_upload(client, business_id, owner)
        job = asyncio.run(process_next(cast(FastAPI, client.app)))
        assert job is not None and job.status == JobStatus.SUCCEEDED
        persisted_asset, persisted_job = asyncio.run(stored_asset(str(asset["id"])))
        assert persisted_asset.ingest_status == IngestStatus.READY_FOR_ANALYSIS
        assert persisted_asset.status == MediaAssetStatus.UPLOADED
        assert persisted_job.status == JobStatus.SUCCEEDED


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_ingest_rejects_storage_size_checksum_and_type_mismatch() -> None:
    owner = auth("ingest-mismatch", "ingest-mismatch@example.com")
    scenarios = (
        StoredObjectMetadata(129, "video/mp4", CHECKSUM, "etag-size"),
        StoredObjectMetadata(128, "video/mp4", "b" * 64, "etag-checksum"),
        StoredObjectMetadata(128, "application/pdf", CHECKSUM, "etag-type"),
    )
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Mismatch", "timezone": "UTC"}
        ).json()["id"]
        fake = cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage)
        for metadata in scenarios:
            asset = complete_upload(client, business_id, owner)
            _, object_key = asyncio.run(upload_details_for_asset(str(asset["id"])))
            fake.set_object_metadata_for_testing(object_key=object_key, metadata=metadata)
            job = asyncio.run(process_next(cast(FastAPI, client.app)))
            assert job is not None and job.status == JobStatus.FAILED
            persisted, _ = asyncio.run(stored_asset(str(asset["id"])))
            assert persisted.ingest_status == IngestStatus.REJECTED
            assert persisted.status == MediaAssetStatus.REJECTED


async def upload_details_for_asset(asset_id: str) -> tuple[str, str]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT s.storage_upload_id, a.storage_object_key "
                        "FROM media_upload_sessions s JOIN media_assets a ON a.id = s.asset_id "
                        "WHERE a.id = :id"
                    ),
                    {"id": asset_id},
                )
            ).one()
            return str(row.storage_upload_id), str(row.storage_object_key)
    finally:
        await engine.dispose()


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_ingest_quarantines_infected_media_and_claims_once() -> None:
    owner, other = (
        auth("ingest-claim", "ingest-claim@example.com"),
        auth("ingest-other", "ingest-other@example.com"),
    )
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Claim", "timezone": "UTC"}
        ).json()["id"]
        other_business = client.post(
            "/v1/businesses", headers=other, json={"name": "Other", "timezone": "UTC"}
        ).json()["id"]
        asset = complete_upload(client, business_id, owner)
        _, object_key = asyncio.run(upload_details_for_asset(str(asset["id"])))
        scanner = cast(FakeMalwareScanner, cast(FastAPI, client.app).state.malware_scanner)
        scanner.set_result_for_testing(object_key=object_key, status=MalwareScanStatus.INFECTED)
        job = asyncio.run(process_next(cast(FastAPI, client.app)))
        assert job is not None and job.status == JobStatus.FAILED
        persisted, persisted_job = asyncio.run(stored_asset(str(asset["id"])))
        assert persisted.ingest_status == IngestStatus.REJECTED
        assert persisted.status == MediaAssetStatus.QUARANTINED
        assert persisted_job.next_attempt_at is None
        assert (
            client.get(
                f"/v1/businesses/{other_business}/media/{asset['id']}", headers=other
            ).status_code
            == 404
        )


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_transient_storage_failure_retries_then_dead_and_concurrent_claim_is_exclusive() -> None:
    owner = auth("ingest-retry", "ingest-retry@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Retry", "timezone": "UTC"}
        ).json()["id"]
        asset = complete_upload(client, business_id, owner)
        _, object_key = asyncio.run(upload_details_for_asset(str(asset["id"])))
        fake = cast(FakeMultipartStorage, application.state.storage)
        fake.fail_object_for_testing(object_key)

        async def claim_pair() -> int:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            try:
                async with factory() as first, factory() as second:
                    first_service = MediaIngestService(
                        first,
                        config(),
                        fake,
                        application.state.content_inspector,
                        application.state.malware_scanner,
                    )
                    second_service = MediaIngestService(
                        second,
                        config(),
                        fake,
                        application.state.content_inspector,
                        application.state.malware_scanner,
                    )
                    claims = await asyncio.gather(
                        first_service.claim_next(), second_service.claim_next()
                    )
                    return sum(value is not None for value in claims)
            finally:
                await engine.dispose()

        assert asyncio.run(claim_pair()) == 1

        # The successful claimant is already running; execute it through a fresh service.
        async def running_job() -> tuple[UUID, UUID]:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with engine.connect() as connection:
                    row = (
                        await connection.execute(
                            text("SELECT business_id, id FROM jobs WHERE resource_id = :id"),
                            {"id": str(asset["id"])},
                        )
                    ).one()
                    return cast(UUID, row.business_id), cast(UUID, row.id)
            finally:
                await engine.dispose()

        async def finish_running() -> BackgroundJob:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            business, job_id = await running_job()
            try:
                async with factory() as session:
                    service = MediaIngestService(
                        session,
                        config(),
                        fake,
                        application.state.content_inspector,
                        application.state.malware_scanner,
                    )
                    return await service.process_claimed(business_id=business, job_id=job_id)
            finally:
                await engine.dispose()

        assert asyncio.run(finish_running()).status == JobStatus.FAILED
        for attempt in range(2):

            async def make_due() -> None:
                engine = create_async_engine(os.environ["DATABASE_URL"])
                try:
                    async with engine.begin() as connection:
                        await connection.execute(
                            text(
                                "UPDATE jobs SET next_attempt_at = timezone('utc', now()) WHERE resource_id = :id"
                            ),
                            {"id": str(asset["id"])},
                        )
                finally:
                    await engine.dispose()

            asyncio.run(make_due())
            result = asyncio.run(process_next(cast(FastAPI, client.app)))
            assert result is not None
            assert result.status == (JobStatus.DEAD if attempt == 1 else JobStatus.FAILED)
        persisted, job = asyncio.run(stored_asset(str(asset["id"])))
        assert persisted.ingest_status == IngestStatus.DEAD
        assert job.status == JobStatus.DEAD
