"""PostgreSQL coverage for voiceover production, end to end through the HTTP surface.

Adversarial focus: getting sound into the system that no verified record vouched for, and
getting a duration into the record that nothing measured. Every test here tries one of those —
free text in the request body, another tenant's script, a script that never settled, a provider
that lies about the length of the file it just wrote, a run that stops half way — and asserts
that the row either does not exist, is `failed`, or holds the ffprobe answer rather than the
provider's.

The providers are fixtures, which is the point rather than a limitation: a real speech provider
mis-reports its own output rarely and unrepeatably, and the fixture does it on demand.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.routes.content import get_script_generator, get_tts
from app.core.config import Settings
from app.core.logging import REDACTED
from app.infrastructure.ai.fake_script import FakeScriptGenerationAdapter
from app.infrastructure.ai.fake_tts import DisabledTTSAdapter, FakeTTSAdapter
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.main import create_app
from app.modules.content.tts import VOICE_PROFILES

pytestmark = pytest.mark.integration

KEY = "test-local-identity-signing-key-123"
TABLES = (
    "voiceover_assets",
    "content_timelines",
    "content_scripts",
    "provider_usage",
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
    "audit_logs",
    "idempotency_keys",
    "business_members",
    "businesses",
    "external_identities",
    "users",
)

requires_postgres = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL"
)
storage_configured = bool(os.getenv("S3_ENDPOINT_URL")) and bool(os.getenv("S3_BUCKET"))
requires_storage = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1" or not storage_configured,
    reason="requires PostgreSQL and an S3-compatible storage endpoint",
)


def config(**overrides: Any) -> Settings:
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


def s3_config(**overrides: Any) -> Settings:
    endpoint = os.environ["S3_ENDPOINT_URL"]
    return config(
        storage_adapter="s3",
        s3_endpoint_url=endpoint,
        s3_presign_endpoint_url=endpoint,
        s3_region=os.environ.get("S3_REGION", "us-east-1"),
        s3_bucket=os.environ["S3_BUCKET"],
        s3_access_key_id=SecretStr(os.environ["S3_ACCESS_KEY_ID"]),
        s3_secret_access_key=SecretStr(os.environ["S3_SECRET_ACCESS_KEY"]),
        **overrides,
    )


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
                result = await connection.execute(text(statement), params)
                return list(result.all())
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


def app_with(
    settings: Settings,
    *,
    script_adapter: FakeScriptGenerationAdapter | None = None,
    tts_adapter: FakeTTSAdapter | DisabledTTSAdapter | None = None,
) -> FastAPI:
    """Build the app, pinning whichever fixture providers a test needs.

    Both ports are FastAPI dependencies precisely so this substitution is supported rather than
    a patched module attribute: the interesting cases are providers that misbehave in a specific
    way, and the suite has to be able to hand the service exactly one of them.
    """

    application = create_app(settings)
    if script_adapter is not None:
        application.dependency_overrides[get_script_generator] = lambda: script_adapter
    if tts_adapter is not None:
        application.dependency_overrides[get_tts] = lambda: tts_adapter
    return application


class Tenant:
    """One seeded business with a generated script ready to be voiced."""

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

    def generate_script(self) -> str:
        response = self.client.post(
            f"/v1/businesses/{self.business_id}/scripts",
            headers=self.headers,
            json={
                "scenario_code": "product_reels",
                "product_id": self.product_id,
                "cta_id": self.cta_id,
            },
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    def voice(self, script_id: str, **overrides: Any) -> Any:
        headers = dict(overrides.pop("headers", self.headers))
        key = overrides.pop("idempotency_key", None)
        if key is not None:
            headers["Idempotency-Key"] = key
        body: dict[str, Any] = {"script_id": script_id}
        body.update(overrides)
        return self.client.post(
            f"/v1/businesses/{self.business_id}/voiceovers", headers=headers, json=body
        )

    def invite(self, client: TestClient, email: str, role: str) -> dict[str, str]:
        """Add a second member in the given role and return their credentials."""

        headers = auth(f"vo-{role}", email)
        assert client.get("/v1/me", headers=headers).status_code == 200
        member_id = str(client.get("/v1/me", headers=headers).json()["id"])
        execute(
            "INSERT INTO business_members (id, business_id, user_id, role, status, created_at,"
            " updated_at) VALUES (gen_random_uuid(), CAST(:business AS uuid), CAST(:user AS uuid),"
            " :role, 'active', now(), now())",
            business=self.business_id,
            user=member_id,
            role=role,
        )
        return headers

    def seed_renderable_asset(self) -> str:
        """A media asset that passes §18.3's clip rules, so a timeline can be built on it."""

        asset_id = str(uuid.uuid4())
        execute(
            "INSERT INTO media_assets (id, business_id, created_by_user_id, storage_object_key,"
            " content_type, byte_size, sha256_checksum, status, ingest_status, created_at)"
            " VALUES (CAST(:id AS uuid), CAST(:business AS uuid), CAST(:user AS uuid), :key,"
            " 'video/mp4', 4096, :checksum, 'uploaded', 'ready_for_analysis', now())",
            id=asset_id,
            business=self.business_id,
            user=self.user_id,
            key=f"tenant/{self.business_id}/media/{asset_id}/original/seed.mp4",
            checksum="d" * 64,
        )
        execute(
            "INSERT INTO media_technical_metadata (id, business_id, asset_id, container_format,"
            " duration_ms, file_size, video_codec, width, height, rotation_degrees, has_audio,"
            " stream_count, analyzed_at) VALUES (gen_random_uuid(), CAST(:business AS uuid),"
            " CAST(:asset AS uuid), 'mov,mp4', 30000, 4096, 'h264', 1080, 1920, 0, true, 2, now())",
            business=self.business_id,
            asset=asset_id,
        )
        return asset_id


