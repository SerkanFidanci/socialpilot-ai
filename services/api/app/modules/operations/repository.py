"""Tenant-scoped persistence operations for durable operational records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operations.models import (
    AuditLog,
    BackgroundJob,
    IdempotencyKey,
    JobAttempt,
    JobStatus,
    OutboxEvent,
    OutboxStatus,
)


class OperationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self, value: AuditLog | BackgroundJob | IdempotencyKey | JobAttempt | OutboxEvent
    ) -> None:
        self._session.add(value)

    async def get_idempotency_for_update(
        self, *, business_id: UUID, actor_user_id: UUID, operation: str, key: str
    ) -> IdempotencyKey | None:
        statement = (
            select(IdempotencyKey)
            .where(
                IdempotencyKey.business_id == business_id,
                IdempotencyKey.actor_user_id == actor_user_id,
                IdempotencyKey.operation == operation,
                IdempotencyKey.idempotency_key == key,
            )
            .with_for_update()
        )
        return cast(IdempotencyKey | None, await self._session.scalar(statement))

    async def create_idempotency_if_absent(self, value: IdempotencyKey) -> IdempotencyKey | None:
        statement = (
            insert(IdempotencyKey)
            .values(
                id=value.id,
                business_id=value.business_id,
                actor_user_id=value.actor_user_id,
                operation=value.operation,
                idempotency_key=value.idempotency_key,
                request_fingerprint=value.request_fingerprint,
                status=value.status,
                correlation_id=value.correlation_id,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_scope")
            .returning(IdempotencyKey.id)
        )
        created = await self._session.scalar(statement)
        if created is None:
            return None
        return await self.get_idempotency_for_update(
            business_id=value.business_id,
            actor_user_id=value.actor_user_id,
            operation=value.operation,
            key=value.idempotency_key,
        )

    async def claim_next_outbox_event(self) -> OutboxEvent | None:
        now = datetime.now(UTC)
        statement = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status.in_((OutboxStatus.PENDING, OutboxStatus.FAILED)),
                OutboxEvent.next_attempt_at <= now,
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return cast(OutboxEvent | None, await self._session.scalar(statement))

    async def get_job_for_update(self, business_id: UUID, job_id: UUID) -> BackgroundJob | None:
        statement = (
            select(BackgroundJob)
            .where(BackgroundJob.business_id == business_id, BackgroundJob.id == job_id)
            .with_for_update()
        )
        return cast(BackgroundJob | None, await self._session.scalar(statement))

    async def claim_next_ingest_job(self) -> BackgroundJob | None:
        now = datetime.now(UTC)
        statement = (
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type == "media.ingest",
                BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.FAILED)),
                (BackgroundJob.status == JobStatus.QUEUED)
                | (
                    BackgroundJob.next_attempt_at.is_not(None)
                    & (BackgroundJob.next_attempt_at <= now)
                ),
            )
            .order_by(BackgroundJob.requested_at, BackgroundJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return cast(BackgroundJob | None, await self._session.scalar(statement))

    async def get_attempt_for_update(self, job_id: UUID, attempt_number: int) -> JobAttempt | None:
        statement = (
            select(JobAttempt)
            .where(JobAttempt.job_id == job_id, JobAttempt.attempt_number == attempt_number)
            .with_for_update()
        )
        return cast(JobAttempt | None, await self._session.scalar(statement))

    async def list_outbox_for_business(self, business_id: UUID) -> list[OutboxEvent]:
        statement = select(OutboxEvent).where(OutboxEvent.business_id == business_id)
        return list((await self._session.scalars(statement)).all())
