"""PostgreSQL integration coverage for the durable operational foundation."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.telemetry import TRACEPARENT_FIELD, continue_trace
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.main import create_app
from app.modules.media.storage import StoredObjectMetadata
from app.modules.operations.models import (
    AuditLog,
    BackgroundJob,
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.modules.operations.service import (
    JobRecoveryService,
    JobStateService,
    OutboxDispatchService,
    TransientPublishError,
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
                    "TRUNCATE audit_logs, idempotency_keys, job_attempts, jobs, outbox_events, "
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


def create_upload(
    client: TestClient, business_id: str, headers: dict[str, str]
) -> dict[str, object]:
    response = client.post(
        f"/v1/businesses/{business_id}/media/uploads",
        headers=headers,
        json={
            "filename": "clip.mp4",
            "content_type": "video/mp4",
            "byte_size": 128,
            "sha256_checksum": CHECKSUM,
            "part_count": 2,
        },
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def mark_uploaded(client: TestClient, upload_id: str) -> None:
    fake = cast(FakeMultipartStorage, cast(FastAPI, client.app).state.storage)
    fake.mark_uploaded_for_testing(
        storage_upload_id=asyncio.run(storage_id(upload_id)),
        parts={1: "one", 2: "two"},
        metadata=StoredObjectMetadata(128, "video/mp4", CHECKSUM),
    )


def complete_path(business_id: str, upload_id: str) -> str:
    return f"/v1/businesses/{business_id}/media/uploads/{upload_id}/complete"


def complete_body(checksum: str = CHECKSUM) -> dict[str, object]:
    return {
        "sha256_checksum": checksum,
        "parts": [{"part_number": 1, "etag": "one"}, {"part_number": 2, "etag": "two"}],
    }


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_media_completion_commits_asset_job_outbox_audit_and_idempotency_atomically() -> None:
    owner = auth("operations-owner", "operations-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Operations", "timezone": "UTC"}
        ).json()["id"]
        upload = create_upload(client, business_id, owner)
        mark_uploaded(client, str(upload["id"]))
        headers = {**owner, "Idempotency-Key": "completion-1"}
        first = client.post(
            complete_path(business_id, str(upload["id"])), headers=headers, json=complete_body()
        )
        replay = client.post(
            complete_path(business_id, str(upload["id"])), headers=headers, json=complete_body()
        )
        assert first.status_code == replay.status_code == 200
        assert first.json() == replay.json()

        async def counts() -> tuple[int, int, int, int]:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with engine.connect() as connection:
                    values = await asyncio.gather(
                        connection.scalar(text("SELECT count(*) FROM jobs")),
                        connection.scalar(text("SELECT count(*) FROM outbox_events")),
                        connection.scalar(text("SELECT count(*) FROM audit_logs")),
                        connection.scalar(text("SELECT count(*) FROM idempotency_keys")),
                    )
                    return tuple(int(value or 0) for value in values)  # type: ignore[return-value]
            finally:
                await engine.dispose()

        assert asyncio.run(counts()) == (1, 1, 1, 1)
        conflict = client.post(
            complete_path(business_id, str(upload["id"])),
            headers=headers,
            json=complete_body("b" * 64),
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_same_idempotency_key_executes_once_concurrently_and_rollbacks_leave_no_event() -> None:
    owner = auth("operations-concurrent", "operations-concurrent@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Concurrent", "timezone": "UTC"}
        ).json()["id"]
        upload = create_upload(client, business_id, owner)
        mark_uploaded(client, str(upload["id"]))
        headers = {**owner, "Idempotency-Key": "same-key"}

        def complete() -> int:
            return int(
                client.post(
                    complete_path(business_id, str(upload["id"])),
                    headers=headers,
                    json=complete_body(),
                ).status_code
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(lambda _: complete(), range(2))) == [200, 200]

        bad = create_upload(client, business_id, owner)
        rollback = client.post(
            complete_path(business_id, str(bad["id"])),
            headers={**owner, "Idempotency-Key": "rollback-key"},
            json=complete_body(),
        )
        assert rollback.status_code == 503

        async def rollback_events() -> int:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with engine.connect() as connection:
                    return int(
                        await connection.scalar(
                            text(
                                "SELECT count(*) FROM outbox_events WHERE event_type = 'media.ingest.requested'"
                            )
                        )
                        or 0
                    )
            finally:
                await engine.dispose()

        assert asyncio.run(rollback_events()) == 1


class SuccessfulPublisher:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, event: OutboxEvent) -> None:
        self.published.append(str(event.id))


class TransientPublisher:
    async def publish(self, event: OutboxEvent) -> None:
        raise TransientPublishError("BROKER_TEMPORARY")


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_outbox_locking_retry_dead_letter_job_states_and_audit_scope() -> None:
    owner = auth("operations-dispatch", "operations-dispatch@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Dispatch", "timezone": "UTC"}
        ).json()["id"]
        upload = create_upload(client, business_id, owner)
        mark_uploaded(client, str(upload["id"]))
        assert (
            client.post(
                complete_path(business_id, str(upload["id"])),
                headers={**owner, "Idempotency-Key": "dispatch-key"},
                json=complete_body(),
            ).status_code
            == 200
        )

    async def dispatch_and_check() -> None:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            publisher = SuccessfulPublisher()
            async with factory() as first, factory() as second:
                results = await asyncio.gather(
                    OutboxDispatchService(first, publisher).dispatch_one(),
                    OutboxDispatchService(second, publisher).dispatch_one(),
                )
                assert sum(value is not None for value in results) == 1
                assert len(publisher.published) == 1
            async with factory() as session:
                event = await session.scalar(select(OutboxEvent))
                job = await session.scalar(select(BackgroundJob))
                audit = await session.scalar(select(AuditLog))
                assert event is not None and event.status == OutboxStatus.PUBLISHED
                assert job is not None and audit is not None
                assert audit.business_id == job.business_id and audit.actor_user_id is not None
                await session.commit()
                async with session.begin():
                    transitioned = await JobStateService(session).transition(
                        business_id=job.business_id, job_id=job.id, target=JobStatus.RUNNING
                    )
                    assert transitioned.attempt_count == 1
                async with session.begin():
                    transitioned = await JobStateService(session).transition(
                        business_id=job.business_id,
                        job_id=job.id,
                        target=JobStatus.FAILED,
                        error_code="TEMPORARY",
                        error_summary="temporary failure",
                    )
                    assert transitioned.status == JobStatus.FAILED
                async with session.begin():
                    with pytest.raises(Exception) as error:
                        await JobStateService(session).transition(
                            business_id=job.business_id, job_id=job.id, target=JobStatus.SUCCEEDED
                        )
                    assert getattr(error.value, "code", None) == "JOB_STATE_CONFLICT"
                event.status = OutboxStatus.PENDING
                event.attempt_count = 0
                event.next_attempt_at = event.created_at
                await session.commit()
            async with factory() as retry_session:
                retried = await OutboxDispatchService(
                    retry_session, TransientPublisher()
                ).dispatch_one()
                assert retried is not None and retried.status == OutboxStatus.FAILED
                assert retried.attempt_count == 1
            async with factory() as session:
                event = await session.scalar(select(OutboxEvent))
                assert event is not None
                event.attempt_count = event.max_attempts - 1
                event.next_attempt_at = event.created_at
                await session.commit()
            async with factory() as retry_session:
                exhausted = await OutboxDispatchService(
                    retry_session, TransientPublisher()
                ).dispatch_one()
                assert exhausted is not None and exhausted.status == OutboxStatus.DEAD
                assert exhausted.attempt_count == exhausted.max_attempts
        finally:
            await engine.dispose()

    asyncio.run(dispatch_and_check())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_stale_running_jobs_finalize_attempts_and_retry_or_dead_letter() -> None:
    owner = auth("stale-recovery", "stale-recovery@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Stale recovery", "timezone": "UTC"}
        ).json()["id"]
        retry_upload = create_upload(client, business_id, owner)
        dead_upload = create_upload(client, business_id, owner)
        mark_uploaded(client, str(retry_upload["id"]))
        mark_uploaded(client, str(dead_upload["id"]))
        for upload, key in ((retry_upload, "retry"), (dead_upload, "dead")):
            assert (
                client.post(
                    complete_path(business_id, str(upload["id"])),
                    headers={**owner, "Idempotency-Key": key},
                    json=complete_body(),
                ).status_code
                == 200
            )

    async def recover() -> tuple[JobStatus, JobStatus, list[JobAttempt]]:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with factory() as session:
                jobs = list(
                    (await session.scalars(select(BackgroundJob).order_by(BackgroundJob.id))).all()
                )
                assert len(jobs) == 2
                retry_job = jobs[0]
                await session.commit()
                for job in jobs:
                    async with session.begin():
                        await JobStateService(session).transition(
                            business_id=job.business_id, job_id=job.id, target=JobStatus.RUNNING
                        )
                dead_job = jobs[1]
                await session.execute(
                    text(
                        "UPDATE jobs SET timeout_seconds = 1, "
                        "started_at = timezone('utc', now()) - interval '20 seconds'"
                    )
                )
                await session.execute(
                    text("UPDATE jobs SET max_attempts = 1 WHERE id = :job_id"),
                    {"job_id": str(dead_job.id)},
                )
                await session.commit()
            async with factory() as recovery_session:
                recovered = await JobRecoveryService(recovery_session).recover_stale_running_jobs(
                    business_id=UUID(str(business_id))
                )
                assert len(recovered) == 2
            async with factory() as session:
                jobs = list(
                    (await session.scalars(select(BackgroundJob).order_by(BackgroundJob.id))).all()
                )
                attempts = list((await session.scalars(select(JobAttempt))).all())
                statuses = {job.id: job.status for job in jobs}
                return statuses[retry_job.id], statuses[dead_job.id], attempts
        finally:
            await engine.dispose()

    retry_status, dead_status, attempts = asyncio.run(recover())
    assert retry_status == JobStatus.FAILED and dead_status == JobStatus.DEAD
    assert all(attempt.status == JobAttemptStatus.FAILED for attempt in attempts)
    assert all(attempt.error_code == "JOB_TIMEOUT" for attempt in attempts)


# ------------------------------------------------------- durable trace continuity (W12)


def telemetry_config() -> Settings:
    settings = config()
    return settings.model_copy(update={"otel_exporter_otlp_endpoint": "http://collector:4318"})


class TraceRecordingPublisher:
    """A publisher that records nothing but the trace it was called in."""

    def __init__(self) -> None:
        self.trace_ids: list[int] = []

    async def publish(self, event: OutboxEvent) -> None:
        self.trace_ids.append(trace.get_current_span().get_span_context().trace_id)


def complete_one_upload(client: TestClient, owner: dict[str, str], name: str) -> None:
    business_id = client.post(
        "/v1/businesses", headers=owner, json={"name": name, "timezone": "UTC"}
    ).json()["id"]
    upload = create_upload(client, business_id, owner)
    mark_uploaded(client, str(upload["id"]))
    assert (
        client.post(
            complete_path(business_id, str(upload["id"])),
            headers={**owner, "Idempotency-Key": f"trace-{name}"},
            json=complete_body(),
        ).status_code
        == 200
    )


async def outbox_payload() -> dict[str, object]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            row = await connection.scalar(text("SELECT payload FROM outbox_events"))
            return cast(dict[str, object], row)
    finally:
        await engine.dispose()


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_worker_continues_the_trace_of_the_request_that_wrote_the_event() -> None:
    """API and worker stop being two islands: the durable envelope is what carries the link.

    Work crosses to the worker through the outbox and a Beat tick, not a direct enqueue, so
    in-process propagation cannot reach it. The envelope carries `traceparent` (§26.4) and the
    drain re-attaches it before publishing, which is what puts the enqueued task in the trace
    of the request that caused it.
    """

    memory = InMemorySpanExporter()
    app = create_app(
        telemetry_config(),
        telemetry_span_exporter=memory,
        telemetry_metric_reader=InMemoryMetricReader(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        complete_one_upload(client, auth("trace-owner", "trace-owner@example.com"), "Traced")
        app.state.telemetry.tracer_provider.force_flush()
        request_spans = [s for s in memory.get_finished_spans() if s.name.endswith("/complete")]
        assert request_spans, [s.name for s in memory.get_finished_spans()]
        request_trace_id = request_spans[0].context.trace_id

    payload = asyncio.run(outbox_payload())
    assert TRACEPARENT_FIELD in payload, payload
    assert f"{request_trace_id:032x}" in str(payload[TRACEPARENT_FIELD])
    # The envelope gained trace context and nothing else.
    assert set(payload) == {"job_id", "asset_id", TRACEPARENT_FIELD}

    async def dispatch() -> list[int]:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            publisher = TraceRecordingPublisher()
            async with factory() as session:
                dispatched = await OutboxDispatchService(session, publisher).dispatch_one(
                    publish_scope=continue_trace
                )
                assert dispatched is not None
            return publisher.trace_ids
        finally:
            await engine.dispose()

    assert asyncio.run(dispatch()) == [request_trace_id]


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_telemetry_disabled_writes_the_same_envelope_it_always_wrote() -> None:
    """W05's zero-cost promise covers the envelope too: no endpoint, no field, no side effect."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        complete_one_upload(client, auth("plain-owner", "plain-owner@example.com"), "Plain")

    payload = asyncio.run(outbox_payload())
    assert set(payload) == {"job_id", "asset_id"}

    async def dispatch() -> list[int]:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            publisher = TraceRecordingPublisher()
            async with factory() as session:
                assert (
                    await OutboxDispatchService(session, publisher).dispatch_one(
                        publish_scope=continue_trace
                    )
                    is not None
                )
            return publisher.trace_ids
        finally:
            await engine.dispose()

    # No provider, no span: the invalid span context reports trace id 0.
    assert asyncio.run(dispatch()) == [0]
