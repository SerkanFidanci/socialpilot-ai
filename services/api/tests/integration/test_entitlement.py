"""Slice W20 against real PostgreSQL: the ledger, the race, and everything that must not work.

The end-to-end proof that a finished project settles its own hold lives in
`test_content_lifecycle.py`, beside the machinery that drives a project to a preview. What is
here is the part that needs no media at all: the arithmetic, the concurrency, the constraints the
database enforces on its own, and a list of things that are supposed to fail.

Two tests deserve a note before they are read.

`test_two_concurrent_projects_cannot_spend_the_same_last_credit` drives the service directly on
two sessions rather than through the HTTP client, because the point is genuinely parallel
transactions — a test client serialises requests through one portal and would prove nothing about
the lock.

The constraint tests go through raw SQL on purpose. Their claim is that the *database* refuses
these writes, so routing them through the service would only prove the service does not attempt
them.

The last section is slice W23, and it is the same idea taken to the end. Nobody sends raw SQL at
our database; what those tests pin is what the *next* writer gets — Phase 3's store webhook,
Phase 5's advertising accounting, a maintenance script written at two in the morning. None of
them will know that a balance must be read under a tenant advisory lock, or that a refund's
idempotency key has to be derived from the reservation it compensates. The three attacks an
independent round landed on 2026-08-03 are reproduced there the way that round set them up: real
parallel transactions, a barrier, and rows that satisfy every constraint slice W20 wrote.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.errors import ProblemException
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.main import create_app
from app.modules.content.project_service import (
    ContentProjectReservationProbe,
    ContentProjectService,
)
from app.modules.content.render import RenderProfile
from app.modules.content.script import ScenarioCode
from app.modules.entitlement.ledger import (
    ERROR_INSUFFICIENT_CREDITS,
    RESERVATION_ABANDONED,
    ReservationStatus,
    SourceOutcome,
)
from app.modules.entitlement.models import SOURCE_CONTENT_PROJECT
from app.modules.entitlement.points import (
    POINT_TABLE_V1,
    POINT_TABLES,
    ContentPointKind,
    PointTable,
)
from app.modules.entitlement.repository import ADVISORY_LOCK_NAMESPACE
from app.modules.entitlement.service import AbandonedReservationSweeper, EntitlementService

pytestmark = pytest.mark.integration

KEY = "test-local-identity-signing-key-123"

requires_postgres = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL"
)

TABLES = (
    # Named rather than left to the cascade. `TRUNCATE businesses CASCADE` would reach both
    # anyway, but this list is the file's statement of what a test starts from, and these two
    # are what this file is about.
    "credit_ledger",
    "usage_reservations",
    "content_project_transitions",
    "content_projects",
    "provider_usage",
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

# What one `product_reels` project on the default profile costs under version 1 of PRD §12.4.
REEL_CREDITS = POINT_TABLE_V1.credits_for(
    ScenarioCode.PRODUCT_REELS, RenderProfile.INSTAGRAM_REELS_1080X1920
)


def config(**overrides: Any) -> Settings:
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
            # TRUNCATE does not fire row triggers, so the append-only guard on `credit_ledger`
            # does not stand in the way of teardown.
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


# --- one seeded tenant, with no media at all ------------------------------------------------------


class Tenant:
    """A business with a brand, a priced product and an approved CTA. No footage, on purpose.

    A project opened with no sources is still a project: it reserves, and then waits for media.
    That is exactly the shape these tests want — every entitlement rule, none of the encoding.
    """

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
                "tone": "sıcak",
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

    def grant(self, credits: int, **overrides: Any) -> Any:
        headers = dict(overrides.pop("headers", self.headers))
        key = overrides.pop("idempotency_key", None)
        if key is not None:
            headers["Idempotency-Key"] = key
        body: dict[str, Any] = {"credits": credits}
        body.update(overrides)
        return self.client.post(
            f"/v1/businesses/{self.business_id}/entitlement/grants", headers=headers, json=body
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

    def balance(self, **overrides: Any) -> Any:
        return self.client.get(
            f"/v1/businesses/{self.business_id}/entitlement/balance",
            headers=overrides.pop("headers", self.headers),
        )

    def ledger(self, **overrides: Any) -> Any:
        return self.client.get(
            f"/v1/businesses/{self.business_id}/entitlement/ledger",
            headers=overrides.pop("headers", self.headers),
        )

    def reservations(self, **overrides: Any) -> Any:
        return self.client.get(
            f"/v1/businesses/{self.business_id}/entitlement/reservations",
            headers=overrides.pop("headers", self.headers),
        )

    def invite(self, role: str) -> dict[str, str]:
        headers = auth(
            f"ent-{role}-{uuid.uuid4().hex[:6]}", f"{role}-{uuid.uuid4().hex[:6]}@e.test"
        )
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


def entries(business_id: str) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in query(
            "SELECT entry_type, delta_credits, points_table_version, reservation_id, reason"
            " FROM credit_ledger WHERE business_id = CAST(:business AS uuid)"
            " ORDER BY created_at, id",
            business=business_id,
        )
    ]


def reservations_of(business_id: str) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in query(
            "SELECT id, status, credits, points_table_version, point_kind, source_id, failure_code"
            " FROM usage_reservations WHERE business_id = CAST(:business AS uuid)"
            " ORDER BY created_at, id",
            business=business_id,
        )
    ]


def summed_balance(business_id: str) -> int:
    return int(
        query(
            "SELECT COALESCE(SUM(delta_credits), 0) FROM credit_ledger"
            " WHERE business_id = CAST(:business AS uuid)",
            business=business_id,
        )[0][0]
    )


# --- the ledger is the balance --------------------------------------------------------------------


@requires_postgres
def test_the_balance_is_derived_from_the_entries_and_stored_in_no_column() -> None:
    """Criterion 2: there is nothing to read a balance *from* except the sum of the entries."""

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-1", "owner1@entitlement.test"), "Balance")
        assert tenant.grant(50).status_code == 201
        assert tenant.balance().json()["balance_credits"] == 50

        assert tenant.create_project().status_code == 201
        body = tenant.balance().json()
        assert body["balance_credits"] == 50 - REEL_CREDITS
        # The hold is reported separately and is *not* a second subtraction: it is already gone
        # from the spendable number above.
        assert body["reserved_credits"] == REEL_CREDITS
        assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS

    # No column anywhere in the schema stores a balance. A stored total is the failure mode this
    # design exists to avoid, so its absence is asserted rather than assumed.
    columns = query(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND (column_name LIKE '%%balance%%'"
        " OR column_name LIKE '%%credits_remaining%%')"
    )
    assert columns == []


@requires_postgres
def test_an_open_reservation_holds_credit_before_any_work_has_run() -> None:
    """PRD §12.8: the right is held when the job starts, not when it finishes."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-2", "owner2@entitlement.test"), "Hold")
        tenant.grant(20)
        created = tenant.create_project()
        assert created.status_code == 201
        rows = reservations_of(tenant.business_id)
        assert len(rows) == 1
        reservation = rows[0]
        assert reservation[1] == ReservationStatus.RESERVED.value
        assert reservation[2] == REEL_CREDITS
        assert reservation[3] == POINT_TABLE_V1.version
        assert reservation[4] == ContentPointKind.STANDARD_REELS.value
        assert str(reservation[5]) == created.json()["id"]
        # One entry, and it is the charge. Nothing waits for the work to end.
        assert entries(tenant.business_id) == [
            ("grant", 20, None, None, None),
            ("consume", -REEL_CREDITS, POINT_TABLE_V1.version, reservation[0], None),
        ]


