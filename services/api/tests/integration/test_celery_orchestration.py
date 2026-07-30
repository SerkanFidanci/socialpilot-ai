"""PostgreSQL and broker coverage for the Phase 1D Celery orchestration slice.

These tests exercise the real durable chain: an outbox event is claimed, a wake-up
message is enqueued, a drain task claims the job from PostgreSQL, and the completion
event records server-calculated coverage. Task functions are invoked directly so one
worker process runs several consecutive tasks, which is the regression guard against
asyncpg connections bound to a per-task event loop.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
import redis
from kombu.exceptions import OperationalError  # type: ignore[import-untyped]
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.infrastructure.celery_app import celery_app, create_celery_app
from app.infrastructure.celery_publisher import (
    DRAIN_TASK_BY_EVENT,
    NOTIFICATION_ONLY_EVENTS,
    CeleryOutboxPublisher,
)
from app.infrastructure.media.fake_ingest import FakeMediaMaterializer
from app.infrastructure.media.fake_video_understanding import (
    FakeFrameExtractionAdapter,
    FakeVideoUnderstandingAdapter,
)
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
from app.modules.media.video_understanding_service import (
    VideoUnderstandingSchedulingService,
    VideoUnderstandingService,
)
from app.modules.operations.models import BackgroundJob, JobAttempt, JobStatus, OutboxEvent
from app.modules.operations.models import OutboxStatus as Status
from app.worker import composition
from app.worker.tasks import (
    dispatch_outbox,
    drain_media_ingest,
    drain_video_understanding,
    recover_stale_jobs,
)

pytestmark = pytest.mark.integration

if os.getenv("RUN_INTEGRATION_TESTS") != "1":  # pragma: no cover - environment guard
    pytest.skip("requires PostgreSQL and Redis test services", allow_module_level=True)

QUEUE = "default"
Recorded = list[tuple[str, tuple[object, ...], dict[str, object]]]


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


@asynccontextmanager
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Own one engine per block so no test leaves a pooled connection behind."""

    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    finally:
        await engine.dispose()


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


def broker() -> redis.Redis:
    return redis.Redis.from_url(os.environ["CELERY_BROKER_URL"])


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
    asyncio.run(clear())
    with broker() as client:
        client.delete(QUEUE)
    yield
    asyncio.run(clear())
    with broker() as client:
        client.delete(QUEUE)


@pytest.fixture
def worker() -> Iterator[None]:
    """Install one process-local worker context, exactly as `worker_process_init` would."""

    composition.start_worker_process()
    try:
        yield
    finally:
        composition.shutdown_worker_process()


@pytest.fixture
def isolated_broker(worker: None) -> Iterator[str]:
    """Publish wake-ups to a private queue on the real broker.

    A running `celery-worker` consumes the shared development queue and would remove the
    message before this test could inspect it. Celery resolves `broker_url` from the
    `CELERY_BROKER_URL` environment variable ahead of any configured value, so the queue
    name — not the broker URL — is what an isolated probe can control.
    """

    queue = f"wake-up-probe-{uuid4().hex}"
    application = create_celery_app(config())
    application.conf.task_default_queue = queue
    context = composition.get_worker_context()
    composition._context = replace(context, outbox_publisher=CeleryOutboxPublisher(application))
    try:
        yield queue
    finally:
        with broker() as client:
            client.delete(queue)
        composition._context = context


@pytest.fixture
def recorded_broker() -> Iterator[Recorded]:
    """Capture wake-up messages instead of publishing, then restore the real broker call."""

    sent: Recorded = []
    original = celery_app.send_task
    celery_app.send_task = lambda name, args, kwargs: sent.append((name, args, kwargs))
    try:
        yield sent
    finally:
        celery_app.send_task = original


@pytest.fixture
def failing_broker() -> Iterator[Callable[[Exception], None]]:
    original = celery_app.send_task

    def install(error: Exception) -> None:
        def send(*_args: object, **_kwargs: object) -> None:
            raise error

        celery_app.send_task = send

    try:
        yield install
    finally:
        celery_app.send_task = original


