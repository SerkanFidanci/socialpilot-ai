"""PRD §13's planner against real PostgreSQL: demand becomes work, and work becomes a schedule.

Slices 2A–2F could produce and approve a content project, but only ever because a person pressed
something. What this file proves is that the loop closes without one: a standing demand
materialises an obligation for a window, the obligation becomes a project with credit reserved,
the pipeline runs it to a preview, an approval lands, and the planner gives the approved content a
publication slot inside the business's own quiet-hour rules.

Four properties get the most attention because each one is a way to lose money, a window, or a
customer's trust.

**Planning is idempotent, including under a race.** The same window is planned twice, in sequence
and then in two genuinely concurrent transactions against the real database, and the second run
produces nothing either time. The natural key is the last line of that defence and it is exercised
directly.

**A conversion that cannot pay leaves nothing behind.** With an insufficient balance the
obligation blocks with a documented code, no project row exists, and the ledger has not moved —
and the block is readable from the API, because an obligation that disappears silently is the
specific failure this slice was asked to make impossible.

**The ranking is the PRD's, and it is explainable.** The plan endpoint returns §13.2's order with
every priority's reason attached, and the same order decides what the dispatcher converts.

**Nothing reaches a calendar unapproved.** The scheduling claim only sees `approved`, and a
project sitting in `waiting_approval` stays there however many times the drain runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.infrastructure.ai import create_audio_probe, create_script_generator, create_tts
from app.infrastructure.ai.fake_visual_qc import FakeVisualQcAdapter
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.infrastructure.media.s3_materializer import S3MediaMaterializer
from app.infrastructure.render import create_render
from app.infrastructure.render.qc_probe import FFmpegQcProbe
from app.infrastructure.storage import create_storage
from app.infrastructure.storage.s3 import S3MultipartStorage
from app.main import create_app
from app.modules.content.project_service import ContentProjectAdvanceService
from app.modules.content.qc_service import ContentQcService
from app.modules.content.render_service import ContentRenderService
from app.modules.planner.obligation import DEFAULT_MIX_SHARES, ContentCategory
from app.modules.planner.service import (
    ObligationDispatchService,
    ObligationPlanningService,
    ProjectSchedulingService,
)

pytestmark = pytest.mark.integration

KEY = "test-local-identity-signing-key-123"
FFMPEG = "/usr/bin/ffmpeg"

storage_configured = bool(os.getenv("S3_ENDPOINT_URL")) and bool(os.getenv("S3_BUCKET"))
requires_postgres = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL"
)
requires_media = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1"
    or not storage_configured
    or not Path(FFMPEG).exists(),
    reason="requires PostgreSQL, an S3-compatible endpoint and the ffmpeg binary",
)

TABLES = (
    "content_obligations",
    "planner_subscription_items",
    "planner_settings",
    "content_revisions",
    "content_approvals",
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


_PLANNER_TEST_TUNING: dict[str, Any] = {
    "lifecycle_poll_seconds": 1,
    "lifecycle_lease_seconds": 30,
    # The planner's own claims want the same treatment: short leases, so a test does not have to
    # wait out a production interval to see a second pass.
    "planner_plan_lease_seconds": 10,
    "planner_replan_interval_seconds": 60,
    "planner_dispatch_lease_seconds": 30,
    "planner_dispatch_retry_seconds": 1,
    "planner_blocked_retry_seconds": 60,
    # Exactly the current window, so "one item planned one obligation" is an assertion about the
    # planner rather than about how many days the deployment default looks ahead. The horizon is
    # exercised on its own, through the settings endpoint.
    "planner_planning_horizon_days": 0,
}


def api_config(**overrides: Any) -> Settings:
    """Settings for the tests that never touch a byte — storage stays the byte-free fake."""

    base: dict[str, Any] = {
        "app_env": "test",
        "database_url": os.environ["DATABASE_URL"],
        "redis_url": os.environ["REDIS_URL"],
        "celery_broker_url": os.environ["CELERY_BROKER_URL"],
        "celery_result_backend": os.environ["CELERY_RESULT_BACKEND"],
        "local_identity_signing_key": SecretStr(KEY),
        "storage_adapter": "fake",
        **_PLANNER_TEST_TUNING,
    }
    return Settings(**(base | overrides))


def media_config(**overrides: Any) -> Settings:
    """Settings for the end-to-end test: real MinIO, real FFmpeg, real ffprobe measurement."""

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
        **_PLANNER_TEST_TUNING,
    }
    return Settings(**(base | overrides))


def encode(path: Path, *, seconds: int) -> None:
    """A real vertical clip with sound, so the render and its measurement are real too."""

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


def auth(subject: str, email: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + LocalIdentityVerifier.sign_for_testing(signing_key=KEY, subject=subject, email=email)
    }


async def _clear() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
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


def plan_once(settings: Settings) -> dict[str, int] | None:
    async def run() -> dict[str, int] | None:
        async with factory()() as session:
            return await ObligationPlanningService(session, settings).process_next()

    return asyncio.run(run())


def dispatch_once(settings: Settings) -> dict[str, object] | None:
    async def run() -> dict[str, object] | None:
        async with factory()() as session:
            return await ObligationDispatchService(session, settings).process_next()

    return asyncio.run(run())


def schedule_once(settings: Settings) -> dict[str, int] | None:
    async def run() -> dict[str, int] | None:
        async with factory()() as session:
            return await ProjectSchedulingService(session, settings).process_next()

    return asyncio.run(run())


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
                storage=create_storage(settings),
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


def drive(
    settings: Settings, workdir: Path, project_id: str, *targets: str, rounds: int = 40
) -> str:
    """Run the three content workers until this project reaches one of `targets`.

    Borrowed wholesale from slice 2E's own end-to-end test: the planner does not reimplement the
    pipeline, so the test that proves the planner drives it must not either.
    """

    for _ in range(rounds):
        before = state_of(project_id)
        if before in targets:
            return before
        advance_once(settings)
        render_once(settings, workdir)
        qc_once(settings, workdir)
        if state_of(project_id) == before:
            # Nothing moved: the project is inside its poll window, and waiting it out is the
            # point — the gate is real and the test refuses to reach past it.
            time.sleep(1.1)
    return state_of(project_id)


class Tenant:
    """A business with a brand, a priced product, an approved CTA, an analysed asset and credit."""

    def __init__(
        self,
        client: TestClient,
        headers: dict[str, str],
        name: str,
        *,
        media: tuple[Settings, Path] | None = None,
    ) -> None:
        self.client = client
        self.headers = headers
        self.media = media
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
        self.asset_id = self.seed_asset()

    def seed_asset(self) -> str:
        """One analysed, renderable, portrait source asset — §13.2/5 and §13.2/7's inputs.

        The bytes are only put into storage when the tenant was built for the end-to-end test;
        every other test in this file measures the planner, which never reads a byte.
        """

        asset_id = str(uuid.uuid4())
        object_key = f"tenant/{self.business_id}/media/{asset_id}/original/seed.mp4"
        if self.media is not None:
            settings, video = self.media
            asyncio.run(
                S3MultipartStorage(settings).persist_file(
                    object_key=object_key, source_path=video, content_type="video/mp4"
                )
            )
        execute(
            "INSERT INTO media_assets (id, business_id, created_by_user_id, storage_object_key,"
            " content_type, byte_size, sha256_checksum, status, ingest_status, created_at,"
            " uploaded_at) VALUES (CAST(:id AS uuid), CAST(:business AS uuid),"
            " CAST(:user AS uuid), :key, 'video/mp4', 4096, :checksum, 'uploaded',"
            " 'ready_for_analysis', now(), now())",
            id=asset_id,
            business=self.business_id,
            key=object_key,
            user=self.user_id,
            checksum="0" * 64,
        )
        execute(
            "INSERT INTO media_technical_metadata (id, business_id, asset_id, container_format,"
            " duration_ms, file_size, video_codec, width, height, rotation_degrees, has_audio,"
            " stream_count, analyzed_at) VALUES (gen_random_uuid(), CAST(:business AS uuid),"
            " CAST(:asset AS uuid), 'mov,mp4', 12000, 4096, 'h264', 1080, 1920, 0, true, 2,"
            " now())",
            business=self.business_id,
            asset=asset_id,
        )
        return asset_id

    def grant_credits(self, credits: int) -> Any:
        return self.client.post(
            f"/v1/businesses/{self.business_id}/entitlement/grants",
            headers=self.headers,
            json={"credits": credits},
        )

    def balance(self) -> int:
        response = self.client.get(
            f"/v1/businesses/{self.business_id}/entitlement/balance", headers=self.headers
        )
        assert response.status_code == 200, response.text
        return int(response.json()["balance_credits"])

    # --- the endpoints under test ------------------------------------------------------------

    def put_settings(self, **overrides: Any) -> Any:
        body: dict[str, Any] = {
            "enabled": True,
            "quiet_hours_start_minute": 22 * 60,
            "quiet_hours_end_minute": 8 * 60,
            "planning_horizon_days": 0,
        }
        body.update(overrides)
        return self.client.put(
            f"/v1/businesses/{self.business_id}/planner/settings",
            headers=overrides.pop("headers", self.headers),
            json=body,
        )

    def create_item(self, **overrides: Any) -> Any:
        headers = dict(overrides.pop("headers", self.headers))
        key = overrides.pop("idempotency_key", None)
        if key is not None:
            headers["Idempotency-Key"] = key
        body: dict[str, Any] = {
            "content_type": "instagram_reels",
            "category": "product_service",
            "period": "daily",
            # Noon local: comfortably outside a 22:00–08:00 quiet window.
            "publish_minute": 12 * 60,
            "lead_time_minutes": 60,
            "product_id": self.product_id,
            "cta_id": self.cta_id,
            "source_asset_ids": [self.asset_id],
        }
        body.update(overrides)
        return self.client.post(
            f"/v1/businesses/{self.business_id}/planner/subscription-items",
            headers=headers,
            json=body,
        )

    def obligations(self, **params: Any) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/v1/businesses/{self.business_id}/planner/obligations",
            headers=self.headers,
            params=params,
        )
        assert response.status_code == 200, response.text
        return list(response.json()["items"])

    def plan(self) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/v1/businesses/{self.business_id}/planner/plan", headers=self.headers
        )
        assert response.status_code == 200, response.text
        return list(response.json()["entries"])

    def mix(self) -> dict[str, Any]:
        response = self.client.get(
            f"/v1/businesses/{self.business_id}/planner/mix", headers=self.headers
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    def decide(self, project_id: str, **overrides: Any) -> Any:
        body: dict[str, Any] = {"approved": True}
        body.update(overrides)
        return self.client.post(
            f"/v1/businesses/{self.business_id}/content/projects/{project_id}/approvals",
            headers=self.headers,
            json=body,
        )

    def invite(self, email: str, role: str) -> dict[str, str]:
        headers = auth(f"pl-{role}-{uuid.uuid4().hex[:6]}", email)
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


def state_of(project_id: str) -> str:
    rows = query("SELECT state FROM content_projects WHERE id = CAST(:id AS uuid)", id=project_id)
    assert rows
    return str(rows[0][0])


def snapshot(project_id: str) -> tuple[Any, ...]:
    rows = query(
        "SELECT state, render_id, qc_report_id, render_attempts, failure_code FROM"
        " content_projects WHERE id = CAST(:id AS uuid)",
        id=project_id,
    )
    assert rows
    return tuple(rows[0])


def seed_scene(asset_id: str, business_id: str) -> None:
    """One detected scene with a tag the fixture script asks for, so composition succeeds."""

    scene_id = str(uuid.uuid4())
    execute(
        "INSERT INTO media_scenes (id, business_id, asset_id, scene_index, start_ms, end_ms,"
        " duration_ms, confidence, created_at) VALUES (CAST(:id AS uuid),"
        " CAST(:business AS uuid), CAST(:asset AS uuid), 0, 0, 12000, 12000, 0.9, now())",
        id=scene_id,
        business=business_id,
        asset=asset_id,
    )
    execute(
        "INSERT INTO media_scene_understandings (id, business_id, asset_id, scene_id, status,"
        " provider, model_name, summary, visual_description, transcript_context, confidence,"
        " labels, objects, actions, visible_text, dominant_topics, safety_flags,"
        " quality_signals, created_at, updated_at) VALUES (gen_random_uuid(),"
        " CAST(:business AS uuid), CAST(:asset AS uuid), CAST(:scene AS uuid), 'completed',"
        " 'fake', 'fake-v1', '', '', '', 0.9, CAST(:labels AS jsonb), '[]'::jsonb, '[]'::jsonb,"
        " '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, now(), now())",
        business=business_id,
        asset=asset_id,
        scene=scene_id,
        labels=json.dumps(["urun", "hazirlik", "sonuc"]),
    )


# --- the end-to-end path (criterion 2) -----------------------------------------------------------


@requires_media
def test_a_standing_demand_becomes_a_scheduled_content_project(tmp_path: Path) -> None:
    """§13's whole claim, end to end, on real PostgreSQL, MinIO and FFmpeg.

    A subscription item produces an obligation for today's window; the obligation becomes a
    project with credit reserved; the pipeline renders and measures it for real; an approver
    signs it off; the planner gives it a publication slot and the obligation is fulfilled. Every
    step leaves a row, which is what makes the chain auditable rather than merely observed.
    """

    video = tmp_path / "source.mp4"
    encode(video, seconds=8)
    settings = media_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(
            client,
            auth("planner-e2e", "planner-e2e@example.com"),
            "Planlayici",
            media=(settings, video),
        )
        assert tenant.grant_credits(50).status_code == 201
        seed_scene(tenant.asset_id, tenant.business_id)
        assert tenant.put_settings().status_code == 200
        created = tenant.create_item()
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]

        # 1. Planning materialises §13.1's record for the current window, and only that.
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        obligations = tenant.obligations()
        assert len(obligations) == 1
        obligation = obligations[0]
        assert obligation["status"] == "planned"
        assert obligation["subscription_item_id"] == item_id
        assert obligation["content_type"] == "instagram_reels"
        assert obligation["project_id"] is None
        assert obligation["quiet_hours_shifted"] is False
        assert obligation["generation_deadline_at"] < obligation["planned_publish_at"]

        # 2. The plan endpoint explains its order before anything is converted.
        entries = tenant.plan()
        assert [entry["obligation_id"] for entry in entries] == [obligation["id"]]
        assert [reason["priority"] for reason in entries[0]["reasons"]] == list(range(1, 11))

        # 3. Conversion opens a project and reserves credit in the same transaction.
        before = tenant.balance()
        assert dispatch_once(settings) == {"converted": 1, "blocked": 0}
        converted = tenant.obligations()[0]
        assert converted["status"] == "in_progress"
        project_id = converted["project_id"]
        assert project_id is not None
        assert tenant.balance() < before
        assert query(
            "SELECT status FROM usage_reservations WHERE source_id = CAST(:id AS uuid)",
            id=project_id,
        ) == [("reserved",)]

        # 4. The pipeline walks it to a preview and the approver signs it off.
        reached = drive(settings, tmp_path, project_id, "waiting_approval", "failed")
        assert reached == "waiting_approval", snapshot(project_id)
        assert tenant.decide(project_id).status_code == 200
        assert state_of(project_id) == "approved"

        # 5. The planner gives it the obligation's slot and fulfils the obligation.
        assert schedule_once(settings) == {"scheduled": 1}
        assert state_of(project_id) == "scheduled"
        fulfilled = tenant.obligations()[0]
        assert fulfilled["status"] == "fulfilled"
        assert fulfilled["next_attempt_at"] is None

        slot = query(
            "SELECT scheduled_publish_at FROM content_projects WHERE id = CAST(:id AS uuid)",
            id=project_id,
        )[0][0]
        assert slot is not None
        # Every transition is recorded, and the last one is §20's own arrow.
        transitions = query(
            "SELECT to_state, event FROM content_project_transitions WHERE project_id ="
            " CAST(:id AS uuid) ORDER BY sequence",
            id=project_id,
        )
        assert transitions[-1] == ("scheduled", "scheduled")
        assert ("approved", "approved") in transitions


# --- idempotency (criterion 3) --------------------------------------------------------------------


@requires_postgres
def test_a_second_planning_run_over_the_same_window_produces_nothing() -> None:
    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-idem", "planner-idem@example.com"), "Tekrar")
        assert tenant.create_item().status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        # The lease is still held, so the item is not even claimable; force it due and re-run.
        execute("UPDATE planner_subscription_items SET next_plan_at = now()")
        assert plan_once(settings) == {"planned": 0, "skipped": 0}
        assert len(tenant.obligations()) == 1


@requires_postgres
def test_two_concurrent_planning_runs_produce_one_obligation() -> None:
    """A real race on real PostgreSQL, not two sequential calls wearing a costume.

    Both transactions are opened, both reach the planning step, and the tenant advisory lock is
    what serialises them. The unique index on `(subscription_item_id, period_start)` is the
    second lock on the same door and would refuse the duplicate even if the first were removed.
    """

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-race", "planner-race@example.com"), "Yaris")
        assert tenant.create_item().status_code == 201

        async def race() -> list[Any]:
            sessions = factory()
            async with sessions() as first, sessions() as second:
                return list(
                    await asyncio.gather(
                        ObligationPlanningService(first, settings).process_next(),
                        ObligationPlanningService(second, settings).process_next(),
                        return_exceptions=True,
                    )
                )

        results = asyncio.run(race())
        assert not any(isinstance(result, BaseException) for result in results), results
        # One run claimed the item and planned; the other found nothing claimable (SKIP LOCKED).
        assert sorted((result or {}).get("planned", -1) for result in results) == [-1, 1]
        assert len(tenant.obligations()) == 1


@requires_postgres
def test_the_natural_key_refuses_a_duplicate_window_directly() -> None:
    """The database-level half of the guarantee, exercised without the service in the way."""

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-key", "planner-key@example.com"), "Anahtar")
        assert tenant.create_item().status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        row = query(
            "SELECT id, subscription_item_id, period_start, period_end, planned_publish_at,"
            " generation_deadline_at, content_type, category FROM content_obligations"
        )[0]
        with pytest.raises(Exception, match="uq_content_obligation_period"):
            execute(
                "INSERT INTO content_obligations (id, business_id, subscription_item_id,"
                " content_type, category, status, period_start, period_end, planned_publish_at,"
                " generation_deadline_at, quiet_hours_shifted, attempts, next_attempt_at,"
                " correlation_id, created_at, updated_at) VALUES (gen_random_uuid(),"
                " CAST(:business AS uuid), :item, :content_type, :category, 'planned', :start,"
                " :end, :publish, :deadline, false, 0, now(), 'dup', now(), now())",
                business=tenant.business_id,
                item=row[1],
                content_type=row[6],
                category=row[7],
                start=row[2],
                end=row[3],
                publish=row[4],
                deadline=row[5],
            )
        assert len(tenant.obligations()) == 1


@requires_postgres
def test_conversion_is_idempotent_across_a_replayed_dispatch() -> None:
    """The key is derived from the obligation, so a replay replays rather than repays.

    Simulated by putting the obligation back into `planned` with its project reference cleared —
    which is exactly the state a crash between `create_project` committing and the settlement
    committing would leave behind.
    """

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-replay", "planner-replay@example.com"), "Tekrar2")
        assert tenant.grant_credits(50).status_code == 201
        assert tenant.create_item().status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        assert dispatch_once(settings) == {"converted": 1, "blocked": 0}
        first = tenant.obligations()[0]["project_id"]
        after_first = tenant.balance()

        execute(
            "UPDATE content_obligations SET status = 'planned', project_id = NULL,"
            " next_attempt_at = now()"
        )
        assert dispatch_once(settings) == {"converted": 1, "blocked": 0}
        assert tenant.obligations()[0]["project_id"] == first
        # One project, one reservation, one charge — the replay bought nothing.
        assert query("SELECT count(*) FROM content_projects") == [(1,)]
        assert query("SELECT count(*) FROM usage_reservations") == [(1,)]
        assert tenant.balance() == after_first


# --- insufficient credit (criterion 6) ------------------------------------------------------------


@requires_postgres
def test_an_obligation_that_cannot_pay_blocks_visibly_and_spends_nothing() -> None:
    """PM decision 2: yetersiz bakiye → obligation `blocked`, sessizce kaybolmaz.

    No project row, no reservation, no ledger movement — `create_project` reserves inside the
    transaction that creates the project, so a `402` takes the whole thing with it. What is left
    is a queue entry that says, through the API, exactly why it did not become work.
    """

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-broke", "planner-broke@example.com"), "Bakiyesiz")
        assert tenant.create_item().status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        assert tenant.balance() == 0

        assert dispatch_once(settings) == {"converted": 0, "blocked": 1}
        blocked = tenant.obligations()[0]
        assert blocked["status"] == "blocked"
        assert blocked["reason_code"] == "ENTITLEMENT_INSUFFICIENT_CREDITS"
        assert blocked["project_id"] is None
        # It is still convertible: blocking is a state, not a death sentence.
        assert blocked["next_attempt_at"] is not None
        assert query("SELECT count(*) FROM content_projects") == [(0,)]
        assert query("SELECT count(*) FROM usage_reservations") == [(0,)]
        assert query("SELECT count(*) FROM credit_ledger") == [(0,)]

        # Filtering by the status is how a person finds it.
        assert [row["id"] for row in tenant.obligations(status="blocked")] == [blocked["id"]]

        # And topping up lets the next pass convert the same window.
        assert tenant.grant_credits(50).status_code == 201
        execute("UPDATE content_obligations SET next_attempt_at = now()")
        assert dispatch_once(settings) == {"converted": 1, "blocked": 0}
        converted = tenant.obligations()[0]
        assert converted["status"] == "in_progress"
        assert converted["reason_code"] is None


# --- the ranking (criterion 4) ---------------------------------------------------------------------


@requires_postgres
def test_the_plan_orders_by_section_13_2_and_shows_its_working() -> None:
    """An active campaign goes first, and the response says which rule put it there."""

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-rank", "planner-rank@example.com"), "Siralama")
        campaign = client.post(
            f"/v1/businesses/{tenant.business_id}/campaign-offers",
            headers=tenant.headers,
            json={
                "name": "Yaz indirimi",
                "discount_type": "percentage",
                "discount_percent": 20,
                "starts_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                "ends_at": (datetime.now(UTC) + timedelta(days=10)).isoformat(),
                "product_ids": [tenant.product_id],
            },
        )
        assert campaign.status_code == 201, campaign.text
        campaign_id = campaign.json()["id"]
        # W04 refuses an approval decision at creation, so the offer is approved the way an
        # operator would: as a separate act, here written directly because approving campaigns is
        # not what this test is about.
        execute(
            "UPDATE campaign_offers SET approval_status = 'approved' WHERE id = CAST(:id AS uuid)",
            id=campaign_id,
        )

        plain = tenant.create_item(preference_rank=0)
        assert plain.status_code == 201, plain.text
        promoted = tenant.create_item(
            category="campaign",
            campaign_offer_id=campaign_id,
            publish_minute=13 * 60,
            preference_rank=9,
        )
        assert promoted.status_code == 201, promoted.text
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        assert plan_once(settings) == {"planned": 1, "skipped": 0}

        entries = tenant.plan()
        assert len(entries) == 2
        first_reasons = {reason["priority"]: reason["code"] for reason in entries[0]["reasons"]}
        # §13.2/1 put it there, and it beat a candidate the tenant ranked higher by hand (§13.2/9).
        assert first_reasons[1] == "campaign_active"
        second_reasons = {reason["priority"]: reason["code"] for reason in entries[1]["reasons"]}
        assert second_reasons[1] == "campaign_absent"
        # The two priorities with a field and no rule say so, in every entry.
        for entry in entries:
            codes = {reason["priority"]: reason["code"] for reason in entry["reasons"]}
            assert codes[4] == "performance_not_measured"
            assert codes[10] == "special_days_source_not_configured"

        # And the dispatcher converts the one the plan put first.
        assert tenant.grant_credits(50).status_code == 201
        assert dispatch_once(settings) == {"converted": 1, "blocked": 0}
        converted = [row for row in tenant.obligations() if row["status"] == "in_progress"]
        assert [row["id"] for row in converted] == [entries[0]["obligation_id"]]


@requires_postgres
def test_the_same_plan_read_twice_returns_the_same_order() -> None:
    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-pure", "planner-pure@example.com"), "Saf")
        for minute in (9 * 60, 12 * 60, 15 * 60):
            assert tenant.create_item(publish_minute=minute).status_code == 201
        for _ in range(3):
            assert plan_once(settings) == {"planned": 1, "skipped": 0}
        first = [entry["obligation_id"] for entry in tenant.plan()]
        second = [entry["obligation_id"] for entry in tenant.plan()]
        assert len(first) == 3
        assert first == second


# --- quiet hours and the tenant clock (criterion 5) ------------------------------------------------


@requires_postgres
def test_a_slot_inside_the_quiet_window_is_moved_out_and_never_dropped() -> None:
    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-quiet", "planner-quiet@example.com"), "Sessiz")
        assert tenant.put_settings().status_code == 200
        # 23:00 local, inside a 22:00–08:00 window.
        assert tenant.create_item(publish_minute=23 * 60).status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        obligation = tenant.obligations()[0]
        assert obligation["quiet_hours_shifted"] is True
        published = datetime.fromisoformat(obligation["planned_publish_at"])
        local = published.astimezone(ZoneInfo("Europe/Istanbul"))
        assert (local.hour, local.minute) == (8, 0)
        # Moved, not cancelled: the obligation is still convertible.
        assert obligation["status"] == "planned"


@requires_postgres
def test_a_business_in_a_dst_zone_gets_local_windows_not_utc_ones() -> None:
    """Türkiye has no DST, so a planner that assumed the tenant's offset never moves would pass
    every test written against Istanbul. Berlin is here to stop that assumption being invisible."""

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        headers = auth("planner-dst", "planner-dst@example.com")
        created = client.post(
            "/v1/businesses", headers=headers, json={"name": "Berlin", "timezone": "Europe/Berlin"}
        )
        assert created.status_code == 201, created.text
        business_id = str(created.json()["id"])
        client.put(
            f"/v1/businesses/{business_id}/brand",
            headers=headers,
            json={
                "display_name": "Berliner Kaffee",
                "tone": "sakin",
                "communication_language": "tr",
                "default_currency": "TRY",
                "color_palette": ["#202020"],
                "approved_ctas": ["Bugün bizi ziyaret et."],
            },
        )
        product = client.post(
            f"/v1/businesses/{business_id}/products",
            headers=headers,
            json={
                "name": "Filtre Kahve",
                "category": "İçecek",
                "price": {"price_minor": 9990, "currency": "TRY"},
            },
        )
        assert product.status_code == 201, product.text
        cta_id = str(
            query(
                "SELECT id FROM approved_ctas WHERE business_id = CAST(:business AS uuid)",
                business=business_id,
            )[0][0]
        )
        item = client.post(
            f"/v1/businesses/{business_id}/planner/subscription-items",
            headers=headers,
            json={
                "content_type": "instagram_reels",
                "category": "product_service",
                "period": "daily",
                "publish_minute": 12 * 60,
                "lead_time_minutes": 60,
                "product_id": str(product.json()["id"]),
                "cta_id": cta_id,
                "source_asset_ids": [],
            },
        )
        assert item.status_code == 201, item.text
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        row = query("SELECT period_start, period_end, planned_publish_at FROM content_obligations")[
            0
        ]
        berlin = ZoneInfo("Europe/Berlin")
        start, end, publish = (value.astimezone(berlin) for value in row)
        # Local midnight to local midnight, and noon local — none of which is a UTC boundary.
        assert (start.hour, start.minute) == (0, 0)
        assert (end.hour, end.minute) == (0, 0)
        assert (publish.hour, publish.minute) == (12, 0)
        assert end - start in (timedelta(hours=23), timedelta(days=1), timedelta(hours=25))


# --- the mix (criterion 7) --------------------------------------------------------------------------


@requires_postgres
def test_the_mix_is_reported_and_never_blocks_a_campaign() -> None:
    """PM decision 5. A business whose campaign share is far over target still gets its campaign
    obligation converted first — the deviation is measured and reported, never enforced."""

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-mix", "planner-mix@example.com"), "Karma")
        assert tenant.grant_credits(50).status_code == 201
        assert tenant.put_settings().status_code == 200
        # Five campaign items and one educational one: campaign is far over its 10% target.
        for index in range(5):
            assert (
                tenant.create_item(category="campaign", publish_minute=9 * 60 + index).status_code
                == 201
            )
        assert tenant.create_item(category="educational", publish_minute=16 * 60).status_code == 201
        for _ in range(6):
            assert plan_once(settings) == {"planned": 1, "skipped": 0}

        report = tenant.mix()
        by_category = {entry["category"]: entry for entry in report["entries"]}
        assert set(by_category) == {category.value for category in ContentCategory}
        assert by_category["campaign"]["observed"] == 5
        assert by_category["campaign"]["deviation_points"] < 0
        assert (
            by_category["campaign"]["target_share"] == DEFAULT_MIX_SHARES[ContentCategory.CAMPAIGN]
        )
        # Over target and still convertible: no quota anywhere refuses it.
        assert dispatch_once(settings) == {"converted": 1, "blocked": 0}
        converted = [row for row in tenant.obligations() if row["status"] == "in_progress"]
        assert len(converted) == 1
        assert query("SELECT count(*) FROM content_projects") == [(1,)]


# --- tenant isolation and roles (criterion 8) ---------------------------------------------------------


@requires_postgres
def test_another_tenants_obligation_is_invisible_and_unplannable() -> None:
    settings = api_config()
    with TestClient(create_app(settings)) as client:
        theirs = Tenant(client, auth("planner-them", "planner-them@example.com"), "Onlar")
        assert theirs.create_item().status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        their_obligation = theirs.obligations()[0]["id"]

        mine = Tenant(client, auth("planner-me", "planner-me@example.com"), "Ben")
        assert mine.obligations() == []
        # A real id from another tenant answers exactly like a made-up one.
        for target in (their_obligation, str(uuid.uuid4())):
            response = client.get(
                f"/v1/businesses/{mine.business_id}/planner/obligations/{target}",
                headers=mine.headers,
            )
            assert response.status_code == 404, response.text
            assert response.json()["code"] == "PLANNER_OBLIGATION_NOT_FOUND"
        # And the cross-tenant cancel is a 404, not a 403 that confirms the row exists.
        response = client.post(
            f"/v1/businesses/{mine.business_id}/planner/obligations/{their_obligation}/cancel",
            headers=mine.headers,
        )
        assert response.status_code == 404


@requires_postgres
def test_configuring_the_planner_is_business_update_and_reading_it_is_business_read() -> None:
    """PRD §4's line: an editor produces content and does not rewrite the publishing schedule."""

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-roles", "planner-roles@example.com"), "Roller")
        editor = tenant.invite("planner-editor@example.com", "editor")
        viewer = tenant.invite("planner-viewer@example.com", "viewer")

        for headers in (editor, viewer):
            denied = client.put(
                f"/v1/businesses/{tenant.business_id}/planner/settings",
                headers=headers,
                json={"enabled": True, "planning_horizon_days": 3},
            )
            assert denied.status_code == 403, denied.text
            assert denied.json()["code"] == "INSUFFICIENT_PERMISSION"
            denied_item = client.post(
                f"/v1/businesses/{tenant.business_id}/planner/subscription-items",
                headers=headers,
                json={
                    "content_type": "instagram_reels",
                    "category": "product_service",
                    "period": "daily",
                    "publish_minute": 600,
                    "product_id": tenant.product_id,
                    "cta_id": tenant.cta_id,
                },
            )
            assert denied_item.status_code == 403

        # Both may read: a schedule nobody can see is a schedule nobody can question.
        for headers in (editor, viewer):
            allowed = client.get(
                f"/v1/businesses/{tenant.business_id}/planner/settings", headers=headers
            )
            assert allowed.status_code == 200, allowed.text
            assert (
                client.get(
                    f"/v1/businesses/{tenant.business_id}/planner/plan", headers=headers
                ).status_code
                == 200
            )


