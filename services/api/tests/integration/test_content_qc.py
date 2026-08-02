"""Slice 2D's proof: real outputs in real storage, measured by real FFmpeg, judged by real rules.

Nothing about the measurement is simulated. Each test encodes a genuinely defective output —
wholly black, silent, a held frame, a file that is not a container — uploads it to object storage
under a render's own key, and drives the durable QC job end to end. The claim under test is not
"a check exists" but "the check catches it".

The three fail-closed paths get the same treatment, because they are the ones a future change is
most likely to erode:

- a measurement that could not be taken leaves every measured check `unknown`;
- a vision provider that is switched off leaves the four model checks `unknown`;
- and either one keeps the verdict at `needs_review`, from which nothing can reach `passed`.

It needs PostgreSQL, an S3-compatible endpoint and ffmpeg, so it skips unless those are set.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.infrastructure.ai.fake_visual_qc import DisabledVisualQcAdapter, FakeVisualQcAdapter
from app.infrastructure.celery_app import celery_app
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.infrastructure.media.s3_materializer import S3MediaMaterializer
from app.infrastructure.render.qc_probe import FFmpegQcProbe
from app.infrastructure.storage.s3 import S3MultipartStorage
from app.main import create_app
from app.modules.brands.models import (
    CampaignApprovalStatus,
    CampaignOffer,
    CampaignOfferStatus,
    DiscountType,
    Product,
    ProductPrice,
    ProductStatus,
    StockStatus,
)
from app.modules.content.models import (
    ContentTimeline,
    RenderOutput,
    RenderQcReport,
    RenderStatus,
    RenderTrigger,
)
from app.modules.content.qc import (
    MODEL_CHECKS,
    CheckStatus,
    QcCheck,
    QcRunStatus,
    QcVerdict,
    RemediationPath,
)
from app.modules.content.qc_service import ContentQcService
from app.modules.content.render import AiDisclosureState, ProvenanceState, RenderProfile
from app.modules.operations.models import BackgroundJob, ProviderUsage
from app.worker import composition

pytestmark = pytest.mark.integration
KEY = "test-local-identity-signing-key-123"
FFMPEG = "/usr/bin/ffmpeg"

storage_configured = bool(os.getenv("S3_ENDPOINT_URL")) and bool(os.getenv("S3_BUCKET"))
requires_storage = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1" or not storage_configured,
    reason="requires PostgreSQL and an S3-compatible storage endpoint",
)
requires_ffmpeg = pytest.mark.skipif(not Path(FFMPEG).exists(), reason="requires the ffmpeg binary")


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
        # A plain 440 Hz tone integrates near -21.8 LUFS. The window is configuration precisely
        # so a deployment can place it where its own material sits; here it is placed around the
        # fixture so the *mechanism* is what the happy path tests, not the fixture's timbre.
        "qc_loudness_target_lufs": -22.0,
        "qc_loudness_tolerance_lu": 5.0,
    }
    return Settings(**(base | overrides))


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
                    # `prompt_templates` is deliberately absent: migration 0013 seeds the first
                    # prompt version and it is platform configuration, not tenant data. Wiping
                    # it here would leave every script generation in the suite with no active
                    # template to run under.
                    "TRUNCATE credit_ledger, usage_reservations, render_qc_reports, provider_usage, voiceover_assets, "
                    "content_scripts, render_outputs, content_timelines, "
                    "brand_assets, target_audiences, approved_ctas, approved_claims, "
                    "forbidden_claims, campaign_offer_products, campaign_offers, product_prices, "
                    "products, brand_profiles, job_attempts, jobs, outbox_events, "
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


# --- fixtures on disk ------------------------------------------------------------------------


def encode(path: Path, video: str, audio: str | None, *, seconds: int = 3) -> None:
    command = [
        FFMPEG,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        video,
    ]
    if audio is not None:
        command += ["-f", "lavfi", "-i", audio]
    command += ["-t", str(seconds), "-pix_fmt", "yuv420p", "-c:v", "libx264"]
    command += ["-c:a", "aac", "-shortest"] if audio is not None else ["-an"]
    command.append(str(path))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# The model checks a timeline without a logo overlay actually asks a provider. Logo
# visibility is answered from the document instead — see the disabled-provider test.
ASKED_OF_THE_MODEL = tuple(check for check in MODEL_CHECKS if check is not QcCheck.LOGO_VISIBLE)

TONE = "sine=frequency=440:sample_rate=48000"
SILENCE = "anullsrc=channel_layout=stereo:sample_rate=48000"


def healthy(path: Path) -> None:
    encode(path, "testsrc2=size=1080x1920:rate=30", TONE)


# --- rows and objects ------------------------------------------------------------------------


def timeline_document(overlays: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "version": "1.0",
        "canvas": {"width": 1080, "height": 1920, "fps": 30, "duration_ms": 6_000},
        "video_tracks": [
            {
                "track": 1,
                "clips": [
                    {
                        "asset_id": str(uuid.uuid4()),
                        "source_start_ms": 0,
                        "source_end_ms": 3_000,
                        "timeline_start_ms": 0,
                        "crop_mode": "smart_cover",
                        "transition_out": "cut",
                    }
                ],
            }
        ],
        "audio_tracks": [
            {"type": "original", "asset_id": None, "gain_db": 0, "duck_under_voice": False}
        ],
        "overlays": overlays or [],
        "captions": {"enabled": False, "source": "transcript", "style_id": "brand-caption-v1"},
    }


def verified_overlay(source: str, reference_id: uuid.UUID) -> dict[str, Any]:
    return {
        "type": "text",
        "text_source": source,
        "text": None,
        "reference_id": str(reference_id),
        "anchor": "bottom_center",
        "style_id": "brand-caption-v1",
        "start_ms": 0,
        "end_ms": 3_000,
        "safe_area": True,
    }


async def seed_render(
    business_id: uuid.UUID,
    user_id: uuid.UUID,
    video: Path,
    *,
    document: dict[str, Any] | None = None,
    completed_at: datetime | None = None,
    settings: Settings | None = None,
) -> uuid.UUID:
    """Write a finished render and put its master where the worker will look for it."""

    resolved = settings or config()
    render_id = uuid.uuid4()
    object_key = f"tenant/{business_id}/renders/{render_id}/master"
    metadata = await S3MultipartStorage(resolved).persist_file(
        object_key=object_key, source_path=video, content_type="video/mp4"
    )
    async with factory()() as session:
        async with session.begin():
            timeline = ContentTimeline(
                id=uuid.uuid4(),
                business_id=business_id,
                root_id=uuid.uuid4(),
                parent_id=None,
                revision=1,
                document=document or timeline_document(),
                created_by_user_id=user_id,
                correlation_id="qc-test",
            )
            timeline.root_id = timeline.id
            session.add(timeline)
            await session.flush()
            session.add(
                RenderOutput(
                    id=render_id,
                    business_id=business_id,
                    timeline_id=timeline.id,
                    job_id=None,
                    profile=RenderProfile.INSTAGRAM_REELS_1080X1920,
                    status=RenderStatus.SUCCEEDED,
                    trigger=RenderTrigger.INITIAL,
                    consumes_entitlement=True,
                    master_object_key=object_key,
                    preview_object_key=f"tenant/{business_id}/renders/{render_id}/preview",
                    thumbnail_object_key=f"tenant/{business_id}/renders/{render_id}/thumbnail",
                    byte_size=metadata.byte_size,
                    duration_ms=3_000,
                    width=1080,
                    height=1920,
                    video_codec="h264",
                    audio_codec="aac",
                    ai_disclosure_state=AiDisclosureState.NONE,
                    provenance_state=ProvenanceState.STRIPPED_PENDING_REATTACH,
                    correlation_id="qc-test",
                    completed_at=completed_at or datetime.now(UTC),
                )
            )
    return render_id


async def run_qc(workdir: Path, *, settings: Settings | None = None, visual: Any = None) -> Any:
    resolved = settings or config()
    async with factory()() as session:
        return await ContentQcService(
            session,
            resolved,
            S3MediaMaterializer(resolved),
            FFmpegQcProbe(resolved),
            visual if visual is not None else FakeVisualQcAdapter(resolved),
        ).process_next(workdir=workdir)


async def load_report(render_id: uuid.UUID) -> RenderQcReport:
    async with factory()() as session:
        report = await session.scalar(
            select(RenderQcReport).where(RenderQcReport.render_id == render_id)
        )
        assert report is not None
        return report


def status_of(report: RenderQcReport, check: QcCheck) -> str:
    entry = next(item for item in report.checks if item["check"] == check.value)
    return cast(str, entry["status"])


def code_of(report: RenderQcReport, check: QcCheck) -> str | None:
    entry = next(item for item in report.checks if item["check"] == check.value)
    return cast(str | None, entry["code"])


def make_business(client: TestClient, owner: dict[str, str]) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a business through the API and read back the owner it registered."""

    created = client.post("/v1/businesses", headers=owner, json={"name": "QC", "timezone": "UTC"})
    assert created.status_code == 201, created.text
    business_id = uuid.UUID(created.json()["id"])

    async def owner_id() -> uuid.UUID:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with engine.connect() as connection:
                value = await connection.scalar(
                    text("SELECT user_id FROM business_members WHERE business_id = :id LIMIT 1"),
                    {"id": str(business_id)},
                )
                assert value is not None
                return uuid.UUID(str(value))
        finally:
            await engine.dispose()

    return business_id, asyncio.run(owner_id())


