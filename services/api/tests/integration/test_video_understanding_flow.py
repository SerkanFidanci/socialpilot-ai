"""PostgreSQL coverage for Phase 1D-A2 durable video-understanding flow."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.modules.businesses.models import Business
from app.modules.identity.models import User
from app.modules.media.models import (
    IngestStatus,
    MediaAsset,
    MediaAssetStatus,
    MediaDerivative,
    MediaDerivativeStatus,
    MediaScene,
    MediaSceneUnderstanding,
    Transcript,
    TranscriptSegment,
    TranscriptStatus,
)
from app.modules.media.repository import MediaRepository
from app.modules.media.video_understanding import (
    FakeFrameExtractionAdapter,
    FakeVideoUnderstandingAdapter,
)
from app.modules.media.video_understanding_service import (
    VideoUnderstandingSchedulingService,
    VideoUnderstandingService,
)
from app.modules.operations.models import (
    BackgroundJob,
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    OutboxEvent,
)
from app.modules.operations.service import JobRecoveryService

pytestmark = pytest.mark.integration


def config() -> Settings:
    return Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        celery_broker_url=os.environ["CELERY_BROKER_URL"],
        celery_result_backend=os.environ["CELERY_RESULT_BACKEND"],
    )


def factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(os.environ["DATABASE_URL"]),
        expire_on_commit=False,
        class_=AsyncSession,
    )


async def clear() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE audit_logs, idempotency_keys, job_attempts, jobs, outbox_events, "
                    "media_scene_understandings, transcript_segments, transcripts, media_scenes, "
                    "media_derivatives, media_technical_metadata, media_technical_analyses, "
                    "media_upload_sessions, media_assets, business_members, businesses, "
                    "external_identities, users CASCADE"
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


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_scheduler_requires_completed_scene_speech_and_is_idempotent() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                incomplete_business, incomplete_asset = await seed(session, scenes=0)
                assert (
                    await VideoUnderstandingSchedulingService(
                        session, config()
                    ).schedule_after_scene_speech(
                        business_id=incomplete_business,
                        asset_id=incomplete_asset,
                        correlation_id="incomplete",
                    )
                    is None
                )
                business_id, asset_id = await seed(session, scenes=1)
                scheduler = VideoUnderstandingSchedulingService(session, config())
                first = await scheduler.schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="first"
                )
                second = await scheduler.schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="second"
                )
                assert first is not None and second is not None and first.id == second.id
            events = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == "media.video_understanding.requested"
                        )
                    )
                ).all()
            )
            assert len(events) == 1 and events[0].payload["job_id"] == str(first.id)

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_fake_flow_persists_every_scene_context_and_completion_event() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as seed_session:
            async with seed_session.begin():
                business_id, asset_id = await seed(seed_session, scenes=2, transcript=True)
                job = await VideoUnderstandingSchedulingService(
                    seed_session, config()
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="success"
                )
                assert job is not None
        async with session_factory() as worker_session:
            service = VideoUnderstandingService(
                worker_session,
                config(),
                FakeFrameExtractionAdapter(),
                FakeVideoUnderstandingAdapter(),
            )
            claimed = await service.claim_next()
            assert claimed is not None
            finished = await service.process_claimed(business_id=business_id, job_id=claimed.id)
            assert finished.status == JobStatus.SUCCEEDED
        async with session_factory() as session:
            values = list(
                (
                    await session.scalars(
                        select(MediaSceneUnderstanding)
                        .where(MediaSceneUnderstanding.business_id == business_id)
                        .order_by(MediaSceneUnderstanding.created_at)
                    )
                ).all()
            )
            attempts = list(
                (await session.scalars(select(JobAttempt).where(JobAttempt.job_id == job.id))).all()
            )
            event_types = list(
                await session.scalars(
                    select(OutboxEvent.event_type).where(OutboxEvent.business_id == business_id)
                )
            )
            assert len(values) == 2
            assert [value.transcript_context for value in values] == ["first scene", "second scene"]
            assert len(attempts) == 1 and attempts[0].status == JobAttemptStatus.SUCCEEDED
            assert event_types.count("media.video_understanding.completed") == 1

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_no_speech_and_duplicate_delivery_complete_without_duplicate_records() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                business_id, asset_id = await seed(session, scenes=1, transcript=False)
                job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="no-speech"
                )
                assert job is not None
        async with session_factory() as worker:
            service = VideoUnderstandingService(
                worker,
                config(),
                FakeFrameExtractionAdapter(),
                FakeVideoUnderstandingAdapter(),
            )
            claimed = await service.claim_next()
            assert claimed is not None
            assert (
                await service.process_claimed(business_id=business_id, job_id=claimed.id)
            ).status == (JobStatus.SUCCEEDED)
            assert (
                await service.process_claimed(business_id=business_id, job_id=claimed.id)
            ).status == (JobStatus.SUCCEEDED)
        async with session_factory() as session:
            values = list(await session.scalars(select(MediaSceneUnderstanding)))
            assert len(values) == 1 and values[0].transcript_context == ""

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_tenant_scoped_repository_cannot_cross_scene_proxy_or_understanding_boundaries() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                first_business, first_asset = await seed(session, scenes=1)
                second_business, _ = await seed(session, scenes=1)
                job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=first_business, asset_id=first_asset, correlation_id="tenant"
                )
                assert job is not None
        async with session_factory() as worker:
            service = VideoUnderstandingService(
                worker, config(), FakeFrameExtractionAdapter(), FakeVideoUnderstandingAdapter()
            )
            claimed = await service.claim_next()
            assert claimed is not None
            await service.process_claimed(business_id=first_business, job_id=claimed.id)
        async with session_factory() as session:
            repository = MediaRepository(session)
            scene = (await repository.list_scenes(first_business, first_asset))[0]
            understanding = await repository.get_scene_understanding_for_scene(
                first_business, scene.id
            )
            assert understanding is not None
            assert await repository.get_scene(second_business, scene.id) is None
            assert await repository.get_ready_proxy(second_business, first_asset) is None
            assert (
                await repository.get_scene_understanding(second_business, understanding.id) is None
            )

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_provider_errors_retry_then_dead_and_finalize_attempts() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                business_id, asset_id = await seed(session, scenes=1)
                job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="retry"
                )
                assert job is not None
        for attempt in range(3):
            async with session_factory() as worker:
                service = VideoUnderstandingService(
                    worker,
                    config(),
                    FakeFrameExtractionAdapter(),
                    FakeVideoUnderstandingAdapter("transient"),
                )
                claimed = await service.claim_next()
                assert claimed is not None
                result = await service.process_claimed(business_id=business_id, job_id=claimed.id)
                assert result.status == (JobStatus.DEAD if attempt == 2 else JobStatus.FAILED)
            if attempt < 2:
                async with session_factory() as session:
                    async with session.begin():
                        stored = await session.get(BackgroundJob, job.id, with_for_update=True)
                        assert stored is not None
                        stored.next_attempt_at = stored.requested_at
        async with session_factory() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(JobAttempt)
                        .where(JobAttempt.job_id == job.id)
                        .order_by(JobAttempt.attempt_number)
                    )
                ).all()
            )
            assert len(attempts) == 3
            assert all(value.status == JobAttemptStatus.FAILED for value in attempts)
            assert all(value.finished_at is not None for value in attempts)

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_concurrent_claim_allows_only_one_worker() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                business_id, asset_id = await seed(session, scenes=1)
                job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="claim"
                )
                assert job is not None
        async with session_factory() as first, session_factory() as second:
            claims = await asyncio.gather(
                VideoUnderstandingService(
                    first, config(), FakeFrameExtractionAdapter(), FakeVideoUnderstandingAdapter()
                ).claim_next(),
                VideoUnderstandingService(
                    second, config(), FakeFrameExtractionAdapter(), FakeVideoUnderstandingAdapter()
                ).claim_next(),
            )
            assert sum(value is not None for value in claims) == 1

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_invalid_output_missing_proxy_and_stale_recovery_are_safe() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                invalid_business, invalid_asset = await seed(session, scenes=1)
                invalid_job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=invalid_business, asset_id=invalid_asset, correlation_id="invalid"
                )
                assert invalid_job is not None
        async with session_factory() as worker:
            invalid = VideoUnderstandingService(
                worker,
                config(),
                FakeFrameExtractionAdapter(),
                FakeVideoUnderstandingAdapter("invalid"),
            )
            claimed = await invalid.claim_next()
            assert claimed is not None
            assert (
                await invalid.process_claimed(business_id=invalid_business, job_id=claimed.id)
            ).status == (JobStatus.FAILED)
        async with session_factory() as session:
            async with session.begin():
                missing_proxy_business, missing_proxy_asset = await seed(
                    session, scenes=1, proxy=False
                )
                proxy_job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=missing_proxy_business,
                    asset_id=missing_proxy_asset,
                    correlation_id="proxy",
                )
                assert proxy_job is not None
        async with session_factory() as worker:
            missing = VideoUnderstandingService(
                worker, config(), FakeFrameExtractionAdapter(), FakeVideoUnderstandingAdapter()
            )
            claimed = await missing.claim_next()
            assert claimed is not None
            assert (
                await missing.process_claimed(business_id=missing_proxy_business, job_id=claimed.id)
            ).status == JobStatus.FAILED
        async with session_factory() as session:
            async with session.begin():
                stored = await session.get(BackgroundJob, invalid_job.id, with_for_update=True)
                assert stored is not None
                stored.status = JobStatus.RUNNING
                stored.attempt_count = 1
                stored.max_attempts = 1
                stored.started_at = stored.requested_at.replace(year=2000)
                attempt = await session.scalar(
                    select(JobAttempt).where(
                        JobAttempt.job_id == stored.id,
                        JobAttempt.attempt_number == 1,
                    )
                )
                assert attempt is not None
                attempt.status = JobAttemptStatus.STARTED
                attempt.finished_at = None
                attempt.error_code = None
                attempt.error_summary = None
        async with session_factory() as session:
            recovered = await JobRecoveryService(session).recover_stale_running_jobs(
                business_id=invalid_business
            )
            assert len(recovered) == 1 and recovered[0].status == JobStatus.DEAD

    asyncio.run(run())


async def seed(
    session: AsyncSession,
    *,
    scenes: int,
    transcript: bool = True,
    proxy: bool = True,
) -> tuple[UUID, UUID]:
    token = uuid4().hex
    user = User(email=f"video-{token}@example.com")
    session.add(user)
    await session.flush()
    business = Business(
        name=f"Video {token}",
        slug=f"video-{token}",
        timezone="UTC",
        created_by_user_id=user.id,
    )
    session.add(business)
    await session.flush()
    asset = MediaAsset(
        business_id=business.id,
        created_by_user_id=user.id,
        storage_object_key=f"tenant/{business.id}/media/{token}/original",
        content_type="video/mp4",
        byte_size=128,
        sha256_checksum="a" * 64,
        status=MediaAssetStatus.UPLOADED,
        ingest_status=IngestStatus.READY_FOR_ANALYSIS,
    )
    session.add(asset)
    await session.flush()
    if proxy:
        session.add(
            MediaDerivative(
                business_id=business.id,
                asset_id=asset.id,
                kind="proxy",
                storage_object_key=f"tenant/{business.id}/media/{asset.id}/proxy",
                content_type="video/mp4",
                byte_size=64,
                sha256_checksum="b" * 64,
                status=MediaDerivativeStatus.READY,
            )
        )
    record = Transcript(
        business_id=business.id,
        asset_id=asset.id,
        language="en" if transcript else "und",
        duration_ms=1_000,
        full_text="first scene second scene" if transcript else "",
        provider="fake" if transcript else "none",
        status=TranscriptStatus.COMPLETED if transcript else TranscriptStatus.NO_SPEECH,
    )
    session.add(record)
    await session.flush()
    if transcript:
        session.add_all(
            [
                TranscriptSegment(
                    transcript_id=record.id,
                    segment_index=0,
                    start_ms=100,
                    end_ms=400,
                    text="first scene",
                    confidence=0.9,
                ),
                TranscriptSegment(
                    transcript_id=record.id,
                    segment_index=1,
                    start_ms=600,
                    end_ms=900,
                    text="second scene",
                    confidence=0.9,
                ),
            ]
        )
    for index in range(scenes):
        session.add(
            MediaScene(
                business_id=business.id,
                asset_id=asset.id,
                scene_index=index,
                start_ms=index * 500,
                end_ms=(index + 1) * 500,
                duration_ms=500,
                confidence=1.0,
            )
        )
    await session.flush()
    return business.id, asset.id
