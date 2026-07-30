"""Reproducible development seed for the media analysis result screen.

Why this exists: the demo tenant and its analyzed asset were once created by hand, and a
Docker reset destroyed them with no way to rebuild. Every flow that creates development data
now has a script.

Run it from `services/api`:

    python -m scripts.seed_dev

or inside the development stack:

    docker compose exec -T api python -m scripts.seed_dev

The script is idempotent. Rows are keyed on fixed UUIDs, so a second run updates the same
rows instead of adding new ones; where a unique natural key exists (user email, business
slug, provider subject) an already-present owner is adopted, because signing in with the demo
token provisions a user before any seeding. It refuses to run outside `development` and holds
no credentials of its own.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.infrastructure.database.session import create_database
from app.modules.businesses.models import (
    Business,
    BusinessMember,
    BusinessRole,
    BusinessStatus,
    MembershipStatus,
)
from app.modules.identity.models import ExternalIdentity, User, UserStatus
from app.modules.media.models import (
    IngestStatus,
    MalwareScanStatus,
    MediaAsset,
    MediaAssetStatus,
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
from app.modules.media.video_understanding import SceneAnalysisMode
from app.modules.operations.models import BackgroundJob, JobStatus

# Fixed identifiers are what make the seed idempotent and referable from a client.
USER_ID = UUID("00000000-0000-4000-8000-000000000001")
IDENTITY_ID = UUID("00000000-0000-4000-8000-000000000002")
BUSINESS_ID = UUID("00000000-0000-4000-8000-000000000003")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000000004")
ASSET_ID = UUID("00000000-0000-4000-8000-000000000005")
UPLOAD_SESSION_ID = UUID("00000000-0000-4000-8000-000000000006")
INSPECTION_ID = UUID("00000000-0000-4000-8000-000000000007")
SCAN_ID = UUID("00000000-0000-4000-8000-000000000008")
METADATA_ID = UUID("00000000-0000-4000-8000-000000000009")
ANALYSIS_ID = UUID("00000000-0000-4000-8000-00000000000a")
TRANSCRIPT_ID = UUID("00000000-0000-4000-8000-00000000000b")

SEED_SUBJECT = "seed-demo-owner"
SEED_EMAIL = "demo@socialpilot.local"
BUSINESS_NAME = "Demo Isletme"
BUSINESS_SLUG = "demo-isletme"
ASSET_BYTE_SIZE = 8_452_096
DURATION_MS = 24_000

SCENES: tuple[tuple[int, int, int, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        0,
        0,
        8_000,
        "Kafenin vitrini ve tabelasi gunes isiginda gorunuyor.",
        "Genis aci: cam vitrin, ahsap tabela, kaldirimda masalar.",
        ("kafe", "vitrin", "gun isigi"),
        ("tabela", "masa", "sandalye"),
    ),
    (
        1,
        8_000,
        16_500,
        "Barista espresso hazirliyor, buhar ve krema detaylari one cikiyor.",
        "Yakin plan: espresso makinesi, portafiltre, sut kopurtme.",
        ("barista", "espresso", "yakin plan"),
        ("espresso makinesi", "fincan", "sut surahisi"),
    ),
    (
        2,
        16_500,
        24_000,
        "Musteri kahvesini alip kameraya gulumsuyor, kapanis karesi.",
        "Orta plan: tezgah onunde musteri, arkada menu tahtasi.",
        ("musteri", "kapanis", "gulumseme"),
        ("bardak", "menu tahtasi", "tezgah"),
    ),
)

SEGMENTS: tuple[tuple[int, int, int, str], ...] = (
    (0, 400, 7_200, "Gunaydin, bugun size yeni sezon kahvelerimizi tanitiyoruz."),
    (1, 8_200, 15_900, "Cekirdekleri her sabah taze ogutuyoruz, bu yuzden aroma cok yogun."),
    (2, 16_800, 23_400, "Hafta sonu gelin, ilk kahveniz bizden olsun."),
)
FULL_TEXT = " ".join(text for _, _, _, text in SEGMENTS)


def scene_id(index: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-1000000000{index:02x}")


def understanding_id(index: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-2000000000{index:02x}")


def segment_id(index: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-3000000000{index:02x}")


def job_id(index: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-4000000000{index:02x}")


async def upsert[ModelT](session: AsyncSession, model: type[ModelT], identifier: UUID) -> ModelT:
    """Return the existing row for a fixed identifier, or a new instance registered for insert."""

    existing = await session.get(model, identifier)
    if existing is not None:
        return existing
    instance = model()
    instance.id = identifier  # type: ignore[attr-defined]
    session.add(instance)
    return instance


async def adopt[ModelT](
    session: AsyncSession, model: type[ModelT], identifier: UUID, natural_key: Select[tuple[ModelT]]
) -> ModelT:
    """Prefer whichever row already owns the natural key, then fall back to the fixed id.

    Signing in with the demo token provisions a user for that email before any seeding, and
    email, slug, and provider subject are all unique. Inserting the fixed identifier anyway
    would violate those constraints, so an existing owner is adopted instead.
    """

    existing = (await session.execute(natural_key)).scalars().first()
    if existing is not None:
        return existing
    return await upsert(session, model, identifier)


async def seed(session: AsyncSession, settings: Settings) -> None:
    now = datetime.now(UTC)

    user = await adopt(
        session, User, USER_ID, select(User).where(func.lower(User.email) == SEED_EMAIL)
    )
    user.email = SEED_EMAIL
    user.display_name = "Demo Sahibi"
    user.status = UserStatus.ACTIVE

    identity = await adopt(
        session,
        ExternalIdentity,
        IDENTITY_ID,
        select(ExternalIdentity).where(
            ExternalIdentity.provider == "local",
            ExternalIdentity.provider_subject == SEED_SUBJECT,
        ),
    )
    identity.user_id = user.id
    identity.provider = "local"
    identity.provider_subject = SEED_SUBJECT
    identity.email_at_provider = SEED_EMAIL
    identity.last_seen_at = now

    business = await adopt(
        session, Business, BUSINESS_ID, select(Business).where(Business.slug == BUSINESS_SLUG)
    )
    business.name = BUSINESS_NAME
    business.slug = BUSINESS_SLUG
    business.status = BusinessStatus.ACTIVE
    business.timezone = "Europe/Istanbul"
    business.created_by_user_id = user.id

    member = await adopt(
        session,
        BusinessMember,
        MEMBER_ID,
        select(BusinessMember).where(
            BusinessMember.business_id == business.id, BusinessMember.user_id == user.id
        ),
    )
    member.business_id = business.id
    member.user_id = user.id
    member.role = BusinessRole.OWNER
    member.status = MembershipStatus.ACTIVE
    member.joined_at = now

    business_id = business.id
    object_key = f"tenant/{business_id}/media/{ASSET_ID}/original/seedmedia"
    checksum = hashlib.sha256(f"seed-{ASSET_ID}".encode()).hexdigest()

    asset = await upsert(session, MediaAsset, ASSET_ID)
    asset.business_id = business_id
    asset.created_by_user_id = user.id
    asset.storage_object_key = object_key
    asset.content_type = "video/mp4"
    asset.byte_size = ASSET_BYTE_SIZE
    asset.sha256_checksum = checksum
    asset.status = MediaAssetStatus.UPLOADED
    asset.ingest_status = IngestStatus.READY_FOR_ANALYSIS
    asset.uploaded_at = now - timedelta(minutes=10)

    upload = await upsert(session, MediaUploadSession, UPLOAD_SESSION_ID)
    upload.business_id = business_id
    upload.asset_id = ASSET_ID
    upload.storage_upload_id = f"seed-{UPLOAD_SESSION_ID.hex}"
    upload.expected_part_count = 2
    upload.status = UploadSessionStatus.COMPLETED
    upload.expires_at = now - timedelta(minutes=5)
    upload.completed_at = now - timedelta(minutes=10)

    inspection = await upsert(session, MediaIngestInspection, INSPECTION_ID)
    inspection.business_id = business_id
    inspection.asset_id = ASSET_ID
    inspection.storage_byte_size = ASSET_BYTE_SIZE
    inspection.storage_content_type = "video/mp4"
    inspection.storage_sha256_checksum = checksum
    inspection.storage_etag = "seed-etag"
    inspection.detected_content_type = "video/mp4"

    scan = await upsert(session, MediaMalwareScan, SCAN_ID)
    scan.business_id = business_id
    scan.asset_id = ASSET_ID
    scan.status = MalwareScanStatus.CLEAN
    scan.scanner_name = "seed-scanner"
    scan.safe_error_code = None

    metadata = await upsert(session, MediaTechnicalMetadata, METADATA_ID)
    metadata.business_id = business_id
    metadata.asset_id = ASSET_ID
    metadata.container_format = "mov,mp4,m4a"
    metadata.duration_ms = DURATION_MS
    metadata.file_size = ASSET_BYTE_SIZE
    metadata.video_codec = "h264"
    metadata.width = 1080
    metadata.height = 1920
    metadata.display_aspect_ratio = "9:16"
    metadata.frame_rate_numerator = 30
    metadata.frame_rate_denominator = 1
    metadata.bit_rate = 2_816_000
    metadata.rotation_degrees = 0
    metadata.has_audio = True
    metadata.audio_codec = "aac"
    metadata.audio_sample_rate = 48_000
    metadata.audio_channel_count = 2
    metadata.stream_count = 2

    analysis = await upsert(session, MediaTechnicalAnalysis, ANALYSIS_ID)
    analysis.business_id = business_id
    analysis.asset_id = ASSET_ID
    analysis.status = TechnicalAnalysisStatus.COMPLETED
    analysis.safe_error_code = None
    analysis.completed_at = now - timedelta(minutes=8)

    transcript = await upsert(session, Transcript, TRANSCRIPT_ID)
    transcript.business_id = business_id
    transcript.asset_id = ASSET_ID
    transcript.language = "tr"
    transcript.duration_ms = DURATION_MS
    transcript.full_text = FULL_TEXT
    transcript.provider = "seed-asr"
    transcript.status = TranscriptStatus.COMPLETED

    for index, start_ms, end_ms, text in SEGMENTS:
        segment = await upsert(session, TranscriptSegment, segment_id(index))
        segment.transcript_id = TRANSCRIPT_ID
        segment.segment_index = index
        segment.start_ms = start_ms
        segment.end_ms = end_ms
        segment.text = text
        segment.confidence = 0.93
        segment.speaker_label = "speaker_1"

    for index, start_ms, end_ms, summary, visual, labels, objects in SCENES:
        scene = await upsert(session, MediaScene, scene_id(index))
        scene.business_id = business_id
        scene.asset_id = ASSET_ID
        scene.scene_index = index
        scene.start_ms = start_ms
        scene.end_ms = end_ms
        scene.duration_ms = end_ms - start_ms
        scene.confidence = 0.88

        understanding = await upsert(session, MediaSceneUnderstanding, understanding_id(index))
        understanding.business_id = business_id
        understanding.asset_id = ASSET_ID
        understanding.scene_id = scene_id(index)
        understanding.status = SceneUnderstandingStatus.COMPLETED
        understanding.provider = "seed-vlm"
        understanding.model_name = "seed-vision-1"
        understanding.summary = summary
        understanding.visual_description = visual
        understanding.transcript_context = SEGMENTS[index][3]
        understanding.confidence = 0.82
        understanding.labels = list(labels)
        understanding.objects = list(objects)
        understanding.actions = ["hazirlama", "sunum"]
        understanding.visible_text = ["Yeni sezon"]
        understanding.dominant_topics = ["kahve", "kafe"]
        understanding.safety_flags = []
        # The mode must be a real SceneAnalysisMode value, otherwise the summary endpoint
        # omits the coverage block rather than reporting a number it cannot justify.
        understanding.quality_signals = {
            "analysis_mode": SceneAnalysisMode.VISUAL_AND_TRANSCRIPT.value,
            "visual_input_available": True,
        }

    stages = (
        ("media.ingest", settings.media_ingest_timeout_seconds, settings.media_ingest_max_attempts),
        ("media.technical_analysis", settings.media_technical_job_timeout_seconds, 3),
        ("media.scene_speech_analysis", settings.scene_speech_job_timeout_seconds, 3),
        (
            "media.video_understanding",
            settings.video_understanding_job_max_timeout_seconds,
            settings.video_understanding_max_attempts,
        ),
    )
    for index, (job_type, timeout_seconds, max_attempts) in enumerate(stages):
        job = await upsert(session, BackgroundJob, job_id(index))
        job.business_id = business_id
        job.job_type = job_type
        job.resource_type = "media_asset"
        job.resource_id = ASSET_ID
        job.status = JobStatus.SUCCEEDED
        job.timeout_seconds = timeout_seconds
        job.attempt_count = 1
        job.max_attempts = max_attempts
        job.correlation_id = f"seed-{index}"
        job.last_error_code = None
        job.last_error_summary = None
        job.next_attempt_at = None
        job.started_at = now - timedelta(minutes=9 - index)
        job.finished_at = now - timedelta(minutes=8 - index)


async def run(settings: Settings) -> None:
    if settings.app_env != "development":
        raise SystemExit(
            f"seed_dev refuses to run with APP_ENV={settings.app_env}; "
            "it is a development-only fixture"
        )
    database = create_database(settings)
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await seed(session, settings)
            # Report the tenant that actually exists: an adopted row keeps its own id.
            business = (
                await session.execute(select(Business).where(Business.slug == BUSINESS_SLUG))
            ).scalar_one()
            name, business_id = business.name, business.id
    finally:
        await database.dispose()
    print(f"seeded business {name!r} ({business_id}) with analyzed asset {ASSET_ID}")


def main() -> None:
    asyncio.run(run(get_settings()))


if __name__ == "__main__":
    main()