# --- the deterministic checks against genuinely broken media -----------------------------------


@requires_storage
@requires_ffmpeg
def test_a_healthy_render_passes_every_deterministic_check(tmp_path: Path) -> None:
    video = tmp_path / "good.mp4"
    healthy(video)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-owner", "qc-owner@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        job = asyncio.run(run_qc(tmp_path))
        assert job is not None

    report = asyncio.run(load_report(render_id))
    assert report.status is QcRunStatus.COMPLETED
    # Every check is present; a report short of one is a report with a hole in it.
    assert [entry["check"] for entry in report.checks] == [check.value for check in QcCheck]
    for check in QcCheck:
        assert status_of(report, check) == CheckStatus.PASSED.value, check
    assert report.verdict is QcVerdict.PASSED
    assert report.recommended_path is RemediationPath.NONE
    # The measurement is the file's own account, not the render row's.
    assert report.measurement["has_audio_stream"] is True
    assert report.measurement["integrated_loudness_lufs"] is not None
    # The thresholds that produced this verdict travel with it.
    assert report.thresholds["loudness_target_lufs"] == -22.0
    assert report.qc_version == report.thresholds["version"]


@requires_storage
@requires_ffmpeg
def test_a_wholly_black_output_fails_and_asks_for_different_footage(tmp_path: Path) -> None:
    video = tmp_path / "black.mp4"
    encode(video, "color=c=black:size=1080x1920:rate=30", TONE)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-black", "qc-black@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(run_qc(tmp_path))

    report = asyncio.run(load_report(render_id))
    assert status_of(report, QcCheck.BLACK_FRAMES) == CheckStatus.FAILED.value
    assert code_of(report, QcCheck.BLACK_FRAMES) == "QC_BLACK_FRAMES_EXCEED_LIMIT"
    assert report.verdict is QcVerdict.FAILED
    # No other scene from this source can help; PRD §19.4's fifth path is the honest one.
    assert report.recommended_path is RemediationPath.REQUEST_NEW_MEDIA