async def seed(session: AsyncSession, *, scenes: int) -> tuple[UUID, UUID]:
    """Create a READY asset with a proxy, one transcript segment, and ordered scenes."""

    token = uuid4().hex
    user = User(email=f"orchestration-{token}@example.com")
    session.add(user)
    await session.flush()
    business = Business(
        name=f"Orchestration {token}",
        slug=f"orchestration-{token}",
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
        language="en",
        duration_ms=1_000,
        full_text="first scene",
        provider="fake",
        status=TranscriptStatus.COMPLETED,
    )
    session.add(record)
    await session.flush()
    session.add(
        TranscriptSegment(
            transcript_id=record.id,
            segment_index=0,
            start_ms=100,
            end_ms=400,
            text="first scene",
            confidence=0.9,
        )
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


def schedule(*, scenes: int) -> tuple[UUID, UUID, UUID]:
    """Create the durable job plus its requested outbox event, committed and closed."""

    async def run() -> tuple[UUID, UUID, UUID]:
        async with sessions() as session_factory, session_factory() as session:
            async with session.begin():
                business_id, asset_id = await seed(session, scenes=scenes)
                job = await VideoUnderstandingSchedulingService(
                    session, config()
                ).schedule_after_scene_speech(
                    business_id=business_id, asset_id=asset_id, correlation_id="orchestration"
                )
                assert job is not None
                job_id = job.id
            return business_id, asset_id, job_id

    return asyncio.run(run())


def event_of(event_type: str) -> OutboxEvent:
    async def run() -> OutboxEvent:
        async with sessions() as session_factory, session_factory() as session:
            events = list(
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.event_type == event_type)
                )
            )
            assert len(events) == 1, f"expected exactly one {event_type} event, got {len(events)}"
            return events[0]

    return asyncio.run(run())


def test_outbox_dispatch_enqueues_a_bare_wake_up_message_on_the_real_broker(
    isolated_broker: str,
) -> None:
    """The broker sees a task name only; identity and object keys stay in PostgreSQL."""

    business_id, asset_id, job_id = schedule(scenes=2)

    assert dispatch_outbox() == {"status": "dispatched", "processed": 1}

    with broker() as client:
        messages = cast(list[bytes], client.lrange(isolated_broker, 0, -1))
    assert len(messages) == 1
    envelope = json.loads(messages[0])
    assert envelope["headers"]["task"] == "media.video_understanding.drain"
    args, kwargs, _embed = json.loads(base64.b64decode(envelope["body"]))
    assert args == [] and kwargs == {}
    raw = messages[0].decode("utf-8", errors="replace")
    for secret in (
        str(business_id),
        str(asset_id),
        str(job_id),
        f"tenant/{business_id}",
        "X-Amz-Signature",
    ):
        assert secret not in raw

    requested = event_of("media.video_understanding.requested")
    assert requested.status == Status.PUBLISHED
    assert requested.published_at is not None
    assert requested.last_error_code is None


def test_wake_up_drains_the_job_and_duplicate_messages_add_no_duplicate_results(
    worker: None, recorded_broker: Recorded
) -> None:
    _, asset_id, job_id = schedule(scenes=2)

    assert dispatch_outbox() == {"status": "dispatched", "processed": 1}
    assert recorded_broker == [("media.video_understanding.drain", (), {})]

    assert drain_video_understanding() == {"status": "drained", "processed": 1}
    # Redelivered messages must find no due job and must not rewrite results.
    assert drain_video_understanding() == {"status": "drained", "processed": 0}
    assert drain_video_understanding() == {"status": "drained", "processed": 0}

    async def assertions() -> None:
        async with sessions() as session_factory, session_factory() as session:
            understandings = list(await session.scalars(select(MediaSceneUnderstanding)))
            assert len(understandings) == 2
            job = await session.get(BackgroundJob, job_id)
            assert job is not None and job.status == JobStatus.SUCCEEDED
            attempts = list(
                await session.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
            )
            assert len(attempts) == 1

    asyncio.run(assertions())

    completed = event_of("media.video_understanding.completed")
    assert completed.payload == {
        "job_id": str(job_id),
        "asset_id": str(asset_id),
        "total_scene_count": 2,
        "analyzed_scene_count": 2,
        "skipped_scene_count": 0,
        "coverage": "full",
        "frame_backed_scene_count": 0,
        "transcript_only_scene_count": 1,
        "no_context_scene_count": 1,
    }


def test_partial_coverage_is_reported_when_scene_scope_is_capped(
    worker: None, recorded_broker: Recorded
) -> None:
    supported = config().video_understanding_supported_scene_count
    schedule(scenes=supported + 2)

    assert dispatch_outbox() == {"status": "dispatched", "processed": 1}
    assert drain_video_understanding() == {"status": "drained", "processed": 1}

    payload = event_of("media.video_understanding.completed").payload
    assert payload["coverage"] == "partial"
    assert payload["total_scene_count"] == supported + 2
    assert payload["analyzed_scene_count"] == supported
    assert payload["skipped_scene_count"] == 2
    assert payload["frame_backed_scene_count"] == 0
    assert payload["transcript_only_scene_count"] == 1
    assert payload["no_context_scene_count"] == supported - 1