@requires_postgres
def test_a_standing_demand_naming_another_tenants_product_is_refused_at_creation() -> None:
    """Checked here rather than on every conversion attempt, where it would block forever."""

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        theirs = Tenant(client, auth("planner-x1", "planner-x1@example.com"), "Onlarin")
        mine = Tenant(client, auth("planner-x2", "planner-x2@example.com"), "Benim")
        response = mine.create_item(product_id=theirs.product_id)
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "PLANNER_INPUT_NOT_FOUND"


# --- scheduling refuses what nobody approved -------------------------------------------------------


@requires_media
def test_a_project_awaiting_a_decision_is_never_given_a_publication_slot(tmp_path: Path) -> None:
    """The one edge this slice adds is drawn from `approved` alone. Run the drain until it says
    there is nothing to do, and the project is still waiting for its person."""

    video = tmp_path / "gate.mp4"
    encode(video, seconds=8)
    settings = media_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(
            client,
            auth("planner-gate", "planner-gate@example.com"),
            "Kapi",
            media=(settings, video),
        )
        assert tenant.grant_credits(50).status_code == 201
        seed_scene(tenant.asset_id, tenant.business_id)
        assert tenant.create_item().status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        assert dispatch_once(settings) == {"converted": 1, "blocked": 0}
        project_id = tenant.obligations()[0]["project_id"]
        assert drive(settings, tmp_path, project_id, "waiting_approval", "failed") == (
            "waiting_approval"
        ), snapshot(project_id)

        for _ in range(3):
            assert schedule_once(settings) is None
        assert state_of(project_id) == "waiting_approval"
        assert query(
            "SELECT scheduled_publish_at FROM content_projects WHERE id = CAST(:id AS uuid)",
            id=project_id,
        ) == [(None,)]
        assert tenant.obligations()[0]["status"] == "in_progress"


