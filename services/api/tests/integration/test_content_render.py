"""Slice 2A's proof: a playable MP4 built from two real cuts of a real analyzed asset.

Everything in this file is real except the analysis providers that predate this slice: real
bytes uploaded to real object storage, real ffprobe metadata, real FFmpeg encoding, real
subtitle burn-in, and the output streamed back out of storage and probed. **No AI provider is
called at any point** — the render service has no port that could reach one (see
`tests/unit/test_render_port.py`), and the captions come from transcript rows that already
exist in the database.

It needs network, credentials and ffmpeg, so it skips unless a storage endpoint is configured.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

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
from app.infrastructure.media.s3_materializer import S3MediaMaterializer
from app.infrastructure.render.ffmpeg import FFmpegRenderAdapter
from app.infrastructure.storage.s3 import S3MultipartStorage
from app.main import create_app
from app.modules.content.models import RenderOutput, RenderStatus, RenderTrigger
from app.modules.content.render import AiDisclosureState, ProvenanceState
from app.modules.content.render_service import ContentRenderService
from app.modules.media.ingest import MediaIngestService
from app.modules.media.scene_speech import (
    FFmpegAudioExtractionAdapter,
    SceneSpeechAnalysisService,
    SpeechResult,
    TranscriptCandidate,
)
from app.modules.media.technical import (
    FFmpegDerivativeAdapter,
    FFprobeAdapter,
    TechnicalAnalysisService,
)
from app.modules.operations.models import BackgroundJob, JobStatus

pytestmark = pytest.mark.integration
KEY = "test-local-identity-signing-key-123"
FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"

# The glyphs the work order names, plus a colon and a percent sequence that would break a
# naively built drawtext filter.
TURKISH_OVERLAY = "Fiyat: 149,90 TL — ığşçöüİĞŞÇÖÜ %{pts}"
TURKISH_CAPTION = "Günaydın, çiğ köfte ve şalgam hazır"

storage_configured = bool(os.getenv("S3_ENDPOINT_URL")) and bool(os.getenv("S3_BUCKET"))
requires_storage = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1" or not storage_configured,
    reason="requires PostgreSQL and an S3-compatible storage endpoint",
)
requires_ffmpeg = pytest.mark.skipif(not Path(FFMPEG).exists(), reason="requires the ffmpeg binary")


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
        render_adapter="ffmpeg",
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
                    "TRUNCATE credit_ledger, usage_reservations, render_outputs, content_timelines, media_scene_understandings, "
                    "transcript_segments, transcripts, media_scenes, media_derivatives, "
                    "media_technical_metadata, media_technical_analyses, media_malware_scans, "
                    "media_ingest_inspections, brand_assets, target_audiences, approved_ctas, "
                    "approved_claims, forbidden_claims, campaign_offer_products, campaign_offers, "
                    "product_prices, products, brand_profiles, job_attempts, jobs, outbox_events, "
                    "idempotency_keys, audit_logs, media_upload_sessions, media_assets, "
                    "business_members, businesses, external_identities, users CASCADE"
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


def factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(os.environ["DATABASE_URL"]), expire_on_commit=False, class_=AsyncSession
    )


def make_video(path: Path) -> None:
    """An 8-second 720x1280 source with audio — tall enough to clear the resolution floor."""

    result = subprocess.run(
        [
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=720x1280:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "8",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def make_logo(path: Path) -> None:
    result = subprocess.run(
        [
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=256x256",
            "-frames:v",
            "1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return cast(dict[str, Any], json.loads(result.stdout))


def upload(
    client: TestClient,
    business_id: str,
    headers: dict[str, str],
    data: bytes,
    *,
    filename: str,
    content_type: str,
) -> str:
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
    session = created.json()
    with httpx.Client(timeout=60.0) as put_client:
        response = put_client.put(str(session["parts"][0]["upload_url"]), content=data)
        assert response.status_code == 200, response.text
        etag = response.headers["etag"].strip('"')
    completed = client.post(
        f"/v1/businesses/{business_id}/media/uploads/{session['id']}/complete",
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


async def analyze(application: FastAPI, *, asset_id: str, content_type: str, workdir: Path) -> None:
    """Drive ingest → technical → scene/speech on the real bytes, exactly as W09 does."""

    settings = config()
    storage = cast(S3MultipartStorage, application.state.storage)
    inspector = application.state.content_inspector
    inspector.set_result_for_testing(
        object_key=await object_key_for(asset_id), content_type=content_type
    )
    materializer = S3MediaMaterializer(settings)
    session_factory = factory()

    async with session_factory() as session:
        ingest = await MediaIngestService(
            session, settings, storage, inspector, application.state.malware_scanner
        ).process_next()
        assert ingest is not None and ingest.status == JobStatus.SUCCEEDED, "ingest"

    if content_type != "video/mp4":
        return

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

    speech = FakeSpeechToText()
    # Turkish transcript rows so the burned-in captions exercise real glyph shaping. These are
    # persisted rows, not a provider call — the render never asks anything to produce them.
    speech.set_result_for_testing(
        SpeechResult(
            language="tr",
            provider="fake-asr",
            segments=(
                TranscriptCandidate(0, 2_500, TURKISH_CAPTION, 0.95),
                TranscriptCandidate(4_000, 6_500, "Şubemize bekleriz", 0.92),
            ),
        )
    )
    async with session_factory() as session:
        scene = await SceneSpeechAnalysisService(
            session,
            settings,
            materializer,
            FakeSceneDetector(),
            FFmpegAudioExtractionAdapter(settings),
            speech,
            storage,
        ).process_next(workdir=workdir)
        assert scene is not None and scene.status == JobStatus.SUCCEEDED, "scene/speech"


def timeline_document(asset_id: str, logo_asset_id: str) -> dict[str, Any]:
    """Two real cuts of the analyzed asset, a Turkish overlay, a logo, and captions."""

    return {
        "version": "1.0",
        "canvas": {"width": 1080, "height": 1920, "fps": 30, "duration_ms": 5_000},
        "video_tracks": [
            {
                "track": 1,
                "clips": [
                    {
                        "asset_id": asset_id,
                        "source_start_ms": 500,
                        "source_end_ms": 3_000,
                        "timeline_start_ms": 0,
                        "crop_mode": "smart_cover",
                        "transition_out": "cut",
                    },
                    {
                        "asset_id": asset_id,
                        "source_start_ms": 4_000,
                        "source_end_ms": 6_500,
                        "timeline_start_ms": 2_500,
                        "crop_mode": "blur_pad",
                        "transition_out": "cut",
                    },
                ],
            }
        ],
        "audio_tracks": [
            {"type": "original", "asset_id": None, "gain_db": 0, "duck_under_voice": False}
        ],
        "overlays": [
            {
                "type": "text",
                "text_source": "literal",
                "text": TURKISH_OVERLAY,
                "reference_id": None,
                "anchor": "bottom_center",
                "style_id": "brand-caption-v1",
                "start_ms": 0,
                "end_ms": 4_000,
                "safe_area": True,
            },
            {
                "type": "logo",
                "asset_id": logo_asset_id,
                "anchor": "top_right",
                "style_id": "logo-small",
                "start_ms": 0,
                "end_ms": 5_000,
                "safe_area": True,
            },
        ],
        "captions": {"enabled": True, "source": "transcript", "style_id": "brand-caption-v1"},
    }


async def drain_render(workdir: Path) -> BackgroundJob | None:
    settings = config()
    async with factory()() as session:
        return await ContentRenderService(
            session,
            settings,
            S3MediaMaterializer(settings),
            FFmpegRenderAdapter(settings),
            S3MultipartStorage(settings),
        ).process_next(workdir=workdir)


async def load_render(render_id: str) -> RenderOutput:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with AsyncSession(engine) as session:
            record = await session.scalar(
                select(RenderOutput).where(RenderOutput.id == uuid.UUID(render_id))
            )
            assert record is not None
            return record
    finally:
        await engine.dispose()


async def fetch_object(object_key: str, destination: Path) -> Path:
    """Stream a stored object back out so the output can be probed as a real file."""

    return await S3MediaMaterializer(config()).materialize(
        object_key=object_key, workdir=destination
    )


@requires_ffmpeg
def test_turkish_glyphs_resolve_against_the_bundled_font(tmp_path: Path) -> None:
    """Criterion 3: the Turkish alphabet renders, it is not silently dropped.

    A missing glyph is not an FFmpeg error — `drawtext` warns and draws nothing where the
    character should be, so a zero exit code alone proves nothing. This asserts both: the run
    succeeds *and* the diagnostics contain no glyph-resolution complaint. The hostile
    characters (`:` and `%{...}`) also prove the `textfile` + `expansion=none` route, since
    either would break a filter string that interpolated the text directly.
    """

    settings = Settings(
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
    )
    overlay_file = tmp_path / "overlay.txt"
    overlay_file.write_text(TURKISH_OVERLAY, encoding="utf-8")
    # The bytes survive the round trip to disk that the adapter performs.
    assert overlay_file.read_text(encoding="utf-8") == TURKISH_OVERLAY

    output = tmp_path / "glyphs.mp4"
    result = subprocess.run(
        [
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=1080x1920:rate=30",
            "-t",
            "1",
            "-vf",
            f"drawtext=fontfile={settings.render_font_file}:textfile={overlay_file.name}"
            ":expansion=none:fontsize=64:fontcolor=white:x=40:y=800",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Glyph" not in result.stderr, result.stderr
    assert "not found" not in result.stderr.lower(), result.stderr
    assert output.stat().st_size > 0


@requires_storage
def test_real_render_of_two_scenes_with_turkish_overlay_and_logo(tmp_path: Path) -> None:
    source, logo = tmp_path / "source.mp4", tmp_path / "logo.png"
    make_video(source)
    make_logo(logo)
    owner = auth("render-owner", "render-owner@example.com")

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Render", "timezone": "UTC"}
        ).json()["id"]

        asset_id = upload(
            client,
            business_id,
            owner,
            source.read_bytes(),
            filename="source.mp4",
            content_type="video/mp4",
        )
        asyncio.run(
            analyze(application, asset_id=asset_id, content_type="video/mp4", workdir=tmp_path)
        )
        logo_asset_id = upload(
            client,
            business_id,
            owner,
            logo.read_bytes(),
            filename="logo.png",
            content_type="image/png",
        )
        asyncio.run(
            analyze(application, asset_id=logo_asset_id, content_type="image/png", workdir=tmp_path)
        )

        # Register the logo on the brand so the §18.3 logo rule can accept it.
        brand = client.put(
            f"/v1/businesses/{business_id}/brand",
            headers=owner,
            json={
                "display_name": "Render Test",
                "tone": "sıcak",
                "communication_language": "tr",
                "default_currency": "TRY",
                "assets": [{"role": "logo", "media_asset_id": logo_asset_id}],
                "forbidden_claims": ["mucize"],
            },
        )
        assert brand.status_code == 200, brand.text

        created = client.post(
            f"/v1/businesses/{business_id}/content/timelines",
            headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
            json={
                "profile": "instagram_reels_1080x1920",
                "document": timeline_document(asset_id, logo_asset_id),
            },
        )
        assert created.status_code == 201, created.text
        timeline_id = created.json()["id"]
        assert created.json()["revision"] == 1

        requested = client.post(
            f"/v1/businesses/{business_id}/content/timelines/{timeline_id}/renders",
            headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
            json={"profile": "instagram_reels_1080x1920"},
        )
        assert requested.status_code == 202, requested.text
        render_id = requested.json()["id"]
        assert requested.json()["trigger"] == RenderTrigger.INITIAL.value
        assert requested.json()["consumes_entitlement"] is True

        job = asyncio.run(drain_render(tmp_path))
        assert job is not None and job.status == JobStatus.SUCCEEDED, "render job"

        record = asyncio.run(load_render(render_id))
        assert record.status == RenderStatus.SUCCEEDED, record.failure_code
        # Disclosure and provenance are recorded from the first render, not back-filled.
        assert record.ai_disclosure_state is AiDisclosureState.NONE
        assert record.provenance_state is ProvenanceState.STRIPPED_PENDING_REATTACH
        assert record.provenance_manifest_key is None
        assert record.master_object_key and record.preview_object_key
        assert record.thumbnail_object_key

        # The output is real: pull it back out of storage and probe the actual bytes.
        master = asyncio.run(fetch_object(record.master_object_key, tmp_path / "out"))
        probed = probe(master)
        video = next(s for s in probed["streams"] if s["codec_type"] == "video")
        assert (video["width"], video["height"]) == (1080, 1920)
        assert video["codec_name"] == "h264"
        assert any(s["codec_type"] == "audio" for s in probed["streams"])
        # Two 2.5s cuts, so the master is ~5s. Encoder padding keeps this a range, not equality.
        assert 4.0 <= float(probed["format"]["duration"]) <= 6.0
        assert record.width == 1080 and record.height == 1920

        preview = asyncio.run(fetch_object(record.preview_object_key, tmp_path / "prev"))
        preview_video = next(s for s in probe(preview)["streams"] if s["codec_type"] == "video")
        assert (preview_video["width"], preview_video["height"]) == (540, 960)

        # No render job is left unfinished, and nothing queued a second one.
        assert asyncio.run(drain_render(tmp_path)) is None


@requires_storage
def test_patch_re_renders_without_consuming_a_new_entitlement(tmp_path: Path) -> None:
    """A parametric revision is a re-render, not a new generation (plan §2, PRD §12.8)."""

    source, logo = tmp_path / "source.mp4", tmp_path / "logo.png"
    make_video(source)
    make_logo(logo)
    owner = auth("patch-owner", "patch-owner@example.com")

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Patch", "timezone": "UTC"}
        ).json()["id"]
        asset_id = upload(
            client,
            business_id,
            owner,
            source.read_bytes(),
            filename="s.mp4",
            content_type="video/mp4",
        )
        asyncio.run(
            analyze(application, asset_id=asset_id, content_type="video/mp4", workdir=tmp_path)
        )
        logo_asset_id = upload(
            client,
            business_id,
            owner,
            logo.read_bytes(),
            filename="l.png",
            content_type="image/png",
        )
        asyncio.run(
            analyze(application, asset_id=logo_asset_id, content_type="image/png", workdir=tmp_path)
        )
        client.put(
            f"/v1/businesses/{business_id}/brand",
            headers=owner,
            json={
                "display_name": "Patch Test",
                "tone": "sıcak",
                "communication_language": "tr",
                "default_currency": "TRY",
                "assets": [{"role": "logo", "media_asset_id": logo_asset_id}],
            },
        )
        timeline_id = client.post(
            f"/v1/businesses/{business_id}/content/timelines",
            headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
            json={
                "profile": "instagram_reels_1080x1920",
                "document": timeline_document(asset_id, logo_asset_id),
            },
        ).json()["id"]

        patched = client.post(
            f"/v1/businesses/{business_id}/content/timelines/{timeline_id}/patch",
            headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
            json={
                "profile": "instagram_reels_1080x1920",
                "operations": [
                    {
                        "op": "set_overlay_text",
                        "index": 0,
                        "text_source": "literal",
                        "text": "Güncellenmiş başlık ĞÜŞİÖÇ",
                    },
                    {"op": "set_overlay_anchor", "index": 0, "anchor": "top_center"},
                ],
            },
        )
        assert patched.status_code == 201, patched.text
        revised = patched.json()
        assert revised["revision"] == 2
        assert revised["parent_id"] == timeline_id
        assert revised["root_id"] == timeline_id
        assert revised["document"]["overlays"][0]["anchor"] == "top_center"
        assert revised["document"]["overlays"][0]["text"] == "Güncellenmiş başlık ĞÜŞİÖÇ"

        requested = client.post(
            f"/v1/businesses/{business_id}/content/timelines/{revised['id']}/renders",
            headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
            json={"profile": "instagram_reels_1080x1920"},
        )
        assert requested.status_code == 202, requested.text
        # The whole point: a revision re-render draws on the revision quota, not a fresh right.
        assert requested.json()["trigger"] == RenderTrigger.REVISION.value
        assert requested.json()["consumes_entitlement"] is False

        job = asyncio.run(drain_render(tmp_path))
        assert job is not None and job.status == JobStatus.SUCCEEDED, "revision render"
        record = asyncio.run(load_render(requested.json()["id"]))
        assert record.status == RenderStatus.SUCCEEDED, record.failure_code
        assert record.consumes_entitlement is False
        # The original revision is still there — history is not overwritten by an edit.
        original = client.get(
            f"/v1/businesses/{business_id}/content/timelines/{timeline_id}", headers=owner
        )
        assert original.status_code == 200
        assert original.json()["document"]["overlays"][0]["anchor"] == "bottom_center"


@requires_storage
@requires_ffmpeg
def test_patch_idempotency_compares_the_whole_request_body(tmp_path: Path) -> None:
    """The W11 finding: the same key with different text used to replay the first revision.

    The fingerprint stored only the operation *count*, so "one operation" matched "one
    operation" and a second, different edit returned `201` with the first revision's document.
    The caller had every reason to believe its correction landed.

    The four cases share one setup on purpose — the setup is a real encode plus the full
    ingest/technical/scene analysis of the bytes, and running it four times would cost minutes
    to prove nothing extra. Each case is independent of the others' outcomes and is asserted on
    its own.
    """

    source, logo = tmp_path / "source.mp4", tmp_path / "logo.png"
    make_video(source)
    make_logo(logo)
    owner = auth("fingerprint-owner", "fingerprint-owner@example.com")

    async def revision_count(root_id: str) -> int:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with engine.connect() as connection:
                return int(
                    cast(
                        int,
                        await connection.scalar(
                            text("SELECT count(*) FROM content_timelines WHERE root_id = :root"),
                            {"root": root_id},
                        ),
                    )
                )
        finally:
            await engine.dispose()

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Fingerprint", "timezone": "UTC"}
        ).json()["id"]
        asset_id = upload(
            client,
            business_id,
            owner,
            source.read_bytes(),
            filename="s.mp4",
            content_type="video/mp4",
        )
        asyncio.run(
            analyze(application, asset_id=asset_id, content_type="video/mp4", workdir=tmp_path)
        )
        logo_asset_id = upload(
            client,
            business_id,
            owner,
            logo.read_bytes(),
            filename="l.png",
            content_type="image/png",
        )
        asyncio.run(
            analyze(application, asset_id=logo_asset_id, content_type="image/png", workdir=tmp_path)
        )
        client.put(
            f"/v1/businesses/{business_id}/brand",
            headers=owner,
            json={
                "display_name": "Fingerprint Test",
                "tone": "sıcak",
                "communication_language": "tr",
                "default_currency": "TRY",
                "assets": [{"role": "logo", "media_asset_id": logo_asset_id}],
            },
        )
        timeline_id = client.post(
            f"/v1/businesses/{business_id}/content/timelines",
            headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
            json={
                "profile": "instagram_reels_1080x1920",
                "document": timeline_document(asset_id, logo_asset_id),
            },
        ).json()["id"]
        url = f"/v1/businesses/{business_id}/content/timelines/{timeline_id}/patch"

        def body(headline: str) -> dict[str, Any]:
            return {
                "profile": "instagram_reels_1080x1920",
                "operations": [
                    {
                        "op": "set_overlay_text",
                        "index": 0,
                        "text_source": "literal",
                        "text": headline,
                    }
                ],
            }

        key = str(uuid.uuid4())
        first = client.post(url, headers=owner | {"Idempotency-Key": key}, json=body("ilk metin"))
        assert first.status_code == 201, first.text
        assert first.json()["revision"] == 2

        # 1. Same key, same operation count, different text: a different request.
        conflicting = client.post(
            url, headers=owner | {"Idempotency-Key": key}, json=body("ikinci farkli metin")
        )
        assert conflicting.status_code == 409, conflicting.text
        assert conflicting.json()["code"] == "IDEMPOTENCY_CONFLICT"
        assert asyncio.run(revision_count(timeline_id)) == 2, "the refused patch wrote a revision"

        # 2. Same key, byte-identical body: the stored result, not a second revision.
        replayed = client.post(
            url, headers=owner | {"Idempotency-Key": key}, json=body("ilk metin")
        )
        assert replayed.status_code == 201, replayed.text
        assert replayed.json()["id"] == first.json()["id"]
        assert replayed.json()["revision"] == 2
        assert asyncio.run(revision_count(timeline_id)) == 2

        # 3. A different key with the same body is a different request and must do the work.
        repeated = client.post(
            url, headers=owner | {"Idempotency-Key": str(uuid.uuid4())}, json=body("ilk metin")
        )
        assert repeated.status_code == 201, repeated.text
        assert repeated.json()["id"] != first.json()["id"]
        assert repeated.json()["revision"] == 3
        assert asyncio.run(revision_count(timeline_id)) == 3

        # 4. Canonicality: field order differs and an optional field is spelled out rather than
        # omitted. Hashing the raw body would call this a conflict; it is the same edit.
        equivalent = client.post(
            url,
            headers=owner | {"Idempotency-Key": key},
            json={
                "operations": [
                    {
                        "text": "ilk metin",
                        "reference_id": None,
                        "text_source": "literal",
                        "index": 0,
                        "op": "set_overlay_text",
                    }
                ],
                "profile": "instagram_reels_1080x1920",
            },
        )
        assert equivalent.status_code == 201, equivalent.text
        assert equivalent.json()["id"] == first.json()["id"]
        assert asyncio.run(revision_count(timeline_id)) == 3


@requires_storage
@requires_ffmpeg
def test_an_editor_can_author_and_render_while_a_viewer_and_an_approver_cannot(
    tmp_path: Path,
) -> None:
    """PRD §4 says an editor produces content. Until W14 it could write a script and no more.

    W11 bound timeline writes to `business.update` and W13 bound script generation to
    `content.generate`, so the role that exists to produce content could compose the words and
    then be refused the timeline they were for. This is the aligned matrix at the HTTP boundary,
    where the refusal a real client would see is the thing worth asserting.
    """

    source, logo = tmp_path / "source.mp4", tmp_path / "logo.png"
    make_video(source)
    make_logo(logo)
    owner = auth("role-owner", "role-owner@example.com")
    editor = auth("role-editor", "role-editor@example.com")
    viewer = auth("role-viewer", "role-viewer@example.com")
    approver = auth("role-approver", "role-approver@example.com")

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Roles", "timezone": "UTC"}
        ).json()["id"]
        asset_id = upload(
            client,
            business_id,
            owner,
            source.read_bytes(),
            filename="s.mp4",
            content_type="video/mp4",
        )
        asyncio.run(
            analyze(application, asset_id=asset_id, content_type="video/mp4", workdir=tmp_path)
        )
        logo_asset_id = upload(
            client,
            business_id,
            owner,
            logo.read_bytes(),
            filename="l.png",
            content_type="image/png",
        )
        asyncio.run(
            analyze(application, asset_id=logo_asset_id, content_type="image/png", workdir=tmp_path)
        )
        client.put(
            f"/v1/businesses/{business_id}/brand",
            headers=owner,
            json={
                "display_name": "Roles Test",
                "tone": "sıcak",
                "communication_language": "tr",
                "default_currency": "TRY",
                "assets": [{"role": "logo", "media_asset_id": logo_asset_id}],
            },
        )
        # A member is added by email, so each account has to exist before it can be invited.
        for headers, email, role in (
            (editor, "role-editor@example.com", "editor"),
            (viewer, "role-viewer@example.com", "viewer"),
            (approver, "role-approver@example.com", "approver"),
        ):
            assert client.get("/v1/businesses", headers=headers).status_code == 200
            added = client.post(
                f"/v1/businesses/{business_id}/members",
                headers=owner,
                json={"email": email, "role": role},
            )
            assert added.status_code == 201, added.text

        document = timeline_document(asset_id, logo_asset_id)
        created = client.post(
            f"/v1/businesses/{business_id}/content/timelines",
            headers=editor | {"Idempotency-Key": str(uuid.uuid4())},
            json={"profile": "instagram_reels_1080x1920", "document": document},
        )
        assert created.status_code == 201, created.text
        timeline_id = created.json()["id"]

        patched = client.post(
            f"/v1/businesses/{business_id}/content/timelines/{timeline_id}/patch",
            headers=editor | {"Idempotency-Key": str(uuid.uuid4())},
            json={
                "profile": "instagram_reels_1080x1920",
                "operations": [{"op": "set_overlay_anchor", "index": 0, "anchor": "top_center"}],
            },
        )
        assert patched.status_code == 201, patched.text

        rendered = client.post(
            f"/v1/businesses/{business_id}/content/timelines/{timeline_id}/renders",
            headers=editor | {"Idempotency-Key": str(uuid.uuid4())},
            json={"profile": "instagram_reels_1080x1920"},
        )
        assert rendered.status_code == 202, rendered.text

        for headers in (viewer, approver):
            refusals = [
                client.post(
                    f"/v1/businesses/{business_id}/content/timelines",
                    headers=headers | {"Idempotency-Key": str(uuid.uuid4())},
                    json={"profile": "instagram_reels_1080x1920", "document": document},
                ),
                client.post(
                    f"/v1/businesses/{business_id}/content/timelines/{timeline_id}/patch",
                    headers=headers | {"Idempotency-Key": str(uuid.uuid4())},
                    json={
                        "profile": "instagram_reels_1080x1920",
                        "operations": [
                            {"op": "set_overlay_anchor", "index": 0, "anchor": "top_center"}
                        ],
                    },
                ),
                client.post(
                    f"/v1/businesses/{business_id}/content/timelines/{timeline_id}/renders",
                    headers=headers | {"Idempotency-Key": str(uuid.uuid4())},
                    json={"profile": "instagram_reels_1080x1920"},
                ),
            ]
            for refused in refusals:
                assert refused.status_code == 403, refused.text
                assert refused.json()["code"] == "INSUFFICIENT_PERMISSION"

        # A viewer still reads; an approver holds no permission at all, not even read.
        assert (
            client.get(
                f"/v1/businesses/{business_id}/content/timelines/{timeline_id}", headers=viewer
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/v1/businesses/{business_id}/content/timelines/{timeline_id}", headers=approver
            ).status_code
            == 403
        )


@requires_storage
def test_another_tenant_cannot_place_or_read_this_tenants_work(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    make_video(source)
    owner = auth("iso-owner", "iso-owner@example.com")
    intruder = auth("iso-intruder", "iso-intruder@example.com")

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        victim_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Victim", "timezone": "UTC"}
        ).json()["id"]
        attacker_id = client.post(
            "/v1/businesses", headers=intruder, json={"name": "Attacker", "timezone": "UTC"}
        ).json()["id"]
        asset_id = upload(
            client,
            victim_id,
            owner,
            source.read_bytes(),
            filename="s.mp4",
            content_type="video/mp4",
        )
        asyncio.run(
            analyze(application, asset_id=asset_id, content_type="video/mp4", workdir=tmp_path)
        )

        # The attacker names the victim's asset inside their own business's timeline.
        document = timeline_document(asset_id, asset_id)
        document["overlays"] = [document["overlays"][0]]
        stolen = client.post(
            f"/v1/businesses/{attacker_id}/content/timelines",
            headers=intruder | {"Idempotency-Key": str(uuid.uuid4())},
            json={"profile": "instagram_reels_1080x1920", "document": document},
        )
        assert stolen.status_code == 422, stolen.text
        codes = {issue["code"] for issue in stolen.json()["meta"]["issues"]}
        assert "TIMELINE_ASSET_NOT_ACCESSIBLE" in codes

        # And cannot reach the victim's business at all.
        blind = client.post(
            f"/v1/businesses/{victim_id}/content/timelines",
            headers=intruder | {"Idempotency-Key": str(uuid.uuid4())},
            json={"profile": "instagram_reels_1080x1920", "document": document},
        )
        assert blind.status_code == 404, blind.text


@requires_storage
def test_validation_rejects_before_any_render_is_scheduled(tmp_path: Path) -> None:
    """Every §18.3 rejection is a documented code and leaves no job behind."""

    source = tmp_path / "source.mp4"
    make_video(source)
    owner = auth("reject-owner", "reject-owner@example.com")

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Reject", "timezone": "UTC"}
        ).json()["id"]
        asset_id = upload(
            client,
            business_id,
            owner,
            source.read_bytes(),
            filename="s.mp4",
            content_type="video/mp4",
        )
        asyncio.run(
            analyze(application, asset_id=asset_id, content_type="video/mp4", workdir=tmp_path)
        )
        client.put(
            f"/v1/businesses/{business_id}/brand",
            headers=owner,
            json={
                "display_name": "Reject Test",
                "tone": "sıcak",
                "communication_language": "tr",
                "default_currency": "TRY",
                "forbidden_claims": ["mucize"],
            },
        )

        def submit(mutate: Any) -> set[str]:
            document = timeline_document(asset_id, asset_id)
            document["overlays"] = [document["overlays"][0]]
            mutate(document)
            response = client.post(
                f"/v1/businesses/{business_id}/content/timelines",
                headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
                json={"profile": "instagram_reels_1080x1920", "document": document},
            )
            assert response.status_code == 422, response.text
            body = response.json()
            if body["code"] == "TIMELINE_SCHEMA_INVALID":
                return {body["meta"]["issue"]}
            return {issue["code"] for issue in body["meta"]["issues"]}

        def beyond_source(document: dict[str, Any]) -> None:
            document["video_tracks"][0]["clips"][0]["source_end_ms"] = 30_000
            document["video_tracks"][0]["clips"][0]["source_start_ms"] = 20_000

        def forbidden_word(document: dict[str, Any]) -> None:
            document["overlays"][0]["text"] = "Mucize ürün"

        def outside_safe_area(document: dict[str, Any]) -> None:
            document["overlays"][0]["text"] = "ç" * 150

        def duplicate_clip(document: dict[str, Any]) -> None:
            document["video_tracks"][0]["clips"][1] = dict(
                document["video_tracks"][0]["clips"][0], timeline_start_ms=2_500
            )

        def invented_price(document: dict[str, Any]) -> None:
            document["overlays"][0]["text_source"] = "verified_product.price"
            document["overlays"][0]["reference_id"] = str(uuid.uuid4())
            document["overlays"][0]["text"] = None

        assert "TIMELINE_CLIP_RANGE_INVALID" in submit(beyond_source)
        assert "TIMELINE_FORBIDDEN_TERM" in submit(forbidden_word)
        assert "TIMELINE_TEXT_OUTSIDE_SAFE_AREA" in submit(outside_safe_area)
        assert "TIMELINE_DUPLICATE_CLIP" in submit(duplicate_clip)
        assert "TIMELINE_VERIFIED_FIELD_NOT_FOUND" in submit(invented_price)

        # A rejected timeline scheduled nothing: no render row, no durable job.
        async def counts() -> tuple[int, int]:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with AsyncSession(engine) as session:
                    renders = len(list(await session.scalars(select(RenderOutput))))
                    jobs = len(
                        list(
                            await session.scalars(
                                select(BackgroundJob).where(
                                    BackgroundJob.job_type == "content.render"
                                )
                            )
                        )
                    )
                    return renders, jobs
            finally:
                await engine.dispose()

        assert asyncio.run(counts()) == (0, 0)


@requires_storage
def test_idempotent_replay_and_no_credential_leak(tmp_path: Path) -> None:
    """Repeating a render request returns the same record, and no secret escapes anywhere.

    The response body, the audit trail and any failure detail are all checked together: these
    are the three surfaces where a signed URL or an access key would end up if the module ever
    started handing them around instead of storing object keys.
    """

    source, logo = tmp_path / "source.mp4", tmp_path / "logo.png"
    make_video(source)
    make_logo(logo)
    owner = auth("idem-owner", "idem-owner@example.com")

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        application = cast(FastAPI, client.app)
        business_id = client.post(
            "/v1/businesses", headers=owner, json={"name": "Idem", "timezone": "UTC"}
        ).json()["id"]
        asset_id = upload(
            client,
            business_id,
            owner,
            source.read_bytes(),
            filename="s.mp4",
            content_type="video/mp4",
        )
        asyncio.run(
            analyze(application, asset_id=asset_id, content_type="video/mp4", workdir=tmp_path)
        )
        logo_asset_id = upload(
            client,
            business_id,
            owner,
            logo.read_bytes(),
            filename="l.png",
            content_type="image/png",
        )
        asyncio.run(
            analyze(application, asset_id=logo_asset_id, content_type="image/png", workdir=tmp_path)
        )
        client.put(
            f"/v1/businesses/{business_id}/brand",
            headers=owner,
            json={
                "display_name": "Idem Test",
                "tone": "sıcak",
                "communication_language": "tr",
                "default_currency": "TRY",
                "assets": [{"role": "logo", "media_asset_id": logo_asset_id}],
            },
        )
        timeline_id = client.post(
            f"/v1/businesses/{business_id}/content/timelines",
            headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
            json={
                "profile": "instagram_reels_1080x1920",
                "document": timeline_document(asset_id, logo_asset_id),
            },
        ).json()["id"]

        key = str(uuid.uuid4())
        body = {"profile": "instagram_reels_1080x1920"}
        first = client.post(
            f"/v1/businesses/{business_id}/content/timelines/{timeline_id}/renders",
            headers=owner | {"Idempotency-Key": key},
            json=body,
        )
        second = client.post(
            f"/v1/businesses/{business_id}/content/timelines/{timeline_id}/renders",
            headers=owner | {"Idempotency-Key": key},
            json=body,
        )
        assert first.status_code == 202 and second.status_code == 202
        # One render, one job — the repeat did not queue a second encode.
        assert first.json()["id"] == second.json()["id"]

        async def render_job_count() -> int:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with AsyncSession(engine) as session:
                    return len(
                        list(
                            await session.scalars(
                                select(BackgroundJob).where(
                                    BackgroundJob.job_type == "content.render"
                                )
                            )
                        )
                    )
            finally:
                await engine.dispose()

        assert asyncio.run(render_job_count()) == 1

        job = asyncio.run(drain_render(tmp_path))
        assert job is not None and job.status == JobStatus.SUCCEEDED

        fetched = client.get(
            f"/v1/businesses/{business_id}/content/renders/{first.json()['id']}", headers=owner
        )
        assert fetched.status_code == 200
        # Object keys, never a signed URL: the response carries no signature material.
        assert "X-Amz-Signature" not in fetched.text
        assert "socialpilot_local_only" not in fetched.text
        assert fetched.json()["master_object_key"].startswith(f"tenant/{business_id}/renders/")

        async def audit_text() -> str:
            engine = create_async_engine(os.environ["DATABASE_URL"])
            try:
                async with engine.connect() as connection:
                    rows = await connection.execute(
                        text("SELECT action, metadata::text FROM audit_logs")
                    )
                    return " ".join(str(cell) for row in rows for cell in row)
            finally:
                await engine.dispose()

        recorded = asyncio.run(audit_text())
        assert "content.render.requested" in recorded
        for secret in ("X-Amz-Signature", "socialpilot_local_only", "Authorization"):
            assert secret not in recorded
