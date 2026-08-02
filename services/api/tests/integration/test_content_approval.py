"""PRD §21's approval and revision loop against real PostgreSQL — and one real render.

Slice 2E left a project sitting at `PREVIEW_READY` with nothing anybody could do about it. What
this file proves is that the loop closes: a policy decides whether a person has to look, that
person accepts or rejects with a closed reason, an editor says what should change, the pipeline
restarts where that change actually matters, and the second preview is approved.

Three properties get the most attention because each one is a way to lose money or trust.

**A revision spends allowance, never credit.** The reservation a project opened at creation covers
every step and every render it will ever run (K4), so the end-to-end test below watches the
balance across a whole reject-revise-rerender-approve cycle and asserts it moved exactly once.

**A withdrawn project gives the credit back — once.** Cancelling is refused on a project that has
already finished, so a duplicate cancel cannot refund twice; and a project that had already
produced a preview keeps its charge, because the customer has the preview.

**The rejection note is stored and goes nowhere else.** It is written with a sentinel and then
hunted for in every log record any handler in the process could render, in `audit_logs`, and in
the transition history.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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
from app.modules.content.lifecycle import ProjectState
from app.modules.content.project_service import (
    AbandonedProjectSweeper,
    ContentProjectAdvanceService,
)
from app.modules.content.qc_service import ContentQcService
from app.modules.content.render_service import ContentRenderService

pytestmark = pytest.mark.integration

KEY = "test-local-identity-signing-key-123"
FFMPEG = "/usr/bin/ffmpeg"
NOTE_SENTINEL = "sentinel-note-3f9c1a-the-cup-is-the-wrong-colour"

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
        "lifecycle_poll_seconds": 1,
        "lifecycle_lease_seconds": 30,
    }
    return Settings(**(base | overrides))


def media_config(**overrides: Any) -> Settings:
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
        "lifecycle_poll_seconds": 1,
        "lifecycle_lease_seconds": 30,
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


def sweep_projects(settings: Settings) -> dict[str, int] | None:
    async def run() -> dict[str, int] | None:
        async with factory()() as session:
            return await AbandonedProjectSweeper(session, settings).process_next()

    return asyncio.run(run())


class Tenant:
    """A business with a brand, a priced product, an approved CTA and enough credit."""

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
        self.grant_credits(500)

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
        headers = auth(f"ap-{role}-{uuid.uuid4().hex[:6]}", email)
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

    # --- the endpoints under test ------------------------------------------------------------

    def decide(self, project_id: str, **overrides: Any) -> Any:
        headers = dict(overrides.pop("headers", self.headers))
        key = overrides.pop("idempotency_key", None)
        if key is not None:
            headers["Idempotency-Key"] = key
        body: dict[str, Any] = {"approved": True}
        body.update(overrides)
        return self.client.post(
            f"/v1/businesses/{self.business_id}/content/projects/{project_id}/approvals",
            headers=headers,
            json=body,
        )

    def revise(self, project_id: str, fields: list[str], **overrides: Any) -> Any:
        headers = dict(overrides.pop("headers", self.headers))
        key = overrides.pop("idempotency_key", None)
        if key is not None:
            headers["Idempotency-Key"] = key
        return self.client.post(
            f"/v1/businesses/{self.business_id}/content/projects/{project_id}/revisions",
            headers=headers,
            json={"fields": fields},
        )

    def cancel(self, project_id: str, **overrides: Any) -> Any:
        return self.client.post(
            f"/v1/businesses/{self.business_id}/content/projects/{project_id}/cancel",
            headers=overrides.pop("headers", self.headers),
        )

    def project(self, project_id: str) -> dict[str, Any]:
        response = self.client.get(
            f"/v1/businesses/{self.business_id}/content/projects/{project_id}",
            headers=self.headers,
        )
        assert response.status_code == 200, response.text
        return dict(response.json())


def app_with(settings: Settings) -> Any:
    return create_app(settings)


def state_of(project_id: str) -> str:
    rows = query("SELECT state FROM content_projects WHERE id = CAST(:id AS uuid)", id=project_id)
    assert rows
    return str(rows[0][0])


def transitions(project_id: str) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in query(
            "SELECT sequence, from_state, to_state, event, reason FROM"
            " content_project_transitions WHERE project_id = CAST(:id AS uuid) ORDER BY sequence",
            id=project_id,
        )
    ]


def seed_previewed_project(
    tenant: Tenant,
    *,
    policy: str = "always",
    quota: int = 3,
    quota_used: int = 0,
) -> str:
    """A project that has already produced a preview, without running the pipeline to get there.

    The end-to-end test below does run it, over real FFmpeg, because that is the acceptance
    criterion. Everything *after* the preview — the decision, the note, the quota, the roles —
    is independent of how the preview was made, and seeding it keeps those assertions from
    costing an encode each.

    The rows are the minimum the schema insists on: a timeline, the render that a decision is
    about, and a project whose `preview_delivered_at` records that PRD §12.7 already charged.
    """

    timeline_id = str(uuid.uuid4())
    render_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    execute(
        "INSERT INTO content_timelines (id, business_id, root_id, revision, document,"
        " created_by_user_id, correlation_id, created_at) VALUES (CAST(:id AS uuid),"
        " CAST(:business AS uuid), CAST(:id AS uuid), 1, CAST(:document AS jsonb),"
        " CAST(:user AS uuid), 'seed', now())",
        id=timeline_id,
        business=tenant.business_id,
        user=tenant.user_id,
        document=json.dumps({"version": "1.0"}),
    )
    execute(
        "INSERT INTO render_outputs (id, business_id, timeline_id, profile, status, trigger,"
        " consumes_entitlement, ai_disclosure_state, provenance_state, correlation_id,"
        " created_at, completed_at, qc_claimed_at) VALUES (CAST(:id AS uuid),"
        " CAST(:business AS uuid), CAST(:timeline AS uuid), 'instagram_reels_1080x1920',"
        " 'succeeded', 'initial', true, 'none', 'stripped_pending_reattach', 'seed', now(),"
        " now(), now())",
        id=render_id,
        business=tenant.business_id,
        timeline=timeline_id,
    )
    execute(
        "INSERT INTO content_projects (id, business_id, scenario_code, profile, state, product_id,"
        " cta_id, source_asset_ids, timeline_id, render_id, render_attempts, step_attempts,"
        " requires_human_review, recommended_path, approval_policy, revision_quota,"
        " revisions_requested, revision_quota_used, preview_delivered_at, state_entered_at,"
        " next_check_at, requested_by_user_id, correlation_id, created_at, updated_at)"
        " VALUES (CAST(:id AS uuid), CAST(:business AS uuid), 'product_reels',"
        " 'instagram_reels_1080x1920', 'preview_ready', CAST(:product AS uuid),"
        " CAST(:cta AS uuid), '[]'::jsonb, CAST(:timeline AS uuid), CAST(:render AS uuid), 1, 0,"
        " true, 'none', CAST(:policy AS content_approval_policy), :quota, 0, :used, now(), now(),"
        " now(), CAST(:user AS uuid), 'seed', now(), now())",
        id=project_id,
        business=tenant.business_id,
        product=tenant.product_id,
        cta=tenant.cta_id,
        timeline=timeline_id,
        render=render_id,
        policy=policy,
        quota=quota,
        used=quota_used,
        user=tenant.user_id,
    )
    return project_id


def advance_until(settings: Settings, project_id: str, *targets: str, rounds: int = 12) -> str:
    """Walk the sequencer until *this* project reaches one of `targets`.

    The claim takes whichever project is due first, so a test holding several cannot assume one
    call moves the one it means. Looping is the honest way to say "run the worker until this
    happens" without reaching past the claim.
    """

    for _ in range(rounds):
        current = state_of(project_id)
        if current in targets:
            return current
        advance_once(settings)
    current = state_of(project_id)
    assert current in targets, f"{project_id} stopped at {current}, wanted {targets}"
    return current


def awaiting_decision(tenant: Tenant, settings: Settings, **kwargs: Any) -> str:
    """A seeded preview, walked until §21.1's policy has actually been applied to it."""

    project_id = seed_previewed_project(tenant, **kwargs)
    advance_until(settings, project_id, ProjectState.WAITING_APPROVAL.value)
    return project_id