@requires_storage
@requires_ffmpeg
def test_a_silent_output_fails_the_audio_check(tmp_path: Path) -> None:
    video = tmp_path / "silent.mp4"
    encode(video, "testsrc2=size=1080x1920:rate=30", SILENCE)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-silent", "qc-silent@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(run_qc(tmp_path))

    report = asyncio.run(load_report(render_id))
    # An AAC track full of digital silence satisfies "there is an audio stream" and still fails.
    assert report.measurement["has_audio_stream"] is True
    assert code_of(report, QcCheck.AUDIO_PRESENT) == "QC_AUDIO_SILENT"
    assert report.verdict is QcVerdict.FAILED
    assert report.recommended_path is RemediationPath.RETRY_RENDER


@requires_storage
@requires_ffmpeg
def test_a_held_frame_is_caught_without_being_black(tmp_path: Path) -> None:
    video = tmp_path / "frozen.mp4"
    encode(video, "color=c=blue:size=1080x1920:rate=30", TONE)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-frozen", "qc-frozen@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(run_qc(tmp_path))

    report = asyncio.run(load_report(render_id))
    assert status_of(report, QcCheck.STATIC_FRAMES) == CheckStatus.FAILED.value
    assert status_of(report, QcCheck.BLACK_FRAMES) == CheckStatus.PASSED.value