@requires_postgres
def test_a_generation_with_no_credit_behind_it_never_starts() -> None:
    """Criterion 4: not enough credit means the work is not created, not that it fails later."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-3", "owner3@entitlement.test"), "Empty")
        refused = tenant.create_project()
        assert refused.status_code == 402, refused.text
        body = refused.json()
        assert body["code"] == ERROR_INSUFFICIENT_CREDITS
        assert body["meta"]["required_credits"] == REEL_CREDITS
        assert body["meta"]["available_credits"] == 0
        # Nothing survived the refusal: no project, no reservation, no entry, no idempotency row.
        assert (
            query(
                "SELECT count(*) FROM content_projects WHERE business_id = CAST(:b AS uuid)",
                b=tenant.business_id,
            )[0][0]
            == 0
        )
        assert reservations_of(tenant.business_id) == []
        assert entries(tenant.business_id) == []


@requires_postgres
def test_one_credit_short_is_still_short() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-4", "owner4@entitlement.test"), "Short")
        tenant.grant(REEL_CREDITS - 1)
        assert tenant.create_project().status_code == 402
        tenant.grant(1)
        assert tenant.create_project().status_code == 201
        assert summed_balance(tenant.business_id) == 0


# --- the race -------------------------------------------------------------------------------------


@requires_postgres
def test_two_concurrent_projects_cannot_spend_the_same_last_credit() -> None:
    """Criterion 3, with real parallel transactions rather than a mocked one.

    Both coroutines run `create_project` on their own session against the same tenant, whose
    ledger holds exactly one generation's worth of credit. Exactly one may win. Without the
    advisory lock both would read the same balance and both would pass, because neither
    transaction modifies a row the other read and PostgreSQL has nothing to detect.
    """

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-5", "owner5@entitlement.test"), "Race")
        assert tenant.grant(REEL_CREDITS).status_code == 201

    async def open_one(index: int) -> object:
        async with factory()() as session:
            service = ContentProjectService(session, settings)
            return await service.create_project(
                user_id=uuid.UUID(tenant.user_id),
                business_id=uuid.UUID(tenant.business_id),
                scenario_code=ScenarioCode.PRODUCT_REELS,
                profile=RenderProfile.INSTAGRAM_REELS_1080X1920,
                product_id=uuid.UUID(tenant.product_id),
                cta_id=uuid.UUID(tenant.cta_id),
                campaign_offer_id=None,
                source_asset_ids=(),
                idempotency_key=None,
                correlation_id=f"race-{index}",
            )

    async def both() -> list[Any]:
        return list(await asyncio.gather(open_one(1), open_one(2), return_exceptions=True))

    results = asyncio.run(both())
    refusals = [item for item in results if isinstance(item, ProblemException)]
    successes = [item for item in results if not isinstance(item, BaseException)]
    assert len(successes) == 1, results
    assert len(refusals) == 1, results
    assert refusals[0].status == 402
    assert refusals[0].code == ERROR_INSUFFICIENT_CREDITS

    assert summed_balance(tenant.business_id) == 0
    assert len(reservations_of(tenant.business_id)) == 1
    assert (
        query(
            "SELECT count(*) FROM content_projects WHERE business_id = CAST(:b AS uuid)",
            b=tenant.business_id,
        )[0][0]
        == 1
    )


@requires_postgres
def test_ten_concurrent_projects_against_three_generations_of_credit_open_exactly_three() -> None:
    """The same claim with the width turned up: the lock is a queue, not a coin flip."""

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-6", "owner6@entitlement.test"), "Rush")
        assert tenant.grant(REEL_CREDITS * 3).status_code == 201

    async def open_one(index: int) -> object:
        async with factory()() as session:
            return await ContentProjectService(session, settings).create_project(
                user_id=uuid.UUID(tenant.user_id),
                business_id=uuid.UUID(tenant.business_id),
                scenario_code=ScenarioCode.PRODUCT_REELS,
                profile=RenderProfile.INSTAGRAM_REELS_1080X1920,
                product_id=uuid.UUID(tenant.product_id),
                cta_id=uuid.UUID(tenant.cta_id),
                campaign_offer_id=None,
                source_asset_ids=(),
                idempotency_key=None,
                correlation_id=f"rush-{index}",
            )

    async def all_ten() -> list[Any]:
        return list(
            await asyncio.gather(*(open_one(index) for index in range(10)), return_exceptions=True)
        )

    results = asyncio.run(all_ten())
    successes = [item for item in results if not isinstance(item, BaseException)]
    refusals = [item for item in results if isinstance(item, ProblemException)]
    assert len(successes) == 3, results
    assert len(refusals) == 7, results
    assert {refusal.code for refusal in refusals} == {ERROR_INSUFFICIENT_CREDITS}
    assert summed_balance(tenant.business_id) == 0
    assert len(reservations_of(tenant.business_id)) == 3


@requires_postgres
def test_a_replayed_project_creation_reserves_once() -> None:
    """The same request twice is one project and one charge, not two of either."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-7", "owner7@entitlement.test"), "Replay")
        tenant.grant(50)
        first = tenant.create_project(idempotency_key="same-key")
        second = tenant.create_project(idempotency_key="same-key")
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert len(reservations_of(tenant.business_id)) == 1
        assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS


# --- settlement -----------------------------------------------------------------------------------


def settle(
    settings: Settings,
    *,
    business_id: str,
    source_id: str,
    outcome: SourceOutcome,
    failure_code: str | None = None,
) -> Any:
    async def run() -> Any:
        async with factory()() as session:
            service = EntitlementService(session, settings)
            async with session.begin():
                return await service.settle(
                    business_id=uuid.UUID(business_id),
                    source_type=SOURCE_CONTENT_PROJECT,
                    source_id=uuid.UUID(source_id),
                    outcome=outcome,
                    failure_code=failure_code,
                    correlation_id="settle-test",
                )

    return asyncio.run(run())