def reopen_for_decision(project_id: str) -> None:
    """Put a project back in front of an approver without re-running the pipeline.

    A revision clears the render it invalidated, and the schema insists a project awaiting a
    decision names the render that decision is about. Restoring both is what makes the quota
    assertions below about the *allowance* rather than about how a preview gets produced.
    """

    execute(
        "UPDATE content_projects SET state = 'waiting_approval', state_entered_at = now(),"
        " render_id = (SELECT id FROM render_outputs WHERE business_id ="
        " content_projects.business_id ORDER BY created_at LIMIT 1) WHERE id = CAST(:id AS uuid)",
        id=project_id,
    )


# --- criterion 6: cancellation and the credit it hands back --------------------------------------


@requires_postgres
def test_cancelling_a_project_that_never_previewed_hands_the_credit_back() -> None:
    """W20's open gap, closed. A project parked on a customer held its credit forever."""

    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-cancel", "ap-cancel@example.com"), "Cancel")
        opening = tenant.balance()

        created = tenant.create_project(source_asset_ids=[])
        assert created.status_code == 201, created.text
        project_id = str(created.json()["id"])
        held = tenant.balance()
        assert held < opening, "opening a project must reserve credit"

        advance_once(settings)
        assert state_of(project_id) == ProjectState.WAITING_MEDIA.value

        cancelled = tenant.cancel(project_id)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["state"] == ProjectState.CANCELLED.value
        assert cancelled.json()["failure_code"] == "PROJECT_CANCELLED"
        assert tenant.balance() == opening

        reservations = query(
            "SELECT status, failure_code FROM usage_reservations WHERE source_id ="
            " CAST(:id AS uuid)",
            id=project_id,
        )
        assert [tuple(row) for row in reservations] == [("released", "PROJECT_CANCELLED")]
        # One consume and one refund, and nothing else: the refund is a compensating entry, not
        # an edit of the charge.
        entries = query(
            "SELECT entry_type, delta_credits FROM credit_ledger WHERE source_id ="
            " CAST(:id AS uuid) ORDER BY created_at",
            id=project_id,
        )
        kinds = [row[0] for row in entries]
        assert kinds == ["consume", "refund"]
        assert sum(int(row[1]) for row in entries) == 0


