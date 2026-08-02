"""PostgreSQL coverage for the tenant-scoped aggregate processing-summary endpoint."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.main import create_app
from app.modules.businesses.models import Business
from app.modules.media.models import (
    IngestStatus,
    MalwareScanStatus,
    MediaAsset,
    MediaAssetStatus,
    MediaDerivative,
    MediaDerivativeStatus,
    MediaIngestInspection,
    MediaMalwareScan,
    MediaScene,
    MediaSceneUnderstanding,
    MediaTechnicalAnalysis,
    MediaTechnicalMetadata,
    MediaUploadSession,
    SceneUnderstandingStatus,
    TechnicalAnalysisStatus,
    Transcript,
    TranscriptSegment,
    TranscriptStatus,
    UploadSessionStatus,
)
from app.modules.operations.models import BackgroundJob, JobStatus

pytestmark = pytest.mark.integration

if os.getenv("RUN_INTEGRATION_TESTS") != "1":  # pragma: no cover - environment guard
    pytest.skip("requires PostgreSQL and Redis test services", allow_module_level=True)

KEY = "test-local-identity-signing-key-123"
OBJECT_KEY_MARKER = "must-not-leak-object-key"
UPLOAD_ID_MARKER = "must-not-leak-upload-id"
ETAG_MARKER = "must-not-leak-etag"


def config(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": os.environ["DATABASE_URL"],
        "redis_url": os.environ["REDIS_URL"],
        "celery_broker_url": os.environ["CELERY_BROKER_URL"],
        "celery_result_backend": os.environ["CELERY_RESULT_BACKEND"],
        "local_identity_signing_key": SecretStr(KEY),
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def auth(subject: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + LocalIdentityVerifier.sign_for_testing(
            signing_key=KEY, subject=subject, email=f"{subject}@example.com"
        )
    }


def create_business(client: TestClient, subject: str) -> UUID:
    """Create a business through the API so the caller becomes its owner."""

    response = client.post(
        "/v1/businesses", headers=auth(subject), json={"name": subject, "timezone": "UTC"}
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


@asynccontextmanager
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    finally:
        await engine.dispose()


def write(work: Any) -> Any:
    """Run one committed transaction against the test database."""

    async def run() -> Any:
        async with sessions() as factory, factory() as session:
            async with session.begin():
                return await work(session)

    return asyncio.run(run())


async def clear() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE audit_logs, idempotency_keys, job_attempts, jobs, outbox_events, "
                    "media_scene_understandings, transcript_segments, transcripts, media_scenes, "
                    "media_derivatives, media_technical_metadata, media_technical_analyses, "
                    "media_malware_scans, media_ingest_inspections, media_upload_sessions, "
                    "media_assets, business_members, businesses, external_identities, users CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
    asyncio.run(clear())
    yield
    asyncio.run(clear())


async def seed_asset(
    session: AsyncSession,
    *,
    business_id: UUID,
    asset_status: MediaAssetStatus = MediaAssetStatus.UPLOADED,
    ingest_status: IngestStatus = IngestStatus.PENDING,
) -> UUID:
    owner_id = await session.scalar(
        select(Business.created_by_user_id).where(Business.id == business_id)
    )
    assert owner_id is not None
    asset = MediaAsset(
        business_id=business_id,
        created_by_user_id=owner_id,
        storage_object_key=f"tenant/{business_id}/{OBJECT_KEY_MARKER}/{uuid4().hex}",
        content_type="video/mp4",
        byte_size=2048,
        sha256_checksum="a" * 64,
        status=asset_status,
        ingest_status=ingest_status,
        uploaded_at=datetime.now(UTC),
    )
    session.add(asset)
    await session.flush()
    session.add(
        MediaUploadSession(
            business_id=business_id,
            asset_id=asset.id,
            storage_upload_id=f"{UPLOAD_ID_MARKER}-{uuid4().hex}",
            expected_part_count=2,
            status=UploadSessionStatus.COMPLETED,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            completed_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return asset.id


async def add_inspection(session: AsyncSession, business_id: UUID, asset_id: UUID) -> None:
    session.add(
        MediaIngestInspection(
            business_id=business_id,
            asset_id=asset_id,
            storage_byte_size=2048,
            storage_content_type="video/mp4",
            storage_sha256_checksum="a" * 64,
            storage_etag=ETAG_MARKER,
            detected_content_type="video/mp4",
        )
    )
    await session.flush()


async def add_scan(
    session: AsyncSession,
    business_id: UUID,
    asset_id: UUID,
    *,
    status: MalwareScanStatus = MalwareScanStatus.CLEAN,
    safe_error_code: str | None = None,
) -> None:
    session.add(
        MediaMalwareScan(
            business_id=business_id,
            asset_id=asset_id,
            status=status,
            scanner_name="fake",
            safe_error_code=safe_error_code,
        )
    )
    await session.flush()


async def add_technical(
    session: AsyncSession,
    business_id: UUID,
    asset_id: UUID,
    *,
    status: TechnicalAnalysisStatus = TechnicalAnalysisStatus.COMPLETED,
    safe_error_code: str | None = None,
) -> None:
    session.add(
        MediaTechnicalAnalysis(
            business_id=business_id,
            asset_id=asset_id,
            status=status,
            safe_error_code=safe_error_code,
            completed_at=(
                datetime.now(UTC) if status == TechnicalAnalysisStatus.COMPLETED else None
            ),
        )
    )
    if status == TechnicalAnalysisStatus.COMPLETED:
        session.add(
            MediaTechnicalMetadata(
                business_id=business_id,
                asset_id=asset_id,
                container_format="mp4",
                duration_ms=4_000,
                file_size=2048,
                video_codec="h264",
                width=1920,
                height=1080,
                display_aspect_ratio="16:9",
                frame_rate_numerator=30,
                frame_rate_denominator=1,
                bit_rate=2_000_000,
                rotation_degrees=0,
                has_audio=True,
                audio_codec="aac",
                audio_sample_rate=48_000,
                audio_channel_count=2,
                stream_count=2,
            )
        )
        session.add(
            MediaDerivative(
                business_id=business_id,
                asset_id=asset_id,
                kind="proxy",
                storage_object_key=f"tenant/{business_id}/{OBJECT_KEY_MARKER}/{uuid4().hex}",
                content_type="video/mp4",
                byte_size=1024,
                sha256_checksum="b" * 64,
                status=MediaDerivativeStatus.READY,
            )
        )
    await session.flush()


async def add_scene_speech(
    session: AsyncSession,
    business_id: UUID,
    asset_id: UUID,
    *,
    scenes: int,
    transcript_status: TranscriptStatus = TranscriptStatus.COMPLETED,
) -> list[UUID]:
    record = Transcript(
        business_id=business_id,
        asset_id=asset_id,
        language="tr",
        duration_ms=4_000,
        full_text="birinci sahne",
        provider="fake",
        status=transcript_status,
    )
    session.add(record)
    await session.flush()
    session.add(
        TranscriptSegment(
            transcript_id=record.id,
            segment_index=0,
            start_ms=100,
            end_ms=400,
            text="birinci sahne",
            confidence=0.9,
            speaker_label="konusmaci-1",
        )
    )
    scene_ids: list[UUID] = []
    for index in range(scenes):
        scene = MediaScene(
            business_id=business_id,
            asset_id=asset_id,
            scene_index=index,
            start_ms=index * 500,
            end_ms=(index + 1) * 500,
            duration_ms=500,
            confidence=1.0,
        )
        session.add(scene)
        await session.flush()
        scene_ids.append(scene.id)
    return scene_ids


async def add_understandings(
    session: AsyncSession,
    business_id: UUID,
    asset_id: UUID,
    scene_ids: list[UUID],
    *,
    modes: list[str],
) -> None:
    for scene_id, mode in zip(scene_ids, modes, strict=True):
        visual = mode in {"visual", "visual_and_transcript"}
        session.add(
            MediaSceneUnderstanding(
                business_id=business_id,
                asset_id=asset_id,
                scene_id=scene_id,
                status=SceneUnderstandingStatus.COMPLETED,
                provider="fake-vlm",
                model_name="deterministic",
                summary="Sahne analiz edildi",
                visual_description="Belirleyici gorsel sahne",
                transcript_context="birinci sahne",
                confidence=0.9 if visual else 0.5,
                labels=["sahne"],
                objects=["masa"],
                actions=["konusma"],
                visible_text=[],
                dominant_topics=["tanitim"],
                safety_flags=[],
                quality_signals={
                    "analysis_mode": mode,
                    "visual_input_available": visual,
                    "frame_count": 3 if visual else 0,
                },
            )
        )
    await session.flush()


async def add_job(
    session: AsyncSession,
    business_id: UUID,
    asset_id: UUID,
    *,
    job_type: str,
    status: JobStatus,
    last_error_code: str | None = None,
) -> None:
    session.add(
        BackgroundJob(
            business_id=business_id,
            job_type=job_type,
            resource_type="media_asset",
            resource_id=asset_id,
            status=status,
            timeout_seconds=120,
            attempt_count=1,
            max_attempts=3,
            correlation_id="summary-test",
            last_error_code=last_error_code,
        )
    )
    await session.flush()


def summary_of(client: TestClient, business_id: UUID, asset_id: UUID, subject: str) -> Any:
    response = client.get(
        f"/v1/businesses/{business_id}/media/{asset_id}/processing-summary",
        headers=auth(subject),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_summary_reports_uploaded_step_before_ingest_starts() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = create_business(client, "summary-uploaded")

        async def build(session: AsyncSession) -> UUID:
            asset_id = await seed_asset(session, business_id=business_id)
            await add_job(
                session, business_id, asset_id, job_type="media.ingest", status=JobStatus.QUEUED
            )
            return asset_id

        asset_id = write(build)
        body = summary_of(client, business_id, asset_id, "summary-uploaded")

    assert body["current_step"] == "uploaded"
    assert body["terminal_failure_code"] is None
    assert body["ingest"]["status"] == "pending"
    assert body["ingest"]["job_status"] == "queued"
    assert body["ingest"]["max_attempts"] == 3
    assert body["upload"] == {
        "status": "completed",
        "expected_part_count": 2,
        "expires_at": body["upload"]["expires_at"],
        "completed_at": body["upload"]["completed_at"],
    }
    assert body["scenes"] == [] and body["understandings"] == []
    assert body["transcript"] is None and body["coverage"] is None
    assert body["technical_metadata"] is None


def test_summary_walks_each_step_while_processing_continues() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = create_business(client, "summary-steps")

        async def scanning(session: AsyncSession) -> UUID:
            return await seed_asset(
                session, business_id=business_id, ingest_status=IngestStatus.SCANNING
            )

        asset_id = write(scanning)
        assert summary_of(client, business_id, asset_id, "summary-steps")["current_step"] == (
            "security_check"
        )

        async def ready(session: AsyncSession) -> None:
            await session.execute(
                text("UPDATE media_assets SET ingest_status = 'ready_for_analysis'")
            )
            await add_inspection(session, business_id, asset_id)
            await add_scan(session, business_id, asset_id)

        write(ready)
        assert summary_of(client, business_id, asset_id, "summary-steps")["current_step"] == (
            "technical_analysis"
        )

        async def technical(session: AsyncSession) -> None:
            await add_technical(session, business_id, asset_id)

        write(technical)
        body = summary_of(client, business_id, asset_id, "summary-steps")
        assert body["current_step"] == "scene_speech_analysis"
        assert body["technical"]["status"] == "completed"
        assert body["technical_metadata"]["container_format"] == "mp4"
        assert body["technical_metadata"]["width"] == 1920
        assert body["technical_metadata"]["has_audio"] is True
        assert body["malware_scan_status"] == "clean"
        assert body["detected_content_type"] == "video/mp4"

        async def scene_speech(session: AsyncSession) -> list[UUID]:
            ids = await add_scene_speech(session, business_id, asset_id, scenes=2)
            await add_job(
                session,
                business_id,
                asset_id,
                job_type="media.video_understanding",
                status=JobStatus.RUNNING,
            )
            return ids

        scene_ids = write(scene_speech)
        body = summary_of(client, business_id, asset_id, "summary-steps")
        assert body["current_step"] == "video_understanding"
        assert [scene["scene_index"] for scene in body["scenes"]] == [0, 1]
        assert body["scenes"][1]["start_ms"] == 500 and body["scenes"][1]["end_ms"] == 1_000
        assert body["transcript"]["full_text"] == "birinci sahne"
        assert body["transcript"]["language"] == "tr"
        assert body["transcript_segments"][0]["text"] == "birinci sahne"
        assert body["transcript_segments"][0]["speaker_label"] == "konusmaci-1"
        assert body["coverage"] is None

        async def finish(session: AsyncSession) -> None:
            await add_understandings(
                session,
                business_id,
                asset_id,
                scene_ids,
                modes=["transcript_only", "no_context"],
            )
            await session.execute(
                text("UPDATE jobs SET status = 'succeeded' WHERE job_type = :job_type"),
                {"job_type": "media.video_understanding"},
            )

        write(finish)
        body = summary_of(client, business_id, asset_id, "summary-steps")

    assert body["current_step"] == "completed"
    assert body["terminal_failure_code"] is None
    assert body["video_understanding"]["status"] == "completed"
    assert body["video_understanding"]["job_status"] == "succeeded"
    assert body["coverage"] == {
        "total_scene_count": 2,
        "analyzed_scene_count": 2,
        "skipped_scene_count": 0,
        "coverage": "full",
        "frame_backed_scene_count": 0,
        "transcript_only_scene_count": 1,
        "no_context_scene_count": 1,
    }
    first = body["understandings"][0]
    assert first["analysis_mode"] == "transcript_only"
    assert first["visual_input_available"] is False
    assert first["summary"] == "Sahne analiz edildi"
    assert first["labels"] == ["sahne"] and first["actions"] == ["konusma"]
    assert first["confidence"] == 0.5


def test_summary_reports_partial_coverage_when_scenes_exceed_analyzed_scope() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = create_business(client, "summary-partial")

        async def build(session: AsyncSession) -> UUID:
            asset_id = await seed_asset(
                session, business_id=business_id, ingest_status=IngestStatus.READY_FOR_ANALYSIS
            )
            await add_scan(session, business_id, asset_id)
            await add_technical(session, business_id, asset_id)
            scene_ids = await add_scene_speech(session, business_id, asset_id, scenes=5)
            await add_understandings(
                session,
                business_id,
                asset_id,
                scene_ids[:3],
                modes=["visual_and_transcript", "visual", "no_context"],
            )
            await add_job(
                session,
                business_id,
                asset_id,
                job_type="media.video_understanding",
                status=JobStatus.SUCCEEDED,
            )
            return asset_id

        asset_id = write(build)
        body = summary_of(client, business_id, asset_id, "summary-partial")

    assert body["current_step"] == "completed"
    assert body["coverage"] == {
        "total_scene_count": 5,
        "analyzed_scene_count": 3,
        "skipped_scene_count": 2,
        "coverage": "partial",
        "frame_backed_scene_count": 2,
        "transcript_only_scene_count": 0,
        "no_context_scene_count": 1,
    }
    assert len(body["scenes"]) == 5
    assert len(body["understandings"]) == 3
    # Results follow the video timeline, not the random insert-order tie-break.
    scene_order = {scene["id"]: scene["scene_index"] for scene in body["scenes"]}
    assert [scene_order[value["scene_id"]] for value in body["understandings"]] == [0, 1, 2]
    assert [value["analysis_mode"] for value in body["understandings"]] == [
        "visual_and_transcript",
        "visual",
        "no_context",
    ]


def test_summary_reports_terminal_failure_for_quarantine_and_dead_stages() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        quarantined_business = create_business(client, "summary-quarantine")

        async def quarantined(session: AsyncSession) -> UUID:
            asset_id = await seed_asset(
                session,
                business_id=quarantined_business,
                asset_status=MediaAssetStatus.QUARANTINED,
                ingest_status=IngestStatus.REJECTED,
            )
            await add_scan(
                session,
                quarantined_business,
                asset_id,
                status=MalwareScanStatus.INFECTED,
                safe_error_code="MEDIA_MALWARE_DETECTED",
            )
            return asset_id

        asset_id = write(quarantined)
        body = summary_of(client, quarantined_business, asset_id, "summary-quarantine")
        assert body["current_step"] == "failed"
        assert body["terminal_failure_code"] == "MEDIA_MALWARE_DETECTED"
        assert body["malware_scan_status"] == "infected"

        dead_business = create_business(client, "summary-dead")

        async def dead(session: AsyncSession) -> UUID:
            dead_asset = await seed_asset(
                session, business_id=dead_business, ingest_status=IngestStatus.READY_FOR_ANALYSIS
            )
            await add_scan(session, dead_business, dead_asset)
            await add_technical(
                session,
                dead_business,
                dead_asset,
                status=TechnicalAnalysisStatus.DEAD,
                safe_error_code="MEDIA_PROBE_FAILED",
            )
            await add_job(
                session,
                dead_business,
                dead_asset,
                job_type="media.technical_analysis",
                status=JobStatus.DEAD,
                last_error_code="MEDIA_PROBE_FAILED",
            )
            return dead_asset

        dead_asset = write(dead)
        body = summary_of(client, dead_business, dead_asset, "summary-dead")

    assert body["current_step"] == "failed"
    assert body["terminal_failure_code"] == "MEDIA_PROBE_FAILED"
    assert body["technical"]["status"] == "dead"
    assert body["technical"]["job_status"] == "dead"


def test_retryable_failed_job_is_not_reported_as_terminal() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = create_business(client, "summary-retryable")

        async def build(session: AsyncSession) -> UUID:
            asset_id = await seed_asset(
                session, business_id=business_id, ingest_status=IngestStatus.READY_FOR_ANALYSIS
            )
            await add_scan(session, business_id, asset_id)
            await add_technical(
                session, business_id, asset_id, status=TechnicalAnalysisStatus.FAILED
            )
            await add_job(
                session,
                business_id,
                asset_id,
                job_type="media.technical_analysis",
                status=JobStatus.FAILED,
                last_error_code="MEDIA_PROBE_UNAVAILABLE",
            )
            return asset_id

        asset_id = write(build)
        body = summary_of(client, business_id, asset_id, "summary-retryable")

    assert body["terminal_failure_code"] is None
    assert body["current_step"] == "technical_analysis"
    assert body["technical"]["job_status"] == "failed"


def test_other_tenant_asset_and_non_member_business_are_not_found() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        first_business = create_business(client, "summary-tenant-a")
        second_business = create_business(client, "summary-tenant-b")

        async def build(session: AsyncSession) -> UUID:
            asset_id = await seed_asset(session, business_id=first_business)
            await seed_asset(session, business_id=second_business)
            return asset_id

        first_asset = write(build)

        # Tenant B names its own business but tenant A's asset id.
        crossed = client.get(
            f"/v1/businesses/{second_business}/media/{first_asset}/processing-summary",
            headers=auth("summary-tenant-b"),
        )
        assert crossed.status_code == 404
        assert crossed.json()["code"] == "MEDIA_ASSET_NOT_FOUND"

        # Tenant B names tenant A's business, where it holds no membership.
        foreign = client.get(
            f"/v1/businesses/{first_business}/media/{first_asset}/processing-summary",
            headers=auth("summary-tenant-b"),
        )
        assert foreign.status_code == 404
        assert foreign.json()["code"] == "BUSINESS_NOT_FOUND"

        # Tenant A still reads its own asset, proving the 404s were scope, not absence.
        assert summary_of(client, first_business, first_asset, "summary-tenant-a")["asset"][
            "id"
        ] == str(first_asset)

        # Anonymous access is rejected before any tenant read happens.
        assert (
            client.get(
                f"/v1/businesses/{first_business}/media/{first_asset}/processing-summary"
            ).status_code
            == 401
        )


def test_summary_never_exposes_object_keys_upload_ids_etags_or_raw_signals() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = create_business(client, "summary-secrets")

        async def build(session: AsyncSession) -> UUID:
            asset_id = await seed_asset(
                session, business_id=business_id, ingest_status=IngestStatus.READY_FOR_ANALYSIS
            )
            await add_inspection(session, business_id, asset_id)
            await add_scan(session, business_id, asset_id)
            await add_technical(session, business_id, asset_id)
            scene_ids = await add_scene_speech(session, business_id, asset_id, scenes=2)
            await add_understandings(
                session, business_id, asset_id, scene_ids, modes=["visual", "no_context"]
            )
            await add_job(
                session,
                business_id,
                asset_id,
                job_type="media.video_understanding",
                status=JobStatus.SUCCEEDED,
            )
            return asset_id

        asset_id = write(build)
        response = client.get(
            f"/v1/businesses/{business_id}/media/{asset_id}/processing-summary",
            headers=auth("summary-secrets"),
        )

    assert response.status_code == 200
    raw = response.text
    for marker in (
        OBJECT_KEY_MARKER,
        UPLOAD_ID_MARKER,
        ETAG_MARKER,
        "X-Amz-Signature",
        "storage_object_key",
        "storage_upload_id",
        "storage_etag",
        # Raw provider diagnostics stay out; only authoritative signals are surfaced.
        "quality_signals",
        "frame_count",
    ):
        assert marker not in raw, f"{marker} leaked into the processing summary"
    body = json.loads(raw)
    assert body["detected_content_type"] == "video/mp4"
    assert body["understandings"][0]["analysis_mode"] == "visual"
    assert body["understandings"][0]["visual_input_available"] is True


def test_summary_bounds_collections_with_a_safe_upper_limit() -> None:
    bounded = config(processing_summary_max_items=2)
    with TestClient(create_app(bounded), raise_server_exceptions=False) as client:
        business_id = create_business(client, "summary-limit")

        async def build(session: AsyncSession) -> UUID:
            asset_id = await seed_asset(
                session, business_id=business_id, ingest_status=IngestStatus.READY_FOR_ANALYSIS
            )
            await add_scan(session, business_id, asset_id)
            await add_technical(session, business_id, asset_id)
            await add_scene_speech(session, business_id, asset_id, scenes=4)
            return asset_id

        asset_id = write(build)
        body = summary_of(client, business_id, asset_id, "summary-limit")

    assert len(body["scenes"]) == 2
    assert body["scenes_truncated"] is True
    assert len(body["transcript_segments"]) == 1
    assert body["transcript_segments_truncated"] is False