@requires_storage
@requires_ffmpeg
def test_an_output_far_from_the_planned_length_is_caught(tmp_path: Path) -> None:
    """The timeline asks for three seconds of cuts; the file is one."""

    video = tmp_path / "short.mp4"
    encode(video, "testsrc2=size=1080x1920:rate=30", TONE, seconds=1)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-short", "qc-short@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(run_qc(tmp_path))

    report = asyncio.run(load_report(render_id))
    assert code_of(report, QcCheck.DURATION_MATCHES_PLAN) == "QC_DURATION_OUT_OF_TOLERANCE"
    assert report.verdict is QcVerdict.FAILED


@requires_storage
@requires_ffmpeg
def test_an_output_that_is_not_a_container_fails_rather_than_going_unknown(
    tmp_path: Path,
) -> None:
    """A file that does not open is a verdict about the output, not an outage."""

    video = tmp_path / "broken.mp4"
    video.write_bytes(b"this is not a container, it is a sentence")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-broken", "qc-broken@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(run_qc(tmp_path))

    report = asyncio.run(load_report(render_id))
    assert status_of(report, QcCheck.CONTAINER_READABLE) == CheckStatus.FAILED.value
    assert report.verdict is QcVerdict.FAILED
    assert report.recommended_path is RemediationPath.RETRY_RENDER
    # Everything that needed the measurement is unknown rather than quietly acceptable.
    assert status_of(report, QcCheck.LOUDNESS) == CheckStatus.UNKNOWN.value
    assert report.measurement == {}


# --- fail-closed, proved three ways ------------------------------------------------------------


@requires_storage
@requires_ffmpeg
def test_a_measurement_that_could_not_run_leaves_every_measured_check_unknown(
    tmp_path: Path,
) -> None:
    """Path one: the probe itself is unavailable. Nothing was learned, so nothing is approved."""

    video = tmp_path / "good.mp4"
    healthy(video)
    settings = config(ffprobe_binary="/nonexistent/ffprobe", qc_max_attempts=1)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-blind", "qc-blind@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(run_qc(tmp_path, settings=settings))

    report = asyncio.run(load_report(render_id))
    # The run failed as a *run*; the verdict is still recorded, and it is not approval.
    assert report.status is QcRunStatus.FAILED
    assert report.failure_code == "QC_PROBE_UNAVAILABLE"
    assert report.verdict is QcVerdict.NEEDS_REVIEW
    assert report.recommended_path is RemediationPath.HUMAN_REVIEW
    assert report.measurement == {}
    # Everything that needed the file is unknown. The three that can pass are facts about rows
    # and about the document, not about the output: no voiceover to drift, no verified reference
    # to have gone stale, no logo to be invisible.
    unmeasured = set(QcCheck) - {
        QcCheck.SPEECH_SYNC,
        QcCheck.VERIFIED_VALUES_CURRENT,
        QcCheck.LOGO_VISIBLE,
    }
    for check in unmeasured:
        assert status_of(report, check) == CheckStatus.UNKNOWN.value, check