@requires_postgres
def test_an_obligation_whose_project_ended_is_reconciled_rather_than_left_in_progress() -> None:
    """Otherwise the planner would believe that window is being served by a dead project."""

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-dead", "planner-dead@example.com"), "Olu")
        assert tenant.grant_credits(50).status_code == 201
        assert tenant.create_item().status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        assert dispatch_once(settings) == {"converted": 1, "blocked": 0}
        project_id = tenant.obligations()[0]["project_id"]
        cancelled = client.post(
            f"/v1/businesses/{tenant.business_id}/content/projects/{project_id}/cancel",
            headers=tenant.headers,
        )
        assert cancelled.status_code == 200, cancelled.text

        assert schedule_once(settings) == {"reconciled": 1, "batch_full": 0}
        obligation = tenant.obligations()[0]
        assert obligation["status"] == "cancelled"
        assert obligation["reason_code"] == "PLANNER_PROJECT_ENDED"
        assert obligation["next_attempt_at"] is None


@requires_postgres
def test_a_window_that_closed_without_becoming_work_expires() -> None:
    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-late", "planner-late@example.com"), "Gec")
        assert tenant.create_item().status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        # Only the window's end moves: the natural key is the *start*, and rewriting that would
        # be testing a row the planner never wrote.
        execute("UPDATE content_obligations SET period_end = now() - interval '1 minute'")
        assert schedule_once(settings) == {"expired": 1, "batch_full": 0}
        obligation = tenant.obligations()[0]
        assert obligation["status"] == "expired"
        assert obligation["reason_code"] == "PLANNER_WINDOW_CLOSED"
        assert obligation["next_attempt_at"] is None
        # An expired window is never re-planned: the natural key still holds it.
        execute("UPDATE planner_subscription_items SET next_plan_at = now()")
        assert plan_once(settings) == {"planned": 0, "skipped": 0}
        assert len(tenant.obligations()) == 1


