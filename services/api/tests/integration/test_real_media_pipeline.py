"""End-to-end Phase 1 exit criterion against a real S3-compatible provider (MinIO).

This is the proof that the worker reads real bytes: a real video is PUT straight to storage,
the worker streams it back through the real materializer, and ffprobe/FFmpeg run on the actual
file — no fixture byte at any step. ASR, frame extraction, and the VLM stay fake (AI-input prep
is out of this slice), but technical analysis and audio extraction run on the real materialized
bytes, and the proxy is materialized from real storage at every downstream stage.

It needs network, credentials, and ffmpeg, so it skips unless a storage endpoint is configured.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.infrastructure.media.fake_scene_speech import FakeSceneDetector, FakeSpeechToText
from app.infrastructure.media.fake_video_understanding import (
    FakeFrameExtractionAdapter,
    FakeVideoUnderstandingAdapter,
)
from app.infrastructure.media.s3_materializer import S3MediaMaterializer
from app.infrastructure.storage.s3 import S3MultipartStorage
from app.main import create_app
from app.modules.media.ingest import MediaIngestService
from app.modules.media.models import (
    MediaScene,
    MediaSceneUnderstanding,
    Transcript,
    TranscriptStatus,
)
from app.modules.media.scene_speech import FFmpegAudioExtractionAdapter, SceneSpeechAnalysisService
from app.modules.media.technical import (
    FFmpegDerivativeAdapter,
    FFprobeAdapter,
    TechnicalAnalysisService,
)
from app.modules.media.video_understanding_service import VideoUnderstandingService
from app.modules.operations.models import BackgroundJob, JobStatus

pytestmark = pytest.mark.integration
KEY = "test-local-identity-signing-key-123"
FFMPEG = "/usr/bin/ffmpeg"

storage_configured = bool(os.getenv("S3_ENDPOINT_URL")) and bool(os.getenv("S3_BUCKET"))
requires_storage = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1" or not storage_configured,
    reason="requires PostgreSQL and an S3-compatible storage endpoint",
)


def config() -> Settings:
    endpoint = os.environ["S3_ENDPOINT_URL"]
    return Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        celery_broker_url=os.environ["CELERY_BROKER_URL"],
        celery_result_backend=os.environ["CELERY_RESULT_BACKEND"],
        local_identity_signing_key=SecretStr(KEY),
        storage_adapter="s3",
        materializer_adapter="s3",
        s3_endpoint_url=endpoint,
        s3_presign_endpoint_url=endpoint,
        s3_region=os.environ.get("S3_REGION", "us-east-1"),
        s3_bucket=os.environ["S3_BUCKET"],
        s3_access_key_id=SecretStr(os.environ["S3_ACCESS_KEY_ID"]),
        s3_secret_access_key=SecretStr(os.environ["S3_SECRET_ACCESS_KEY"]),
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
                    "TRUNCATE credit_ledger, usage_reservations, media_scene_understandings, transcript_segments, transcripts, "
                    "media_scenes, media_derivatives, media_technical_metadata, "
                    "media_technical_analyses, media_malware_scans, media_ingest_inspections, "
                    "job_attempts, jobs, outbox_events, idempotency_keys, audit_logs, "
                    "media_upload_sessions, media_assets, business_members, businesses, "
                    "external_identities, users CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
    if os.getenv("RUN_INTEGRATION_TESTS") == "1" and storage_configured:
        asyncio.run(clear())
    yield
    if os.getenv("RUN_INTEGRATION_TESTS") == "1" and storage_configured:
        asyncio.run(clear())


def _ffmpeg(args: list[str]) -> bool:
    result = subprocess.run(
        [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def make_video(
    path: Path, *, size: str, codec: str, container_args: list[str], audio: bool
) -> bool:
    args = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    args += ["-t", "1", "-pix_fmt", "yuv420p", "-c:v", codec]
    if codec == "libx265":
        args += ["-tag:v", "hvc1"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    args += [*container_args, str(path)]
    return _ffmpeg(args)


def factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(os.environ["DATABASE_URL"]), expire_on_commit=False, class_=AsyncSession
    )


def upload_real_video(
    client: TestClient,
    business_id: str,
    headers: dict[str, str],
    data: bytes,
    *,
    filename: str,
    content_type: str,
) -> str:
    """Create a session, PUT the single part straight to storage, and complete."""

    created = client.post(
        f"/v1/businesses/{business_id}/media/uploads",
        headers=headers,
        json={
            "filename": filename,
            "content_type": content_type,
            "byte_size": len(data),
            "sha256_checksum": hashlib.sha256(data).hexdigest(),
            "part_count": 1,
        },
    )
    assert created.status_code == 201, created.text
    upload = created.json()
    with httpx.Client(timeout=60.0) as put_client:
        instruction = upload["parts"][0]
        response = put_client.put(str(instruction["upload_url"]), content=data)
        assert response.status_code == 200, response.text
        etag = response.headers["etag"].strip('"')
    completed = client.post(
        f"/v1/businesses/{business_id}/media/uploads/{upload['id']}/complete",
        headers=headers | {"Idempotency-Key": str(uuid.uuid4())},
        json={
            "sha256_checksum": hashlib.sha256(data).hexdigest(),
            "parts": [{"part_number": 1, "etag": etag}],
        },
    )
    assert completed.status_code == 200, completed.text
    return str(completed.json()["id"])


async def object_key_for(asset_id: str) -> str:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            return str(
                await connection.scalar(
                    text("SELECT storage_object_key FROM media_assets WHERE id = :id"),
                    {"id": asset_id},
                )
            )
    finally:
        await engine.dispose()


async def run_worker_chain(
    application: FastAPI, *, business_id: str, asset_id: str, content_type: str, workdir: Path
) -> None:
    """Drive ingest → technical → scene/speech → video understanding on real bytes."""

    settings = config()
    storage = cast(S3MultipartStorage, application.state.storage)
    inspector = application.state.content_inspector
    object_key = await object_key_for(asset_id)
    # The fake inspector's verdict must agree with the content type storage actually holds.
    inspector.set_result_for_testing(object_key=object_key, content_type=content_type)
    materializer = S3MediaMaterializer(settings)
    session_factory = factory()

    async with session_factory() as session:
        ingest = await MediaIngestService(
            session, settings, storage, inspector, application.state.malware_scanner
        ).process_next()
        assert ingest is not None and ingest.status == JobStatus.SUCCEEDED, "ingest"

    async with session_factory() as session:
        technical = await TechnicalAnalysisService(
            session,
            settings,
            materializer,
            FFprobeAdapter(settings),
            FFmpegDerivativeAdapter(settings),
            storage,
        ).process_next(workdir=workdir)
        assert technical is not None and technical.status == JobStatus.SUCCEEDED, "technical"

    async with session_factory() as session:
        scene_speech = await SceneSpeechAnalysisService(
            session,
            settings,
            materializer,
            FakeSceneDetector(),
            FFmpegAudioExtractionAdapter(settings),
            FakeSpeechToText(),
            storage,
        ).process_next(workdir=workdir)
        assert scene_speech is not None and scene_speech.status == JobStatus.SUCCEEDED, "scene"

    async with session_factory() as session:
        # The materializer still streams the real proxy from storage; frame extraction and the
        # VLM stay fake — AI-input prep is out of this slice (see W09 scope), and real frame
        # extraction is covered by test_video_understanding_flow.
        understanding = VideoUnderstandingService(
            session,
            settings,
            FakeFrameExtractionAdapter(settings),
            FakeVideoUnderstandingAdapter(settings),
            materializer,
        )
        claimed = await understanding.claim_next()
        assert claimed is not None, "no video-understanding job scheduled"
        finished = await understanding.process_claimed(
            business_id=claimed.business_id, job_id=claimed.id, workdir=workdir
        )
        assert finished.status == JobStatus.SUCCEEDED, "video understanding"


async def durable_results(asset_id: str) -> tuple[int, TranscriptStatus | None, int]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with AsyncSession(engine) as session:
            scene_count = len(
                list(
                    await session.scalars(select(MediaScene).where(MediaScene.asset_id == asset_id))
                )
            )
            transcript = await session.scalar(
                select(Transcript).where(Transcript.asset_id == asset_id)
            )
            understanding_count = len(
                list(
                    await session.scalars(
                        select(MediaSceneUnderstanding).where(
                            MediaSceneUnderstanding.asset_id == asset_id
                        )
                    )
                )
            )
            return scene_count, transcript.status if transcript else None, understanding_count
    finally:
        await engine.dispose()


@requires_storage
def test_phase_1_exit_criterion_with_three_real_videos(tmp_path: Path) -> None:
    landscape = tmp_path / "landscape.mov"
    vertical = tmp_path / "vertical.mp4"
    voiced = tmp_path / "voiced.mp4"

    # One .mov/HEVC (proves the QuickTime container + HEVC codec path), one vertical, one voiced.
    hevc_ok = make_video(
        landscape, size="320x240", codec="libx265", container_args=["-f", "mov"], audio=False
    )
    if not hevc_ok:
        # Some ffmpeg builds ship without libx265; fall back to H.264 in a .mov container so the
        # QuickTime-container path is still proven end to end (the codec gate is unit-covered).
        assert make_video(
            landscape, size="320x240", codec="libx264", container_args=["-f", "mov"], audio=False
        ), "cannot encode a .mov fixture"
    assert make_video(vertical, size="240x426", codec="libx264", container_args=[], audio=False), (
        "cannot encode the vertical fixture"
    )
    assert make_video(voiced, size="426x240", codec="libx264", container_args=[], audio=True), (
        "cannot encode the voiced fixture"
    )

    videos = [
        (landscape, "clip.mov", "video/quicktime", TranscriptStatus.NO_SPEECH),
        (vertical, "vertical.mp4", "video/mp4", TranscriptStatus.NO_SPEECH),
        (voiced, "voiced.mp4", "video/mp4", TranscriptStatus.COMPLETED),
    ]

    owner = auth("pipeline-owner", "pipeline-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Pipeline", "timezone": "UTC"}
        ).json()["id"]

        for source, filename, content_type, expected_transcript in videos:
            data = source.read_bytes()
            asset_id = upload_real_video(
                client, business_id, owner, data, filename=filename, content_type=content_type
            )
            asyncio.run(
                run_worker_chain(
                    application,
                    business_id=business_id,
                    asset_id=asset_id,
                    content_type=content_type,
                    workdir=tmp_path,
                )
            )
            scene_count, transcript_status, understanding_count = asyncio.run(
                durable_results(asset_id)
            )
            assert scene_count >= 1, (filename, "scenes")
            assert transcript_status == expected_transcript, (filename, "transcript")
            assert understanding_count == scene_count, (filename, "understanding")

            summary = client.get(
                f"/v1/businesses/{business_id}/media/{asset_id}/processing-summary", headers=owner
            ).json()
            assert summary["current_step"] == "completed", (filename, summary["current_step"])
            assert summary["terminal_failure_code"] is None
            assert summary["coverage"] is not None
            assert len(summary["scenes"]) >= 1
            assert summary["technical_metadata"] is not None
            # No fixture sentinel bytes: the technical metadata came from the real file.
            assert summary["technical_metadata"]["file_size"] == len(data)

    # No worker media job is left unfinished across all three videos.
    async def unfinished() -> int:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with AsyncSession(engine) as session:
                return len(
                    list(
                        await session.scalars(
                            select(BackgroundJob).where(
                                BackgroundJob.status.notin_([JobStatus.SUCCEEDED, JobStatus.DEAD])
                            )
                        )
                    )
                )
        finally:
            await engine.dispose()

    assert asyncio.run(unfinished()) == 0
