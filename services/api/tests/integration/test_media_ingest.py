"""PostgreSQL coverage for the tenant-safe Phase 1A ingest gate."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
from collections.abc import Generator
from pathlib import Path
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
from app.infrastructure.media.fake_ingest import FakeMalwareScanner, FakeMediaMaterializer
from app.infrastructure.media.fake_scene_speech import (
    FakeAudioExtractor,
    FakeSceneDetector,
    FakeSpeechToText,
)
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.main import create_app
from app.modules.media.ingest import MediaIngestService
from app.modules.media.models import (
    IngestStatus,
    MalwareScanStatus,
    MediaAsset,
    MediaAssetStatus,
    MediaDerivative,
    MediaMalwareScan,
    MediaScene,
    MediaTechnicalMetadata,
    Transcript,
    TranscriptSegment,
    TranscriptStatus,
)
from app.modules.media.repository import MediaRepository
from app.modules.media.scene_speech import (
    AudioExtractionPort,
    FFmpegAudioExtractionAdapter,
    SceneSpeechAnalysisService,
    SpeechResult,
    SpeechToTextPort,
    TranscriptCandidate,
)
from app.modules.media.storage import StoredObjectMetadata
from app.modules.media.technical import (
    FFmpegDerivativeAdapter,
    FFprobeAdapter,
    TechnicalAnalysisService,
)
from app.modules.operations.models import (
    BackgroundJob,
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    OutboxEvent,
)

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
                    "TRUNCATE credit_ledger, usage_reservations, media_malware_scans, media_ingest_inspections, audit_logs, "
                    "idempotency_keys, job_attempts, jobs, outbox_events, media_upload_sessions, "
                    "media_assets, business_members, businesses, external_identities, users CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(clear())
    yield
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(clear())


def upload_payload(
    *, byte_size: int = 128, checksum: str = CHECKSUM, content_type: str = "video/mp4"
) -> dict[str, object]:
    return {
        "filename": {
            "image/jpeg": "image.jpg",
            "image/png": "image.png",
            "audio/mpeg": "audio.mp3",
            "image/heic": "photo.heic",
            "image/heif": "photo.heif",
            "video/quicktime": "clip.mov",
        }.get(content_type, "clip.mp4"),
        "content_type": content_type,
        "byte_size": byte_size,
        "sha256_checksum": checksum,
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
    client: TestClient,
    business_id: str,
    headers: dict[str, str],
    *,
    byte_size: int = 128,
    checksum: str = CHECKSUM,
    content_type: str = "video/mp4",
) -> dict[str, object]:
    created = client.post(
        f"/v1/businesses/{business_id}/media/uploads",
        headers=headers,
        json=upload_payload(byte_size=byte_size, checksum=checksum, content_type=content_type),
    )
    assert created.status_code == 201
    upload = cast(dict[str, object], created.json())
    fake = cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage)
    storage_upload_id, _ = asyncio.run(upload_details(str(upload["id"])))
    fake.mark_uploaded_for_testing(
        storage_upload_id=storage_upload_id,
        parts={1: "one", 2: "two"},
        metadata=StoredObjectMetadata(byte_size, content_type, checksum, "etag-1"),
    )
    completed = client.post(
        f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
        headers={**headers, "Idempotency-Key": f"complete-{upload['id']}"},
        json={
            "sha256_checksum": checksum,
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


async def process_technical_next(
    *, materializer: FakeMediaMaterializer, storage: FakeMultipartStorage, workdir: Path
) -> BackgroundJob | None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            service = TechnicalAnalysisService(
                session,
                config(),
                materializer,
                FFprobeAdapter(config()),
                FFmpegDerivativeAdapter(config()),
                storage,
            )
            return await service.process_next(workdir=workdir)
    finally:
        await engine.dispose()


async def process_scene_speech_next(
    *,
    materializer: FakeMediaMaterializer,
    storage: FakeMultipartStorage,
    workdir: Path,
    resolved_settings: Settings | None = None,
    audio_extractor: AudioExtractionPort | None = None,
    speech_to_text: SpeechToTextPort | None = None,
) -> BackgroundJob | None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            worker_settings = resolved_settings or config()
            return await SceneSpeechAnalysisService(
                session,
                worker_settings,
                materializer,
                FakeSceneDetector(),
                audio_extractor or FakeAudioExtractor(),
                speech_to_text or FakeSpeechToText(),
                storage,
            ).process_next(workdir=workdir)
    finally:
        await engine.dispose()


async def stored_asset(asset_id: str) -> tuple[MediaAsset, BackgroundJob]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            asset = await session.scalar(select(MediaAsset).where(MediaAsset.id == asset_id))
            job = await session.scalar(
                select(BackgroundJob).where(
                    BackgroundJob.resource_id == asset_id,
                    BackgroundJob.job_type == "media.ingest",
                )
            )
            assert asset is not None and job is not None
            return asset, job
    finally:
        await engine.dispose()


async def technical_records(asset_id: str) -> tuple[int, int]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            job_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM jobs WHERE resource_id = :asset_id "
                    "AND job_type = 'media.technical_analysis'"
                ),
                {"asset_id": asset_id},
            )
            event_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM outbox_events WHERE aggregate_id = :asset_id "
                    "AND event_type = 'media.technical_analysis.requested'"
                ),
                {"asset_id": asset_id},
            )
            return int(job_count or 0), int(event_count or 0)
    finally:
        await engine.dispose()


async def malware_scan_count(asset_id: str) -> int:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with AsyncSession(engine) as session:
            return int(
                await session.scalar(
                    select(text("count(*)"))
                    .select_from(MediaMalwareScan)
                    .where(MediaMalwareScan.asset_id == asset_id)
                )
                or 0
            )
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
        technical_job_count, technical_event_count = asyncio.run(
            technical_records(str(asset["id"]))
        )
        assert technical_job_count == 1 and technical_event_count == 1


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_technical_analysis_persists_metadata_and_derivatives(tmp_path: Path) -> None:
    source = tmp_path / "verified.mp4"
    subprocess.run(
        [
            config().ffmpeg_binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=360x640:r=12",
            "-t",
            "1",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    owner = auth("technical-owner", "technical-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Technical", "timezone": "UTC"}
        ).json()["id"]
        other_business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Other tenant", "timezone": "UTC"}
        ).json()["id"]
        asset = complete_upload(
            client,
            business_id,
            owner,
            byte_size=source.stat().st_size,
            checksum=checksum,
        )
        assert asyncio.run(process_next(cast(FastAPI, client.app))) is not None
        persisted_asset, _ = asyncio.run(stored_asset(str(asset["id"])))
        materializer = FakeMediaMaterializer()
        materializer.register_for_testing(
            object_key=persisted_asset.storage_object_key, fixture_path=source
        )
        job = asyncio.run(
            process_technical_next(
                materializer=materializer,
                storage=cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage),
                workdir=tmp_path,
            )
        )
        assert job is not None and job.status == JobStatus.SUCCEEDED
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:

            async def stored_results() -> tuple[
                MediaTechnicalMetadata | None,
                list[MediaDerivative],
                BackgroundJob | None,
                JobAttempt | None,
            ]:
                async with AsyncSession(engine) as session:
                    metadata = await session.scalar(
                        select(MediaTechnicalMetadata).where(
                            MediaTechnicalMetadata.asset_id == asset["id"]
                        )
                    )
                    derivatives = list(
                        (
                            await session.scalars(
                                select(MediaDerivative).where(
                                    MediaDerivative.asset_id == asset["id"]
                                )
                            )
                        ).all()
                    )
                    technical_job = await session.scalar(
                        select(BackgroundJob).where(BackgroundJob.id == job.id)
                    )
                    attempt = await session.scalar(
                        select(JobAttempt).where(JobAttempt.job_id == job.id)
                    )
                    return metadata, derivatives, technical_job, attempt

            metadata, derivatives, technical_job, attempt = asyncio.run(stored_results())
        finally:
            asyncio.run(engine.dispose())
        assert metadata is not None and metadata.width == 360 and metadata.height == 640
        assert {item.kind for item in derivatives} == {"thumbnail", "proxy"}
        assert all(item.sha256_checksum and item.byte_size for item in derivatives)
        assert technical_job is not None and technical_job.attempt_count == 1
        assert attempt is not None and attempt.status == JobAttemptStatus.SUCCEEDED
        storage = cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage)
        for derivative in derivatives:
            persisted_metadata = asyncio.run(
                storage.get_object_metadata(object_key=derivative.storage_object_key)
            )
            assert persisted_metadata.byte_size == derivative.byte_size
            assert storage.persisted_file_for_testing(derivative.storage_object_key).is_file()

        proxy = next(item for item in derivatives if item.kind == "proxy")
        materializer.register_for_testing(
            object_key=proxy.storage_object_key,
            fixture_path=storage.persisted_file_for_testing(proxy.storage_object_key),
        )
        scene_job = asyncio.run(
            process_scene_speech_next(materializer=materializer, storage=storage, workdir=tmp_path)
        )
        assert scene_job is not None and scene_job.status == JobStatus.SUCCEEDED

        async def no_speech_result() -> tuple[TranscriptStatus | None, int]:
            scene_engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with AsyncSession(scene_engine) as session:
                    transcript = await session.scalar(
                        select(Transcript).where(Transcript.asset_id == asset["id"])
                    )
                    scene_count = int(
                        await session.scalar(
                            select(text("count(*)"))
                            .select_from(MediaScene)
                            .where(MediaScene.asset_id == asset["id"])
                        )
                        or 0
                    )
                    return transcript.status if transcript else None, scene_count
            finally:
                await scene_engine.dispose()

        assert asyncio.run(no_speech_result()) == (TranscriptStatus.NO_SPEECH, 1)

        async def cross_tenant_records_are_hidden() -> None:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with AsyncSession(engine) as session:
                    repository = MediaRepository(session)
                    asset_id = UUID(str(asset["id"]))
                    other_business = UUID(str(other_business_id))
                    assert await repository.get_technical_analysis(other_business, asset_id) is None
                    assert await repository.get_technical_metadata(other_business, asset_id) is None
                    assert await repository.list_derivatives(other_business, asset_id) == []
                    assert await repository.get_transcript(other_business, asset_id) is None
                    assert await repository.list_scenes(other_business, asset_id) == []
            finally:
                await engine.dispose()

        asyncio.run(cross_tenant_records_are_hidden())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_scene_speech_has_audio_persists_wav_and_long_text_transcript(tmp_path: Path) -> None:
    source = tmp_path / "with-audio.mp4"
    subprocess.run(
        [
            config().ffmpeg_binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x48:r=12",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=16000",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    owner = auth("scene-audio", "scene-audio@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Scene audio", "timezone": "UTC"}
        ).json()["id"]
        asset = complete_upload(
            client, business_id, owner, byte_size=source.stat().st_size, checksum=checksum
        )
        assert asyncio.run(process_next(application)) is not None
        persisted_asset, _ = asyncio.run(stored_asset(str(asset["id"])))
        storage = cast(FakeMultipartStorage, application.state.storage)
        materializer = FakeMediaMaterializer()
        materializer.register_for_testing(
            object_key=persisted_asset.storage_object_key, fixture_path=source
        )
        technical_job = asyncio.run(
            process_technical_next(materializer=materializer, storage=storage, workdir=tmp_path)
        )
        assert technical_job is not None and technical_job.status == JobStatus.SUCCEEDED

        async def proxy_path() -> tuple[str, Path]:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with AsyncSession(engine) as session:
                    proxy = await session.scalar(
                        select(MediaDerivative).where(
                            MediaDerivative.business_id == business_id,
                            MediaDerivative.asset_id == asset["id"],
                            MediaDerivative.kind == "proxy",
                        )
                    )
                    assert proxy is not None
                    return proxy.storage_object_key, storage.persisted_file_for_testing(
                        proxy.storage_object_key
                    )
            finally:
                await engine.dispose()

        proxy_key, proxy_file = asyncio.run(proxy_path())
        materializer.register_for_testing(object_key=proxy_key, fixture_path=proxy_file)
        asr = FakeSpeechToText()
        asr.set_result_for_testing(
            SpeechResult(
                language="tr",
                provider="fake-asr",
                segments=tuple(
                    TranscriptCandidate(index * 100, (index + 1) * 100, "x" * 3_500, 0.9)
                    for index in range(6)
                ),
            )
        )
        scene_settings = config().model_copy(update={"transcript_max_total_chars": 25_000})
        scene_job = asyncio.run(
            process_scene_speech_next(
                materializer=materializer,
                storage=storage,
                workdir=tmp_path,
                resolved_settings=scene_settings,
                audio_extractor=FFmpegAudioExtractionAdapter(scene_settings),
                speech_to_text=asr,
            )
        )
        assert scene_job is not None and scene_job.status == JobStatus.SUCCEEDED

        async def persisted_audio_result() -> tuple[Transcript, int, MediaDerivative, int]:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with AsyncSession(engine) as session:
                    transcript = await session.scalar(
                        select(Transcript).where(
                            Transcript.business_id == business_id,
                            Transcript.asset_id == asset["id"],
                        )
                    )
                    audio = await session.scalar(
                        select(MediaDerivative).where(
                            MediaDerivative.business_id == business_id,
                            MediaDerivative.asset_id == asset["id"],
                            MediaDerivative.kind == "audio",
                        )
                    )
                    outbox_count = int(
                        await session.scalar(
                            select(text("count(*)"))
                            .select_from(OutboxEvent)
                            .where(
                                OutboxEvent.business_id == business_id,
                                OutboxEvent.aggregate_id == asset["id"],
                                OutboxEvent.event_type == "media.scene_speech.completed",
                            )
                        )
                        or 0
                    )
                    assert transcript is not None and audio is not None
                    segment_count = int(
                        await session.scalar(
                            select(text("count(*)"))
                            .select_from(TranscriptSegment)
                            .where(TranscriptSegment.transcript_id == transcript.id)
                        )
                        or 0
                    )
                    return transcript, segment_count, audio, outbox_count
            finally:
                await engine.dispose()

        transcript, segment_count, audio, outbox_count = asyncio.run(persisted_audio_result())
        assert (
            transcript.status == TranscriptStatus.COMPLETED and len(transcript.full_text) > 20_000
        )
        assert segment_count == 6 and audio.status == "ready" and audio.byte_size
        assert storage.persisted_file_for_testing(audio.storage_object_key).is_file()
        assert outbox_count == 1


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_derivative_storage_failure_does_not_create_ready_records(tmp_path: Path) -> None:
    source = tmp_path / "failure.mp4"
    subprocess.run(
        [
            config().ffmpeg_binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x48:r=12",
            "-t",
            "1",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    owner = auth("derivative-storage", "derivative-storage@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Storage failure", "timezone": "UTC"}
        ).json()["id"]
        asset = complete_upload(
            client, business_id, owner, byte_size=source.stat().st_size, checksum=checksum
        )
        assert asyncio.run(process_next(cast(FastAPI, client.app))) is not None
        persisted_asset, _ = asyncio.run(stored_asset(str(asset["id"])))
        storage = cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage)
        storage.fail_object_for_testing(
            f"tenant/{business_id}/media/{persisted_asset.id}/derivatives/thumbnail"
        )
        materializer = FakeMediaMaterializer()
        materializer.register_for_testing(
            object_key=persisted_asset.storage_object_key, fixture_path=source
        )
        job = asyncio.run(
            process_technical_next(materializer=materializer, storage=storage, workdir=tmp_path)
        )
        assert job is not None and job.status == JobStatus.FAILED

        async def make_retry_due() -> None:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE jobs SET next_attempt_at = timezone('utc', now()) "
                            "WHERE id = :job_id"
                        ),
                        {"job_id": str(job.id)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(make_retry_due())
        retried_job = asyncio.run(
            process_technical_next(materializer=materializer, storage=storage, workdir=tmp_path)
        )
        assert retried_job is not None and retried_job.status == JobStatus.FAILED

        async def derivative_count_and_attempts() -> tuple[int, int, JobAttemptStatus | None]:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with AsyncSession(engine) as session:
                    ready_count = int(
                        await session.scalar(
                            select(text("count(*)"))
                            .select_from(MediaDerivative)
                            .where(
                                MediaDerivative.asset_id == asset["id"],
                                MediaDerivative.status == "ready",
                            )
                        )
                        or 0
                    )
                    attempts = list(
                        (
                            await session.scalars(
                                select(JobAttempt)
                                .where(JobAttempt.job_id == job.id)
                                .order_by(JobAttempt.attempt_number)
                            )
                        ).all()
                    )
                    return ready_count, len(attempts), attempts[-1].status if attempts else None
            finally:
                await engine.dispose()

        assert asyncio.run(derivative_count_and_attempts()) == (
            0,
            2,
            JobAttemptStatus.FAILED,
        )


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_image_and_audio_ingest_do_not_schedule_video_technical_analysis() -> None:
    owner = auth("non-video", "non-video@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Non video", "timezone": "UTC"}
        ).json()["id"]
        inspector = cast(FastAPI, client.app).state.content_inspector
        for content_type in ("image/jpeg", "audio/mpeg"):
            asset = complete_upload(client, business_id, owner, content_type=content_type)
            _, object_key = asyncio.run(upload_details_for_asset(str(asset["id"])))
            inspector.set_result_for_testing(object_key=object_key, content_type=content_type)
            job = asyncio.run(process_next(cast(FastAPI, client.app)))
            assert job is not None and job.status == JobStatus.SUCCEEDED
            technical_job_count, technical_event_count = asyncio.run(
                technical_records(str(asset["id"]))
            )
            assert technical_job_count == 0 and technical_event_count == 0


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_quicktime_ingest_schedules_video_technical_analysis() -> None:
    owner = auth("mov-owner", "mov-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "QuickTime", "timezone": "UTC"}
        ).json()["id"]
        inspector = cast(FastAPI, client.app).state.content_inspector
        asset = complete_upload(client, business_id, owner, content_type="video/quicktime")
        _, object_key = asyncio.run(upload_details_for_asset(str(asset["id"])))
        inspector.set_result_for_testing(object_key=object_key, content_type="video/quicktime")
        job = asyncio.run(process_next(cast(FastAPI, client.app)))
        assert job is not None and job.status == JobStatus.SUCCEEDED
        # `.mov` is now admitted to the analysis pipeline instead of stopping after ingest.
        technical_job_count, technical_event_count = asyncio.run(
            technical_records(str(asset["id"]))
        )
        assert technical_job_count == 1 and technical_event_count == 1


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_heic_ingest_is_declined_explicitly_without_silent_death() -> None:
    owner = auth("heic-owner", "heic-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "HEIC", "timezone": "UTC"}
        ).json()["id"]
        inspector = cast(FastAPI, client.app).state.content_inspector
        for content_type in ("image/heic", "image/heif"):
            asset = complete_upload(client, business_id, owner, content_type=content_type)
            _, object_key = asyncio.run(upload_details_for_asset(str(asset["id"])))
            inspector.set_result_for_testing(object_key=object_key, content_type=content_type)
            job = asyncio.run(process_next(cast(FastAPI, client.app)))
            # The upload succeeded (W01 byte path), but analysis is explicitly declined: the
            # asset is rejected with a documented code, never left silently mid-pipeline.
            assert job is not None and job.status == JobStatus.FAILED
            assert job.last_error_code == "INGEST_ANALYSIS_UNSUPPORTED_MEDIA_TYPE"
            persisted, _ = asyncio.run(stored_asset(str(asset["id"])))
            assert persisted.ingest_status == IngestStatus.REJECTED
            assert persisted.status == MediaAssetStatus.REJECTED
            technical_job_count, _ = asyncio.run(technical_records(str(asset["id"])))
            assert technical_job_count == 0


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_unsupported_codec_rejects_the_asset_with_a_documented_code(tmp_path: Path) -> None:
    # An mp4 container the ingest gate admits, but wrapping a codec the pipeline cannot proxy.
    source = tmp_path / "mpeg4.mp4"
    subprocess.run(
        [
            config().ffmpeg_binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x48:r=12",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    owner = auth("codec-owner", "codec-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Codec", "timezone": "UTC"}
        ).json()["id"]
        asset = complete_upload(
            client, business_id, owner, byte_size=source.stat().st_size, checksum=checksum
        )
        assert asyncio.run(process_next(cast(FastAPI, client.app))) is not None
        persisted_asset, _ = asyncio.run(stored_asset(str(asset["id"])))
        materializer = FakeMediaMaterializer()
        materializer.register_for_testing(
            object_key=persisted_asset.storage_object_key, fixture_path=source
        )
        job = asyncio.run(
            process_technical_next(
                materializer=materializer,
                storage=cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage),
                workdir=tmp_path,
            )
        )
        assert job is not None and job.status == JobStatus.FAILED
        assert job.next_attempt_at is None  # unsupported media does not retry
        assert job.last_error_code == "TECHNICAL_VIDEO_CODEC_UNSUPPORTED"

        async def rejected_state() -> tuple[MediaAssetStatus, IngestStatus, str | None]:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with AsyncSession(engine) as session:
                    reloaded = await session.scalar(
                        select(MediaAsset).where(MediaAsset.id == asset["id"])
                    )
                    analysis = await MediaRepository(session).get_technical_analysis(
                        UUID(str(business_id)), UUID(str(asset["id"]))
                    )
                    assert reloaded is not None
                    return (
                        reloaded.status,
                        reloaded.ingest_status,
                        analysis.safe_error_code if analysis is not None else None,
                    )
            finally:
                await engine.dispose()

        status, ingest_status, safe_code = asyncio.run(rejected_state())
        assert status == MediaAssetStatus.REJECTED
        assert ingest_status == IngestStatus.REJECTED
        assert safe_code == "TECHNICAL_VIDEO_CODEC_UNSUPPORTED"


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_concurrent_technical_claim_allows_one_worker() -> None:
    owner = auth("technical-claim", "technical-claim@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Technical claim", "timezone": "UTC"}
        ).json()["id"]
        complete_upload(client, business_id, owner)
        assert asyncio.run(process_next(application)) is not None
        storage = cast(FakeMultipartStorage, application.state.storage)

        async def claim_pair() -> int:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            try:
                async with factory() as first, factory() as second:
                    first_service = TechnicalAnalysisService(
                        first,
                        config(),
                        FakeMediaMaterializer(),
                        FFprobeAdapter(config()),
                        FFmpegDerivativeAdapter(config()),
                        storage,
                    )
                    second_service = TechnicalAnalysisService(
                        second,
                        config(),
                        FakeMediaMaterializer(),
                        FFprobeAdapter(config()),
                        FFmpegDerivativeAdapter(config()),
                        storage,
                    )
                    claims = await asyncio.gather(
                        first_service.claim_next(), second_service.claim_next()
                    )
                    return sum(value is not None for value in claims)
            finally:
                await engine.dispose()

        assert asyncio.run(claim_pair()) == 1


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
            assert asyncio.run(malware_scan_count(str(asset["id"]))) == 0


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