@requires_postgres
def test_delivered_work_consumes_its_hold_and_writes_no_further_entry() -> None:
    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-8", "owner8@entitlement.test"), "Consume")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]

    settle(
        settings,
        business_id=tenant.business_id,
        source_id=project_id,
        outcome=SourceOutcome.DELIVERED,
    )
    rows = reservations_of(tenant.business_id)
    assert rows[0][1] == ReservationStatus.CONSUMED.value
    assert [entry[0] for entry in entries(tenant.business_id)] == ["grant", "consume"]
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS


@requires_postgres
def test_abandoned_work_releases_its_hold_and_the_balance_returns() -> None:
    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-9", "owner9@entitlement.test"), "Release")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]

    settle(
        settings,
        business_id=tenant.business_id,
        source_id=project_id,
        outcome=SourceOutcome.ABANDONED,
        failure_code="PROJECT_RENDER_ATTEMPTS_EXHAUSTED",
    )
    rows = reservations_of(tenant.business_id)
    assert rows[0][1] == ReservationStatus.RELEASED.value
    assert rows[0][6] == "PROJECT_RENDER_ATTEMPTS_EXHAUSTED"
    assert [entry[0] for entry in entries(tenant.business_id)] == ["grant", "consume", "refund"]
    assert summed_balance(tenant.business_id) == 50


@requires_postgres
def test_a_released_reservation_cannot_be_released_a_second_time() -> None:
    """The adversarial case: a replayed settlement must not hand the credit back twice."""

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-10", "owner10@entitlement.test"), "Twice")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]

    for _ in range(4):
        settle(
            settings,
            business_id=tenant.business_id,
            source_id=project_id,
            outcome=SourceOutcome.ABANDONED,
            failure_code="PROJECT_STATE_TIMEOUT",
        )
    assert [entry[0] for entry in entries(tenant.business_id)] == ["grant", "consume", "refund"]
    assert summed_balance(tenant.business_id) == 50


@requires_postgres
def test_a_consumed_reservation_cannot_be_turned_into_a_refund() -> None:
    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-11", "owner11@entitlement.test"), "Conflict")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]

    settle(
        settings,
        business_id=tenant.business_id,
        source_id=project_id,
        outcome=SourceOutcome.DELIVERED,
    )
    with pytest.raises(ProblemException) as raised:
        settle(
            settings,
            business_id=tenant.business_id,
            source_id=project_id,
            outcome=SourceOutcome.ABANDONED,
            failure_code="PROJECT_STATE_TIMEOUT",
        )
    assert raised.value.status == 409
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS


@requires_postgres
def test_settling_work_that_is_still_running_holds_the_credit() -> None:
    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-12", "owner12@entitlement.test"), "Running")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]

    assert (
        settle(
            settings,
            business_id=tenant.business_id,
            source_id=project_id,
            outcome=SourceOutcome.RUNNING,
        )
        is None
    )
    assert reservations_of(tenant.business_id)[0][1] == ReservationStatus.RESERVED.value
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS


# --- what the database refuses on its own ----------------------------------------------------------


@requires_postgres
def test_the_ledger_refuses_an_update_and_a_delete() -> None:
    """Criterion 1's "append-only" as a property of the table, not a convention in a service."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-13", "owner13@entitlement.test"), "Append")
        tenant.grant(50)

    with pytest.raises(DBAPIError, match="append-only"):
        execute(
            "UPDATE credit_ledger SET delta_credits = 999 WHERE business_id = CAST(:b AS uuid)",
            b=tenant.business_id,
        )
    with pytest.raises(DBAPIError, match="append-only"):
        execute(
            "DELETE FROM credit_ledger WHERE business_id = CAST(:b AS uuid)", b=tenant.business_id
        )
    assert summed_balance(tenant.business_id) == 50


@requires_postgres
def test_the_database_refuses_an_entry_that_would_make_the_balance_negative() -> None:
    """PRD §32.4: "Negatif bakiye oluşmamalıdır" — enforced below the application."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-14", "owner14@entitlement.test"), "Negative")
        tenant.grant(10)
        project_id = tenant.create_project().json()["id"]
    reservation_id = reservations_of(tenant.business_id)[0][0]

    with pytest.raises(DBAPIError, match="negative"):
        execute(
            "INSERT INTO credit_ledger (id, business_id, entry_type, delta_credits,"
            " points_table_version, source_type, source_id, reservation_id, correlation_id,"
            " created_at) VALUES (gen_random_uuid(), CAST(:b AS uuid), 'consume', -1000, 1,"
            " 'content_project', CAST(:s AS uuid), CAST(:r AS uuid), 'hostile', now())",
            b=tenant.business_id,
            s=project_id,
            r=str(reservation_id),
        )
    assert summed_balance(tenant.business_id) == 10 - REEL_CREDITS


@requires_postgres
def test_an_entry_whose_sign_disagrees_with_its_type_cannot_exist() -> None:
    """A `consume` that adds credit, and a `grant` that removes it. Neither is representable."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-15", "owner15@entitlement.test"), "Signs")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]
    reservation_id = str(reservations_of(tenant.business_id)[0][0])

    with pytest.raises(IntegrityError, match="ck_credit_ledger_delta_sign"):
        execute(
            "INSERT INTO credit_ledger (id, business_id, entry_type, delta_credits,"
            " points_table_version, source_type, source_id, reservation_id, correlation_id,"
            " created_at) VALUES (gen_random_uuid(), CAST(:b AS uuid), 'consume', 500, 1,"
            " 'content_project', CAST(:s AS uuid), CAST(:r AS uuid), 'hostile', now())",
            b=tenant.business_id,
            s=project_id,
            r=reservation_id,
        )
    # A grant that removes credit. The magnitude is small on purpose: a `BEFORE INSERT` trigger
    # runs ahead of the check constraints, so a large negative would be caught by the
    # non-negative guard first and this test would prove that one twice.
    with pytest.raises(IntegrityError, match="ck_credit_ledger_delta_sign"):
        execute(
            "INSERT INTO credit_ledger (id, business_id, entry_type, delta_credits, source_type,"
            " correlation_id, created_at) VALUES (gen_random_uuid(), CAST(:b AS uuid), 'grant',"
            " -1, 'manual_grant', 'hostile', now())",
            b=tenant.business_id,
        )
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS


@requires_postgres
def test_a_charge_cannot_exist_without_a_version_or_a_reservation() -> None:
    """A `consume` names the price list it was computed from and the hold that authorised it."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-16", "owner16@entitlement.test"), "Unbacked")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]
    reservation_id = str(reservations_of(tenant.business_id)[0][0])

    # Authorised but unpriced: a charge nobody can explain the size of.
    with pytest.raises(IntegrityError, match="ck_credit_ledger_consume_versioned"):
        execute(
            "INSERT INTO credit_ledger (id, business_id, entry_type, delta_credits, source_type,"
            " source_id, reservation_id, correlation_id, created_at)"
            " VALUES (gen_random_uuid(), CAST(:b AS uuid), 'consume', -1, 'content_project',"
            " CAST(:s AS uuid), CAST(:r AS uuid), 'hostile', now())",
            b=tenant.business_id,
            s=project_id,
            r=reservation_id,
        )
    # Priced but unauthorised: a charge nothing can ever release.
    with pytest.raises(IntegrityError, match="ck_credit_ledger_consume_reserved"):
        execute(
            "INSERT INTO credit_ledger (id, business_id, entry_type, delta_credits,"
            " points_table_version, source_type, correlation_id, created_at)"
            " VALUES (gen_random_uuid(), CAST(:b AS uuid), 'consume', -1, 1, 'content_project',"
            " 'hostile', now())",
            b=tenant.business_id,
        )
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS


