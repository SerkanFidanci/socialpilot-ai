"""Guards for the development seed: idempotent, development-only, no invented values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.modules.businesses.models import (
    Business,
    BusinessMember,
    BusinessRole,
    BusinessStatus,
    MembershipStatus,
)
from app.modules.identity.models import ExternalIdentity, User
from app.modules.media.models import (
    IngestStatus,
    MediaAsset,
    MediaAssetStatus,
    MediaScene,
    MediaSceneUnderstanding,
    TranscriptSegment,
)
from app.modules.media.video_understanding import SceneAnalysisMode
from app.modules.operations.models import BackgroundJob, JobStatus
from scripts import seed_dev


class NaturalKeyResult:
    def __init__(self, row: Any | None) -> None:
        self._row = row

    def scalars(self) -> NaturalKeyResult:
        return self

    def first(self) -> Any | None:
        return self._row


class RecordingSession:
    """Stands in for an AsyncSession, serving `get` from whatever was added before.

    Natural-key lookups cannot be evaluated without a database, so `owners` scripts what a
    given model's lookup should return.
    """

    def __init__(self, owners: dict[type[Any], Any] | None = None) -> None:
        self.rows: dict[tuple[type[Any], UUID], Any] = {}
        self.added: list[Any] = []
        self.owners = owners or {}

    async def get(self, model: type[Any], identifier: UUID) -> Any | None:
        return self.rows.get((model, identifier))

    async def execute(self, statement: Any) -> NaturalKeyResult:
        model = statement.column_descriptions[0]["entity"]
        return NaturalKeyResult(self.owners.get(model))

    def add(self, instance: Any) -> None:
        self.added.append(instance)
        self.rows[(type(instance), instance.id)] = instance


def settings(app_env: str = "development") -> Settings:
    return Settings.model_validate(
        {
            "app_env": app_env,
            "database_url": "postgresql+asyncpg://user:password@localhost:5432/socialpilot",
            "redis_url": "redis://localhost:6379/0",
            "celery_broker_url": "redis://localhost:6379/1",
            "celery_result_backend": "redis://localhost:6379/2",
            "local_identity_signing_key": SecretStr("development-seed-signing-key-not-for-prod"),
            "storage_adapter": "fake",
        }
    )


async def test_second_run_reuses_every_row_it_created() -> None:
    session = RecordingSession()

    await seed_dev.seed(session, settings())  # type: ignore[arg-type]
    first_pass = list(session.added)
    session.added.clear()
    await seed_dev.seed(session, settings())  # type: ignore[arg-type]

    assert first_pass
    # Nothing new is inserted on a repeat run, so the demo tenant cannot be duplicated.
    assert session.added == []


async def test_seed_adopts_a_user_that_already_owns_the_demo_email() -> None:
    """A demo-token sign-in provisions this email first; the seed must not fight it."""

    signed_in = User()
    signed_in.id = UUID("99999999-9999-4999-8999-999999999999")
    signed_in.email = seed_dev.SEED_EMAIL
    session = RecordingSession(owners={User: signed_in})

    await seed_dev.seed(session, settings())  # type: ignore[arg-type]

    assert signed_in not in session.added
    # Everything the tenant owns points at the adopted user, not the fixed identifier.
    assert session.rows[(ExternalIdentity, seed_dev.IDENTITY_ID)].user_id == signed_in.id
    assert session.rows[(Business, seed_dev.BUSINESS_ID)].created_by_user_id == signed_in.id
    assert session.rows[(BusinessMember, seed_dev.MEMBER_ID)].user_id == signed_in.id
    assert session.rows[(MediaAsset, seed_dev.ASSET_ID)].created_by_user_id == signed_in.id


async def test_seed_adopts_a_business_that_already_owns_the_demo_slug() -> None:
    existing = Business()
    existing.id = UUID("88888888-8888-4888-8888-888888888888")
    existing.slug = seed_dev.BUSINESS_SLUG
    session = RecordingSession(owners={Business: existing})

    await seed_dev.seed(session, settings())  # type: ignore[arg-type]

    assert existing not in session.added
    # Every tenant-scoped row follows the adopted business, so no cross-tenant rows appear.
    asset = session.rows[(MediaAsset, seed_dev.ASSET_ID)]
    assert asset.business_id == existing.id
    assert str(existing.id) in asset.storage_object_key
    for scene in (session.rows[(MediaScene, seed_dev.scene_id(index))] for index in range(3)):
        assert scene.business_id == existing.id
    for job in (session.rows[(BackgroundJob, seed_dev.job_id(index))] for index in range(4)):
        assert job.business_id == existing.id


async def test_seed_fills_every_stage_the_result_screen_reads() -> None:
    session = RecordingSession()

    await seed_dev.seed(session, settings())  # type: ignore[arg-type]

    by_type: dict[type[Any], list[Any]] = {}
    for instance in session.added:
        by_type.setdefault(type(instance), []).append(instance)

    asset = by_type[MediaAsset][0]
    assert asset.status == MediaAssetStatus.UPLOADED
    assert asset.ingest_status == IngestStatus.READY_FOR_ANALYSIS
    scenes = by_type[MediaScene]
    understandings = by_type[MediaSceneUnderstanding]
    segments = by_type[TranscriptSegment]
    assert len(scenes) == len(understandings) == len(segments) == 3
    # Scenes must tile the timeline in order, or the result screen renders gaps.
    assert [scene.scene_index for scene in scenes] == [0, 1, 2]
    assert scenes[0].start_ms == 0
    for previous, current in zip(scenes, scenes[1:], strict=False):
        assert previous.end_ms == current.start_ms
        assert current.duration_ms == current.end_ms - current.start_ms
    # Coverage is only reported when every mode is a real service-decided value.
    for understanding in understandings:
        assert understanding.quality_signals["analysis_mode"] in set(SceneAnalysisMode)
    jobs = {job.job_type: job for job in by_type[BackgroundJob]}
    assert set(jobs) == {
        "media.ingest",
        "media.technical_analysis",
        "media.scene_speech_analysis",
        "media.video_understanding",
    }
    for job in jobs.values():
        assert job.status == JobStatus.SUCCEEDED
        assert job.attempt_count <= job.max_attempts
        assert job.timeout_seconds > 0


async def test_seed_owner_can_reach_the_tenant() -> None:
    session = RecordingSession()

    await seed_dev.seed(session, settings())  # type: ignore[arg-type]

    business = session.rows[(Business, seed_dev.BUSINESS_ID)]
    member = session.rows[(BusinessMember, seed_dev.MEMBER_ID)]
    identity = session.rows[(ExternalIdentity, seed_dev.IDENTITY_ID)]
    assert business.status == BusinessStatus.ACTIVE
    assert member.role == BusinessRole.OWNER
    assert member.status == MembershipStatus.ACTIVE
    assert member.business_id == seed_dev.BUSINESS_ID
    assert member.user_id == seed_dev.USER_ID
    # The local verifier resolves a token subject through this provider/subject pair.
    assert (identity.provider, identity.provider_subject) == ("local", seed_dev.SEED_SUBJECT)


def test_fixed_identifiers_do_not_collide() -> None:
    identifiers = [
        seed_dev.USER_ID,
        seed_dev.IDENTITY_ID,
        seed_dev.BUSINESS_ID,
        seed_dev.MEMBER_ID,
        seed_dev.ASSET_ID,
        seed_dev.UPLOAD_SESSION_ID,
        seed_dev.INSPECTION_ID,
        seed_dev.SCAN_ID,
        seed_dev.METADATA_ID,
        seed_dev.ANALYSIS_ID,
        seed_dev.TRANSCRIPT_ID,
        *(seed_dev.scene_id(index) for index in range(3)),
        *(seed_dev.understanding_id(index) for index in range(3)),
        *(seed_dev.segment_id(index) for index in range(3)),
        *(seed_dev.job_id(index) for index in range(4)),
    ]

    assert len(set(identifiers)) == len(identifiers)


@dataclass(frozen=True)
class EnvironmentOnly:
    """The guard runs before any database work, so only the environment is ever read."""

    app_env: str


@pytest.mark.parametrize("app_env", ["production", "test"])
async def test_seed_refuses_to_run_outside_development(app_env: str) -> None:
    with pytest.raises(SystemExit, match=app_env):
        await seed_dev.run(cast(Settings, EnvironmentOnly(app_env)))