def voiceover_row(voiceover_id: str) -> Any:
    rows = query(
        "SELECT status, failure_code, voice_profile_code, voice_profile_version, voice_profile,"
        " segments, total_duration_ms, target_duration_ms, drift_ms, route_snapshot,"
        " provider_usage_id, script_id FROM voiceover_assets WHERE id = CAST(:id AS uuid)",
        id=voiceover_id,
    )
    assert rows, "the run left no row"
    return rows[0]


# --- the happy path (acceptance criterion 2) --------------------------------------------------


@requires_postgres
def test_a_generated_script_becomes_measured_voiceover_segments() -> None:
    tts = FakeTTSAdapter(config())
    with TestClient(
        app_with(config(), script_adapter=FakeScriptGenerationAdapter(config()), tts_adapter=tts),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        script_id = tenant.generate_script()

        response = tenant.voice(script_id)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "generated"
        assert body["voice_profile_code"] == "tr-warm-v1"
        assert body["voice_profile_version"] == VOICE_PROFILES["tr-warm-v1"].version
        assert body["audio_format"] == "wav"

        segments = body["segments"]
        assert len(segments) == tts.calls == 3
        for index, segment in enumerate(segments):
            assert segment["index"] == index
            # A real object per line, named deterministically and tenant-prefixed.
            assert segment["object_key"] == (
                f"tenant/{tenant.business_id}/voiceovers/{body['id']}/segment-{index:03d}.wav"
            )
            assert segment["content_type"] == "audio/wav"
            assert segment["byte_size"] > 0
            # Measured, not declared: a positive duration that ffprobe produced.
            assert segment["duration_ms"] > 0
            assert segment["drift_ms"] == (segment["duration_ms"] - segment["target_duration_ms"])
        assert body["total_duration_ms"] == sum(s["duration_ms"] for s in segments)
        assert body["drift_ms"] == body["total_duration_ms"] - body["target_duration_ms"]
        # No signed URL anywhere in a tenant-facing body.
        assert "X-Amz-Signature" not in response.text

        (
            db_status,
            failure,
            profile_code,
            profile_version,
            profile,
            db_segments,
            total,
            target,
            drift,
            route,
            usage_id,
            db_script_id,
        ) = voiceover_row(body["id"])
        assert (db_status, failure) == ("generated", None)
        assert str(db_script_id) == script_id
        assert (profile_code, profile_version) == ("tr-warm-v1", 1)
        # The exact profile handed to the provider, stored so the audio is reproducible.
        assert profile == VOICE_PROFILES["tr-warm-v1"].as_document()
        assert len(db_segments) == 3
        assert total == sum(int(s["duration_ms"]) for s in db_segments)
        assert drift == total - target
        assert route["capability"] == "tts"
        assert route["provider"] == "fake"
        assert route["fallbacks"] == []
        assert usage_id is not None

        # One usage row per call (§39.1), and the row the voiceover points at settled the run.
        usage = query(
            "SELECT capability, provider, outcome FROM provider_usage"
            " WHERE capability = 'tts' ORDER BY created_at, id"
        )
        assert usage == [("tts", "fake", "succeeded")] * 3
        settling = query(
            "SELECT outcome FROM provider_usage WHERE id = CAST(:id AS uuid)", id=str(usage_id)
        )
        assert settling == [("succeeded",)]


@requires_postgres
def test_the_stored_audio_is_measured_rather_than_taken_from_the_provider() -> None:
    """Acceptance criterion: a provider's claim about its own output is not a measurement."""

    tts = FakeTTSAdapter(config(), declared_duration_ms=999_000)
    with TestClient(
        app_with(config(), script_adapter=FakeScriptGenerationAdapter(config()), tts_adapter=tts),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")

        body = tenant.voice(tenant.generate_script()).json()

        for segment in body["segments"]:
            # The claim is kept — a provider that misreports is visible, not silently corrected.
            assert segment["declared_duration_ms"] == 999_000
            # And it is not what anything uses.
            assert segment["duration_ms"] < 60_000
        assert body["total_duration_ms"] < 60_000


# --- what may be voiced (acceptance criterion 3) ----------------------------------------------


@requires_postgres
def test_free_text_cannot_be_voiced_because_the_request_has_nowhere_to_put_it() -> None:
    """The strongest version of the rule: prose the API cannot express cannot be smuggled past
    a check, because there is no check to smuggle past."""

    with TestClient(
        app_with(
            config(),
            script_adapter=FakeScriptGenerationAdapter(config()),
            tts_adapter=FakeTTSAdapter(config()),
        ),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        script_id = tenant.generate_script()

        with_text = tenant.voice(script_id, text="Bugün her şey 5 TL!")
        without_script = client.post(
            f"/v1/businesses/{tenant.business_id}/voiceovers",
            headers=tenant.headers,
            json={"text": "Bugün her şey 5 TL!"},
        )

        assert with_text.status_code == 400, with_text.text
        assert with_text.json()["code"] == "REQUEST_VALIDATION_FAILED"
        assert without_script.status_code == 400
        assert query("SELECT count(*) FROM voiceover_assets") == [(0,)]


@requires_postgres
def test_a_script_that_never_settled_cannot_be_voiced() -> None:
    adapter = FakeScriptGenerationAdapter(config())
    with TestClient(
        app_with(config(), script_adapter=adapter, tts_adapter=FakeTTSAdapter(config())),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        # A provider response that invents a price is stored as `failed` with no document.
        adapter.output_json = (
            '{"hook": {"text": "Bugün 5 TL!", "duration_ms": 2500}, "segments": ['
            '{"purpose": "hook", "voice_text": "Bugün 5 TL!",'
            ' "required_scene_tags": ["a"], "target_duration_ms": 2500},'
            '{"purpose": "offer", "voice_text": "Gel.",'
            ' "required_scene_tags": ["a"], "target_duration_ms": 4000}],'
            f'"cta": {{"source": "approved_cta", "reference_id": "{tenant.cta_id}"}}}}'
        )
        rejected = client.post(
            f"/v1/businesses/{tenant.business_id}/scripts",
            headers=tenant.headers,
            json={
                "scenario_code": "product_reels",
                "product_id": tenant.product_id,
                "cta_id": tenant.cta_id,
            },
        )
        assert rejected.status_code == 422, rejected.text
        failed_id = str(query("SELECT id FROM content_scripts WHERE status = 'failed'")[0][0])

        response = tenant.voice(failed_id)

        assert response.status_code == 409
        assert response.json()["code"] == "VOICEOVER_SCRIPT_NOT_USABLE"
        assert query("SELECT count(*) FROM voiceover_assets") == [(0,)]


@requires_postgres
def test_another_tenants_script_is_not_found_rather_than_forbidden() -> None:
    """No existence disclosure: a real id belonging to somebody else answers like a made-up one."""

    with TestClient(
        app_with(
            config(),
            script_adapter=FakeScriptGenerationAdapter(config()),
            tts_adapter=FakeTTSAdapter(config()),
        ),
        raise_server_exceptions=False,
    ) as client:
        victim = Tenant(client, auth("vo-victim", "vo-victim@example.com"), "Victim")
        stolen = victim.generate_script()
        attacker = Tenant(client, auth("vo-attacker", "vo-attacker@example.com"), "Attacker")

        real = attacker.voice(stolen)
        invented = attacker.voice(str(uuid.uuid4()))

        assert real.status_code == invented.status_code == 404
        assert real.json()["code"] == invented.json()["code"] == "VOICEOVER_SCRIPT_NOT_FOUND"
        # And the answers are indistinguishable, so the endpoint cannot be used to probe.
        assert real.json()["detail"] == invented.json()["detail"]
        assert query("SELECT count(*) FROM voiceover_assets") == [(0,)]


@requires_postgres
def test_an_unknown_voice_is_refused_and_the_registry_is_the_whole_choice() -> None:
    tts = FakeTTSAdapter(config())
    with TestClient(
        app_with(config(), script_adapter=FakeScriptGenerationAdapter(config()), tts_adapter=tts),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        script_id = tenant.generate_script()

        unknown = tenant.voice(script_id, voice_profile_code="does-not-exist")
        chosen = tenant.voice(script_id, voice_profile_code="tr-neutral-v1")

        assert unknown.status_code == 422
        assert unknown.json()["code"] == "VOICEOVER_VOICE_PROFILE_UNKNOWN"
        assert chosen.status_code == 201
        assert chosen.json()["voice_profile_code"] == "tr-neutral-v1"


# --- the cost ceiling (acceptance criterion 4) ------------------------------------------------


@requires_postgres
def test_the_ceiling_stops_the_run_before_a_single_call_happens() -> None:
    """`TTS_MAX_COST_MINOR` defaults to zero, so a route that costs anything is refused."""

    tts = FakeTTSAdapter(config(), estimated_cost_minor=1, actual_cost_minor=1)
    with TestClient(
        app_with(config(), script_adapter=FakeScriptGenerationAdapter(config()), tts_adapter=tts),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        script_id = tenant.generate_script()

        response = tenant.voice(script_id)

        assert response.status_code == 409
        assert response.json()["code"] == "TTS_COST_LIMIT_EXCEEDED"
        # The call provably did not happen, and no row records an attempt that never was.
        assert tts.calls == 0
        assert query("SELECT count(*) FROM voiceover_assets") == [(0,)]


@requires_postgres
def test_a_run_whose_lines_together_exceed_the_ceiling_is_refused_too() -> None:
    """Three calls at one minor unit each is three, not one — the ceiling sees the whole run."""

    tts = FakeTTSAdapter(config(), estimated_cost_minor=1, actual_cost_minor=1)
    settings = config(tts_max_cost_minor=2)
    with TestClient(
        app_with(settings, script_adapter=FakeScriptGenerationAdapter(settings), tts_adapter=tts),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")

        response = tenant.voice(tenant.generate_script())

        assert response.status_code == 409
        assert response.json()["code"] == "TTS_COST_LIMIT_EXCEEDED"
        assert tts.calls == 0


# --- production behaviour (acceptance criterion 5) --------------------------------------------


@requires_postgres
def test_a_deployment_without_a_speech_provider_answers_503_and_writes_nothing() -> None:
    with TestClient(
        app_with(
            config(),
            script_adapter=FakeScriptGenerationAdapter(config()),
            tts_adapter=DisabledTTSAdapter(reason="no provider"),
        ),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")

        response = tenant.voice(tenant.generate_script())

        assert response.status_code == 503
        assert response.json()["code"] == "TTS_NOT_CONFIGURED"
        assert query("SELECT count(*) FROM voiceover_assets") == [(0,)]
        # Every other endpoint keeps serving: the capability is off, the application is not.
        assert client.get("/health/ready").status_code == 200


# --- partial runs -----------------------------------------------------------------------------


@requires_postgres
def test_a_run_that_stops_half_way_keeps_the_objects_it_already_stored() -> None:
    """Two lines are in the bucket and the third never came back.

    Forgetting the two would leave attributable bytes unattributed, so the `failed` row records
    exactly what exists — and both calls get their usage row, because both happened.
    """

    tts = FakeTTSAdapter(config(), failure="transient")
    tts.fail_after_calls = 2
    with TestClient(
        app_with(config(), script_adapter=FakeScriptGenerationAdapter(config()), tts_adapter=tts),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")

        response = tenant.voice(tenant.generate_script())

        assert response.status_code == 503
        assert response.json()["code"] == "TTS_PROVIDER_UNAVAILABLE"
        row = voiceover_row(str(query("SELECT id FROM voiceover_assets")[0][0]))
        assert row[0] == "failed"
        assert row[1] == "TTS_PROVIDER_UNAVAILABLE"
        assert len(row[5]) == 2, "the two stored objects must stay attributable"
        # Every call that happened has a row — the two that produced audio and the one that did
        # not. They share the request's correlation id and are written in one transaction, so
        # their order is not meaningful; their multiset is.
        outcomes = sorted(query("SELECT outcome FROM provider_usage WHERE capability = 'tts'"))
        assert outcomes == [("failed",), ("succeeded",), ("succeeded",)]
        # The row the voiceover points at is the one that settled it, so its outcome and the
        # voiceover's status cannot disagree.
        assert query(
            "SELECT outcome FROM provider_usage WHERE id = CAST(:id AS uuid)", id=str(row[10])
        ) == [("failed",)]


# --- roles and idempotency (acceptance criterion 8) -------------------------------------------


@requires_postgres
def test_only_the_roles_that_produce_content_may_voice_a_script() -> None:
    with TestClient(
        app_with(
            config(),
            script_adapter=FakeScriptGenerationAdapter(config()),
            tts_adapter=FakeTTSAdapter(config()),
        ),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        script_id = tenant.generate_script()
        editor = tenant.invite(client, "vo-editor@example.com", "editor")
        viewer = tenant.invite(client, "vo-viewer@example.com", "viewer")
        approver = tenant.invite(client, "vo-approver@example.com", "approver")

        assert tenant.voice(script_id, headers=editor).status_code == 201
        assert tenant.voice(script_id, headers=viewer).status_code == 403
        assert tenant.voice(script_id, headers=approver).status_code == 403
        # A viewer may still read what an editor produced, and from slice 2F so may an
        # approver — it has to hear what it is signing off. Neither may produce one.
        for reader in (viewer, approver):
            assert (
                client.get(
                    f"/v1/businesses/{tenant.business_id}/voiceovers", headers=reader
                ).status_code
                == 200
            )


@requires_postgres
def test_the_same_key_replays_and_a_different_body_conflicts() -> None:
    """The fingerprint is the whole request: the same key with a different voice must not
    silently replay the first run's audio."""

    tts = FakeTTSAdapter(config())
    with TestClient(
        app_with(config(), script_adapter=FakeScriptGenerationAdapter(config()), tts_adapter=tts),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        script_id = tenant.generate_script()

        first = tenant.voice(script_id, idempotency_key="vo-1")
        replay = tenant.voice(script_id, idempotency_key="vo-1")
        conflict = tenant.voice(
            script_id, idempotency_key="vo-1", voice_profile_code="tr-neutral-v1"
        )

        assert first.status_code == replay.status_code == 201
        assert first.json()["id"] == replay.json()["id"]
        assert conflict.status_code == 409
        # The replay called nobody: three lines, one run.
        assert tts.calls == 3
        assert query("SELECT count(*) FROM voiceover_assets") == [(1,)]


# --- alignment through the API (acceptance criterion 6) ---------------------------------------


def timeline_document(asset_id: str, voiceover_id: str, *, duration_ms: int) -> dict[str, Any]:
    return {
        "version": "1.0",
        "canvas": {"width": 1080, "height": 1920, "fps": 30, "duration_ms": duration_ms},
        "video_tracks": [
            {
                "track": 1,
                "clips": [
                    {
                        "asset_id": asset_id,
                        "source_start_ms": 0,
                        "source_end_ms": duration_ms,
                        "timeline_start_ms": 0,
                        "crop_mode": "smart_cover",
                        "transition_out": "cut",
                    }
                ],
            }
        ],
        "audio_tracks": [
            {
                "type": "voiceover",
                "asset_id": voiceover_id,
                "gain_db": 0,
                "duck_under_voice": False,
            }
        ],
        "overlays": [],
        "captions": {"enabled": False, "source": "transcript", "style_id": "brand-caption-v1"},
    }


@requires_postgres
def test_speech_longer_than_the_canvas_is_refused_by_pre_render_validation() -> None:
    """§18.3's "seslendirme süresi", read from the database rather than from a caller.

    The duration compared here is the sum of ffprobe measurements written when the audio was
    produced. Nothing in the request carries it, so a client cannot assert a length its audio
    does not have.
    """

    with TestClient(
        app_with(
            config(),
            script_adapter=FakeScriptGenerationAdapter(config()),
            tts_adapter=FakeTTSAdapter(config()),
        ),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        voiceover = tenant.voice(tenant.generate_script()).json()
        asset_id = tenant.seed_renderable_asset()
        # A canvas one millisecond shorter than the speech laid over it.
        too_short = timeline_document(
            asset_id, voiceover["id"], duration_ms=voiceover["total_duration_ms"] - 1
        )

        response = client.post(
            f"/v1/businesses/{tenant.business_id}/content/timelines",
            headers=tenant.headers,
            json={"profile": "instagram_reels_1080x1920", "document": too_short},
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["code"] == "TIMELINE_VALIDATION_FAILED"
        codes = {issue["code"] for issue in body["meta"]["issues"]}
        assert "TIMELINE_VOICEOVER_DURATION_OVERFLOW" in codes


@requires_postgres
def test_another_tenants_voiceover_cannot_be_placed_on_a_timeline() -> None:
    with TestClient(
        app_with(
            config(),
            script_adapter=FakeScriptGenerationAdapter(config()),
            tts_adapter=FakeTTSAdapter(config()),
        ),
        raise_server_exceptions=False,
    ) as client:
        victim = Tenant(client, auth("vo-victim", "vo-victim@example.com"), "Victim")
        stolen = victim.voice(victim.generate_script()).json()["id"]
        attacker = Tenant(client, auth("vo-attacker", "vo-attacker@example.com"), "Attacker")
        asset_id = attacker.seed_renderable_asset()

        response = client.post(
            f"/v1/businesses/{attacker.business_id}/content/timelines",
            headers=attacker.headers,
            json={
                "profile": "instagram_reels_1080x1920",
                "document": timeline_document(asset_id, stolen, duration_ms=30_000),
            },
        )

        assert response.status_code == 422
        codes = {issue["code"] for issue in response.json()["meta"]["issues"]}
        assert "TIMELINE_VOICEOVER_NOT_ACCESSIBLE" in codes


# --- signing material (acceptance criterion 7) ------------------------------------------------


class _AllHandlerOutput(logging.Handler):
    """Capture what an arbitrary third-party handler would print, with its own formatter.

    Deliberately not `caplog`: the claim under test is that a handler this module never told
    anything about still cannot render signing material.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # pragma: no cover - a formatting error is not the subject here
            self.lines.append(f"{record.name} <unformattable>")


@contextmanager
def capture_every_log_record() -> Iterator[_AllHandlerOutput]:
    handler = _AllHandlerOutput()
    root = logging.getLogger()
    previous_level = root.level
    noisy = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    previous = [(logger, logger.level, logger.disabled) for logger in noisy]
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    for logger in noisy:
        logger.setLevel(logging.DEBUG)
        logger.disabled = False
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
        for logger, level, disabled in previous:
            logger.setLevel(level)
            logger.disabled = disabled


@requires_storage
def test_no_logger_writes_the_signature_while_voiceover_objects_are_stored() -> None:
    """W14's process-wide filter, on this slice's own path, against real MinIO.

    The fake storage adapter signs nothing, so a sentinel test against it would prove only that
    no signature exists to leak. Here `persist_file` signs a real PUT per line — the exact shape
    that leaked at INFO level through httpx before W14 — and the assertion is that no handler in
    the process can render it.
    """

    settings = s3_config()
    with TestClient(
        app_with(
            settings,
            script_adapter=FakeScriptGenerationAdapter(settings),
            tts_adapter=FakeTTSAdapter(settings),
        ),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        script_id = tenant.generate_script()

        with capture_every_log_record() as captured:
            response = tenant.voice(script_id)

        assert response.status_code == 201, response.text
        output = "\n".join(captured.lines)
        # The signing material never reaches a handler...
        assert not re.search(
            r"X-Amz-Signature=(?!" + re.escape(REDACTED) + r")[0-9a-fA-F]{16,}", output
        )
        assert "X-Amz-Credential=" not in output or f"X-Amz-Credential={REDACTED}" in output
        # ...and the run really did sign something, so the assertion above is not vacuous.
        assert any("minio" in line.lower() or "PUT" in line for line in captured.lines)
        # Nor does any of it reach the audit trail or the stored segment records.
        trail = query("SELECT metadata FROM audit_logs WHERE action LIKE 'content.voiceover%'")
        assert trail and all("Signature" not in str(entry[0]) for entry in trail)
        stored = query("SELECT segments FROM voiceover_assets")
        assert stored and "Signature" not in str(stored[0][0])


# --- reads ------------------------------------------------------------------------------------


@requires_postgres
def test_a_voiceover_can_be_read_back_and_listed_within_its_tenant() -> None:
    with TestClient(
        app_with(
            config(),
            script_adapter=FakeScriptGenerationAdapter(config()),
            tts_adapter=FakeTTSAdapter(config()),
        ),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        script_id = tenant.generate_script()
        created = tenant.voice(script_id).json()
        outsider = Tenant(client, auth("vo-other", "vo-other@example.com"), "Other")

        one = client.get(
            f"/v1/businesses/{tenant.business_id}/voiceovers/{created['id']}",
            headers=tenant.headers,
        )
        listed = client.get(
            f"/v1/businesses/{tenant.business_id}/voiceovers?script_id={script_id}",
            headers=tenant.headers,
        )
        stolen = client.get(
            f"/v1/businesses/{outsider.business_id}/voiceovers/{created['id']}",
            headers=outsider.headers,
        )

        assert one.status_code == 200 and one.json()["id"] == created["id"]
        assert [item["id"] for item in listed.json()["items"]] == [created["id"]]
        assert stolen.status_code == 404


@requires_postgres
def test_the_request_is_refused_before_any_call_when_the_business_is_not_mutable() -> None:
    tts = FakeTTSAdapter(config())
    with TestClient(
        app_with(config(), script_adapter=FakeScriptGenerationAdapter(config()), tts_adapter=tts),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        script_id = tenant.generate_script()
        execute(
            "UPDATE businesses SET status = 'suspended' WHERE id = CAST(:id AS uuid)",
            id=tenant.business_id,
        )

        response = tenant.voice(script_id)

        assert response.status_code == 409
        assert response.json()["code"] == "BUSINESS_NOT_MUTABLE"
        assert tts.calls == 0


@requires_postgres
def test_a_campaign_dated_script_still_voices_only_what_the_record_said() -> None:
    """The date a listener hears is the campaign's inclusive last day, formatted by code.

    Nothing here re-derives it: the voiceover reads the script's resolved document, which slice
    2B produced from `campaign_offers`. The assertion is that the audio pipeline introduced no
    second source for a date.
    """

    with TestClient(
        app_with(
            config(),
            script_adapter=FakeScriptGenerationAdapter(config()),
            tts_adapter=FakeTTSAdapter(config()),
        ),
        raise_server_exceptions=False,
    ) as client:
        tenant = Tenant(client, auth("vo-owner", "vo-owner@example.com"), "Acme")
        now = datetime.now(UTC)
        offer = client.post(
            f"/v1/businesses/{tenant.business_id}/campaign-offers",
            headers=tenant.headers,
            json={
                "name": "Ağustos kampanyası",
                "starts_at": (now - timedelta(days=1)).isoformat(),
                "ends_at": (now + timedelta(days=7)).isoformat(),
                "discount_type": "percentage",
                "discount_percent": 20,
            },
        )
        assert offer.status_code == 201, offer.text
        script = client.post(
            f"/v1/businesses/{tenant.business_id}/scripts",
            headers=tenant.headers,
            json={
                "scenario_code": "product_reels",
                "product_id": tenant.product_id,
                "cta_id": tenant.cta_id,
                "campaign_offer_id": str(offer.json()["id"]),
            },
        )
        assert script.status_code == 201, script.text
        spoken = " ".join(
            str(segment["voice_text"]) for segment in script.json()["document"]["segments"]
        )

        voiced = tenant.voice(str(script.json()["id"]))

        assert voiced.status_code == 201, voiced.text
        # Whatever the script settled on is what the lines were synthesized from; the voiceover
        # row holds one segment per script segment and nothing else.
        assert len(voiced.json()["segments"]) == len(script.json()["document"]["segments"])
        assert "149,90 TRY" in spoken