@requires_storage
@requires_ffmpeg
def test_a_disabled_vision_provider_keeps_the_verdict_at_needs_review(tmp_path: Path) -> None:
    """Path two: the model checks cannot be answered, so a flawless output is still unreviewed.

    This is the state of every production deployment until W08's benchmark picks a provider, and
    it is intended: an approval nobody computed must never look like one.
    """

    video = tmp_path / "good.mp4"
    healthy(video)
    settings = config(visual_qc_adapter="disabled")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-noeyes", "qc-noeyes@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(
            run_qc(
                tmp_path,
                settings=settings,
                visual=DisabledVisualQcAdapter(reason="switched off for this test"),
            )
        )

    report = asyncio.run(load_report(render_id))
    assert report.status is QcRunStatus.COMPLETED
    for check in ASKED_OF_THE_MODEL:
        assert status_of(report, check) == CheckStatus.UNKNOWN.value, check
        assert code_of(report, check) == "QC_VISUAL_PROVIDER_DISABLED"
    # This timeline draws no logo, so "is the logo visible" is not applicable rather than
    # unmeasured — a known state of the document, not a question nobody asked.
    assert status_of(report, QcCheck.LOGO_VISIBLE) == CheckStatus.PASSED.value
    for check in (QcCheck.CONTAINER_READABLE, QcCheck.AUDIO_PRESENT, QcCheck.LOUDNESS):
        assert status_of(report, check) == CheckStatus.PASSED.value
    assert report.verdict is QcVerdict.NEEDS_REVIEW
    assert report.recommended_path is RemediationPath.HUMAN_REVIEW
    # No call left this process, so nothing was billed and no usage row exists.
    assert report.provider_usage_id is None


@requires_storage
@requires_ffmpeg
def test_no_check_can_be_left_out_of_a_report(tmp_path: Path) -> None:
    """Path three: whatever happened, the report carries the whole check set."""

    video = tmp_path / "black.mp4"
    encode(video, "color=c=black:size=1080x1920:rate=30", SILENCE)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-full", "qc-full@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(run_qc(tmp_path))

    report = asyncio.run(load_report(render_id))
    assert {entry["check"] for entry in report.checks} == {check.value for check in QcCheck}
    assert all(entry["status"] in {"passed", "failed", "unknown"} for entry in report.checks)
    assert all("remediation" in entry for entry in report.checks)


# --- price and date conformance (acceptance criterion 4) ----------------------------------------


async def seed_priced_product(business_id: uuid.UUID, *, price_minor: int) -> uuid.UUID:
    product_id = uuid.uuid4()
    async with factory()() as session:
        async with session.begin():
            session.add(
                Product(
                    id=product_id,
                    business_id=business_id,
                    name="Menemen",
                    normalized_name="menemen",
                    category=None,
                    description=None,
                    status=ProductStatus.ACTIVE,
                    stock_status=StockStatus.AVAILABLE,
                    valid_locations=[],
                )
            )
            session.add(
                ProductPrice(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    product_id=product_id,
                    price_minor=price_minor,
                    currency="TRY",
                    effective_from=datetime.now(UTC) - timedelta(days=7),
                    effective_to=None,
                )
            )
    return product_id


async def supersede_price(business_id: uuid.UUID, product_id: uuid.UUID, *, at: datetime) -> None:
    """Close the open row and append a new one, exactly as a real price change does."""

    async with factory()() as session:
        async with session.begin():
            current = await session.scalar(
                select(ProductPrice).where(
                    ProductPrice.product_id == product_id, ProductPrice.effective_to.is_(None)
                )
            )
            assert current is not None
            current.effective_to = at
            session.add(
                ProductPrice(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    product_id=product_id,
                    price_minor=current.price_minor + 5_000,
                    currency="TRY",
                    effective_from=at,
                    effective_to=None,
                )
            )