@requires_postgres
def test_a_cancelled_project_cannot_be_cancelled_again_or_advanced() -> None:
    """The second refund is impossible for two independent reasons, and both are checked."""

    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-twice", "ap-twice@example.com"), "Twice")
        opening = tenant.balance()
        project_id = str(tenant.create_project(source_asset_ids=[]).json()["id"])
        assert tenant.cancel(project_id).status_code == 200
        restored = tenant.balance()
        assert restored == opening

        again = tenant.cancel(project_id)
        assert again.status_code == 409, again.text
        assert again.json()["code"] == "PROJECT_TRANSITION_NOT_ALLOWED"
        assert tenant.balance() == restored

        # And the sequencer will not touch it either: a terminal project carries no due time, so
        # it is not merely filtered out of the claim — it is absent from the claim's index.
        before = state_of(project_id)
        advance_once(settings)
        assert state_of(project_id) == before == ProjectState.CANCELLED.value
        assert (
            query(
                "SELECT count(*) FROM credit_ledger WHERE source_id = CAST(:id AS uuid)",
                id=project_id,
            )[0][0]
            == 2
        )


@requires_postgres
def test_a_cancelled_preview_keeps_its_charge_because_the_customer_has_it() -> None:
    """§12.7 consumes on "ön izleme başarıyla hazır", and cancelling afterwards does not undo it.

    This is the case that would have deadlocked the sequencer if `source_outcome` read only the
    final state: the ledger refuses to release a hold it already consumed, and rightly so.
    """

    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-kept", "ap-kept@example.com"), "Kept")
        project_id = awaiting_decision(tenant, settings)
        assert state_of(project_id) == ProjectState.WAITING_APPROVAL.value
        before = tenant.balance()

        cancelled = tenant.cancel(project_id)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["state"] == ProjectState.CANCELLED.value
        # No reservation exists for a seeded project, so the balance is untouched either way;
        # what this pins is that the attempt did not raise, which is what a released-vs-consumed
        # contradiction would have done.
        assert tenant.balance() == before


@requires_postgres
def test_the_project_sweep_withdraws_an_old_wait_and_leaves_a_healthy_one_alone() -> None:
    """The clock is the only thing that can observe a customer who stopped caring."""

    # The threshold is validated to exceed one whole step timeout, which is itself validated to
    # cover a render plus a QC run. Both bounds are respected here rather than worked around; the
    # project is aged past the threshold instead.
    settings = api_config(lifecycle_abandoned_project_age_seconds=7_200)
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-sweep", "ap-sweep@example.com"), "Sweep")
        opening = tenant.balance()
        stale = str(tenant.create_project(source_asset_ids=[]).json()["id"])
        healthy = str(tenant.create_project(source_asset_ids=[]).json()["id"])
        for project_id in (stale, healthy):
            advance_until(settings, project_id, ProjectState.WAITING_MEDIA.value)
        reserved = tenant.balance()
        assert reserved < opening

        execute(
            "UPDATE content_projects SET state_entered_at = now() - interval '4 hours'"
            " WHERE id = CAST(:id AS uuid)",
            id=stale,
        )
        result = sweep_projects(settings)
        assert result == {"cancelled": 1, "batch_full": 0}

        assert state_of(stale) == ProjectState.CANCELLED.value
        assert state_of(healthy) == ProjectState.WAITING_MEDIA.value
        # Exactly the stale project's credit came back; the healthy one is still holding its own,
        # so the balance sits halfway between the two.
        per_project = (opening - reserved) // 2
        assert tenant.balance() == reserved + per_project
        assert [
            tuple(row)
            for row in query(
                "SELECT status FROM usage_reservations WHERE source_id = CAST(:id AS uuid)",
                id=healthy,
            )
        ] == [("reserved",)]
        # And a second pass finds nothing, so the drain stops rather than spinning.
        assert sweep_projects(settings) is None


