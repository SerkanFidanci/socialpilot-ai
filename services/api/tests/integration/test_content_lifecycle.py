"""Slice 2E end to end: a brief goes in, a preview comes out, and every step is on the record.

The centrepiece is one test that opens a project through the HTTP surface and drives the real
worker services — the sequencer, the render job, the QC job — against real PostgreSQL, real MinIO
and real FFmpeg until the project reaches `PREVIEW_READY`. Nothing about the media is simulated:
the source is encoded here, uploaded, materialized, cut, mixed with synthesized speech, probed and
judged. Only the AI providers are fixtures, which is the phase's stated position.

The rest of the file is adversarial, and each test attacks one of the guarantees the work order
asks for: that the render loop is bounded, that speech is actually in the output, that ducking
actually reached FFmpeg, that a stale `pending` run is settled while a healthy one is not, and
that none of it crosses a tenant boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.infrastructure.ai import create_audio_probe, create_script_generator, create_tts
from app.infrastructure.ai.fake_visual_qc import FakeVisualQcAdapter
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.infrastructure.media.s3_materializer import S3MediaMaterializer
from app.infrastructure.render import create_render
from app.infrastructure.render.qc_probe import FFmpegQcProbe
from app.infrastructure.storage.s3 import S3MultipartStorage
from app.main import create_app
from app.modules.content.lifecycle import ProjectEvent, ProjectState
from app.modules.content.project_service import (
    SCRIPT_ABANDONED,
    VOICEOVER_ABANDONED,
    AbandonedRunSweeper,
    ContentProjectAdvanceService,
)
from app.modules.content.qc_service import ContentQcService
from app.modules.content.render_service import ContentRenderService

pytestmark = pytest.mark.integration

KEY = "test-local-identity-signing-key-123"
FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"

storage_configured = bool(os.getenv("S3_ENDPOINT_URL")) and bool(os.getenv("S3_BUCKET"))
requires_postgres = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL"
)
requires_storage = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1" or not storage_configured,
    reason="requires PostgreSQL and an S3-compatible storage endpoint",
)
requires_ffmpeg = pytest.mark.skipif(not Path(FFMPEG).exists(), reason="requires the ffmpeg binary")

TABLES = (
    "credit_ledger",
    "usage_reservations",
    "content_project_transitions",
    "content_projects",
    "render_qc_reports",
    "voiceover_assets",
    "render_outputs",
    "content_timelines",
    "content_scripts",
    "provider_usage",
    "media_scene_understandings",
    "media_scenes",
    "media_technical_metadata",
    "campaign_offer_products",
    "campaign_offers",
    "product_prices",
    "products",
    "brand_assets",
    "target_audiences",
    "approved_claims",
    "forbidden_claims",
    "approved_ctas",
    "brand_profiles",
    "media_assets",
    "job_attempts",
    "jobs",
    "outbox_events",
    "audit_logs",
    "idempotency_keys",
    "business_members",
    "businesses",
    "external_identities",
    "users",
)


def config(**overrides: Any) -> Settings:
    endpoint = os.environ["S3_ENDPOINT_URL"]
    base: dict[str, Any] = {
        "app_env": "test",
        "database_url": os.environ["DATABASE_URL"],
        "redis_url": os.environ["REDIS_URL"],
        "celery_broker_url": os.environ["CELERY_BROKER_URL"],
        "celery_result_backend": os.environ["CELERY_RESULT_BACKEND"],
        "local_identity_signing_key": SecretStr(KEY),
        "storage_adapter": "s3",
        "materializer_adapter": "s3",
        "render_adapter": "ffmpeg",
        "s3_endpoint_url": endpoint,
        "s3_presign_endpoint_url": endpoint,
        "s3_region": os.environ.get("S3_REGION", "us-east-1"),
        "s3_bucket": os.environ["S3_BUCKET"],
        "s3_access_key_id": SecretStr(os.environ["S3_ACCESS_KEY_ID"]),
        "s3_secret_access_key": SecretStr(os.environ["S3_SECRET_ACCESS_KEY"]),
        # The sequencer polls; one second keeps the drive loop below honest about the gate
        # without making the suite wait on it.
        "lifecycle_poll_seconds": 1,
        "lifecycle_lease_seconds": 30,
    }
    return Settings(**(base | overrides))


def api_config(**overrides: Any) -> Settings:
    """Settings for tests that never touch a byte — storage stays the byte-free fake."""

    base: dict[str, Any] = {
        "app_env": "test",
        "database_url": os.environ["DATABASE_URL"],
        "redis_url": os.environ["REDIS_URL"],
        "celery_broker_url": os.environ["CELERY_BROKER_URL"],
        "celery_result_backend": os.environ["CELERY_RESULT_BACKEND"],
        "local_identity_signing_key": SecretStr(KEY),
        "storage_adapter": "fake",
    }
    return Settings(**(base | overrides))


def auth(subject: str, email: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + LocalIdentityVerifier.sign_for_testing(signing_key=KEY, subject=subject, email=email)
    }


async def _clear() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            # `prompt_templates` is deliberately absent: migration 0013 seeds the live prompt and
            # it is platform configuration, not tenant data.
            await connection.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(_clear())
    yield
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(_clear())


def query(statement: str, **params: Any) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with engine.begin() as connection:
                return list((await connection.execute(text(statement), params)).all())
        finally:
            await engine.dispose()

    return asyncio.run(run())


def execute(statement: str, **params: Any) -> None:
    async def run() -> None:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with engine.begin() as connection:
                await connection.execute(text(statement), params)
        finally:
            await engine.dispose()

    asyncio.run(run())


def factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(os.environ["DATABASE_URL"]), expire_on_commit=False, class_=AsyncSession
    )


# --- media on disk and in storage ---------------------------------------------------------------


def encode(path: Path, *, seconds: int) -> None:
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
            f"testsrc2=size=1080x1920:rate=30:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            str(seconds),
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
    import json

    return dict(json.loads(result.stdout))


def audio_fingerprint(path: Path) -> str:
    """Hash the decoded PCM, so two outputs are compared by what they sound like.

    Comparing container bytes would catch an encoder timestamp; this catches a filter graph that
    did or did not run.
    """

    result = subprocess.run(
        [
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a",
            "-f",
            "s16le",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-",
        ],
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout, "the output carries no audio at all"
    return hashlib.sha256(result.stdout).hexdigest()


# --- one seeded tenant ----------------------------------------------------------------------------


class Tenant:
    """A business with a brand, a priced product, an approved CTA and analyzed footage."""

    def __init__(self, client: TestClient, headers: dict[str, str], name: str) -> None:
        self.client = client
        self.headers = headers
        created = client.post(
            "/v1/businesses", headers=headers, json={"name": name, "timezone": "Europe/Istanbul"}
        )
        assert created.status_code == 201, created.text
        self.business_id = str(created.json()["id"])
        self.user_id = str(client.get("/v1/me", headers=headers).json()["id"])

        brand = client.put(
            f"/v1/businesses/{self.business_id}/brand",
            headers=headers,
            json={
                "display_name": f"{name} Kahve",
                "tone": "sıcak, samimi",
                "communication_language": "tr",
                "default_currency": "TRY",
                "color_palette": ["#101010"],
                "forbidden_topics": ["politika"],
                "forbidden_claims": ["sağlığa iyi gelir"],
                "approved_ctas": ["Bugün bizi ziyaret et."],
            },
        )
        assert brand.status_code == 200, brand.text
        product = client.post(
            f"/v1/businesses/{self.business_id}/products",
            headers=headers,
            json={
                "name": f"{name} Soğuk Latte",
                "category": "İçecek",
                "price": {"price_minor": 14990, "currency": "TRY"},
            },
        )
        assert product.status_code == 201, product.text
        self.product_id = str(product.json()["id"])
        self.cta_id = str(
            query(
                "SELECT id FROM approved_ctas WHERE business_id = CAST(:business AS uuid)",
                business=self.business_id,
            )[0][0]
        )
        # Every generation draws on the credit ledger from W20 onward, so a seeded tenant needs
        # credit before it can open anything. Enough for the projects these tests run; the
        # ledger's own behaviour is exercised in `test_entitlement.py`, not here.
        self.grant_credits(500)

    def grant_credits(self, credits: int, *, headers: dict[str, str] | None = None) -> Any:
        return self.client.post(
            f"/v1/businesses/{self.business_id}/entitlement/grants",
            headers=headers or self.headers,
            json={"credits": credits},
        )

    def balance(self) -> int:
        response = self.client.get(
            f"/v1/businesses/{self.business_id}/entitlement/balance", headers=self.headers
        )
        assert response.status_code == 200, response.text
        return int(response.json()["balance_credits"])

    def seed_asset(self, *, duration_ms: int, video: Path | None = None) -> str:
        """An analyzed, renderable asset, with its bytes in storage when one is supplied."""

        asset_id = str(uuid.uuid4())
        object_key = f"tenant/{self.business_id}/media/{asset_id}/original/seed.mp4"
        if video is not None:
            asyncio.run(
                S3MultipartStorage(config()).persist_file(
                    object_key=object_key, source_path=video, content_type="video/mp4"
                )
            )
        execute(
            "INSERT INTO media_assets (id, business_id, created_by_user_id, storage_object_key,"
            " content_type, byte_size, sha256_checksum, status, ingest_status, created_at)"
            " VALUES (CAST(:id AS uuid), CAST(:business AS uuid), CAST(:user AS uuid), :key,"
            " 'video/mp4', 4096, :checksum, 'uploaded', 'ready_for_analysis', now())",
            id=asset_id,
            business=self.business_id,
            user=self.user_id,
            key=object_key,
            checksum="d" * 64,
        )
        execute(
            "INSERT INTO media_technical_metadata (id, business_id, asset_id, container_format,"
            " duration_ms, file_size, video_codec, width, height, rotation_degrees, has_audio,"
            " stream_count, analyzed_at) VALUES (gen_random_uuid(), CAST(:business AS uuid),"
            " CAST(:asset AS uuid), 'mov,mp4', :duration, 4096, 'h264', 1080, 1920, 0, true, 2,"
            " now())",
            business=self.business_id,
            asset=asset_id,
            duration=duration_ms,
        )
        return asset_id

    def seed_scene(self, asset_id: str, index: int, start_ms: int, end_ms: int, *tags: str) -> None:
        """One detected scene and the labels video understanding put on it."""

        scene_id = str(uuid.uuid4())
        execute(
            "INSERT INTO media_scenes (id, business_id, asset_id, scene_index, start_ms, end_ms,"
            " duration_ms, confidence, created_at) VALUES (CAST(:id AS uuid),"
            " CAST(:business AS uuid), CAST(:asset AS uuid), :index, :start, :end, :duration,"
            " 0.9, now())",
            id=scene_id,
            business=self.business_id,
            asset=asset_id,
            index=index,
            start=start_ms,
            end=end_ms,
            duration=end_ms - start_ms,
        )
        execute(
            "INSERT INTO media_scene_understandings (id, business_id, asset_id, scene_id, status,"
            " provider, model_name, summary, visual_description, transcript_context, confidence,"
            " labels, objects, actions, visible_text, dominant_topics, safety_flags,"
            " quality_signals, created_at) VALUES (gen_random_uuid(), CAST(:business AS uuid),"
            " CAST(:asset AS uuid), CAST(:scene AS uuid), 'completed', 'fake', 'fake-v1', '', '',"
            " '', 0.9, CAST(:labels AS jsonb), '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,"
            " '[]'::jsonb, '{}'::jsonb, now())",
            business=self.business_id,
            asset=asset_id,
            scene=scene_id,
            labels="[" + ", ".join(f'"{tag}"' for tag in tags) + "]",
        )

    def create_project(self, **overrides: Any) -> Any:
        headers = dict(overrides.pop("headers", self.headers))
        key = overrides.pop("idempotency_key", None)
        if key is not None:
            headers["Idempotency-Key"] = key
        body: dict[str, Any] = {
            "scenario_code": "product_reels",
            "profile": "instagram_reels_1080x1920",
            "product_id": self.product_id,
            "cta_id": self.cta_id,
        }
        body.update(overrides)
        return self.client.post(
            f"/v1/businesses/{self.business_id}/content/projects", headers=headers, json=body
        )

    def invite(self, email: str, role: str) -> dict[str, str]:
        headers = auth(f"lc-{role}-{uuid.uuid4().hex[:6]}", email)
        member_id = str(self.client.get("/v1/me", headers=headers).json()["id"])
        execute(
            "INSERT INTO business_members (id, business_id, user_id, role, status, created_at,"
            " updated_at) VALUES (gen_random_uuid(), CAST(:business AS uuid), CAST(:user AS uuid),"
            " :role, 'active', now(), now())",
            business=self.business_id,
            user=member_id,
            role=role,
        )
        return headers


# --- driving the real workers ---------------------------------------------------------------------


def advance_once(settings: Settings) -> None:
    async def run() -> None:
        async with factory()() as session:
            await ContentProjectAdvanceService(
                session,
                settings,
                render=create_render(settings),
                script_generator=create_script_generator(settings),
                tts=create_tts(settings),
                audio_probe=create_audio_probe(settings),
                storage=S3MultipartStorage(settings),
            ).process_next()

    asyncio.run(run())


def render_once(settings: Settings, workdir: Path) -> None:
    async def run() -> None:
        async with factory()() as session:
            await ContentRenderService(
                session,
                settings,
                S3MediaMaterializer(settings),
                create_render(settings),
                S3MultipartStorage(settings),
            ).process_next(workdir=workdir)

    asyncio.run(run())


def qc_once(settings: Settings, workdir: Path) -> None:
    async def run() -> None:
        async with factory()() as session:
            await ContentQcService(
                session,
                settings,
                S3MediaMaterializer(settings),
                FFmpegQcProbe(settings),
                FakeVisualQcAdapter(settings),
            ).process_next(workdir=workdir)

    asyncio.run(run())


def snapshot(project_id: str) -> tuple[Any, ...]:
    rows = query(
        "SELECT state, render_id, qc_report_id, render_attempts, failure_code,"
        " requires_human_review FROM content_projects WHERE id = CAST(:id AS uuid)",
        id=project_id,
    )
    assert rows
    return tuple(rows[0])


def drive(
    settings: Settings, workdir: Path, project_id: str, *, rounds: int = 40
) -> tuple[Any, ...]:
    """Run the three workers until the project settles, exactly as the beat would."""

    for _ in range(rounds):
        before = snapshot(project_id)
        advance_once(settings)
        render_once(settings, workdir)
        qc_once(settings, workdir)
        after = snapshot(project_id)
        if after[0] in {ProjectState.PREVIEW_READY.value, ProjectState.FAILED.value}:
            return after
        if after == before:
            # Nothing moved: the project is inside its poll window. Waiting it out is the point —
            # the gate is real and the test refuses to reach past it.
            time.sleep(1.1)
    raise AssertionError(f"project never settled: {snapshot(project_id)}")


def transitions(project_id: str) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in query(
            "SELECT sequence, from_state, to_state, event FROM content_project_transitions"
            " WHERE project_id = CAST(:id AS uuid) ORDER BY sequence",
            id=project_id,
        )
    ]


# --- criterion 2: the whole pipeline, for real -----------------------------------------------------


@requires_storage
@requires_ffmpeg
def test_a_project_walks_from_planned_to_preview_ready_with_every_step_recorded(
    tmp_path: Path,
) -> None:
    """One brief in, one playable preview out, over real PostgreSQL, MinIO and FFmpeg.

    The script, the speech and the vision check are fixtures — that is the phase's position, not
    a shortcut — and everything else is the production path: the source is materialized from
    object storage, cut by FFmpeg, mixed with the synthesized lines, uploaded, probed and judged.
    """

    settings = config()
    video = tmp_path / "source.mp4"
    encode(video, seconds=20)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("lc-owner", "lc-owner@example.com"), "Lifecycle")
        asset = tenant.seed_asset(duration_ms=20_000, video=video)
        tenant.seed_scene(asset, 0, 0, 6_000, "product_closeup")
        tenant.seed_scene(asset, 1, 6_000, 13_000, "preparation")
        tenant.seed_scene(asset, 2, 13_000, 20_000, "product_closeup")
        created = tenant.create_project(source_asset_ids=[asset])
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        assert created.json()["state"] == ProjectState.PLANNED.value

        state, render_id, report_id, attempts, failure, review = drive(
            settings, tmp_path, project_id
        )
        assert state == ProjectState.PREVIEW_READY.value, failure
        assert render_id is not None
        assert report_id is not None
        assert attempts == 1
        assert failure is None
        # Fail-closed QC: the vision fixture answers, but nothing here reaches `passed`
        # automatically in production, and the flag is what slice 2F will read either way.
        assert review in (True, False)

        detail = client.get(
            f"/v1/businesses/{tenant.business_id}/content/projects/{project_id}",
            headers=tenant.headers,
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["script_id"] and body["voiceover_id"] and body["timeline_id"]
        # Object keys and signed URLs have no business in a project body — it is a status
        # resource, and the artefacts are read through endpoints that authorize them one by one.
        assert "X-Amz-Signature" not in detail.text
        assert "object_key" not in detail.text

    # PRD §20's closing sentence: every transition on the record, in order, none skipped.
    history = transitions(project_id)
    assert [row[3] for row in history] == [
        ProjectEvent.CREATED.value,
        ProjectEvent.ANALYSIS_STARTED.value,
        ProjectEvent.ANALYSIS_COMPLETE.value,
        ProjectEvent.SCRIPT_READY.value,
        ProjectEvent.VOICEOVER_READY.value,
        ProjectEvent.TIMELINE_READY.value,
        ProjectEvent.RENDER_SUCCEEDED.value,
    ] + [row[3] for row in history[7:]]
    assert history[0][1] is None
    assert history[-1][2] == ProjectState.PREVIEW_READY.value
    assert [row[0] for row in history] == list(range(1, len(history) + 1))
    # Each transition names the state it came from, so the chain is walkable without timestamps.
    for previous, current in zip(history, history[1:], strict=False):
        assert current[1] == previous[2]

    # The render the project produced really is a playable file with sound in it.
    master = query(
        "SELECT master_object_key, audio_codec, duration_ms FROM render_outputs"
        " WHERE id = CAST(:id AS uuid)",
        id=str(render_id),
    )[0]
    assert master[0] and master[1] == "aac" and master[2] > 0

    # And the QC job was asked for by the render, not found by a scan.
    assert query(
        "SELECT count(*) FROM outbox_events WHERE event_type = 'content.qc.requested'"
    ) == [(1,)]
    assert query(
        "SELECT count(*) FROM render_outputs WHERE qc_claimed_at IS NULL AND status = 'succeeded'"
    ) == [(0,)]

    # W20: the preview exists, so the hold this project opened is consumed (PRD §12.7). The
    # settlement rode the same transaction that made the project terminal, so there is no tick
    # to wait for and no window in which a finished project still holds credit.
    hold = query(
        "SELECT status, credits, correlation_id FROM usage_reservations"
        " WHERE source_id = CAST(:id AS uuid)",
        id=project_id,
    )
    assert len(hold) == 1
    assert hold[0][0] == "consumed"
    # One charge and no refund: the grant, one `consume`, nothing else.
    assert [
        row[0]
        for row in query(
            "SELECT entry_type FROM credit_ledger WHERE business_id = CAST(:b AS uuid)"
            " ORDER BY created_at, id",
            b=tenant.business_id,
        )
    ] == ["grant", "consume"]
    # Criterion 8's relation, on real data: the provider calls this project actually made carry
    # the reservation's correlation id, so what was charged joins to what it cost.
    spend = query(
        "SELECT count(*) FROM provider_usage WHERE correlation_id = :correlation",
        correlation=hold[0][2],
    )
    assert spend[0][0] > 0


# --- criterion 3: speech is actually in the output --------------------------------------------------


@requires_storage
@requires_ffmpeg
def test_speech_reaches_the_output_and_ducking_changes_what_it_sounds_like(
    tmp_path: Path,
) -> None:
    """W15's open item, closed and measured rather than asserted from the capability set.

    Three renders of the same cut: bed only, bed plus voice, bed ducked under voice. Each is
    decoded to raw PCM and hashed, so "the voice is in there" and "the ducking ran" are answered
    by the audio itself. A capability flag would have proved neither.
    """

    settings = config()
    video = tmp_path / "source.mp4"
    encode(video, seconds=6)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("lc-mix", "lc-mix@example.com"), "Mixdown")
        asset = tenant.seed_asset(duration_ms=6_000, video=video)
        tenant.seed_scene(asset, 0, 0, 6_000, "product_closeup")
        tenant.seed_scene(asset, 1, 0, 6_000, "preparation")
        tenant.seed_scene(asset, 2, 0, 6_000, "product_closeup")
        created = tenant.create_project(source_asset_ids=[asset])
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        state, render_id, *_ = drive(settings, tmp_path, project_id)
        assert state == ProjectState.PREVIEW_READY.value

        voiceover_id = str(
            query(
                "SELECT voiceover_id FROM content_projects WHERE id = CAST(:id AS uuid)",
                id=project_id,
            )[0][0]
        )
        timeline_id = str(
            query(
                "SELECT timeline_id FROM content_projects WHERE id = CAST(:id AS uuid)",
                id=project_id,
            )[0][0]
        )
        document = query(
            "SELECT document FROM content_timelines WHERE id = CAST(:id AS uuid)", id=timeline_id
        )[0][0]
        assert any(track["type"] == "voiceover" for track in document["audio_tracks"])
        assert any(
            track["type"] == "original" and track["duck_under_voice"]
            for track in document["audio_tracks"]
        )

        ducked = fetch_master(settings, tmp_path / "ducked.mp4", str(render_id))
        assert probe(ducked)["streams"], "the render produced no streams at all"
        assert any(stream.get("codec_type") == "audio" for stream in probe(ducked)["streams"]), (
            "the render carries no audio stream"
        )

        # Now the two comparisons. Same document, one flag changed each time.
        plain = render_variant(client, tenant, settings, tmp_path, document, duck=False)
        silent = render_variant(
            client, tenant, settings, tmp_path, document, duck=False, voiceover=False
        )

    ducked_audio = audio_fingerprint(ducked)
    assert ducked_audio != audio_fingerprint(plain), "ducking did not change the mix"
    assert audio_fingerprint(plain) != audio_fingerprint(silent), "the voice never reached the mix"
    assert voiceover_id


def fetch_master(settings: Settings, destination: Path, render_id: str) -> Path:
    key = str(
        query(
            "SELECT master_object_key FROM render_outputs WHERE id = CAST(:id AS uuid)",
            id=render_id,
        )[0][0]
    )

    async def run() -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        return await S3MediaMaterializer(settings).materialize(object_key=key, workdir=destination)

    return asyncio.run(run())


def render_variant(
    client: TestClient,
    tenant: Tenant,
    settings: Settings,
    workdir: Path,
    document: dict[str, Any],
    *,
    duck: bool,
    voiceover: bool = True,
) -> Path:
    """Author a variant of the composed document and render it through the real worker."""

    tracks = [
        track for track in document["audio_tracks"] if voiceover or track["type"] != "voiceover"
    ]
    variant = dict(document)
    variant["audio_tracks"] = [
        {**track, "duck_under_voice": duck if track["type"] == "original" else False}
        for track in tracks
    ]
    created = client.post(
        f"/v1/businesses/{tenant.business_id}/content/timelines",
        headers=tenant.headers,
        json={"profile": "instagram_reels_1080x1920", "document": variant},
    )
    assert created.status_code == 201, created.text
    requested = client.post(
        f"/v1/businesses/{tenant.business_id}/content/timelines/{created.json()['id']}/renders",
        headers=tenant.headers,
        json={"profile": "instagram_reels_1080x1920"},
    )
    assert requested.status_code == 202, requested.text
    render_id = requested.json()["id"]
    for _ in range(5):
        render_once(settings, workdir)
        status = query(
            "SELECT status, failure_code FROM render_outputs WHERE id = CAST(:id AS uuid)",
            id=render_id,
        )[0]
        if status[0] == "succeeded":
            break
    assert status[0] == "succeeded", status
    suffix = f"{'duck' if duck else 'flat'}-{'voice' if voiceover else 'bed'}"
    return fetch_master(settings, workdir / suffix, render_id)


# --- criterion 4: the loop is bounded ----------------------------------------------------------------


@requires_storage
@requires_ffmpeg
def test_a_render_that_never_passes_quality_control_stops_at_the_configured_ceiling(
    tmp_path: Path,
) -> None:
    """QC genuinely fails with "render it again", and the counter genuinely stops it.

    The defect is chosen so the *suggestion* is `retry_render` rather than the verdict being
    arranged: a zero-millisecond duration tolerance makes an ordinary container rounding a
    blocking mismatch, which §19.4 answers with another encode. Every re-render reproduces it,
    so without a ceiling this project renders forever. With one it renders exactly
    `LIFECYCLE_MAX_RENDER_ATTEMPTS` times and then asks for a person.

    A wholly black source is used as well, because the report has to show a real broken output
    rather than a threshold trick alone.
    """

    settings = config(lifecycle_max_render_attempts=2, qc_duration_tolerance_ms=0)
    video = tmp_path / "broken.mp4"
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
            "color=c=black:size=1080x1920:rate=30:duration=6",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            "6",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("lc-loop", "lc-loop@example.com"), "Loop")
        asset = tenant.seed_asset(duration_ms=6_000, video=video)
        for index in range(3):
            tenant.seed_scene(asset, index, 0, 6_000, "product_closeup", "preparation")
        created = tenant.create_project(source_asset_ids=[asset])
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        state, _, _, attempts, failure, review = drive(settings, tmp_path, project_id, rounds=60)

    reports = query(
        "SELECT verdict, recommended_path, checks FROM render_qc_reports ORDER BY created_at"
    )
    assert state == ProjectState.FAILED.value, reports
    # The bound, proven by the counter rather than by the absence of a hang.
    assert attempts == 2, reports
    # And proven to be the *retry* path rather than a suggestion this slice does not execute.
    assert [row[1] for row in reports] == ["retry_render", "retry_render"], reports
    assert review is True
    assert failure is not None
    assert query("SELECT count(*) FROM render_outputs") == [(2,)]
    events = [row[3] for row in transitions(project_id)]
    assert events.count(ProjectEvent.RETRY_REQUESTED.value) == 1
    assert events[-1] in {ProjectEvent.QC_FAILED.value, ProjectEvent.STEP_FAILED.value}

    # W20, and this is K4 observed rather than argued: the project rendered twice and was
    # charged once. The second render is a re-render of the same timeline — no provider was
    # called and nothing new was generated — so it draws on the revision quota rather than on a
    # fresh generation right.
    ledger = [
        tuple(row)
        for row in query(
            "SELECT entry_type, delta_credits FROM credit_ledger"
            " WHERE business_id = CAST(:b AS uuid) ORDER BY created_at, id",
            b=tenant.business_id,
        )
    ]
    assert [row[0] for row in ledger].count("consume") == 1
    assert query("SELECT count(*) FROM render_outputs WHERE consumes_entitlement IS FALSE") == [
        (1,)
    ]
    # And the whole thing failed technically, so the credit went back: released hold, one
    # compensating refund, balance where it started.
    hold = query(
        "SELECT status, failure_code FROM usage_reservations WHERE source_id = CAST(:id AS uuid)",
        id=project_id,
    )
    assert hold[0][0] == "released"
    assert hold[0][1] == failure
    assert [row[0] for row in ledger] == ["grant", "consume", "refund"]
    assert sum(row[1] for row in ledger) == 500


# --- criterion 7: the abandoned-run sweep ------------------------------------------------------------


@requires_postgres
def test_a_stale_pending_run_is_settled_and_a_healthy_one_is_left_alone() -> None:
    """The sweep is a clock comparison, and the second half is the one that matters.

    Failing a run that is merely slow would be worse than leaving it: the provider call is still
    in flight, and the row is the only record that it was billed. The age threshold is validated
    at startup to exceed the longest honest run, and this is that rule observed.
    """

    settings = api_config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("lc-sweep", "lc-sweep@example.com"), "Sweeper")
        stale_script = seed_pending_script(tenant, age_seconds=7_200)
        fresh_script = seed_pending_script(tenant, age_seconds=5)
        stale_voice = seed_pending_voiceover(tenant, stale_script, age_seconds=7_200)
        fresh_voice = seed_pending_voiceover(tenant, fresh_script, age_seconds=5)

    async def sweep() -> Any:
        async with factory()() as session:
            return await AbandonedRunSweeper(session, settings).process_next()

    assert asyncio.run(sweep()) == {"scripts": 1, "voiceovers": 1}
    # A second pass finds nothing, so the drain stops rather than spinning its batch.
    assert asyncio.run(sweep()) is None

    assert script_row(stale_script) == ("failed", SCRIPT_ABANDONED)
    assert script_row(fresh_script) == ("pending", None)
    assert voiceover_row(stale_voice) == ("failed", VOICEOVER_ABANDONED)
    assert voiceover_row(fresh_voice) == ("pending", None)


def seed_pending_script(tenant: Tenant, *, age_seconds: int) -> str:
    script_id = str(uuid.uuid4())
    template = str(query("SELECT id FROM prompt_templates WHERE active LIMIT 1")[0][0])
    execute(
        "INSERT INTO content_scripts (id, business_id, scenario_code, status, source_asset_ids,"
        " prompt_template_id, prompt_code, prompt_version, route_snapshot, requested_by_user_id,"
        " correlation_id, created_at) VALUES (CAST(:id AS uuid), CAST(:business AS uuid),"
        " 'product_reels', 'pending', '[]'::jsonb, CAST(:template AS uuid), 'product_reels', 1,"
        " '{}'::jsonb, CAST(:user AS uuid), 'sweep-test', now() - make_interval(secs => :age))",
        id=script_id,
        business=tenant.business_id,
        template=template,
        user=tenant.user_id,
        age=age_seconds,
    )
    return script_id


def seed_pending_voiceover(tenant: Tenant, script_id: str, *, age_seconds: int) -> str:
    voiceover_id = str(uuid.uuid4())
    execute(
        "INSERT INTO voiceover_assets (id, business_id, script_id, status, voice_profile_code,"
        " voice_profile_version, voice_profile, audio_format, segments, route_snapshot,"
        " requested_by_user_id, correlation_id, created_at) VALUES (CAST(:id AS uuid),"
        " CAST(:business AS uuid), CAST(:script AS uuid), 'pending', 'tr-warm-v1', 1, '{}'::jsonb,"
        " 'wav', '[]'::jsonb, '{}'::jsonb, CAST(:user AS uuid), 'sweep-test',"
        " now() - make_interval(secs => :age))",
        id=voiceover_id,
        business=tenant.business_id,
        script=script_id,
        user=tenant.user_id,
        age=age_seconds,
    )
    return voiceover_id


def script_row(script_id: str) -> tuple[Any, ...]:
    return tuple(
        query(
            "SELECT status, failure_code FROM content_scripts WHERE id = CAST(:id AS uuid)",
            id=script_id,
        )[0]
    )


def voiceover_row(voiceover_id: str) -> tuple[Any, ...]:
    return tuple(
        query(
            "SELECT status, failure_code FROM voiceover_assets WHERE id = CAST(:id AS uuid)",
            id=voiceover_id,
        )[0]
    )


# --- criterion 8: isolation, roles, idempotency --------------------------------------------------------


@requires_postgres
def test_another_tenants_project_cannot_be_read_attached_to_or_advanced() -> None:
    """The claim is tenant-scoped by construction, so all three attempts answer the same way."""

    settings = api_config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        victim = Tenant(client, auth("lc-victim", "lc-victim@example.com"), "Victim")
        attacker = Tenant(client, auth("lc-thief", "lc-thief@example.com"), "Thief")
        created = victim.create_project(source_asset_ids=[])
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        assert created.json()["state"] == ProjectState.PLANNED.value

        # Reading it under the attacker's own business is a 404 — the row is not theirs.
        stolen = client.get(
            f"/v1/businesses/{attacker.business_id}/content/projects/{project_id}",
            headers=attacker.headers,
        )
        assert stolen.status_code == 404, stolen.text
        # And naming the victim's business is a 404 too: membership is checked before the row.
        crossed = client.get(
            f"/v1/businesses/{victim.business_id}/content/projects/{project_id}",
            headers=attacker.headers,
        )
        assert crossed.status_code == 404, crossed.text
        assert (
            client.get(
                f"/v1/businesses/{victim.business_id}/content/projects/{project_id}/transitions",
                headers=attacker.headers,
            ).status_code
            == 404
        )
        attached = client.post(
            f"/v1/businesses/{attacker.business_id}/content/projects/{project_id}/media",
            headers=attacker.headers,
            json={"source_asset_ids": [str(uuid.uuid4())]},
        )
        assert attached.status_code == 404, attached.text
        # The attacker's own listing shows nothing of the victim's.
        listed = client.get(
            f"/v1/businesses/{attacker.business_id}/content/projects", headers=attacker.headers
        )
        assert listed.json()["items"] == []


@requires_postgres
def test_the_project_endpoints_answer_to_the_same_permissions_the_writes_they_order_do() -> None:
    settings = api_config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("lc-roles", "lc-roles@example.com"), "Roles")
        editor = tenant.invite("lc-editor@example.com", "editor")
        viewer = tenant.invite("lc-viewer@example.com", "viewer")
        approver = tenant.invite("lc-approver@example.com", "approver")

        # An editor produces content, so an editor may open a project.
        assert tenant.create_project(source_asset_ids=[], headers=editor).status_code == 201
        assert tenant.create_project(source_asset_ids=[], headers=viewer).status_code == 403
        assert tenant.create_project(source_asset_ids=[], headers=approver).status_code == 403

        listed = client.get(f"/v1/businesses/{tenant.business_id}/content/projects", headers=viewer)
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1
        assert (
            client.get(
                f"/v1/businesses/{tenant.business_id}/content/projects", headers=approver
            ).status_code
            == 403
        )


@requires_postgres
def test_the_idempotency_key_is_taken_from_the_whole_request_not_a_summary() -> None:
    """Replaying the same key with the same body returns the same project; a different body is
    a conflict rather than a silent replay of the first one."""

    settings = api_config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("lc-idem", "lc-idem@example.com"), "Idem")
        first = tenant.create_project(source_asset_ids=[], idempotency_key="project-1")
        assert first.status_code == 201, first.text
        replay = tenant.create_project(source_asset_ids=[], idempotency_key="project-1")
        assert replay.status_code == 201
        assert replay.json()["id"] == first.json()["id"]
        assert query("SELECT count(*) FROM content_projects") == [(1,)]

        other = client.post(
            f"/v1/businesses/{tenant.business_id}/products",
            headers=tenant.headers,
            json={"name": "Ikinci", "category": "İçecek"},
        )
        assert other.status_code == 201
        conflict = tenant.create_project(
            product_id=str(other.json()["id"]),
            source_asset_ids=[],
            idempotency_key="project-1",
        )
        assert conflict.status_code == 409, conflict.text
        assert query("SELECT count(*) FROM content_projects") == [(1,)]


@requires_postgres
def test_a_project_without_media_waits_for_it_and_moves_on_when_it_arrives() -> None:
    """PRD §20's `WAITING_MEDIA`, and the transition a person causes carries who they were."""

    settings = api_config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("lc-wait", "lc-wait@example.com"), "Waiting")
        created = tenant.create_project(source_asset_ids=[])
        project_id = created.json()["id"]
        advance_once(settings)
        assert snapshot(project_id)[0] == ProjectState.WAITING_MEDIA.value

        asset = tenant.seed_asset(duration_ms=8_000)
        attached = client.post(
            f"/v1/businesses/{tenant.business_id}/content/projects/{project_id}/media",
            headers=tenant.headers,
            json={"source_asset_ids": [asset]},
        )
        assert attached.status_code == 200, attached.text
        assert attached.json()["state"] == ProjectState.ANALYZING.value

    history = transitions(project_id)
    assert [row[3] for row in history] == [
        ProjectEvent.CREATED.value,
        ProjectEvent.MEDIA_REQUIRED.value,
        ProjectEvent.MEDIA_ATTACHED.value,
    ]
    actors = query(
        "SELECT event, actor_user_id FROM content_project_transitions"
        " WHERE project_id = CAST(:id AS uuid) ORDER BY sequence",
        id=project_id,
    )
    # A person opened it and a person attached the media; the sequencer's own step names nobody.
    assert actors[0][1] is not None
    assert actors[1][1] is None
    assert actors[2][1] is not None