@requires_storage
@requires_ffmpeg
def test_a_price_changed_after_the_render_is_caught(tmp_path: Path) -> None:
    """The frame quotes a figure the record no longer holds. Nothing else in the file is wrong."""

    video = tmp_path / "good.mp4"
    healthy(video)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-price", "qc-price@example.com"))
        product_id = asyncio.run(seed_priced_product(business_id, price_minor=14_990))
        rendered_at = datetime.now(UTC) - timedelta(hours=2)
        render_id = asyncio.run(
            seed_render(
                business_id,
                user_id,
                video,
                document=timeline_document(
                    [verified_overlay("verified_product.price", product_id)]
                ),
                completed_at=rendered_at,
            )
        )
        asyncio.run(supersede_price(business_id, product_id, at=rendered_at + timedelta(hours=1)))
        asyncio.run(run_qc(tmp_path))

    report = asyncio.run(load_report(render_id))
    assert code_of(report, QcCheck.VERIFIED_VALUES_CURRENT) == "QC_VERIFIED_VALUE_SUPERSEDED"
    assert report.verdict is QcVerdict.FAILED
    # Re-rendering would quietly print a figure nobody approved, so a person decides.
    assert report.recommended_path is RemediationPath.HUMAN_REVIEW
    # The old and the new price are nowhere in the report.
    assert "14990" not in repr(report.checks)
    assert "19990" not in repr(report.checks)


@requires_storage
@requires_ffmpeg
def test_an_unchanged_price_is_not_reported_stale(tmp_path: Path) -> None:
    video = tmp_path / "good.mp4"
    healthy(video)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-ok", "qc-ok@example.com"))
        product_id = asyncio.run(seed_priced_product(business_id, price_minor=14_990))
        render_id = asyncio.run(
            seed_render(
                business_id,
                user_id,
                video,
                document=timeline_document(
                    [verified_overlay("verified_product.price", product_id)]
                ),
            )
        )
        asyncio.run(run_qc(tmp_path))

    report = asyncio.run(load_report(render_id))
    assert status_of(report, QcCheck.VERIFIED_VALUES_CURRENT) == CheckStatus.PASSED.value


@requires_storage
@requires_ffmpeg
def test_a_campaign_that_ended_after_the_render_is_caught(tmp_path: Path) -> None:
    video = tmp_path / "good.mp4"
    healthy(video)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-camp", "qc-camp@example.com"))
        campaign_id = uuid.uuid4()

        async def seed_campaign() -> None:
            async with factory()() as session:
                async with session.begin():
                    session.add(
                        CampaignOffer(
                            id=campaign_id,
                            business_id=business_id,
                            name="Yaz kampanyası",
                            status=CampaignOfferStatus.ACTIVE,
                            approval_status=CampaignApprovalStatus.APPROVED,
                            starts_at=datetime.now(UTC) - timedelta(days=10),
                            # Already over by the time QC runs.
                            ends_at=datetime.now(UTC) - timedelta(hours=1),
                            discount_type=DiscountType.PERCENTAGE,
                            discount_percent=20,
                            discount_amount_minor=None,
                            discount_currency=None,
                            valid_locations=[],
                            stock_limit=None,
                            coupon_code=None,
                            legal_text=None,
                        )
                    )

        asyncio.run(seed_campaign())
        render_id = asyncio.run(
            seed_render(
                business_id,
                user_id,
                video,
                document=timeline_document(
                    [verified_overlay("verified_campaign.title", campaign_id)]
                ),
                completed_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        asyncio.run(run_qc(tmp_path))

    report = asyncio.run(load_report(render_id))
    assert code_of(report, QcCheck.VERIFIED_VALUES_CURRENT) == "QC_VERIFIED_VALUE_OUT_OF_WINDOW"
    assert report.verdict is QcVerdict.FAILED


# --- the beat-driven path (follow-up 1) ----------------------------------------------------------


@requires_storage
@requires_ffmpeg
def test_the_beat_entry_drives_a_real_qc_run_through_the_registered_task(tmp_path: Path) -> None:
    """Follow the chain the deployment actually follows: beat entry → task registry → report.

    The task function is resolved by the name the beat schedule holds rather than imported, so
    there is no hand-written link in the middle. If the entry, the registration and the drain
    ever stop agreeing, this fails instead of a worker logging an unregistered-task error every
    thirty seconds where nobody reads it.

    Adapters are substituted on the process context the way `test_celery_orchestration.py` does:
    the composition root reads environment settings, and this suite runs against real storage.
    """

    video = tmp_path / "good.mp4"
    healthy(video)
    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-beat", "qc-beat@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video, settings=settings))

    # Importing the module is what registers the tasks — the worker entry point does exactly
    # this, and a beat entry naming a task nobody imported is a tick that logs an error forever.
    import app.worker.tasks  # noqa: F401

    # Renamed by slice 2E: the tick is a sweep now that `content.qc.requested` triggers the drain.
    # The chain being asserted is unchanged — beat entry, registered task, real report.
    task_name = celery_app.conf.beat_schedule["sweep-content-qc"]["task"]
    assert task_name in celery_app.tasks

    composition.start_worker_process()
    context = composition.get_worker_context()
    composition._context = replace(
        context,
        settings=settings,
        materializer=S3MediaMaterializer(settings),
        qc_probe=FFmpegQcProbe(settings),
        visual_qc=FakeVisualQcAdapter(settings),
    )
    try:
        drained = celery_app.tasks[task_name]()
        assert drained == {"status": "drained", "processed": 1}
        # An idle tick claims nothing and stops after one query rather than spinning the batch.
        assert celery_app.tasks[task_name]() == {"status": "drained", "processed": 0}
    finally:
        composition.shutdown_worker_process()

    report = asyncio.run(load_report(render_id))
    assert report.status is QcRunStatus.COMPLETED
    assert len(report.checks) == len(QcCheck)


