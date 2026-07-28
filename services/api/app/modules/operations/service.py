"""Application services for atomic operational records and state transitions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.modules.operations.models import (
    AuditLog,
    BackgroundJob,
    IdempotencyKey,
    IdempotencyStatus,
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.modules.operations.repository import OperationsRepository


@dataclass(frozen=True)
class IdempotencyResult:
    record: IdempotencyKey
    is_replay: bool


def request_fingerprint(value: dict[str, object]) -> str:
    """Hash a canonical safe request representation without retaining the raw body."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


class IdempotencyService:
    def __init__(self, repository: OperationsRepository) -> None:
        self._repository = repository

    async def acquire(
        self,
        *,
        business_id: UUID,
        actor_user_id: UUID,
        operation: str,
        key: str,
        fingerprint: str,
        correlation_id: str,
    ) -> IdempotencyResult:
        if not key or len(key) > 255:
            raise ProblemException(
                status=400,
                code="IDEMPOTENCY_KEY_INVALID",
                title="Invalid idempotency key",
                detail="The idempotency key is invalid.",
            )
        candidate = IdempotencyKey(
            id=uuid4(),
            business_id=business_id,
            actor_user_id=actor_user_id,
            operation=operation,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            status=IdempotencyStatus.PROCESSING,
            correlation_id=correlation_id,
        )
        record = await self._repository.create_idempotency_if_absent(candidate)
        if record is not None:
            return IdempotencyResult(record=record, is_replay=False)
        record = await self._repository.get_idempotency_for_update(
            business_id=business_id, actor_user_id=actor_user_id, operation=operation, key=key
        )
        if record is None:
            raise ProblemException(
                status=409,
                code="IDEMPOTENCY_IN_PROGRESS",
                title="Request in progress",
                detail="An equivalent request is currently being processed.",
            )
        if record.request_fingerprint != fingerprint:
            raise ProblemException(
                status=409,
                code="IDEMPOTENCY_CONFLICT",
                title="Idempotency conflict",
                detail="The idempotency key was already used for a different request.",
            )
        if record.status == IdempotencyStatus.COMPLETED:
            return IdempotencyResult(record=record, is_replay=True)
        raise ProblemException(
            status=409,
            code="IDEMPOTENCY_IN_PROGRESS",
            title="Request in progress",
            detail="An equivalent request is currently being processed.",
        )