@requires_postgres
def test_one_reservation_cannot_produce_two_refunds_even_by_hand() -> None:
    """The unique index is the last line: it holds when every check above is bypassed."""

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-17", "owner17@entitlement.test"), "Double")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]
    settle(
        settings,
        business_id=tenant.business_id,
        source_id=project_id,
        outcome=SourceOutcome.ABANDONED,
        failure_code="PROJECT_STATE_TIMEOUT",
    )
    reservation_id = str(reservations_of(tenant.business_id)[0][0])

    with pytest.raises(IntegrityError, match="uq_credit_ledger_idempotency"):
        execute(
            "INSERT INTO credit_ledger (id, business_id, entry_type, delta_credits,"
            " points_table_version, source_type, source_id, reservation_id, idempotency_key,"
            " correlation_id, created_at) VALUES (gen_random_uuid(), CAST(:b AS uuid), 'refund',"
            " :credits, 1, 'content_project', CAST(:s AS uuid), CAST(:r AS uuid),"
            " :key, 'hostile', now())",
            b=tenant.business_id,
            s=project_id,
            r=reservation_id,
            credits=REEL_CREDITS,
            key=f"refund:{reservation_id}",
        )
    assert summed_balance(tenant.business_id) == 50


@requires_postgres
def test_a_reservation_cannot_be_open_and_settled_at_the_same_time() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-18", "owner18@entitlement.test"), "Stamp")
        tenant.grant(50)
        tenant.create_project()
    with pytest.raises(IntegrityError, match="ck_usage_reservation_settled_at"):
        execute(
            "UPDATE usage_reservations SET settled_at = now() WHERE business_id = CAST(:b AS uuid)",
            b=tenant.business_id,
        )


# --- tenant isolation and roles --------------------------------------------------------------------


@requires_postgres
def test_another_tenants_ledger_is_not_readable_and_not_spendable() -> None:
    """Criterion 7: a real id from another tenant answers exactly like a made-up one."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        mine = Tenant(client, auth("ent-owner-19", "owner19@entitlement.test"), "Mine")
        theirs = Tenant(client, auth("ent-owner-20", "owner20@entitlement.test"), "Theirs")
        mine.grant(50)
        theirs.grant(50)

        for path in ("balance", "ledger", "reservations"):
            response = client.get(
                f"/v1/businesses/{theirs.business_id}/entitlement/{path}", headers=mine.headers
            )
            assert response.status_code == 404, path
            assert response.json()["code"] == "BUSINESS_NOT_FOUND"
        # And an id that never existed answers identically — no existence is disclosed.
        missing = client.get(
            f"/v1/businesses/{uuid.uuid4()}/entitlement/balance", headers=mine.headers
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "BUSINESS_NOT_FOUND"

        granted = client.post(
            f"/v1/businesses/{theirs.business_id}/entitlement/grants",
            headers=mine.headers,
            json={"credits": 1000},
        )
        assert granted.status_code == 404
        assert summed_balance(theirs.business_id) == 50


@requires_postgres
def test_one_tenants_spend_never_moves_another_tenants_balance() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        mine = Tenant(client, auth("ent-owner-21", "owner21@entitlement.test"), "First")
        theirs = Tenant(client, auth("ent-owner-22", "owner22@entitlement.test"), "Second")
        mine.grant(50)
        theirs.grant(50)
        assert mine.create_project().status_code == 201
        assert mine.balance().json()["balance_credits"] == 50 - REEL_CREDITS
        assert theirs.balance().json()["balance_credits"] == 50
        assert theirs.ledger().json()["items"] == [
            item
            for item in theirs.ledger().json()["items"]
            if item["source_type"] == "manual_grant"
        ]


@requires_postgres
@pytest.mark.parametrize("role", ["admin", "editor", "viewer", "approver"])
def test_only_an_owner_may_create_credit(role: str) -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth(f"ent-o-{role}", f"o-{role}@entitlement.test"), f"Role{role}")
        member = tenant.invite(role)
        refused = tenant.grant(100, headers=member)
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "INSUFFICIENT_PERMISSION"
        assert summed_balance(tenant.business_id) == 0


@requires_postgres
@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
def test_any_member_who_can_see_the_business_can_see_why_a_generation_was_refused(
    role: str,
) -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth(f"ent-r-{role}", f"r-{role}@entitlement.test"), f"Read{role}")
        member = tenant.invite(role)
        tenant.grant(7)
        assert tenant.balance(headers=member).json()["balance_credits"] == 7
        assert tenant.ledger(headers=member).status_code == 200


# --- the point table's version ---------------------------------------------------------------------


@requires_postgres
def test_changing_the_points_version_prices_new_work_and_reinterprets_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 6: yesterday's charge keeps yesterday's price, and its own version says which."""

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-23", "owner23@entitlement.test"), "Version")
        tenant.grant(100)
        assert tenant.create_project().status_code == 201

    before_rows = reservations_of(tenant.business_id)
    before_entries = entries(tenant.business_id)
    assert before_rows[0][3] == 1
    assert before_rows[0][2] == REEL_CREDITS

    # A second registered version that prices the same surface at three times as much.
    dearer = PointTable(
        version=2,
        points={kind: credits * 3 for kind, credits in POINT_TABLE_V1.points.items()},
        surfaces=dict(POINT_TABLE_V1.surfaces),
    )
    monkeypatch.setitem(POINT_TABLES, 2, dearer)
    with TestClient(
        create_app(config(entitlement_points_version=2)), raise_server_exceptions=False
    ) as client:
        second = client.post(
            f"/v1/businesses/{tenant.business_id}/content/projects",
            headers=tenant.headers,
            json={
                "scenario_code": "product_reels",
                "profile": "instagram_reels_1080x1920",
                "product_id": tenant.product_id,
                "cta_id": tenant.cta_id,
            },
        )
        assert second.status_code == 201, second.text

    rows = reservations_of(tenant.business_id)
    assert len(rows) == 2
    # The old hold is untouched: same credits, same version. Nothing re-derived it.
    assert rows[0][:5] == before_rows[0][:5]
    assert rows[1][2] == REEL_CREDITS * 3
    assert rows[1][3] == 2
    # And the entries that already existed are byte-identical, because the balance is their sum
    # and nothing recomputes a stored delta from a table.
    assert entries(tenant.business_id)[: len(before_entries)] == before_entries
    assert summed_balance(tenant.business_id) == 100 - REEL_CREDITS - REEL_CREDITS * 3