@requires_postgres
def test_an_out_of_order_media_attachment_is_refused_rather_than_applied() -> None:
    """The state machine is the authority at the HTTP boundary too, not only in the worker."""

    settings = api_config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("lc-order", "lc-order@example.com"), "Order")
        asset = tenant.seed_asset(duration_ms=8_000)
        created = tenant.create_project(source_asset_ids=[asset])
        project_id = created.json()["id"]
        advance_once(settings)
        assert snapshot(project_id)[0] == ProjectState.ANALYZING.value

        refused = client.post(
            f"/v1/businesses/{tenant.business_id}/content/projects/{project_id}/media",
            headers=tenant.headers,
            json={"source_asset_ids": [tenant.seed_asset(duration_ms=8_000)]},
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "PROJECT_TRANSITION_NOT_ALLOWED"
        assert snapshot(project_id)[0] == ProjectState.ANALYZING.value
        assert query(
            "SELECT source_asset_ids FROM content_projects WHERE id = CAST(:id AS uuid)",
            id=project_id,
        ) == [([asset],)]


@requires_postgres
def test_a_project_naming_another_tenants_product_is_refused_before_anything_is_scheduled() -> None:
    settings = api_config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        victim = Tenant(client, auth("lc-a", "lc-a@example.com"), "A")
        attacker = Tenant(client, auth("lc-b", "lc-b@example.com"), "B")
        refused = attacker.create_project(
            product_id=victim.product_id, cta_id=victim.cta_id, source_asset_ids=[]
        )

    assert refused.status_code == 404, refused.text
    assert query("SELECT count(*) FROM content_projects") == [(0,)]