@requires_storage
@requires_ffmpeg
def test_a_render_that_finished_while_the_worker_was_down_is_still_picked_up(
    tmp_path: Path,
) -> None:
    """Why the claim scans instead of waiting for a message.

    The render here completes with no QC worker running and no queue entry anywhere — exactly
    what a restart, a lost broker message or a deploy window produces. The next tick finds it,
    because the claim asks the database "which succeeded render has no report" rather than
    "what is in the queue". That is the property the scan buys, and it is the reason it stays
    even when an event-driven enqueue is added in front of it.
    """

    video = tmp_path / "good.mp4"
    healthy(video)
    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-down", "qc-down@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video, settings=settings))

    async def queue_is_empty() -> bool:
        async with factory()() as session:
            jobs = await session.scalars(
                select(BackgroundJob).where(BackgroundJob.job_type == "content.qc")
            )
            return not list(jobs)

    assert asyncio.run(queue_is_empty()), "nothing announced this render to anyone"
    assert asyncio.run(run_qc(tmp_path)) is not None

    report = asyncio.run(load_report(render_id))
    assert report.status is QcRunStatus.COMPLETED

    async def job_exists() -> bool:
        async with factory()() as session:
            job = await session.scalar(
                select(BackgroundJob).where(BackgroundJob.job_type == "content.qc")
            )
            return job is not None and job.status.value == "succeeded"

    # The job row is created by the claim, not before it: the durable record exists from the
    # moment work starts and is settled when it ends.
    assert asyncio.run(job_exists())


# --- the durable job, the read endpoint, and tenant isolation -----------------------------------


@requires_storage
@requires_ffmpeg
def test_automatic_qc_runs_once_per_render(tmp_path: Path) -> None:
    """A second drain finds nothing: the report row is what makes the claim idempotent."""

    video = tmp_path / "good.mp4"
    healthy(video)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-once", "qc-once@example.com"))
        asyncio.run(seed_render(business_id, user_id, video))
        assert asyncio.run(run_qc(tmp_path)) is not None
        assert asyncio.run(run_qc(tmp_path)) is None


@requires_storage
@requires_ffmpeg
def test_the_run_uses_a_durable_job_with_an_attempt_row(tmp_path: Path) -> None:
    video = tmp_path / "good.mp4"
    healthy(video)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-job", "qc-job@example.com"))
        asyncio.run(seed_render(business_id, user_id, video))
        job = asyncio.run(run_qc(tmp_path))

    assert job is not None
    assert job.job_type == "content.qc"
    assert job.resource_type == "render_qc_report"
    assert job.status.value == "succeeded"
    assert job.attempt_count == 1
    assert job.timeout_seconds > 0
    assert job.correlation_id == "qc-test"