# --- the join to what it cost -----------------------------------------------------------------------


@requires_postgres
def test_a_reservation_can_be_joined_to_the_provider_spend_it_caused() -> None:
    """Criterion 8: which consumption produced which provider cost, without a column for it.

    Every paid call a project makes carries the project's correlation id (slices 2B–2D write it
    onto `provider_usage`), and the reservation carries the same one. The relation is therefore a
    join that needs no foreign key between a billing table and a cost table.
    """

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-24", "owner24@entitlement.test"), "Cost")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]
    correlation = str(
        query(
            "SELECT correlation_id FROM usage_reservations WHERE source_id = CAST(:s AS uuid)",
            s=project_id,
        )[0][0]
    )
    for capability, cost in (("script_generation", 120), ("tts", 40)):
        execute(
            "INSERT INTO provider_usage (id, business_id, capability, provider, model,"
            " estimated_cost_minor, actual_cost_minor, currency, duration_ms, outcome,"
            " correlation_id, created_at) VALUES (gen_random_uuid(), CAST(:b AS uuid), :capability,"
            " 'fake', 'fake-v1', :cost, :cost, 'USD', 10, 'succeeded', :correlation, now())",
            b=tenant.business_id,
            capability=capability,
            cost=cost,
            correlation=correlation,
        )

    joined = query(
        "SELECT r.id, sum(u.actual_cost_minor), count(u.id) FROM usage_reservations r"
        " JOIN provider_usage u ON u.correlation_id = r.correlation_id"
        " AND u.business_id = r.business_id"
        " WHERE r.source_id = CAST(:s AS uuid) GROUP BY r.id",
        s=project_id,
    )
    assert len(joined) == 1
    assert joined[0][1] == 160
    assert joined[0][2] == 2


# --- grants -----------------------------------------------------------------------------------------


@requires_postgres
def test_a_grant_replayed_with_the_same_key_credits_once() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-25", "owner25@entitlement.test"), "Grants")
        first = tenant.grant(30, idempotency_key="grant-1")
        second = tenant.grant(30, idempotency_key="grant-1")
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert summed_balance(tenant.business_id) == 30


@requires_postgres
def test_a_grant_amount_is_a_whole_number_within_the_ceiling() -> None:
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-26", "owner26@entitlement.test"), "Bounds")
        # A JSON float is refused rather than coerced, integral or not — the schema rejects it
        # before any rule runs, with the standard validation contract.
        floated = tenant.client.post(
            f"/v1/businesses/{tenant.business_id}/entitlement/grants",
            headers=tenant.headers,
            json={"credits": 5.0},
        )
        assert floated.status_code == 400
        assert floated.json()["code"] == "REQUEST_VALIDATION_FAILED"
        assert tenant.grant(0).status_code == 400
        assert tenant.grant(-5).status_code == 400
        # Inside the field bound but over the configured ceiling: a rule, so a rule's answer.
        over = config().entitlement_max_grant_credits + 1
        capped = tenant.grant(over)
        assert capped.status_code == 422
        assert capped.json()["code"] == "ENTITLEMENT_GRANT_INVALID"
        assert summed_balance(tenant.business_id) == 0


# --- the reconciliation sweep -------------------------------------------------------------------------


def sweep(settings: Settings) -> Any:
    async def run() -> Any:
        async with factory()() as session:
            sweeper = AbandonedReservationSweeper(
                session, settings, ContentProjectReservationProbe(session)
            )
            return await sweeper.process_next()

    return asyncio.run(run())


@requires_postgres
def test_the_sweep_releases_a_hold_whose_project_no_longer_exists() -> None:
    """Criterion 4's last case: a hold nobody will ever close is a customer's credit, held forever."""

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-27", "owner27@entitlement.test"), "Sweep")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]

    execute("DELETE FROM content_projects WHERE id = CAST(:id AS uuid)", id=project_id)
    # Age the hold past the threshold rather than waiting six hours for it.
    execute(
        "UPDATE usage_reservations SET created_at = now() - interval '30 days'"
        " WHERE business_id = CAST(:b AS uuid)",
        b=tenant.business_id,
    )
    result = sweep(settings)
    assert result == {"examined": 1, "released": 1, "batch_full": 0}
    assert reservations_of(tenant.business_id)[0][1] == ReservationStatus.RELEASED.value
    assert reservations_of(tenant.business_id)[0][6] == RESERVATION_ABANDONED
    assert summed_balance(tenant.business_id) == 50
    # And it stops, rather than sweeping the same row every tick.
    assert sweep(settings) is None


@requires_postgres
def test_the_sweep_leaves_a_hold_alone_while_its_project_is_still_live() -> None:
    """The sweep never guesses: age alone is not evidence that work is over."""

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-28", "owner28@entitlement.test"), "Live")
        tenant.grant(50)
        tenant.create_project()

    execute(
        "UPDATE usage_reservations SET created_at = now() - interval '30 days'"
        " WHERE business_id = CAST(:b AS uuid)",
        b=tenant.business_id,
    )
    assert sweep(settings) is None
    assert reservations_of(tenant.business_id)[0][1] == ReservationStatus.RESERVED.value
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS


@requires_postgres
def test_the_sweep_ignores_a_hold_that_is_younger_than_the_threshold() -> None:
    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-29", "owner29@entitlement.test"), "Young")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]
    execute("DELETE FROM content_projects WHERE id = CAST(:id AS uuid)", id=project_id)

    assert sweep(settings) is None
    assert reservations_of(tenant.business_id)[0][1] == ReservationStatus.RESERVED.value


# --- W23: the invariants belong to the schema ---------------------------------------------------