def test_transient_broker_failure_leaves_the_event_unpublished_and_retryable(
    worker: None, failing_broker: Callable[[Exception], None]
) -> None:
    schedule(scenes=1)
    failing_broker(OperationalError("broker unreachable"))

    assert dispatch_outbox() == {"status": "dispatched", "processed": 1}

    requested = event_of("media.video_understanding.requested")
    assert requested.status == Status.FAILED
    assert requested.published_at is None
    assert requested.attempt_count == 1
    assert requested.last_error_code == "CELERY_ENQUEUE_UNAVAILABLE"
    assert requested.next_attempt_at is not None


def test_unknown_event_is_dead_lettered_and_never_published(
    worker: None, recorded_broker: Recorded
) -> None:
    business_id, asset_id, _ = schedule(scenes=1)

    async def add_unknown() -> None:
        async with sessions() as session_factory, session_factory() as session:
            async with session.begin():
                session.add(
                    OutboxEvent(
                        business_id=business_id,
                        event_type="media.unmapped.requested",
                        aggregate_type="media_asset",
                        aggregate_id=asset_id,
                        payload={"asset_id": str(asset_id)},
                        correlation_id="unknown-event",
                        status=Status.PENDING,
                        max_attempts=3,
                        next_attempt_at=datetime.now(UTC),
                    )
                )

    asyncio.run(add_unknown())
    assert dispatch_outbox() == {"status": "dispatched", "processed": 2}
    assert recorded_broker == [("media.video_understanding.drain", (), {})]

    unknown = event_of("media.unmapped.requested")
    assert unknown.status == Status.DEAD
    assert unknown.published_at is None
    assert unknown.last_error_code == "OUTBOX_EVENT_TYPE_UNSUPPORTED"


def test_notification_only_completion_events_publish_without_a_broker_message(
    worker: None, recorded_broker: Recorded
) -> None:
    """Completion events drive no work, so they must neither enqueue nor dead-letter."""

    schedule(scenes=1)
    assert dispatch_outbox() == {"status": "dispatched", "processed": 1}
    assert drain_video_understanding() == {"status": "drained", "processed": 1}
    recorded_broker.clear()

    assert dispatch_outbox() == {"status": "dispatched", "processed": 1}
    assert recorded_broker == []

    completed = event_of("media.video_understanding.completed")
    assert completed.status == Status.PUBLISHED
    assert completed.published_at is not None
    assert completed.last_error_code is None


def test_consecutive_tasks_share_one_worker_loop_and_keep_database_access(
    worker: None, recorded_broker: Recorded
) -> None:
    """Regression guard: a per-task loop would break pooled asyncpg on the second task."""

    schedule(scenes=1)
    loop = composition.get_worker_context().loop

    assert recover_stale_jobs() == {"status": "recovered", "processed": 0}
    assert dispatch_outbox() == {"status": "dispatched", "processed": 1}
    assert drain_media_ingest() == {"status": "drained", "processed": 0}
    assert drain_video_understanding() == {"status": "drained", "processed": 1}
    assert recover_stale_jobs() == {"status": "recovered", "processed": 0}
    assert dispatch_outbox() == {"status": "dispatched", "processed": 1}

    assert composition.get_worker_context().loop is loop
    assert not loop.is_closed()


def test_concurrent_drains_claim_each_job_exactly_once() -> None:
    _, _, job_id = schedule(scenes=2)

    async def race() -> None:
        resolved = config()

        def service(session: AsyncSession) -> VideoUnderstandingService:
            return VideoUnderstandingService(
                session,
                resolved,
                FakeFrameExtractionAdapter(resolved),
                FakeVideoUnderstandingAdapter(resolved),
                FakeMediaMaterializer(allow_missing_for_testing=True),
            )

        async with sessions() as session_factory:
            async with session_factory() as first, session_factory() as second:
                claims = await asyncio.gather(
                    service(first).claim_next(), service(second).claim_next()
                )
            assert sum(claim is not None for claim in claims) == 1
            assert {claim.id for claim in claims if claim is not None} == {job_id}
            async with session_factory() as session:
                job = await session.get(BackgroundJob, job_id)
                assert job is not None
                assert job.status == JobStatus.RUNNING
                assert job.attempt_count == 1
                attempts = list(
                    await session.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id))
                )
                assert len(attempts) == 1

    asyncio.run(race())


def test_every_beat_task_name_is_a_registered_worker_task() -> None:
    import app.worker.tasks  # noqa: F401

    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert scheduled
    assert scheduled <= set(celery_app.tasks)
    assert set(DRAIN_TASK_BY_EVENT.values()) <= scheduled
    assert not scheduled & NOTIFICATION_ONLY_EVENTS