@requires_postgres
def test_pausing_a_standing_demand_stops_planning_and_leaves_commitments_alone() -> None:
    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-pause", "planner-pause@example.com"), "Durdur")
        created = tenant.create_item()
        assert created.status_code == 201
        item_id = created.json()["id"]
        assert plan_once(settings) == {"planned": 1, "skipped": 0}

        paused = client.post(
            f"/v1/businesses/{tenant.business_id}/planner/subscription-items/{item_id}/status",
            headers=tenant.headers,
            json={"status": "paused"},
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["next_plan_at"] is None
        assert plan_once(settings) is None
        # The obligation already planned is a commitment; pausing does not withdraw it.
        assert tenant.obligations()[0]["status"] == "planned"


@requires_postgres
def test_an_obligation_that_became_work_cannot_be_cancelled_from_the_planner() -> None:
    """Cancelling the queue entry would leave a project running with nothing pointing at it.
    The refund lives on the project's own endpoint, with the project's own rules."""

    settings = api_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(client, auth("planner-stop", "planner-stop@example.com"), "Iptal")
        assert tenant.grant_credits(50).status_code == 201
        assert tenant.create_item().status_code == 201
        assert plan_once(settings) == {"planned": 1, "skipped": 0}
        obligation_id = tenant.obligations()[0]["id"]

        withdrawn = client.post(
            f"/v1/businesses/{tenant.business_id}/planner/obligations/{obligation_id}/cancel",
            headers=tenant.headers,
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["status"] == "cancelled"
        assert withdrawn.json()["next_attempt_at"] is None
        # A cancelled window is never converted, and never re-planned.
        assert dispatch_once(settings) is None
        assert query("SELECT count(*) FROM content_projects") == [(0,)]

        # And the same refusal applies once an obligation has become work.
        execute("UPDATE content_obligations SET status = 'planned', next_attempt_at = now()")
        assert dispatch_once(settings) == {"converted": 1, "blocked": 0}
        refused = client.post(
            f"/v1/businesses/{tenant.business_id}/planner/obligations/{obligation_id}/cancel",
            headers=tenant.headers,
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "PLANNER_OBLIGATION_TRANSITION_NOT_ALLOWED"


@requires_media
def test_a_project_created_by_hand_is_still_given_a_slot_after_approval(tmp_path: Path) -> None:
    """`approved` stopped being terminal for everybody, not only for planned content. A project
    nobody planned would otherwise sit in a state nothing ever leaves."""

    video = tmp_path / "manual.mp4"
    encode(video, seconds=8)
    settings = media_config()
    with TestClient(create_app(settings)) as client:
        tenant = Tenant(
            client,
            auth("planner-manual", "planner-manual@example.com"),
            "Elle",
            media=(settings, video),
        )
        assert tenant.grant_credits(50).status_code == 201
        seed_scene(tenant.asset_id, tenant.business_id)
        created = client.post(
            f"/v1/businesses/{tenant.business_id}/content/projects",
            headers=tenant.headers,
            json={
                "scenario_code": "product_reels",
                "profile": "instagram_reels_1080x1920",
                "product_id": tenant.product_id,
                "cta_id": tenant.cta_id,
                "source_asset_ids": [tenant.asset_id],
            },
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        assert drive(settings, tmp_path, project_id, "waiting_approval", "failed") == (
            "waiting_approval"
        ), snapshot(project_id)
        assert tenant.decide(project_id).status_code == 200

        assert schedule_once(settings) == {"scheduled": 1}
        assert state_of(project_id) == "scheduled"
        slot = query(
            "SELECT scheduled_publish_at FROM content_projects WHERE id = CAST(:id AS uuid)",
            id=project_id,
        )[0][0]
        assert slot is not None
        assert slot > datetime.now(UTC)
        # No obligation was involved, and none was invented.
        assert query("SELECT count(*) FROM content_obligations") == [(0,)]