CHARGE_SQL = (
    "INSERT INTO credit_ledger (id, business_id, entry_type, delta_credits, points_table_version,"
    " source_type, source_id, reservation_id, correlation_id, created_at)"
    " VALUES (gen_random_uuid(), CAST(:b AS uuid), 'consume', :delta, 1, 'content_project',"
    " CAST(:s AS uuid), CAST(:r AS uuid), 'hostile', now())"
)

REFUND_SQL = (
    "INSERT INTO credit_ledger (id, business_id, entry_type, delta_credits, points_table_version,"
    " source_type, source_id, reservation_id, idempotency_key, correlation_id, created_at)"
    " VALUES (gen_random_uuid(), CAST(:b AS uuid), 'refund', :delta, 1, 'content_project',"
    " CAST(:s AS uuid), CAST(:r AS uuid), :key, 'hostile', now())"
)


def raw_hold(tenant: Tenant, *, credits: int, source_id: str | None = None) -> tuple[str, str]:
    """A reservation written straight into the table, satisfying every W20 constraint.

    This is what the attacks need and what the service would never produce: a hold with no ledger
    entry behind it, so the credit it names is still spendable. Returns `(reservation_id,
    source_id)`.
    """

    reservation_id = str(uuid.uuid4())
    source = source_id or str(uuid.uuid4())
    execute(
        "INSERT INTO usage_reservations (id, business_id, status, credits, points_table_version,"
        " point_kind, source_type, source_id, idempotency_key, requested_by_user_id,"
        " correlation_id, created_at, updated_at) VALUES (CAST(:id AS uuid), CAST(:b AS uuid),"
        " 'reserved', :credits, 1, :kind, 'content_project', CAST(:s AS uuid), :key,"
        " CAST(:u AS uuid), 'hostile', now(), now())",
        id=reservation_id,
        b=tenant.business_id,
        credits=credits,
        kind=ContentPointKind.STANDARD_REELS.value,
        s=source,
        key=f"raw-{reservation_id}",
        u=tenant.user_id,
    )
    return reservation_id, source


def charge_at_a_barrier(
    charges: list[dict[str, Any]], *, isolation: str | None = None
) -> list[Any]:
    """Run every charge in its own real transaction, all released at the same instant.

    One engine and one connection per charge, `BEGIN` before the barrier and `COMMIT` after the
    insert, so the writes genuinely overlap. Returns one entry per charge: `None` for a commit,
    the exception for a refusal.
    """

    async def one(barrier: asyncio.Barrier, charge: dict[str, Any]) -> Any:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with engine.connect() as connection:
                if isolation is not None:
                    await connection.execution_options(isolation_level=isolation)
                transaction = await connection.begin()
                await barrier.wait()
                try:
                    await connection.execute(text(CHARGE_SQL), charge)
                except Exception as error:  # noqa: BLE001 - the refusal is the result
                    await transaction.rollback()
                    return error
                await transaction.commit()
                return None
        finally:
            await engine.dispose()

    async def run() -> list[Any]:
        barrier = asyncio.Barrier(len(charges))
        return list(await asyncio.gather(*(one(barrier, charge) for charge in charges)))

    return asyncio.run(run())


@requires_postgres
def test_two_concurrent_raw_charges_cannot_drive_the_balance_below_zero() -> None:
    """W20-F2, closed. The trigger's sum was right; what it lacked was an order to sum in.

    Two separate transactions, both holding a valid reservation, both charging the whole balance,
    both released at a barrier. Before W23 both committed and the derived balance was `-5`,
    because a `BEFORE INSERT` trigger reading under READ COMMITTED cannot see the other
    transaction's uncommitted row. It still cannot — it no longer has to, because the second
    writer does not get to run its sum until the first has committed.
    """

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-30", "owner30@entitlement.test"), "Barrier")
        tenant.grant(REEL_CREDITS)
    first, first_source = raw_hold(tenant, credits=REEL_CREDITS)
    second, second_source = raw_hold(tenant, credits=REEL_CREDITS)

    results = charge_at_a_barrier(
        [
            {"b": tenant.business_id, "delta": -REEL_CREDITS, "s": first_source, "r": first},
            {"b": tenant.business_id, "delta": -REEL_CREDITS, "s": second_source, "r": second},
        ]
    )
    refusals = [item for item in results if item is not None]
    assert len(refusals) == 1, results
    assert "negative" in str(refusals[0])
    assert summed_balance(tenant.business_id) == 0


@requires_postgres
def test_eight_concurrent_raw_charges_spend_only_the_credit_that_exists() -> None:
    """The same claim with the width turned up: the trigger's lock is a queue, not a coin flip."""

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-31", "owner31@entitlement.test"), "Stampede")
        tenant.grant(REEL_CREDITS * 3)
    holds = [raw_hold(tenant, credits=REEL_CREDITS) for _ in range(8)]

    results = charge_at_a_barrier(
        [
            {"b": tenant.business_id, "delta": -REEL_CREDITS, "s": source, "r": reservation}
            for reservation, source in holds
        ]
    )
    assert len([item for item in results if item is None]) == 3, results
    assert len([item for item in results if item is not None]) == 5, results
    assert summed_balance(tenant.business_id) == 0


@requires_postgres
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_a_stricter_isolation_level_is_not_a_way_around_the_guard(isolation: str) -> None:
    """Attacking the fix: a snapshot older than the winner's commit must not become a licence.

    Under `REPEATABLE READ` the loser's snapshot predates the winner's entry entirely, so a sum
    taken from that snapshot could read the pre-charge balance. It does not, because the sum runs
    in a trigger that only starts once the winner has committed — and a snapshot older than that
    commit turns the write into a serialisation failure rather than a second charge. Either
    refusal is a refusal; what must never happen is two commits.
    """

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth(f"ent-o-{isolation[:4]}", f"{isolation[:4]}@ent.test"), "Snap")
        tenant.grant(REEL_CREDITS)
    first, first_source = raw_hold(tenant, credits=REEL_CREDITS)
    second, second_source = raw_hold(tenant, credits=REEL_CREDITS)

    results = charge_at_a_barrier(
        [
            {"b": tenant.business_id, "delta": -REEL_CREDITS, "s": first_source, "r": first},
            {"b": tenant.business_id, "delta": -REEL_CREDITS, "s": second_source, "r": second},
        ],
        isolation=isolation,
    )
    assert len([item for item in results if item is None]) == 1, results
    assert summed_balance(tenant.business_id) == 0