# --- criterion 8: who may decide -----------------------------------------------------------------


@requires_postgres
def test_only_an_approver_decides_and_an_editor_only_asks_for_a_revision() -> None:
    """PRD §4's two lines, over the real endpoints."""

    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-roles", "ap-roles@example.com"), "Roles")
        editor = tenant.invite("ap-editor@example.com", "editor")
        viewer = tenant.invite("ap-viewer@example.com", "viewer")
        approver = tenant.invite("ap-approver@example.com", "approver")
        project_id = awaiting_decision(tenant, settings)

        for headers in (editor, viewer):
            refused = tenant.decide(project_id, approved=True, headers=headers)
            assert refused.status_code == 403, refused.text
            assert refused.json()["code"] == "INSUFFICIENT_PERMISSION"

        # A viewer cannot ask for a revision either; an approver cannot, because asking for one
        # is asking for work to be produced.
        for headers in (viewer, approver):
            refused = tenant.revise(project_id, ["cta"], headers=headers)
            assert refused.status_code == 403, refused.text

        # Everyone may read the decisions, including the approver that has to make them.
        for headers in (editor, viewer, approver, tenant.headers):
            listed = client.get(
                f"/v1/businesses/{tenant.business_id}/content/projects/{project_id}/approvals",
                headers=headers,
            )
            assert listed.status_code == 200, listed.text

        rejected = tenant.decide(
            project_id, approved=False, rejection_reason="wrong_cut", headers=approver
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["state"] == ProjectState.REVISION_REQUESTED.value
        # And now the editor — and only the editor's side of the line — may say what changes.
        revised = tenant.revise(project_id, ["single_cut"], headers=editor)
        assert revised.status_code == 200, revised.text
        assert revised.json()["state"] == ProjectState.TIMELINE_BUILDING.value


@requires_postgres
def test_another_tenants_project_can_neither_be_decided_nor_revised() -> None:
    settings = api_config()
    with TestClient(app_with(settings)) as client:
        owner = Tenant(client, auth("ap-own", "ap-own@example.com"), "Own")
        intruder = Tenant(client, auth("ap-int", "ap-int@example.com"), "Int")
        project_id = awaiting_decision(owner, settings)

        for response in (
            intruder.decide(project_id, approved=True),
            intruder.revise(project_id, ["cta"]),
            intruder.cancel(project_id),
            client.get(
                f"/v1/businesses/{intruder.business_id}/content/projects/{project_id}/approvals",
                headers=intruder.headers,
            ),
        ):
            # 404 rather than 403: the query is tenant-scoped, so another tenant's real project
            # id is indistinguishable from a made-up one. Existence is not disclosed.
            assert response.status_code == 404, response.text
            assert response.json()["code"] in {"PROJECT_NOT_FOUND", "BUSINESS_NOT_FOUND"}
        assert state_of(project_id) == ProjectState.WAITING_APPROVAL.value


# --- criterion 7: the note ------------------------------------------------------------------------


class _AllHandlerOutput(logging.Handler):
    """Capture what an arbitrary third-party handler would print, with its own formatter."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s %(args)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # pragma: no cover - a formatting error is not the subject here
            self.lines.append(f"{record.name} <unformattable>")
        self.lines.append(repr(record.__dict__))


@contextmanager
def capture_every_log_record() -> Iterator[_AllHandlerOutput]:
    handler = _AllHandlerOutput()
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


@requires_postgres
def test_the_rejection_note_is_stored_and_reaches_no_log_audit_or_transition() -> None:
    """§21.2's free note is the tenant's own prose, and it goes exactly one place.

    Hunted for with a sentinel in three surfaces that each accumulate forever: every log record
    any handler in the process could render, the audit log, and the transition history. The one
    place it is *expected* is the decision row, and reading it back through the API is asserted
    too — storing something the owner cannot see would be its own kind of wrong.
    """

    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-note", "ap-note@example.com"), "Note")
        project_id = awaiting_decision(tenant, settings)

        with capture_every_log_record() as captured:
            rejected = tenant.decide(
                project_id,
                approved=False,
                rejection_reason="other",
                note=NOTE_SENTINEL,
            )
        assert rejected.status_code == 200, rejected.text

        assert NOTE_SENTINEL not in "\n".join(captured.lines)
        for table, column in (
            ("audit_logs", "metadata"),
            ("content_project_transitions", "reason"),
            ("idempotency_keys", "response_body"),
            ("outbox_events", "payload"),
        ):
            rows = query(f"SELECT CAST({column} AS text) FROM {table}")
            assert all(NOTE_SENTINEL not in str(row[0]) for row in rows), table

        stored = query(
            "SELECT note, rejection_reason, decision FROM content_approvals WHERE project_id ="
            " CAST(:id AS uuid)",
            id=project_id,
        )
        assert [tuple(row) for row in stored] == [(NOTE_SENTINEL, "other", "rejected")]
        readable = client.get(
            f"/v1/businesses/{tenant.business_id}/content/projects/{project_id}/approvals",
            headers=tenant.headers,
        )
        assert readable.status_code == 200
        assert readable.json()["items"][0]["note"] == NOTE_SENTINEL
        # The transition records a code, never the sentence.
        assert [row[4] for row in transitions(project_id)][-1] == "other"


@requires_postgres
def test_other_demands_an_explanation_and_an_approval_refuses_one() -> None:
    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-rules", "ap-rules@example.com"), "Rules")
        project_id = awaiting_decision(tenant, settings)

        missing = tenant.decide(project_id, approved=False, rejection_reason="other")
        assert missing.status_code == 422, missing.text
        assert missing.json()["code"] == "APPROVAL_NOTE_REQUIRED"

        blank = tenant.decide(project_id, approved=False, rejection_reason="other", note="   ")
        assert blank.status_code == 422, blank.text

        on_approval = tenant.decide(project_id, approved=True, note="looks great")
        assert on_approval.status_code == 422, on_approval.text
        assert on_approval.json()["code"] == "APPROVAL_NOTE_NOT_ALLOWED"

        unnamed = tenant.decide(project_id, approved=False)
        assert unnamed.status_code == 422, unnamed.text
        assert unnamed.json()["code"] == "APPROVAL_REASON_REQUIRED"

        # And the mirror image: an approval carrying a rejection reason.
        contradictory = tenant.decide(project_id, approved=True, rejection_reason="wrong_cut")
        assert contradictory.status_code == 422, contradictory.text
        assert contradictory.json()["code"] == "APPROVAL_REASON_NOT_ALLOWED"

        # Nothing above moved the project or wrote a decision.
        assert state_of(project_id) == ProjectState.WAITING_APPROVAL.value
        assert not query(
            "SELECT id FROM content_approvals WHERE project_id = CAST(:id AS uuid)"
            " AND decision <> 'auto_approved'",
            id=project_id,
        )


# --- criterion 5: the revision allowance ----------------------------------------------------------


@requires_postgres
def test_a_minor_revision_costs_one_a_major_costs_two_and_the_allowance_runs_out() -> None:
    """§12.3's three revisions, spent by class and refused when there is nothing left."""

    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-quota", "ap-quota@example.com"), "Quota")
        project_id = awaiting_decision(tenant, settings, quota=3)
        credits_before = tenant.balance()

        def reject_and_revise(fields: list[str]) -> Any:
            rejected = tenant.decide(project_id, approved=False, rejection_reason="new_concept")
            assert rejected.status_code == 200, rejected.text
            return tenant.revise(project_id, fields)

        first = reject_and_revise(["caption_style"])
        assert first.status_code == 200, first.text
        assert first.json()["revision_quota_used"] == 1
        assert first.json()["revisions_requested"] == 1

        # Back in front of the approver without running the pipeline: what is under test here
        # is the allowance, not how a preview is produced.
        reopen_for_decision(project_id)
        second = reject_and_revise(["product"])
        assert second.status_code == 200, second.text
        assert second.json()["revision_quota_used"] == 3
        assert second.json()["revisions_requested"] == 2

        reopen_for_decision(project_id)
        third = reject_and_revise(["cta"])
        assert third.status_code == 409, third.text
        assert third.json()["code"] == "REVISION_QUOTA_EXHAUSTED"
        assert third.json()["meta"]["revision_quota_used"] == 3

        # The receipts, and the arithmetic they carry.
        rows = query(
            "SELECT sequence, revision_class, scope, quota_cost, quota_used_after, fields FROM"
            " content_revisions WHERE project_id = CAST(:id AS uuid) ORDER BY sequence",
            id=project_id,
        )
        assert [(row[1], row[2], row[3], row[4]) for row in rows] == [
            ("minor", "timeline", 1, 1),
            ("major", "script", 2, 3),
        ]
        # And not one credit moved for any of it (K4): revisions spend allowance, not credit.
        assert tenant.balance() == credits_before


@requires_postgres
def test_a_revision_restarts_where_the_change_matters_and_drops_only_what_is_stale() -> None:
    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-scope", "ap-scope@example.com"), "Scope")

        cases: list[tuple[list[str], ProjectState]] = [
            (["caption_style"], ProjectState.TIMELINE_BUILDING),
            (["voice"], ProjectState.VOICE_GENERATION),
            (["cta"], ProjectState.SCRIPTING),
            (["product"], ProjectState.SCRIPTING),
        ]
        for fields, expected_state in cases:
            project_id = awaiting_decision(tenant, settings)
            assert (
                tenant.decide(project_id, approved=False, rejection_reason="wrong_cut").status_code
                == 200
            )
            revised = tenant.revise(project_id, fields)
            assert revised.status_code == 200, revised.text
            assert revised.json()["state"] == expected_state.value
            # A revision restores the automatic render loop's budget, because it is not the
            # automatic loop: what bounds a human asking again is the allowance just spent.
            assert revised.json()["render_attempts"] == 0
            assert revised.json()["render_id"] is None
            assert revised.json()["qc_report_id"] is None


@requires_postgres
def test_a_revision_cannot_be_asked_for_without_a_rejection_or_without_naming_a_field() -> None:
    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-order", "ap-order@example.com"), "Order")
        project_id = awaiting_decision(tenant, settings)

        early = tenant.revise(project_id, ["cta"])
        assert early.status_code == 409, early.text
        assert early.json()["code"] == "REVISION_NOT_REQUESTED"

        assert (
            tenant.decide(project_id, approved=False, rejection_reason="low_quality").status_code
            == 200
        )
        # Refused by the request model before the service is reached — a 400 from the shared
        # validation contract rather than this module's 422. Either way nothing is spent, which
        # is the half that matters: the pure classifier answers `MAJOR` for the empty set, so a
        # request that got through would have cost two units for naming nothing.
        empty = tenant.revise(project_id, [])
        assert empty.status_code == 400, empty.text
        assert empty.json()["code"] == "REQUEST_VALIDATION_FAILED"
        assert tenant.project(project_id)["revision_quota_used"] == 0

        unknown = tenant.revise(project_id, ["make_it_pop"])
        assert unknown.status_code == 400, unknown.text
        assert tenant.project(project_id)["revision_quota_used"] == 0
        assert state_of(project_id) == ProjectState.REVISION_REQUESTED.value


# --- criteria 3 and 8: the policy and idempotency -------------------------------------------------


@requires_postgres
def test_a_policy_that_asks_for_nobody_records_an_automatic_approval() -> None:
    """A decision with no actor is still a decision, and it is recorded as one.

    `campaign_only` on a project with no campaign asks for nothing, so the sequencer approves it
    itself. A system that recorded only the approvals a human gave could not answer "who let this
    out?" for the ones nobody did.
    """

    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-auto", "ap-auto@example.com"), "Auto")
        project_id = seed_previewed_project(tenant, policy="campaign_only")
        advance_once(settings)

        assert state_of(project_id) == ProjectState.APPROVED.value
        rows = query(
            "SELECT decision, policy, actor_user_id, rejection_reason, note FROM"
            " content_approvals WHERE project_id = CAST(:id AS uuid)",
            id=project_id,
        )
        assert [tuple(row) for row in rows] == [
            ("auto_approved", "campaign_only", None, None, None)
        ]
        # `approved` stopped being terminal in slice 2G — the planner gives it a publication slot
        # — so it still carries a due time. What is over is the *decision*: the sequencer has
        # nothing left to compute here and nothing further may be decided about it.
        assert (
            query(
                "SELECT next_check_at, scheduled_publish_at FROM content_projects WHERE id ="
                " CAST(:id AS uuid)",
                id=project_id,
            )[0][1]
            is None
        )
        assert tenant.decide(project_id, approved=True).status_code == 409
        # And however many times the sequencer looks at it, it stays approved: the edge out of
        # this state is the planner's.
        for _ in range(3):
            advance_once(settings)
        assert state_of(project_id) == ProjectState.APPROVED.value


@requires_postgres
def test_every_render_needs_a_person_today_under_low_confidence_only() -> None:
    """Pinned with its reason so nobody reads it as a bug — see `approval.requires_approval`."""

    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-low", "ap-low@example.com"), "Low")
        project_id = seed_previewed_project(tenant, policy="low_confidence_only")
        advance_once(settings)

        assert state_of(project_id) == ProjectState.WAITING_APPROVAL.value


@requires_postgres
def test_the_decision_idempotency_key_is_taken_from_the_whole_request() -> None:
    """Replaying the same decision replays; changing the body under the same key is refused."""

    settings = api_config()
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-idem", "ap-idem@example.com"), "Idem")
        project_id = awaiting_decision(tenant, settings)

        first = tenant.decide(
            project_id,
            approved=False,
            rejection_reason="wrong_price",
            idempotency_key="decide-1",
        )
        assert first.status_code == 200, first.text
        replay = tenant.decide(
            project_id,
            approved=False,
            rejection_reason="wrong_price",
            idempotency_key="decide-1",
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["state"] == first.json()["state"]
        # One decision, not two: the replay returned the stored answer rather than re-deciding.
        assert (
            query(
                "SELECT count(*) FROM content_approvals WHERE project_id = CAST(:id AS uuid)",
                id=project_id,
            )[0][0]
            == 1
        )

        conflicting = tenant.decide(
            project_id,
            approved=False,
            rejection_reason="low_quality",
            idempotency_key="decide-1",
        )
        assert conflicting.status_code == 409, conflicting.text

        # And the same for a revision: the fingerprint is the field set, not the operation name.
        assert state_of(project_id) == ProjectState.REVISION_REQUESTED.value
        revised = tenant.revise(project_id, ["cta"], idempotency_key="revise-1")
        assert revised.status_code == 200, revised.text
        again = tenant.revise(project_id, ["cta"], idempotency_key="revise-1")
        assert again.status_code == 200, again.text
        assert tenant.project(project_id)["revision_quota_used"] == 1
        different = tenant.revise(project_id, ["product"], idempotency_key="revise-1")
        assert different.status_code == 409, different.text


# --- criterion 2: the whole loop, over a real render ---------------------------------------------


def encode(path: Path, *, seconds: int) -> None:
    result = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=1080x1920:rate=30:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()[-2000:]


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


def drive_to(settings: Settings, workdir: Path, project_id: str, *targets: str) -> str:
    """Run the three workers until the project reaches one of `targets`, as the beat would."""

    for _ in range(60):
        before = state_of(project_id)
        advance_once(settings)
        render_once(settings, workdir)
        qc_once(settings, workdir)
        current = state_of(project_id)
        if current in targets:
            return current
        if current == before:
            time.sleep(1.1)
    raise AssertionError(f"project never reached {targets}: {state_of(project_id)}")


def seed_footage(tenant: Tenant, settings: Settings, video: Path, *, duration_ms: int) -> str:
    asset_id = str(uuid.uuid4())
    object_key = f"tenant/{tenant.business_id}/media/{asset_id}/original/seed.mp4"
    asyncio.run(
        S3MultipartStorage(settings).persist_file(
            object_key=object_key, source_path=video, content_type="video/mp4"
        )
    )
    execute(
        "INSERT INTO media_assets (id, business_id, created_by_user_id, storage_object_key,"
        " content_type, byte_size, sha256_checksum, status, ingest_status, created_at)"
        " VALUES (CAST(:id AS uuid), CAST(:business AS uuid), CAST(:user AS uuid), :key,"
        " 'video/mp4', 4096, :checksum, 'uploaded', 'ready_for_analysis', now())",
        id=asset_id,
        business=tenant.business_id,
        user=tenant.user_id,
        key=object_key,
        checksum="e" * 64,
    )
    execute(
        "INSERT INTO media_technical_metadata (id, business_id, asset_id, container_format,"
        " duration_ms, file_size, video_codec, width, height, rotation_degrees, has_audio,"
        " stream_count, analyzed_at) VALUES (gen_random_uuid(), CAST(:business AS uuid),"
        " CAST(:asset AS uuid), 'mov,mp4', :duration, 4096, 'h264', 1080, 1920, 0, true, 2,"
        " now())",
        business=tenant.business_id,
        asset=asset_id,
        duration=duration_ms,
    )
    for index, (start, end, tag) in enumerate(
        ((0, 4_000, "product_closeup"), (4_000, 8_000, "preparation"), (8_000, 12_000, "store"))
    ):
        scene_id = str(uuid.uuid4())
        execute(
            "INSERT INTO media_scenes (id, business_id, asset_id, scene_index, start_ms, end_ms,"
            " duration_ms, confidence, created_at) VALUES (CAST(:id AS uuid),"
            " CAST(:business AS uuid), CAST(:asset AS uuid), :index, :start, :end, :duration,"
            " 0.9, now())",
            id=scene_id,
            business=tenant.business_id,
            asset=asset_id,
            index=index,
            start=start,
            end=end,
            duration=end - start,
        )
        execute(
            "INSERT INTO media_scene_understandings (id, business_id, asset_id, scene_id, status,"
            " provider, model_name, summary, visual_description, transcript_context, confidence,"
            " labels, objects, actions, visible_text, dominant_topics, safety_flags,"
            " quality_signals, created_at) VALUES (gen_random_uuid(), CAST(:business AS uuid),"
            " CAST(:asset AS uuid), CAST(:scene AS uuid), 'completed', 'fake', 'fake-v1', '', '',"
            " '', 0.9, CAST(:labels AS jsonb), '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,"
            " '[]'::jsonb, '{}'::jsonb, now())",
            business=tenant.business_id,
            asset=asset_id,
            scene=scene_id,
            labels=f'["{tag}"]',
        )
    return asset_id


@requires_storage
@requires_ffmpeg
def test_a_preview_is_rejected_revised_re_rendered_and_then_approved(tmp_path: Path) -> None:
    """Acceptance criterion 2, end to end, over real PostgreSQL, MinIO and FFmpeg.

    One brief in; a preview out; a person rejects it with a reason and their own words; an editor
    asks for a different cut; the pipeline restarts at the timeline, renders again, and the second
    preview is approved. Every transition is recorded, and the credit moves exactly once for all
    of it (K4) — which is the property a revision loop is most likely to break.
    """

    settings = media_config()
    source = tmp_path / "source.mp4"
    encode(source, seconds=12)
    with TestClient(app_with(settings)) as client:
        tenant = Tenant(client, auth("ap-e2e", "ap-e2e@example.com"), "EndToEnd")
        asset_id = seed_footage(tenant, settings, source, duration_ms=12_000)
        opening = tenant.balance()

        created = tenant.create_project(source_asset_ids=[asset_id])
        assert created.status_code == 201, created.text
        project_id = str(created.json()["id"])
        charged = opening - tenant.balance()
        assert charged > 0

        assert (
            drive_to(settings, tmp_path, project_id, ProjectState.WAITING_APPROVAL.value)
            == ProjectState.WAITING_APPROVAL.value
        )
        first_render = tenant.project(project_id)["render_id"]
        assert first_render is not None

        rejected = tenant.decide(
            project_id,
            approved=False,
            rejection_reason="wrong_cut",
            note=NOTE_SENTINEL,
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["state"] == ProjectState.REVISION_REQUESTED.value

        revised = tenant.revise(project_id, ["single_cut"])
        assert revised.status_code == 200, revised.text
        assert revised.json()["state"] == ProjectState.TIMELINE_BUILDING.value
        assert revised.json()["revision_quota_used"] == 1

        assert (
            drive_to(settings, tmp_path, project_id, ProjectState.WAITING_APPROVAL.value)
            == ProjectState.WAITING_APPROVAL.value
        )
        second_render = tenant.project(project_id)["render_id"]
        assert second_render is not None and second_render != first_render

        approved = tenant.decide(project_id, approved=True)
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == ProjectState.APPROVED.value

        # One reservation, one consume, no refund — for two renders and two previews.
        assert opening - tenant.balance() == charged
        reservations = query(
            "SELECT status FROM usage_reservations WHERE source_id = CAST(:id AS uuid)",
            id=project_id,
        )
        assert [tuple(row) for row in reservations] == [("consumed",)]
        entries = query(
            "SELECT entry_type FROM credit_ledger WHERE source_id = CAST(:id AS uuid)",
            id=project_id,
        )
        assert [row[0] for row in entries] == ["consume"]

        # §20's record, walked. The approval loop is in it, and so is the second pass.
        events = [row[3] for row in transitions(project_id)]
        assert events.count("approval_required") == 2
        assert "rejected" in events
        assert "revision_scoped_to_timeline" in events
        assert events[-1] == "approved"
        assert [row[2] for row in transitions(project_id)][-1] == ProjectState.APPROVED.value
        # Two decisions, both by a person, and the note stayed on the rejection.
        decisions = query(
            "SELECT decision, note FROM content_approvals WHERE project_id = CAST(:id AS uuid)"
            " ORDER BY sequence",
            id=project_id,
        )
        assert [row[0] for row in decisions] == ["rejected", "approved"]
        assert [row[1] for row in decisions] == [NOTE_SENTINEL, None]