class OperationsService:
    """Create durable job, outbox, and audit records inside a caller transaction."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = OperationsRepository(session)

    async def record_media_ingest(
        self, *, business_id: UUID, actor_user_id: UUID, asset_id: UUID, correlation_id: str
    ) -> BackgroundJob:
        job = BackgroundJob(
            business_id=business_id,
            job_type="media.ingest",
            resource_type="media_asset",
            resource_id=asset_id,
            status=JobStatus.QUEUED,
            timeout_seconds=self._settings.media_ingest_timeout_seconds,
            max_attempts=self._settings.media_ingest_max_attempts,
            correlation_id=correlation_id,
            next_attempt_at=datetime.now(UTC),
        )
        self._repository.add(job)
        await self._session.flush()
        self._repository.add(
            OutboxEvent(
                business_id=business_id,
                event_type="media.ingest.requested",
                aggregate_type="media_asset",
                aggregate_id=asset_id,
                payload={"job_id": str(job.id), "asset_id": str(asset_id)},
                correlation_id=correlation_id,
                status=OutboxStatus.PENDING,
                max_attempts=3,
                next_attempt_at=datetime.now(UTC),
            )
        )
        self._repository.add(
            AuditLog(
                business_id=business_id,
                actor_user_id=actor_user_id,
                action="media.upload.completed",
                resource_type="media_asset",
                resource_id=asset_id,
                correlation_id=correlation_id,
                details={"status": "uploaded"},
            )
        )
        return job

    async def complete_idempotency(
        self, record: IdempotencyKey, *, response_status: int, response_body: dict[str, object]
    ) -> None:
        record.status = IdempotencyStatus.COMPLETED
        record.response_status = response_status
        record.response_body = response_body
        record.completed_at = datetime.now(UTC)

    async def record_technical_analysis(
        self, *, business_id: UUID, asset_id: UUID, correlation_id: str
    ) -> BackgroundJob:
        job = BackgroundJob(
            business_id=business_id,
            job_type="media.technical_analysis",
            resource_type="media_asset",
            resource_id=asset_id,
            status=JobStatus.QUEUED,
            timeout_seconds=self._settings.media_technical_job_timeout_seconds,
            max_attempts=self._settings.media_ingest_max_attempts,
            correlation_id=correlation_id,
            next_attempt_at=datetime.now(UTC),
        )
        self._repository.add(job)
        await self._session.flush()
        self._repository.add(
            OutboxEvent(
                business_id=business_id,
                event_type="media.technical_analysis.requested",
                aggregate_type="media_asset",
                aggregate_id=asset_id,
                payload={"job_id": str(job.id), "asset_id": str(asset_id)},
                correlation_id=correlation_id,
                status=OutboxStatus.PENDING,
                max_attempts=job.max_attempts,
                next_attempt_at=datetime.now(UTC),
            )
        )
        return job

    async def record_scene_speech_analysis(
        self, *, business_id: UUID, asset_id: UUID, correlation_id: str
    ) -> BackgroundJob:
        job = BackgroundJob(
            business_id=business_id,
            job_type="media.scene_speech_analysis",
            resource_type="media_asset",
            resource_id=asset_id,
            status=JobStatus.QUEUED,
            timeout_seconds=self._settings.scene_speech_job_timeout_seconds,
            max_attempts=self._settings.media_ingest_max_attempts,
            correlation_id=correlation_id,
            next_attempt_at=datetime.now(UTC),
        )
        self._repository.add(job)
        await self._session.flush()
        self._repository.add(
            OutboxEvent(
                business_id=business_id,
                event_type="media.scene_speech.requested",
                aggregate_type="media_asset",
                aggregate_id=asset_id,
                payload={"job_id": str(job.id), "asset_id": str(asset_id)},
                correlation_id=correlation_id,
                status=OutboxStatus.PENDING,
                max_attempts=job.max_attempts,
                next_attempt_at=datetime.now(UTC),
            )
        )
        return job

    async def record_video_understanding(
        self, *, business_id: UUID, asset_id: UUID, correlation_id: str, scene_count: int = 1
    ) -> BackgroundJob:
        """Create one durable VLM job/event pair for a completed scene/speech run."""

        candidate = BackgroundJob(
            id=uuid4(),
            business_id=business_id,
            job_type="media.video_understanding",
            resource_type="media_asset",
            resource_id=asset_id,
            status=JobStatus.QUEUED,
            timeout_seconds=calculate_video_understanding_job_timeout(
                self._settings, scene_count=scene_count
            ),
            attempt_count=0,
            max_attempts=self._settings.video_understanding_max_attempts,
            correlation_id=correlation_id,
            next_attempt_at=datetime.now(UTC),
        )
        job, created = await self._repository.create_video_understanding_job_if_absent(candidate)
        if not created:
            return job
        self._repository.add(
            OutboxEvent(
                business_id=business_id,
                event_type="media.video_understanding.requested",
                aggregate_type="media_asset",
                aggregate_id=asset_id,
                payload={"job_id": str(job.id), "asset_id": str(asset_id)},
                correlation_id=correlation_id,
                status=OutboxStatus.PENDING,
                max_attempts=job.max_attempts,
                next_attempt_at=datetime.now(UTC),
            )
        )
        return job


class JobStateService:
    """One central state-machine for durable job transitions."""

    _allowed: dict[JobStatus, frozenset[JobStatus]] = {
        JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.DEAD}),
        JobStatus.RUNNING: frozenset(
            {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.DEAD}
        ),
        JobStatus.FAILED: frozenset({JobStatus.QUEUED, JobStatus.DEAD}),
        JobStatus.SUCCEEDED: frozenset(),
        JobStatus.CANCELLED: frozenset(),
        JobStatus.DEAD: frozenset(),
    }

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = OperationsRepository(session)

    async def transition(
        self,
        *,
        business_id: UUID,
        job_id: UUID,
        target: JobStatus,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> BackgroundJob:
        job = await self._repository.get_job_for_update(business_id, job_id)
        if job is None:
            raise ProblemException(
                status=404,
                code="TENANT_RESOURCE_NOT_FOUND",
                title="Resource not found",
                detail="The requested resource is not available.",
            )
        if target not in self._allowed[job.status]:
            raise ProblemException(
                status=409,
                code="JOB_STATE_CONFLICT",
                title="Invalid job state",
                detail="The requested job transition is not allowed.",
            )
        now = datetime.now(UTC)
        if target == JobStatus.RUNNING:
            job.attempt_count += 1
            job.started_at = now
            self._repository.add(
                JobAttempt(
                    job_id=job.id,
                    attempt_number=job.attempt_count,
                    status=JobAttemptStatus.STARTED,
                    correlation_id=job.correlation_id,
                )
            )
        elif target in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.DEAD}:
            job.finished_at = now
            job.last_error_code = error_code
            job.last_error_summary = self._safe_summary(error_summary)
            if job.attempt_count:
                attempt = await self._repository.get_attempt_for_update(job.id, job.attempt_count)
                if attempt is not None:
                    attempt.status = (
                        JobAttemptStatus.SUCCEEDED
                        if target == JobStatus.SUCCEEDED
                        else JobAttemptStatus.FAILED
                    )
                    attempt.finished_at = now
                    attempt.error_code = error_code
                    attempt.error_summary = self._safe_summary(error_summary)
        job.status = target
        return job

    @staticmethod
    def _safe_summary(value: str | None) -> str | None:
        if value is None:
            return None
        return value[:512]


class JobRecoveryService:
    """Recover timed-out durable jobs without reclaiming active work."""

    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._repository = OperationsRepository(session)
        self._grace_seconds = settings.job_timeout_grace_seconds if settings else 15

    async def recover_stale_running_jobs(
        self, *, business_id: UUID | None = None, limit: int = 100
    ) -> list[BackgroundJob]:
        if limit < 1 or limit > 1_000:
            raise ValueError("recovery limit must be between 1 and 1000")
        now = datetime.now(UTC)
        async with self._session.begin():
            jobs = await self._repository.lock_stale_running_jobs(
                business_id=business_id, grace_seconds=self._grace_seconds, limit=limit
            )
            for job in jobs:
                attempt = await self._repository.get_attempt_for_update(job.id, job.attempt_count)
                if attempt is not None and attempt.status == JobAttemptStatus.STARTED:
                    attempt.status = JobAttemptStatus.FAILED
                    attempt.finished_at = now
                    attempt.error_code = "JOB_TIMEOUT"
                    attempt.error_summary = "Job execution exceeded its timeout."
                job.last_error_code = "JOB_TIMEOUT"
                job.last_error_summary = "Job execution exceeded its timeout."
                job.finished_at = now
                if job.attempt_count < job.max_attempts:
                    job.status = JobStatus.FAILED
                    job.next_attempt_at = now + timedelta(seconds=min(2**job.attempt_count, 60))
                else:
                    job.status = JobStatus.DEAD
                    job.next_attempt_at = None
            return jobs


def calculate_video_understanding_job_timeout(settings: Settings, *, scene_count: int) -> int:
    """Return a durable wall-clock budget; adapters retain their own step timeouts."""

    if scene_count < 1:
        raise ValueError("VIDEO_UNDERSTANDING_SCENE_COUNT_INVALID")
    timeout = (
        settings.video_understanding_job_base_timeout_seconds
        + scene_count * settings.video_understanding_job_per_scene_timeout_seconds
        + settings.video_understanding_job_persistence_timeout_seconds
    )
    if timeout > settings.video_understanding_job_max_timeout_seconds:
        raise ValueError("VIDEO_UNDERSTANDING_JOB_TIMEOUT_EXCEEDED")
    return timeout


class OutboxDispatchService:
    """Claim, publish, and safely retry outbox events using an injected publisher port."""

    def __init__(self, session: AsyncSession, publisher: OutboxPublisherPort) -> None:
        self._session = session
        self._repository = OperationsRepository(session)
        self._publisher = publisher

    async def dispatch_one(self) -> OutboxEvent | None:
        async with self._session.begin():
            event = await self._repository.claim_next_outbox_event()
            if event is None:
                return None
            event.status = OutboxStatus.PROCESSING
            event.claimed_at = datetime.now(UTC)
            event.attempt_count += 1
            event.last_error_code = None
            event_id = event.id
        try:
            await self._publisher.publish(event)
        except TransientPublishError as error:
            return await self._record_failure(event_id, error.code, transient=True)
        except PublishError as error:
            return await self._record_failure(event_id, error.code, transient=False)
        async with self._session.begin():
            event = await self._load_event(event_id, lock=True)
            event.status = OutboxStatus.PUBLISHED
            event.published_at = datetime.now(UTC)
            return event

    async def _load_event(self, event_id: UUID, *, lock: bool = False) -> OutboxEvent:
        from sqlalchemy import select

        statement = select(OutboxEvent).where(OutboxEvent.id == event_id)
        if lock:
            statement = statement.with_for_update()
        event = await self._session.scalar(statement)
        if event is None:
            raise RuntimeError("claimed outbox event was removed")
        return event

    async def _record_failure(self, event_id: UUID, code: str, *, transient: bool) -> OutboxEvent:
        async with self._session.begin():
            event = await self._load_event(event_id, lock=True)
            event.last_error_code = code
            if not transient or event.attempt_count >= event.max_attempts:
                event.status = OutboxStatus.DEAD
                return event
            event.status = OutboxStatus.FAILED
            event.next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=min(2**event.attempt_count, 60)
            )
            return event


class PublishError(Exception):
    def __init__(self, code: str = "PUBLISH_FAILED") -> None:
        self.code = code
        super().__init__(code)


class TransientPublishError(PublishError):
    """A publisher failure that is eligible for bounded retry."""


class OutboxPublisherPort(Protocol):
    async def publish(self, event: OutboxEvent) -> None:
        """Hand an outbox event to an injected transport adapter."""