@requires_postgres
def test_neither_a_savepoint_nor_a_bulk_insert_smuggles_an_overdraft_past_the_guard() -> None:
    """Attacking the fix from inside one transaction: batching, savepoints, and a rolled-back try.

    A `BEFORE INSERT` row trigger sees each row on its own and sees the rows this transaction has
    already written, so a batch is exactly as safe as the same rows one at a time — and a
    savepoint that rolls a legal charge away gives the credit back rather than leaving a hole for
    the next charge to fall through.
    """

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-32", "owner32@entitlement.test"), "Batch")
        tenant.grant(REEL_CREDITS)
    first, first_source = raw_hold(tenant, credits=REEL_CREDITS)
    second, second_source = raw_hold(tenant, credits=REEL_CREDITS)
    one = {"b": tenant.business_id, "delta": -REEL_CREDITS, "s": first_source, "r": first}
    two = {"b": tenant.business_id, "delta": -REEL_CREDITS, "s": second_source, "r": second}

    async def run() -> None:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with engine.connect() as connection:
                async with connection.begin():
                    # Two charges in one statement: the second one's sum already contains the
                    # first one's row, committed or not.
                    with pytest.raises(DBAPIError, match="negative"):
                        await connection.execute(text(CHARGE_SQL), [one, two])
                async with connection.begin():
                    savepoint = await connection.begin_nested()
                    await connection.execute(text(CHARGE_SQL), one)
                    await savepoint.rollback()
                    with pytest.raises(DBAPIError, match="negative"):
                        await connection.execute(
                            text(CHARGE_SQL), two | {"delta": -REEL_CREDITS * 2}
                        )
        finally:
            await engine.dispose()

    asyncio.run(run())
    assert summed_balance(tenant.business_id) == REEL_CREDITS


@requires_postgres
def test_the_trigger_locks_the_same_thing_the_application_locks() -> None:
    """The constant in the trigger body and the constant in Python are one fact in two places.

    A trigger that locked a *different* key would serialise raw writers against each other and
    against nobody else, and the whole fix would be a comment. There is no way to import Python
    into a `plpgsql` body, so the next best thing is to read the installed definition back.
    """

    definition = query(
        "SELECT pg_get_functiondef(oid) FROM pg_proc WHERE proname = 'credit_ledger_guard_insert'"
    )[0][0]
    assert (
        f"pg_advisory_xact_lock({ADVISORY_LOCK_NAMESPACE}, hashtext(NEW.business_id::text))"
        in definition
    )


@requires_postgres
def test_two_tenants_never_wait_on_each_other_to_spend() -> None:
    """The lock is per tenant, and a busy neighbour must not be able to stall anybody.

    Both charges are released at the same barrier, against different businesses. If the trigger
    locked something global rather than the tenant, one of these would have to wait for the
    other; both commit, and each balance lands where its own arithmetic puts it.
    """

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        left = Tenant(client, auth("ent-owner-33", "owner33@entitlement.test"), "Left")
        right = Tenant(client, auth("ent-owner-34", "owner34@entitlement.test"), "Right")
        left.grant(REEL_CREDITS)
        right.grant(REEL_CREDITS)
    left_hold, left_source = raw_hold(left, credits=REEL_CREDITS)
    right_hold, right_source = raw_hold(right, credits=REEL_CREDITS)

    results = charge_at_a_barrier(
        [
            {"b": left.business_id, "delta": -REEL_CREDITS, "s": left_source, "r": left_hold},
            {"b": right.business_id, "delta": -REEL_CREDITS, "s": right_source, "r": right_hold},
        ]
    )
    assert results == [None, None]
    assert summed_balance(left.business_id) == 0
    assert summed_balance(right.business_id) == 0


@requires_postgres
def test_a_second_refund_cannot_be_written_under_an_invented_key() -> None:
    """W20-F1, closed. Deduplicating on the key deduplicates retries; it does not stop invention.

    The canonical key `refund:<reservation>` already made a *replay* harmless. What it could not
    do is refuse the same refund under a key nobody had seen before, which is money created from
    nothing. Uniqueness now names the reservation and the entry type, so a second refund has
    nowhere to go regardless of what the writer calls it.
    """

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-35", "owner35@entitlement.test"), "Twice")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]
    settle(
        settings,
        business_id=tenant.business_id,
        source_id=project_id,
        outcome=SourceOutcome.ABANDONED,
        failure_code="PROJECT_STATE_TIMEOUT",
    )
    reservation_id = str(reservations_of(tenant.business_id)[0][0])
    assert summed_balance(tenant.business_id) == 50

    with pytest.raises(IntegrityError, match="uq_credit_ledger_reservation_entry"):
        execute(
            REFUND_SQL,
            b=tenant.business_id,
            s=project_id,
            r=reservation_id,
            delta=REEL_CREDITS,
            key=f"whatever-{uuid.uuid4().hex}",
        )
    assert summed_balance(tenant.business_id) == 50

    # And the canonical replay still behaves the way slice W20 built it: no new row, no error.
    settle(
        settings,
        business_id=tenant.business_id,
        source_id=project_id,
        outcome=SourceOutcome.ABANDONED,
        failure_code="PROJECT_STATE_TIMEOUT",
    )
    assert len([entry for entry in entries(tenant.business_id) if entry[0] == "refund"]) == 1
    assert summed_balance(tenant.business_id) == 50


@requires_postgres
def test_a_refund_gives_back_what_the_hold_holds_and_nothing_else() -> None:
    """Uniqueness alone bounds the *count* of refunds. This bounds the amount of the one."""

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-36", "owner36@entitlement.test"), "Inflated")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]
    reservation_id = str(reservations_of(tenant.business_id)[0][0])

    with pytest.raises(DBAPIError, match="must return the"):
        execute(
            REFUND_SQL,
            b=tenant.business_id,
            s=project_id,
            r=reservation_id,
            delta=REEL_CREDITS * 100,
            key=f"inflated-{uuid.uuid4().hex}",
        )
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS


@requires_postgres
def test_a_refund_that_names_no_reservation_is_not_a_refund() -> None:
    """The `NULL` that would have let the uniqueness above be satisfied a second time.

    A partial unique index over `reservation_id` counts nothing where the column is null, so a
    refund with no hold behind it would be both unlimited and unexplainable. Only a grant creates
    credit from nowhere, and a grant says so in its type.
    """

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-37", "owner37@entitlement.test"), "Unbacked")
        tenant.grant(50)

    with pytest.raises(IntegrityError, match="ck_credit_ledger_refund_reserved"):
        execute(
            "INSERT INTO credit_ledger (id, business_id, entry_type, delta_credits, source_type,"
            " correlation_id, created_at) VALUES (gen_random_uuid(), CAST(:b AS uuid), 'refund',"
            " 999, 'content_project', 'hostile', now())",
            b=tenant.business_id,
        )
    assert summed_balance(tenant.business_id) == 50


