"""PostgreSQL-backed identity, tenant, and authorization integration tests."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.main import create_app

pytestmark = pytest.mark.integration

SIGNING_KEY = "test-local-identity-signing-key-123"


def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        celery_broker_url=os.environ["CELERY_BROKER_URL"],
        celery_result_backend=os.environ["CELERY_RESULT_BACKEND"],
        local_identity_signing_key=SecretStr(SIGNING_KEY),
    )


def authorization(subject: str, email: str) -> dict[str, str]:
    token = LocalIdentityVerifier.sign_for_testing(
        signing_key=SIGNING_KEY, subject=subject, email=email
    )
    return {"Authorization": f"Bearer {token}"}


async def clear_identity_tables() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE business_members, businesses, external_identities, users CASCADE")
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None, None, None]:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        yield
        return
    asyncio.run(clear_identity_tables())
    yield
    asyncio.run(clear_identity_tables())


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires the local PostgreSQL test service"
)
def test_identity_resolution_business_creation_and_tenant_boundary() -> None:
    with TestClient(create_app(settings()), raise_server_exceptions=False) as client:
        owner = authorization("owner-subject", "Owner@Example.com")
        other = authorization("other-subject", "other@example.com")

        first_me = client.get("/v1/me", headers=owner).json()
        second_me = client.get("/v1/me", headers=owner).json()
        assert first_me["email"] == "owner@example.com"
        assert second_me["id"] == first_me["id"]
        created = client.post(
            "/v1/businesses",
            headers=owner,
            json={"name": "Acme Coffee", "timezone": "Europe/Istanbul"},
        )
        assert created.status_code == 201
        business_id = created.json()["id"]
        assert (
            client.patch(
                f"/v1/businesses/{business_id}", headers=owner, json={"name": "Acme Roastery"}
            ).status_code
            == 200
        )
        assert client.get("/v1/businesses", headers=owner).json()[0]["id"] == business_id
        assert client.get("/v1/businesses", headers=other).json() == []
        denied = client.get(f"/v1/businesses/{business_id}", headers=other)
        assert denied.status_code == 404
        assert denied.json()["code"] == "BUSINESS_NOT_FOUND"


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires the local PostgreSQL test service"
)
def test_membership_authorization_and_owner_protection() -> None:
    with TestClient(create_app(settings()), raise_server_exceptions=False) as client:
        owner = authorization("owner-subject", "owner@example.com")
        admin = authorization("admin-subject", "admin@example.com")
        editor = authorization("editor-subject", "editor@example.com")
        created = client.post(
            "/v1/businesses",
            headers=owner,
            json={"name": "Acme", "timezone": "Europe/Istanbul"},
        )
        business_id = created.json()["id"]
        client.get("/v1/me", headers=admin)
        added = client.post(
            f"/v1/businesses/{business_id}/members",
            headers=owner,
            json={"email": "admin@example.com", "role": "admin"},
        )
        assert added.status_code == 201
        member_id = added.json()["id"]
        assert (
            client.post(
                f"/v1/businesses/{business_id}/members",
                headers=owner,
                json={"email": "admin@example.com", "role": "admin"},
            ).json()["code"]
            == "MEMBER_ALREADY_EXISTS"
        )
        client.get("/v1/me", headers=editor)
        assert (
            client.post(
                f"/v1/businesses/{business_id}/members",
                headers=owner,
                json={"email": "editor@example.com", "role": "editor"},
            ).status_code
            == 201
        )
        assert (
            client.patch(
                f"/v1/businesses/{business_id}", headers=editor, json={"name": "Forbidden"}
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/v1/businesses/{business_id}/members/{member_id}",
                headers=admin,
                json={"role": "owner"},
            ).json()["code"]
            == "INVALID_ROLE_CHANGE"
        )
        members = client.get(f"/v1/businesses/{business_id}/members", headers=owner).json()
        owner_member = next(member for member in members if member["role"] == "owner")
        protected = client.patch(
            f"/v1/businesses/{business_id}/members/{owner_member['id']}",
            headers=owner,
            json={"status": "removed"},
        )
        assert protected.status_code == 409
        assert protected.json()["code"] == "LAST_OWNER_REQUIRED"

        outsider = authorization("outsider-subject", "outsider@example.com")
        outsider_business = client.post(
            "/v1/businesses",
            headers=outsider,
            json={"name": "Outsider business", "timezone": "Europe/Istanbul"},
        )
        outsider_id = outsider_business.json()["id"]
        outsider_member_id = client.get(
            f"/v1/businesses/{outsider_id}/members", headers=outsider
        ).json()[0]["id"]
        cross_tenant_patch = client.patch(
            f"/v1/businesses/{business_id}/members/{outsider_member_id}",
            headers=owner,
            json={"role": "viewer"},
        )
        assert cross_tenant_patch.status_code == 404
        assert cross_tenant_patch.json()["code"] == "MEMBER_NOT_FOUND"


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires the local PostgreSQL test service"
)
def test_identity_conflict_and_business_state_boundaries() -> None:
    with TestClient(create_app(settings()), raise_server_exceptions=False) as client:
        owner = authorization("owner-subject", "owner@example.com")
        second_identity = authorization("second-subject", "OWNER@example.com")
        unauthenticated = client.get("/v1/me")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["WWW-Authenticate"] == "Bearer"
        assert unauthenticated.headers["content-type"].startswith("application/problem+json")

        first_identity = client.get("/v1/me", headers=owner)
        assert first_identity.status_code == 200
        conflicting_identity = client.get("/v1/me", headers=second_identity)
        assert conflicting_identity.status_code == 409
        assert conflicting_identity.json()["code"] == "IDENTITY_CONFLICT"
        assert "OWNER@example.com" not in conflicting_identity.text

        first = client.post(
            "/v1/businesses",
            headers=owner,
            json={"name": "Same name", "timezone": "Europe/Istanbul"},
        )
        second = client.post(
            "/v1/businesses",
            headers=owner,
            json={"name": "Same name", "timezone": "Europe/Istanbul"},
        )
        assert first.status_code == second.status_code == 201
        assert first.json()["slug"] != second.json()["slug"]
        assert (
            client.post(
                "/v1/businesses",
                headers=owner,
                json={"name": "Symbols only ***", "timezone": "Not/AZone"},
            ).status_code
            == 400
        )

        business_id = first.json()["id"]
        archived = client.patch(
            f"/v1/businesses/{business_id}", headers=owner, json={"status": "archived"}
        )
        assert archived.status_code == 200
        assert client.get(f"/v1/businesses/{business_id}", headers=owner).status_code == 200
        immutable = client.patch(
            f"/v1/businesses/{business_id}", headers=owner, json={"name": "No mutation"}
        )
        assert immutable.status_code == 409
        assert immutable.json()["code"] == "BUSINESS_NOT_MUTABLE"


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires the local PostgreSQL test service"
)
def test_concurrent_identity_resolution_creates_one_user() -> None:
    headers = authorization("concurrent-subject", "concurrent@example.com")

    def resolve_identity() -> dict[str, object]:
        with TestClient(create_app(settings()), raise_server_exceptions=False) as client:
            response = client.get("/v1/me", headers=headers)
            assert response.status_code == 200
            return cast(dict[str, object], response.json())

    with ThreadPoolExecutor(max_workers=2) as executor:
        resolved = list(executor.map(lambda _: resolve_identity(), range(2)))

    assert resolved[0]["id"] == resolved[1]["id"]

    async def count_records() -> tuple[int, int]:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with engine.connect() as connection:
                users = await connection.scalar(text("SELECT count(*) FROM users"))
                identities = await connection.scalar(
                    text("SELECT count(*) FROM external_identities")
                )
                return int(users or 0), int(identities or 0)
        finally:
            await engine.dispose()

    assert asyncio.run(count_records()) == (1, 1)


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires the local PostgreSQL test service"
)
def test_concurrent_owner_removal_preserves_an_active_owner() -> None:
    owner_one = authorization("owner-one", "owner-one@example.com")
    owner_two = authorization("owner-two", "owner-two@example.com")
    with TestClient(create_app(settings()), raise_server_exceptions=False) as client:
        created = client.post(
            "/v1/businesses",
            headers=owner_one,
            json={"name": "Concurrent owners", "timezone": "Europe/Istanbul"},
        )
        business_id = created.json()["id"]
        assert client.get("/v1/me", headers=owner_two).status_code == 200
        assert (
            client.post(
                f"/v1/businesses/{business_id}/members",
                headers=owner_one,
                json={"email": "owner-two@example.com", "role": "owner"},
            ).status_code
            == 201
        )
        members = client.get(f"/v1/businesses/{business_id}/members", headers=owner_one).json()
        member_ids = {member["user_id"]: member["id"] for member in members}
        owner_one_id = client.get("/v1/me", headers=owner_one).json()["id"]
        owner_two_id = client.get("/v1/me", headers=owner_two).json()["id"]

    def remove_owner(headers: dict[str, str], member_id: str) -> int:
        with TestClient(create_app(settings()), raise_server_exceptions=False) as client:
            response = client.patch(
                f"/v1/businesses/{business_id}/members/{member_id}",
                headers=headers,
                json={"status": "suspended"},
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(
            executor.map(
                lambda request: remove_owner(*request),
                [(owner_one, member_ids[owner_one_id]), (owner_two, member_ids[owner_two_id])],
            )
        )

    assert sorted(statuses) == [200, 409]
