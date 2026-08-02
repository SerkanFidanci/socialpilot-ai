"""PostgreSQL coverage for Phase 1D-A2 durable video-understanding flow."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Generator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.infrastructure.media.fake_ingest import FakeMediaMaterializer
from app.infrastructure.media.fake_video_understanding import (
    FakeFrameExtractionAdapter,
    FakeVideoUnderstandingAdapter,
)
from app.infrastructure.media.frame_extraction import FFmpegFrameExtractionAdapter
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
from app.modules.media.video_understanding import FrameReference
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


def config(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": os.environ["DATABASE_URL"],
        "redis_url": os.environ["REDIS_URL"],
        "celery_broker_url": os.environ["CELERY_BROKER_URL"],
        "celery_result_backend": os.environ["CELERY_RESULT_BACKEND"],
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


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
                    "TRUNCATE credit_ledger, usage_reservations, audit_logs, idempotency_keys, job_attempts, jobs, outbox_events, "
                    "media_scene_understandings, transcript_segments, transcripts, media_scenes, "
                    "media_derivatives, media_technical_metadata, media_technical_analyses, "
                    "media_upload_sessions, media_assets, business_members, businesses, "
                    "external_identities, users CASCADE"
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
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
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
def test_real_proxy_frames_persist_understandings_and_cleanup_workdir() -> None:
    async def run() -> None:
        session_factory = factory()
        with TemporaryDirectory(prefix="video-understanding-fixture-") as temporary:
            root = Path(temporary)
            fixture = root / "portrait-proxy.mp4"
            subprocess.run(
                [
                    "/usr/bin/ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=180x320:rate=24",
                    "-t",
                    "1",
                    "-pix_fmt",
                    "yuv420p",
                    str(fixture),
                ],
                check=True,
                capture_output=True,
            )
            async with session_factory() as session:
                async with session.begin():
                    business_id, asset_id = await seed(session, scenes=2, transcript=True)
                    proxy = await MediaRepository(session).get_ready_proxy(business_id, asset_id)
                    assert proxy is not None
                    job = await VideoUnderstandingSchedulingService(
                        session, config()
                    ).schedule_after_scene_speech(
                        business_id=business_id, asset_id=asset_id, correlation_id="real-frames"
                    )
                    assert job is not None
            materializer = FakeMediaMaterializer()
            materializer.register_for_testing(
                object_key=proxy.storage_object_key, fixture_path=fixture
            )
            async with session_factory() as worker:
                service = VideoUnderstandingService(
                    worker,
                    config(),
                    FFmpegFrameExtractionAdapter(config()),
                    FakeVideoUnderstandingAdapter(config()),
                    materializer,
                )
                claimed = await service.claim_next()
                assert claimed is not None
                assert (
                    await service.process_claimed(
                        business_id=business_id, job_id=claimed.id, workdir=root
                    )
                ).status == JobStatus.SUCCEEDED
            assert not list(root.glob("video-understanding-*"))
            async with session_factory() as session:
                values = list(
                    await session.scalars(
                        select(MediaSceneUnderstanding).where(
                            MediaSceneUnderstanding.business_id == business_id
                        )
                    )
                )
                assert len(values) == 2
                assert all(value.quality_signals["frame_count"] == 3 for value in values)

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
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
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
            assert values[0].quality_signals["insufficient_context"] is True
            assert values[0].quality_signals["visual_input_available"] is False
            assert values[0].quality_signals["analysis_mode"] == "no_context"
            assert values[0].confidence == 0.5

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_frame_budget_degrades_to_transcript_only_without_failing_job() -> None:
    async def run() -> None:
        session_factory = factory()
        resolved = config(video_understanding_max_frames_per_asset=3)
        frames = tuple(
            FrameReference(
                scene_id=uuid4(),
                timestamp_ms=index * 100,
                local_path=Path(f"frame-{index}.jpg"),
                width=100,
                height=100,
                byte_size=100,
                content_type="image/jpeg",
            )
            for index in range(3)
        )
        async with session_factory() as session:
            async with session.begin():
                business_id, asset_id = await seed(session, scenes=2, transcript=True)
                job = await VideoUnderstandingSchedulingService(
                    session, resolved
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="budget"
                )
                assert job is not None
        async with session_factory() as worker:
            service = VideoUnderstandingService(
                worker,
                resolved,
                FakeFrameExtractionAdapter(resolved, frames),
                FakeVideoUnderstandingAdapter(resolved),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            claimed = await service.claim_next()
            assert claimed is not None
            assert (
                await service.process_claimed(business_id=business_id, job_id=claimed.id)
            ).status == JobStatus.SUCCEEDED
        async with session_factory() as session:
            values = list(
                await session.scalars(
                    select(MediaSceneUnderstanding)
                    .where(MediaSceneUnderstanding.business_id == business_id)
                    .order_by(MediaSceneUnderstanding.created_at)
                )
            )
            counts = [cast(int, value.quality_signals["frame_count"]) for value in values]
            assert counts == [3, 0]
            assert sum(counts) <= resolved.video_understanding_max_frames_per_asset
            assert [value.transcript_context for value in values] == ["first scene", "second scene"]

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_scene_speech_records_are_preserved_when_vlm_scope_is_capped() -> None:
    async def run() -> None:
        session_factory = factory()
        resolved = config()
        async with session_factory() as session:
            async with session.begin():
                business_id, asset_id = await seed(
                    session,
                    scenes=resolved.video_understanding_supported_scene_count + 1,
                )
                job = await VideoUnderstandingSchedulingService(
                    session, resolved
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="capped-scenes"
                )
                assert job is not None
                assert job.timeout_seconds <= resolved.video_understanding_job_max_timeout_seconds
            assert len(await MediaRepository(session).list_scenes(business_id, asset_id)) == (
                resolved.video_understanding_supported_scene_count + 1
            )
            transcript = await MediaRepository(session).get_transcript(business_id, asset_id)
            assert transcript is not None
        async with session_factory() as worker:
            service = VideoUnderstandingService(
                worker,
                resolved,
                FakeFrameExtractionAdapter(resolved),
                FakeVideoUnderstandingAdapter(resolved),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            claimed = await service.claim_next()
            assert claimed is not None
            assert (
                await service.process_claimed(
                    business_id=business_id,
                    job_id=claimed.id,
                    attempt_number=claimed.attempt_count,
                )
            ).status == JobStatus.SUCCEEDED
        async with session_factory() as session:
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "media.video_understanding.completed"
                )
            )
            assert event is not None
            assert event.payload == {
                "job_id": str(job.id),
                "asset_id": str(asset_id),
                "total_scene_count": 6,
                "analyzed_scene_count": 5,
                "skipped_scene_count": 1,
                "coverage": "partial",
                "frame_backed_scene_count": 0,
                "transcript_only_scene_count": 2,
                "no_context_scene_count": 3,
            }

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_stale_video_worker_cannot_complete_a_new_attempt() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                business_id, asset_id = await seed(session, scenes=1)
                job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="fencing"
                )
                assert job is not None
        async with session_factory() as first:
            old_worker = VideoUnderstandingService(
                first,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            old_claim = await old_worker.claim_next()
            assert old_claim is not None
        async with session_factory() as session:
            async with session.begin():
                stored = await session.get(BackgroundJob, job.id, with_for_update=True)
                assert stored is not None
                stored.started_at = stored.requested_at.replace(year=2000)
        async with session_factory() as reaper:
            assert len(await JobRecoveryService(reaper, config()).recover_stale_running_jobs()) == 1
        async with session_factory() as session:
            async with session.begin():
                stored = await session.get(BackgroundJob, job.id, with_for_update=True)
                assert stored is not None
                stored.next_attempt_at = stored.requested_at
        async with session_factory() as second:
            new_worker = VideoUnderstandingService(
                second,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            new_claim = await new_worker.claim_next()
            assert new_claim is not None and new_claim.attempt_count == old_claim.attempt_count + 1
            stale = await old_worker.process_claimed(
                business_id=business_id,
                job_id=old_claim.id,
                attempt_number=old_claim.attempt_count,
            )
            assert (
                stale.status == JobStatus.RUNNING and stale.attempt_count == new_claim.attempt_count
            )
        async with session_factory() as check_session:
            assert not list(await check_session.scalars(select(MediaSceneUnderstanding)))
        async with session_factory() as second:
            new_worker = VideoUnderstandingService(
                second,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            assert (
                await new_worker.process_claimed(
                    business_id=business_id,
                    job_id=new_claim.id,
                    attempt_number=new_claim.attempt_count,
                )
            ).status == JobStatus.SUCCEEDED
        async with session_factory() as session:
            attempts = list(
                await session.scalars(select(JobAttempt).where(JobAttempt.job_id == job.id))
            )
            assert [attempt.status for attempt in attempts] == [
                JobAttemptStatus.FAILED,
                JobAttemptStatus.SUCCEEDED,
            ]
            assert (
                len(
                    list(
                        await session.scalars(
                            select(OutboxEvent).where(
                                OutboxEvent.event_type == "media.video_understanding.completed"
                            )
                        )
                    )
                )
                == 1
            )

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_scene_understanding_unique_jsonb_and_cascade_constraints() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                records: list[tuple[UUID, UUID, UUID]] = []
                for _ in range(3):
                    business_id, asset_id = await seed(session, scenes=1)
                    scene = (await MediaRepository(session).list_scenes(business_id, asset_id))[0]
                    session.add(
                        MediaSceneUnderstanding(
                            business_id=business_id,
                            asset_id=asset_id,
                            scene_id=scene.id,
                            status="completed",
                            provider="test",
                            model_name="test",
                            summary="summary",
                            visual_description="description",
                            transcript_context="context",
                            confidence=0.9,
                            labels=["label"],
                            objects=["object"],
                            actions=["action"],
                            visible_text=["text"],
                            dominant_topics=["topic"],
                            safety_flags=[],
                            quality_signals={"nested": {"number": 7}, "flag": True},
                        )
                    )
                    records.append((business_id, asset_id, scene.id))
                await session.flush()
                first_business, first_asset, first_scene = records[0]
                async with session.begin_nested():
                    session.add(
                        MediaSceneUnderstanding(
                            business_id=first_business,
                            asset_id=first_asset,
                            scene_id=first_scene,
                            status="completed",
                            provider="duplicate",
                            model_name="test",
                            summary="summary",
                            visual_description="description",
                            transcript_context="context",
                            confidence=0.9,
                            labels=[],
                            objects=[],
                            actions=[],
                            visible_text=[],
                            dominant_topics=[],
                            safety_flags=[],
                            quality_signals={},
                        )
                    )
                    with pytest.raises(IntegrityError):
                        await session.flush()
                stored = await session.scalar(
                    select(MediaSceneUnderstanding).where(
                        MediaSceneUnderstanding.scene_id == first_scene
                    )
                )
                assert stored is not None and stored.quality_signals == {
                    "nested": {"number": 7},
                    "flag": True,
                }
                await session.delete(await session.get(MediaScene, first_scene))
                await session.flush()
                assert not await session.scalar(
                    select(MediaSceneUnderstanding.id).where(
                        MediaSceneUnderstanding.scene_id == first_scene
                    )
                )
                _, second_asset, _ = records[1]
                await session.delete(await session.get(MediaAsset, second_asset))
                await session.flush()
                assert not await session.scalar(
                    select(MediaSceneUnderstanding.id).where(
                        MediaSceneUnderstanding.asset_id == second_asset
                    )
                )
                third_business, _, _ = records[2]
                await session.delete(await session.get(Business, third_business))
                await session.flush()
                assert not await session.scalar(
                    select(MediaSceneUnderstanding.id).where(
                        MediaSceneUnderstanding.business_id == third_business
                    )
                )

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_provider_failure_for_one_scene_persists_no_partial_understandings() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                business_id, asset_id = await seed(session, scenes=2)
                job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="partial"
                )
                assert job is not None
        async with session_factory() as worker:
            service = VideoUnderstandingService(
                worker,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config(), "transient"),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            claimed = await service.claim_next()
            assert claimed is not None
            assert (
                await service.process_claimed(business_id=business_id, job_id=claimed.id)
            ).status == JobStatus.FAILED
        async with session_factory() as session:
            assert not list(
                await session.scalars(
                    select(MediaSceneUnderstanding).where(
                        MediaSceneUnderstanding.business_id == business_id
                    )
                )
            )

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
                worker,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
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
                    FakeFrameExtractionAdapter(config()),
                    FakeVideoUnderstandingAdapter(config(), "transient"),
                    FakeMediaMaterializer(allow_missing_for_testing=True),
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
def test_global_recovery_honors_job_budget_grace_and_concurrent_ownership() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                first_business, first_asset = await seed(session, scenes=1)
                second_business, second_asset = await seed(session, scenes=2)
                first_job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=first_business, asset_id=first_asset, correlation_id="global-first"
                )
                second_job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=second_business,
                    asset_id=second_asset,
                    correlation_id="global-second",
                )
                assert first_job is not None and second_job is not None
        async with session_factory() as first, session_factory() as second:
            first_service = VideoUnderstandingService(
                first,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            second_service = VideoUnderstandingService(
                second,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            assert await first_service.claim_next() is not None
            assert await second_service.claim_next() is not None
        async with session_factory() as session:
            assert not await JobRecoveryService(session, config()).recover_stale_running_jobs()
        async with session_factory() as session:
            async with session.begin():
                jobs = list(await session.scalars(select(BackgroundJob).order_by(BackgroundJob.id)))
                for job in jobs:
                    job.started_at = job.requested_at.replace(year=2000)
        async with session_factory() as first, session_factory() as second:
            recovered = await asyncio.gather(
                JobRecoveryService(first, config()).recover_stale_running_jobs(),
                JobRecoveryService(second, config()).recover_stale_running_jobs(),
            )
            assert sum(len(batch) for batch in recovered) == 2
        async with session_factory() as session:
            jobs = list(await session.scalars(select(BackgroundJob)))
            attempts = list(await session.scalars(select(JobAttempt)))
            assert {job.status for job in jobs} == {JobStatus.FAILED}
            assert {attempt.error_code for attempt in attempts} == {"JOB_TIMEOUT"}
            assert {job.business_id for job in jobs} == {first_business, second_business}

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_reaped_video_worker_cannot_persist_late_results() -> None:
    async def run() -> None:
        session_factory = factory()
        async with session_factory() as session:
            async with session.begin():
                business_id, asset_id = await seed(session, scenes=1)
                job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="late-worker"
                )
                assert job is not None
        async with session_factory() as worker:
            service = VideoUnderstandingService(
                worker,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            claimed = await service.claim_next()
            assert claimed is not None
        async with session_factory() as session:
            async with session.begin():
                stored = await session.get(BackgroundJob, job.id, with_for_update=True)
                assert stored is not None
                stored.started_at = stored.requested_at.replace(year=2000)
        async with session_factory() as session:
            assert (
                len(await JobRecoveryService(session, config()).recover_stale_running_jobs()) == 1
            )
        async with session_factory() as worker:
            late = VideoUnderstandingService(
                worker,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )
            result = await late.process_claimed(business_id=business_id, job_id=job.id)
            assert result.status == JobStatus.FAILED
        async with session_factory() as session:
            assert not list(await session.scalars(select(MediaSceneUnderstanding)))

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
                    first,
                    config(),
                    FakeFrameExtractionAdapter(config()),
                    FakeVideoUnderstandingAdapter(config()),
                    FakeMediaMaterializer(allow_missing_for_testing=True),
                ).claim_next(),
                VideoUnderstandingService(
                    second,
                    config(),
                    FakeFrameExtractionAdapter(config()),
                    FakeVideoUnderstandingAdapter(config()),
                    FakeMediaMaterializer(allow_missing_for_testing=True),
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
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config(), "invalid"),
                FakeMediaMaterializer(allow_missing_for_testing=True),
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
                worker,
                config(),
                FakeFrameExtractionAdapter(config()),
                FakeVideoUnderstandingAdapter(config()),
                FakeMediaMaterializer(allow_missing_for_testing=True),
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