@requires_postgres
def test_an_entry_cannot_borrow_another_tenants_reservation() -> None:
    """Tenant isolation stops being a property of the queries and becomes one of the table.

    Every repository statement is tenant-scoped, so this shape has no producer. It is refused
    anyway: a refund pointing at a neighbour's hold would be credit in one ledger authorised by
    another, and the query that would have caught it is the one a future writer forgets.
    """

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        mine = Tenant(client, auth("ent-owner-38", "owner38@entitlement.test"), "MineW23")
        theirs = Tenant(client, auth("ent-owner-39", "owner39@entitlement.test"), "TheirsW23")
        mine.grant(50)
        theirs.grant(50)
        their_project = theirs.create_project().json()["id"]
    their_hold = str(reservations_of(theirs.business_id)[0][0])

    with pytest.raises(DBAPIError, match="another business"):
        execute(
            REFUND_SQL,
            b=mine.business_id,
            s=their_project,
            r=their_hold,
            delta=REEL_CREDITS,
            key=f"borrowed-{uuid.uuid4().hex}",
        )
    assert summed_balance(mine.business_id) == 50


def reserve_directly(
    settings: Settings, tenant: Tenant, *, source_id: str, idempotency_key: str
) -> Any:
    """Call `reserve` the way a future module would, with no project route in the way."""

    async def run() -> Any:
        async with factory()() as session:
            async with session.begin():
                return await EntitlementService(session, settings).reserve(
                    business_id=uuid.UUID(tenant.business_id),
                    user_id=uuid.UUID(tenant.user_id),
                    scenario_code=ScenarioCode.PRODUCT_REELS,
                    profile=RenderProfile.INSTAGRAM_REELS_1080X1920,
                    source_type=SOURCE_CONTENT_PROJECT,
                    source_id=uuid.UUID(source_id),
                    idempotency_key=idempotency_key,
                    correlation_id="w23",
                )

    return asyncio.run(run())


@requires_postgres
def test_one_unit_of_work_cannot_be_charged_twice() -> None:
    """W20-F3, closed, in the service and under it.

    `create_project` derives the reservation's key from the project, so the external route never
    asked for this. `reserve` itself did not refuse it, and "the caller happens not to do that" is
    exactly the guarantee this slice exists to replace: a second key aimed at work that is already
    paid for is a second charge for one delivery.
    """

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-40", "owner40@entitlement.test"), "Once")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]

    with pytest.raises(ProblemException) as refused:
        reserve_directly(
            settings, tenant, source_id=project_id, idempotency_key=f"new-{uuid.uuid4().hex}"
        )
    assert refused.value.status == 409
    assert refused.value.code == "ENTITLEMENT_SOURCE_ALREADY_RESERVED"
    assert len(reservations_of(tenant.business_id)) == 1
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS

    # And under the service, where the next writer will be.
    with pytest.raises(IntegrityError, match="uq_usage_reservations_standing_source"):
        raw_hold(tenant, credits=REEL_CREDITS, source_id=project_id)
    assert len(reservations_of(tenant.business_id)) == 1


@requires_postgres
def test_work_whose_hold_was_refunded_can_be_charged_again() -> None:
    """The other half of the same rule: `released` is not standing.

    A cancelled project gives its credit back, and work that was never charged for in the end has
    to be chargeable again — otherwise cancelling would be a one-way door. The settlement that
    follows must find the *new* hold, not the refunded one it already finished with.
    """

    settings = config()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-41", "owner41@entitlement.test"), "Restart")
        tenant.grant(50)
        project_id = tenant.create_project().json()["id"]
    settle(
        settings,
        business_id=tenant.business_id,
        source_id=project_id,
        outcome=SourceOutcome.ABANDONED,
        failure_code="PROJECT_STATE_TIMEOUT",
    )
    assert summed_balance(tenant.business_id) == 50

    reopened = reserve_directly(
        settings, tenant, source_id=project_id, idempotency_key=f"restart-{uuid.uuid4().hex}"
    )
    assert reopened.status is ReservationStatus.RESERVED
    assert len(reservations_of(tenant.business_id)) == 2
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS

    # The settlement is about the hold that stands, not the one that was handed back.
    settle(
        settings,
        business_id=tenant.business_id,
        source_id=project_id,
        outcome=SourceOutcome.DELIVERED,
        failure_code=None,
    )
    assert sorted(row[1] for row in reservations_of(tenant.business_id)) == [
        ReservationStatus.CONSUMED.value,
        ReservationStatus.RELEASED.value,
    ]
    assert summed_balance(tenant.business_id) == 50 - REEL_CREDITS


@requires_postgres
def test_the_sweep_releases_holds_for_several_tenants_in_one_pass() -> None:
    """The sweep takes the tenant lock first and the rows second, and still sweeps everybody.

    That order is what keeps it from deadlocking against a settlement now that the ledger's insert
    trigger takes the same tenant lock. Doing it one tenant at a time must not quietly turn a
    cross-tenant sweep into a single-tenant one, so this drives three at once.
    """

    settings = config()
    fleet = []
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        for index in range(3):
            tenant = Tenant(
                client,
                auth(f"ent-fleet-{index}", f"fleet{index}@entitlement.test"),
                f"Fleet{index}",
            )
            tenant.grant(50)
            fleet.append((tenant, tenant.create_project().json()["id"]))

    for _, project_id in fleet:
        execute("DELETE FROM content_projects WHERE id = CAST(:id AS uuid)", id=project_id)
    execute("UPDATE usage_reservations SET created_at = now() - interval '30 days'")

    assert sweep(settings) == {"examined": 3, "released": 3, "batch_full": 0}
    for tenant, _ in fleet:
        assert reservations_of(tenant.business_id)[0][1] == ReservationStatus.RELEASED.value
        assert summed_balance(tenant.business_id) == 50
    assert sweep(settings) is None


@requires_postgres
def test_a_grant_larger_than_any_integer_the_ledger_holds_is_refused_before_the_ledger() -> None:
    """Criterion 8's overflow case: the refusal is a contract, not a driver exception.

    `delta_credits` is a 32-bit integer. A number past that boundary has to come back as the same
    request-validation answer every other malformed field gets — never as a database error, and
    never as a row.
    """

    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        tenant = Tenant(client, auth("ent-owner-44", "owner44@entitlement.test"), "Overflow")
        for amount in (2**31, 2**63, 10**30):
            refused = tenant.grant(amount)
            assert refused.status_code == 400, refused.text
            assert refused.json()["code"] == "REQUEST_VALIDATION_FAILED"
        assert entries(tenant.business_id) == []
        assert summed_balance(tenant.business_id) == 0