@requires_storage
@requires_ffmpeg
def test_a_billed_inspection_writes_its_own_usage_row(tmp_path: Path) -> None:
    """Every external call is attributed (§39.1), and the report names the row that settled it."""

    video = tmp_path / "good.mp4"
    healthy(video)
    settings = config(visual_qc_max_cost_minor=100)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-usage", "qc-usage@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(
            run_qc(
                tmp_path,
                settings=settings,
                visual=FakeVisualQcAdapter(settings, cost_minor=7),
            )
        )

    report = asyncio.run(load_report(render_id))
    assert report.provider_usage_id is not None
    assert report.route_snapshot["capability"] == "visual_qc"
    assert report.route_snapshot["max_cost_minor"] == 100

    async def usage() -> ProviderUsage:
        async with factory()() as session:
            row = await session.scalar(
                select(ProviderUsage).where(ProviderUsage.id == report.provider_usage_id)
            )
            assert row is not None
            return row

    row = asyncio.run(usage())
    assert row.capability == "visual_qc"
    assert row.actual_cost_minor == 7
    assert row.outcome == "succeeded"


@requires_storage
@requires_ffmpeg
def test_a_ceiling_stops_the_call_before_it_happens(tmp_path: Path) -> None:
    """A ceiling enforced on the way back has already been paid."""

    video = tmp_path / "good.mp4"
    healthy(video)
    settings = config(visual_qc_max_cost_minor=1)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, auth("qc-cap", "qc-cap@example.com"))
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(
            run_qc(
                tmp_path,
                settings=settings,
                visual=FakeVisualQcAdapter(settings, cost_minor=500),
            )
        )

    report = asyncio.run(load_report(render_id))
    for check in ASKED_OF_THE_MODEL:
        assert code_of(report, check) == "QC_VISUAL_COST_LIMIT_EXCEEDED"
    assert report.provider_usage_id is None
    assert report.verdict is QcVerdict.NEEDS_REVIEW


@requires_storage
@requires_ffmpeg
def test_the_report_is_readable_and_carries_no_signed_url(tmp_path: Path) -> None:
    video = tmp_path / "good.mp4"
    healthy(video)
    owner = auth("qc-read", "qc-read@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, owner)
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(run_qc(tmp_path))

        response = client.get(
            f"/v1/businesses/{business_id}/content/renders/{render_id}/qc", headers=owner
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["checks"]) == len(QcCheck)
        assert body["verdict"] in {"passed", "needs_review", "failed"}
        assert body["thresholds"]["version"] == body["qc_version"]
        # The report is a record, not a download link. Neither the signature material nor the
        # object key may travel in it.
        raw = response.text
        for sentinel in ("X-Amz-Signature", "X-Amz-Credential", "https://", "renders/"):
            assert sentinel not in raw, sentinel


@requires_storage
@requires_ffmpeg
def test_another_tenants_report_is_not_readable(tmp_path: Path) -> None:
    video = tmp_path / "good.mp4"
    healthy(video)
    owner = auth("qc-mine", "qc-mine@example.com")
    intruder = auth("qc-theirs", "qc-theirs@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, owner)
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        asyncio.run(run_qc(tmp_path))

        other_business, _ = make_business(client, intruder)
        # The intruder's own business, someone else's render id: the query is tenant-scoped, so
        # the real id answers exactly like a made-up one.
        response = client.get(
            f"/v1/businesses/{other_business}/content/renders/{render_id}/qc", headers=intruder
        )
        assert response.status_code == 404, response.text
        # And the intruder cannot reach the owner's business at all.
        forbidden = client.get(
            f"/v1/businesses/{business_id}/content/renders/{render_id}/qc", headers=intruder
        )
        assert forbidden.status_code == 404, forbidden.text


@requires_storage
@requires_ffmpeg
def test_a_render_with_no_report_answers_404_rather_than_an_empty_verdict(
    tmp_path: Path,
) -> None:
    video = tmp_path / "good.mp4"
    healthy(video)
    owner = auth("qc-none", "qc-none@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id, user_id = make_business(client, owner)
        render_id = asyncio.run(seed_render(business_id, user_id, video))
        response = client.get(
            f"/v1/businesses/{business_id}/content/renders/{render_id}/qc", headers=owner
        )
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "RENDER_QC_REPORT_NOT_FOUND"
